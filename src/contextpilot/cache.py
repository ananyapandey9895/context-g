"""Deterministic, cache-friendly prefix + Vertex AI / Gemini API explicit context caching.

The prefix (repo map + symbol index + rules) is byte-stable as long as the code structure is unchanged, so
 * explicit caches (CachedContent) are reused across turns and restarts, keyed by the prefix hash, and
 * implicit prefix caching in Gemini CLI / Antigravity keeps hitting because GEMINI.md / the skill prefix
   never churns between turns.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from . import genai_client, tokens
from .optimizer import SYSTEM_INSTRUCTION
from .scrubber import scrub
from .store import Store
from .symbols import load_index

# Explicit caching has a minimum size. Google's Gemini API docs (checked Nov 2026) list 2,048 tokens for Gemini 2.5
# Flash and 2.5 Pro, and Vertex AI uses 2,048 for all models; older pages said 1,024 / 4,096. Below the minimum we skip
# creation and rely on implicit prefix caching. Override with `cache_min_tokens` in .ctxly/config.json if Google changes it.
MIN_CACHE_TOKENS = 2048
# Cached input tokens are billed at ~10% of the normal input price on Gemini 2.5+ models.
CACHED_PRICE_FRACTION = 0.10


def build_prefix(root: Path, store: Store, max_symbols_per_file: int = 40) -> str:
    """Stable project map. Contains structure only (paths + symbol signatures), never file bodies or secrets."""
    index = load_index(store)
    lines = [
        "# ContextPilot project map (stable prefix - do not edit)",
        f"repo: {root.name}",
        f"files: {len(index)}",
        "",
    ]
    for path in sorted(index):
        ntok, syms = index[path]
        lines.append(f"{path}  (~{ntok} tok)")
        for s in syms[:max_symbols_per_file]:
            lines.append(f"  {s.kind[0]} {s.qualname} L{s.start}-{s.end}")
        if len(syms) > max_symbols_per_file:
            lines.append(f"  … {len(syms) - max_symbols_per_file} more")
    return scrub("\n".join(lines) + "\n").text


def prefix_hash(prefix: str) -> str:
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16]


def write_prefix_file(root: Path, store: Store) -> tuple[Path, str]:
    prefix = build_prefix(root, store)
    path = root / ".ctxly" / "prefix.md"
    if not path.exists() or path.read_text(encoding="utf-8") != prefix:
        path.write_text(prefix, encoding="utf-8")
    return path, prefix_hash(prefix)


@dataclass
class CacheResult:
    status: str  # "reused" | "created" | "skipped" | "unavailable" | "error"
    cache_name: str | None
    prefix_hash: str
    token_count: int
    message: str = ""


def ensure_cache(root: Path, store: Store, model: str, ttl_seconds: int = 3600,
                 min_tokens: int | None = None) -> CacheResult:
    prefix = build_prefix(root, store)
    h = prefix_hash(prefix)
    ntok = tokens.estimate(prefix)
    row = store.get_cache(h)
    if row and row["cache_name"] and row["model"] == model and row["expires_at"] > time.time() + 60:
        return CacheResult("reused", row["cache_name"], h, row["token_count"], "prefix unchanged - cache hit")
    minimum = int((min_tokens or 0) or MIN_CACHE_TOKENS)
    if ntok < minimum:
        return CacheResult("skipped", None, h, ntok, f"prefix ~{ntok} tok < {minimum} minimum for explicit caching")
    client = genai_client.get_client()
    if client is None:
        return CacheResult("unavailable", None, h, ntok, "no Gemini/Vertex credentials (set GEMINI_API_KEY or Vertex env)")
    try:
        from google.genai import types

        cache = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                display_name=f"ctxly-{root.name[:20]}-{h[:8]}",
                system_instruction=SYSTEM_INSTRUCTION,
                contents=[types.Content(role="user", parts=[types.Part(text=prefix)])],
                ttl=f"{int(ttl_seconds)}s",
            ),
        )
        um = getattr(cache, "usage_metadata", None)
        exact = getattr(um, "total_token_count", None) or ntok
        store.put_cache(h, cache.name, model, time.time() + ttl_seconds, int(exact))
        store.log("cache_create", genai_client.backend(), cached_tokens=int(exact), detail={"name": cache.name})
        return CacheResult("created", cache.name, h, int(exact), "new CachedContent created")
    except Exception as e:
        return CacheResult("error", None, h, ntok, f"{type(e).__name__}: {e}"[:300])


def savings_estimate(prefix_tokens: int, turns: int) -> dict:
    """Input-token cost of re-sending the prefix every turn vs caching it (in token-price units)."""
    uncached = prefix_tokens * turns
    cached = prefix_tokens + prefix_tokens * CACHED_PRICE_FRACTION * (turns - 1)
    return {"uncached": uncached, "cached": round(cached), "saved_pct": round(100 * (1 - cached / uncached), 1) if uncached else 0}
