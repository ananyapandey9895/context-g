"""Money math: tokens -> USD/INR, savings summaries, budgets.

Prices are list prices per 1M tokens (USD) for prompts <=200k tokens. They change - users can override any of
them in .ctxly/config.json ("prices": {...}) or in the app's Setup tab. Everything computed here is labelled
"estimate" unless it comes from billed usage_metadata.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

DEFAULT_PRICES = {
    # model: (input, cached_input, output) USD per 1M tokens
    "gemini-2.5-pro": (1.25, 0.125, 10.00),
    "gemini-2.5-flash": (0.30, 0.03, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.01, 0.40),
}
DEFAULT_AGENT_MODEL = "gemini-2.5-pro"  # the model that would have read the whole repo
DEFAULT_USD_INR = 88.0


@dataclass
class Prices:
    table: dict
    agent_model: str
    usd_inr: float
    currency: str  # "USD" | "INR"

    def per_token(self, model: str | None) -> tuple[float, float, float]:
        m = model or self.agent_model
        for key in sorted(self.table, key=len, reverse=True):  # longest prefix wins (flash-lite before flash)
            if m.startswith(key):
                i, c, o = self.table[key]
                return i / 1e6, c / 1e6, o / 1e6
        i, c, o = self.table[self.agent_model]
        return i / 1e6, c / 1e6, o / 1e6

    def cost(self, model: str | None, input_tokens: int = 0, cached_tokens: int = 0, output_tokens: int = 0) -> float:
        i, c, o = self.per_token(model)
        fresh = max(0, (input_tokens or 0) - (cached_tokens or 0))
        return fresh * i + (cached_tokens or 0) * c + (output_tokens or 0) * o

    def fmt(self, usd: float) -> str:
        if self.currency == "INR":
            v = usd * self.usd_inr
            return f"₹{v:,.2f}" if v < 100 else f"₹{v:,.0f}"
        return f"${usd:,.4f}" if usd < 0.01 else f"${usd:,.2f}"


def load_prices(settings) -> Prices:
    extra = getattr(settings, "extra", {}) or {}
    table = dict(DEFAULT_PRICES)
    for k, v in (extra.get("prices") or {}).items():
        if isinstance(v, (list, tuple)) and len(v) == 3:
            table[k] = tuple(float(x) for x in v)
    agent = extra.get("agent_model") or DEFAULT_AGENT_MODEL
    if agent not in table:
        table[agent] = DEFAULT_PRICES[DEFAULT_AGENT_MODEL]
    return Prices(table, agent, float(extra.get("usd_inr") or DEFAULT_USD_INR), str(extra.get("currency") or "USD"))


def savings_summary(store, prices: Prices, days: int | None = None) -> dict:
    """Estimated money saved by sending the scoped context instead of the whole repo, plus billed costs."""
    since = time.time() - days * 86400 if days else 0
    rows = store.conn.execute(
        "SELECT * FROM telemetry WHERE event='optimize' AND source='estimate' AND created_at>=?", (since,)).fetchall()
    naive = sum(r["baseline_tokens"] or 0 for r in rows)
    opt = sum(r["optimized_tokens"] or 0 for r in rows)
    naive_cost = prices.cost(prices.agent_model, naive)
    opt_cost = prices.cost(prices.agent_model, opt)
    usage = store.conn.execute(
        "SELECT COALESCE(SUM(cost_usd),0) c, COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(cached_tokens),0) k,"
        " COALESCE(SUM(output_tokens),0) o, COUNT(*) n FROM usage WHERE created_at>=?", (since,)).fetchone()
    # Savings from cache hits that were actually billed: cached tokens at full price minus what was paid.
    cache_saved = 0.0
    for r in store.conn.execute("SELECT model, cached_tokens FROM usage WHERE created_at>=? AND cached_tokens>0", (since,)):
        i, c, _ = prices.per_token(r["model"])
        cache_saved += (r["cached_tokens"] or 0) * (i - c)
    saved = max(0.0, naive_cost - opt_cost)
    return {
        "days": days, "requests": len(rows), "naive_tokens": naive, "optimized_tokens": opt,
        "saved_usd": saved, "saved": prices.fmt(saved),
        "naive_cost": prices.fmt(naive_cost), "optimized_cost": prices.fmt(opt_cost),
        "billed_usd": usage["c"], "billed": prices.fmt(usage["c"]), "billed_calls": usage["n"],
        "billed_cache_saved_usd": cache_saved, "billed_cache_saved": prices.fmt(cache_saved),
        "billed_tokens": {"input": usage["i"], "cached": usage["k"], "output": usage["o"]},
        "agent_model": prices.agent_model, "currency": prices.currency,
    }


def daily_series(store, prices: Prices, days: int = 14) -> list[dict]:
    out = []
    start = time.time() - days * 86400
    buckets: dict[str, float] = {}
    for r in store.conn.execute("SELECT created_at, baseline_tokens, optimized_tokens FROM telemetry WHERE "
                                "event='optimize' AND source='estimate' AND created_at>=?", (start,)):
        day = time.strftime("%Y-%m-%d", time.localtime(r["created_at"]))
        buckets[day] = buckets.get(day, 0) + prices.cost(prices.agent_model, max(0, (r["baseline_tokens"] or 0) - (r["optimized_tokens"] or 0)))
    for i in range(days - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        v = buckets.get(day, 0.0)
        out.append({"day": day, "saved_usd": v, "saved_display": prices.fmt(v)})
    return out


def session_spend(store, session_id: str) -> float:
    r = store.conn.execute("SELECT COALESCE(SUM(cost_usd),0) c FROM usage WHERE session_id=?", (session_id,)).fetchone()
    return float(r["c"])
