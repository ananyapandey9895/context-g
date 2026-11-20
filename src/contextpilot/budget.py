"""Budget guard + compaction defense driven by real model usage.

Gemini CLI hooks (AfterModel / BeforeModel) report the agent's token usage. We record it, price it, warn at 80%
of the session budget, stop the agent at 100%, and checkpoint before the context nears the compaction ceiling.
"""

from __future__ import annotations

import time
from typing import Any

from .pricing import load_prices, session_spend
from .store import Store


def find_usage(payload: Any) -> dict | None:
    """Locate a usageMetadata-like dict anywhere in a hook payload (shapes vary between CLI versions)."""
    if isinstance(payload, dict):
        keys = {k.lower() for k in payload}
        if {"prompttokencount", "candidatestokencount"} & keys or {"prompt_token_count"} & keys or \
                {"input_tokens", "output_tokens"} <= keys:
            return payload
        for v in payload.values():
            r = find_usage(v)
            if r:
                return r
    elif isinstance(payload, list):
        for v in payload:
            r = find_usage(v)
            if r:
                return r
    return None


def _get(d: dict, *names: str) -> int:
    for n in names:
        for k, v in d.items():
            if k.lower() == n.lower() and isinstance(v, (int, float)):
                return int(v)
    return 0


def _find_model(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k.lower() in ("model", "modelversion", "model_version") and isinstance(v, str):
                return v
        for v in payload.values():
            m = _find_model(v)
            if m:
                return m
    return None


def record(store: Store, settings, session_id: str, payload: dict, source: str = "gemini-cli") -> dict | None:
    u = find_usage(payload)
    if not u:
        return None
    prices = load_prices(settings)
    model = _find_model(payload) or prices.agent_model
    inp = _get(u, "promptTokenCount", "prompt_token_count", "input_tokens")
    cached = _get(u, "cachedContentTokenCount", "cached_content_token_count", "cached_tokens")
    out = _get(u, "candidatesTokenCount", "candidates_token_count", "output_tokens")
    cost = prices.cost(model, inp, cached, out)
    store.conn.execute("INSERT INTO usage(session_id, created_at, source, model, input_tokens, cached_tokens,"
                       " output_tokens, cost_usd) VALUES(?,?,?,?,?,?,?,?)",
                       (session_id, time.time(), source, model, inp, cached, out, cost))
    return {"model": model, "input": inp, "cached": cached, "output": out, "cost_usd": cost}


def budget_for(store: Store, settings, session_id: str) -> float | None:
    sess = store.get_session(session_id)
    if sess is not None and sess["budget_usd"]:
        return float(sess["budget_usd"])
    v = (getattr(settings, "extra", {}) or {}).get("session_budget_usd")
    return float(v) if v else None


def status(store: Store, settings, session_id: str) -> dict:
    spent = session_spend(store, session_id)
    budget = budget_for(store, settings, session_id)
    prices = load_prices(settings)
    pct = (spent / budget * 100) if budget else None
    state = "none" if not budget else "over" if spent >= budget else "warn" if pct >= 80 else "ok"
    return {"spent_usd": spent, "spent": prices.fmt(spent), "budget_usd": budget,
            "budget": prices.fmt(budget) if budget else None, "pct": round(pct, 1) if pct is not None else None,
            "state": state}
