"""Measured numbers only - every value here is read back from telemetry rows written at the moment it happened.

Token counts are local estimates (tokens.estimate) unless the row came from Google's usage metadata, which is
reported separately as `billed_*`. Nothing here is extrapolated.
"""

from __future__ import annotations

import json
import time

from .store import Store


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    return round(vals[min(len(vals) - 1, int(q * len(vals)))], 1)


def collect(store: Store, days: int | None = 30) -> dict:
    since = time.time() - days * 86400 if days else 0
    rows = store.conn.execute("SELECT * FROM telemetry WHERE created_at>=? ORDER BY id", (since,)).fetchall()
    opt = [r for r in rows if r["event"] == "optimize"]
    ret = [r["latency_ms"] for r in rows if r["event"] == "retrieve" and r["latency_ms"] is not None]
    hits = sum(1 for r in rows if r["event"] == "cache_hit")
    misses = [r for r in rows if r["event"] == "cache_miss"]
    original = sum(r["baseline_tokens"] or 0 for r in opt)
    optimized = sum(r["optimized_tokens"] or 0 for r in opt)
    checks = [json.loads(r["detail"] or "{}").get("checks") or {} for r in opt]
    cons_total = sum(c.get("constraints_total", 0) for c in checks)
    billed = store.conn.execute("SELECT COUNT(*) n, SUM(input_tokens) i, SUM(cached_tokens) c FROM usage WHERE created_at>=?",
                                (since,)).fetchone()
    mem = store.conn.execute("SELECT kind, status, COUNT(*) n FROM memories GROUP BY kind, status").fetchall()
    return {
        "window_days": days,
        "requests": len(opt),
        "tokens": {"original_est": original, "optimized_est": optimized, "saved_est": max(0, original - optimized),
                   "reduction_pct": round(100 * (1 - optimized / original), 1) if original else None},
        "cache": {"hits": hits, "misses": len(misses),
                  "hit_rate_pct": round(100 * hits / (hits + len(misses)), 1) if hits + len(misses) else None,
                  "miss_reasons": sorted({r["source"] for r in misses})},
        "retrieval_latency_ms": {"p50": _pct(ret, 0.5), "p95": _pct(ret, 0.95), "samples": len(ret)},
        "compile_latency_ms": {"p50": _pct([r["latency_ms"] for r in opt if r["latency_ms"] is not None], 0.5)},
        "quality": {"constraints_kept": sum(c.get("constraints_kept", 0) for c in checks), "constraints_total": cons_total,
                    "duplicate_lines_removed": sum(c.get("duplicate_lines_removed", 0) for c in checks),
                    "invented_files": sum(1 for c in checks if c and not c.get("files_from_selection", True))},
        "billed": {"calls": billed["n"] or 0, "input_tokens": billed["i"] or 0, "cached_tokens": billed["c"] or 0},
        "memory": {f"{r['kind']}:{r['status']}": r["n"] for r in mem},
    }
