"""Transparent token math: local estimates and billed usage are reported separately, never blended."""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import genai_client, gitctx
from .store import Store


def status_dict(root: Path, store: Store) -> dict:
    sess = store.active_session()
    ck = store.latest_checkpoint(sess["id"]) if sess else None
    rows = store.telemetry(500)
    est = [r for r in rows if r["event"] == "optimize" and r["source"] == "estimate"]
    billed = [r for r in rows if r["event"] == "optimize_billed"]
    ckpts = [r for r in rows if r["event"] == "checkpoint" and r["latency_ms"] is not None]
    naive = sum(r["baseline_tokens"] or 0 for r in est)
    opt = sum(r["optimized_tokens"] or 0 for r in est)
    return {
        "repo": str(root),
        "branch": gitctx.branch(root),
        "head": gitctx.head(root),
        "backend": genai_client.backend() if genai_client.configured() else "offline (local compiler)",
        "session": None if not sess else {
            "id": sess["id"], "task": sess["task"], "tool": sess["tool"], "status": sess["status"],
            "external_session_id": sess["external_session_id"],
            "updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sess["updated_at"])),
        },
        "last_checkpoint": None if ck is None else {
            "id": ck["id"], "reason": ck["reason"], "elapsed_ms": round(ck["elapsed_ms"] or 0, 1),
            "touched_files": json.loads(ck["touched_files"] or "[]"),
            "selected_files": json.loads(ck["selected_files"] or "[]"),
            "next_steps": ck["next_steps"],
        },
        "estimates_local": {
            "optimizations": len(est), "naive_tokens": naive, "optimized_tokens": opt,
            "reduction_pct": round(100 * (1 - opt / naive), 1) if naive else None,
        },
        "billed_usage": {
            "calls": len(billed),
            "prompt_tokens": sum(r["optimized_tokens"] or 0 for r in billed),
            "cached_tokens": sum(r["cached_tokens"] or 0 for r in billed),
        },
        "checkpoint_latency_ms": {
            "count": len(ckpts),
            "p50": _pct([r["latency_ms"] for r in ckpts], 50),
            "max": round(max((r["latency_ms"] for r in ckpts), default=0), 1),
        },
    }


def _pct(vals: list[float], p: int) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(len(v) * p / 100))], 1)
