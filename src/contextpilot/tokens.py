"""Token accounting. Local estimates are always labelled as estimates; exact counts come from the Gemini API."""

from __future__ import annotations

import math


def estimate(text: str) -> int:
    """~4 chars/token heuristic (Gemini's documented rule of thumb). Labelled 'estimate' everywhere."""
    return math.ceil(len(text) / 4) if text else 0


def count_exact(text: str, model: str) -> int | None:
    """Exact count via client.models.count_tokens(); None if the SDK/credentials are unavailable."""
    from . import genai_client

    client = genai_client.get_client()
    if client is None:
        return None
    try:
        resp = client.models.count_tokens(model=model, contents=text)
        return int(resp.total_tokens)
    except Exception:  # network, auth, quota
        return None
