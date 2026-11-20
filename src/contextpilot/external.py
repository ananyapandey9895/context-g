"""Human-in-the-loop control for anything that leaves the computer.

Setting `external_mode` (Setup → Privacy, or .ctxly/config.json):
  "auto"  - smart features may call Gemini (default; text is always secret-scrubbed first)
  "ask"   - nothing is sent until the user has seen exactly what would be sent and approved it
  "off"   - never call an external model; everything runs locally
"""

from __future__ import annotations

MODES = ("auto", "ask", "off")


def mode(settings) -> str:
    m = str((settings.extra or {}).get("external_mode") or "auto").lower()
    return m if m in MODES else "auto"


def decide(settings, wants_external: bool, approved: bool = False) -> tuple[bool, bool]:
    """-> (may_call_external, needs_approval)."""
    if not wants_external:
        return False, False
    m = mode(settings)
    if m == "off":
        return False, False
    if m == "ask" and not approved:
        return False, True
    return True, False
