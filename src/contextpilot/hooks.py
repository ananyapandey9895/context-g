"""Gemini CLI hook handlers (`ctxly hook <event>`), registered in .gemini/settings.json.

Contract: read the hook's JSON payload from stdin, write at most one JSON object to stdout, always exit 0.
A ContextPilot failure must never block the user's agent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .checkpoint import create_checkpoint, recovery_bundle
from .config import find_repo_root, load_settings
from .store import Store


def _read_payload() -> dict:
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def run_hook(event: str) -> int:
    payload = _read_payload()
    root = find_repo_root(payload.get("cwd") or None)
    out: dict = {}
    try:
        s = load_settings(root)
        with Store(root) as store:
            ext = payload.get("session_id")
            if event == "session-start":
                sid = store.ensure_session(tool="gemini")
                store.update_session(sid, tool="gemini", **({"external_session_id": ext} if ext else {}))
                bundle = recovery_bundle(root, store, s.recovery_bundle_tokens, sid)
                if bundle and store.latest_checkpoint(sid) is not None:
                    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": bundle}}
            elif event in ("session-end", "pre-compress"):
                sid = store.ensure_session(tool="gemini")
                if ext:
                    store.update_session(sid, external_session_id=ext)
                reason = "pre-compress" if event == "pre-compress" else f"session-end:{payload.get('reason', 'exit')}"
                create_checkpoint(root, store, reason=reason, session_id=sid)
                if event == "pre-compress":
                    from .memory_sync import sync_gemini_md

                    sync_gemini_md(root, store)
                    out = {"systemMessage": "ContextPilot saved state before compression."}
            elif event == "after-model":
                from .budget import record, status as budget_status

                sid = store.ensure_session(tool="gemini")
                u = record(store, s, sid, payload)
                msgs = []
                if u and u["input"] >= s.compaction_threshold_tokens:
                    last = float(store.get_meta(f"compaction_ckpt:{sid}", "0") or 0)
                    import time as _t
                    if _t.time() - last > 300:  # at most every 5 minutes
                        create_checkpoint(root, store, reason="compaction-defense", session_id=sid, code_snapshot="async")
                        store.set_meta(f"compaction_ckpt:{sid}", str(_t.time()))
                        msgs.append(f"ContextPilot: context is {u['input']:,} tokens - progress saved before compaction.")
                b = budget_status(store, s, sid)
                if b["state"] == "warn":
                    msgs.append(f"ContextPilot budget: {b['spent']} of {b['budget']} used ({b['pct']}%).")
                if msgs:
                    out = {"systemMessage": " ".join(msgs)}
            elif event == "before-model":
                from .budget import status as budget_status

                sid = store.ensure_session(tool="gemini")
                b = budget_status(store, s, sid)
                if b["state"] == "over":
                    create_checkpoint(root, store, reason="budget-stop", session_id=sid, code_snapshot="async")
                    msg = (f"ContextPilot budget reached: {b['spent']} of {b['budget']}. Progress saved. "
                           "Raise the budget in the ContextPilot app to continue.")
                    out = {"continue": False, "stopReason": msg, "decision": "deny", "reason": msg, "systemMessage": msg}
    except Exception as e:  # pragma: no cover - defensive
        print(f"[ctxly hook {event}] {e}", file=sys.stderr)
    if out:
        sys.stdout.write(json.dumps(out))
    return 0
