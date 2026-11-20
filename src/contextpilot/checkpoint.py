"""Checkpoints (fast path, target <120ms) and recovery bundles."""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import gitctx, tokens
from .scrubber import scrub
from .store import Store


def create_checkpoint(root: Path, store: Store, reason: str = "manual", notes: str | None = None,
                      next_steps: str | None = None, selected_files: list[str] | None = None,
                      include_diff: bool = True, session_id: str | None = None, code_snapshot: str = "sync") -> dict:
    """Snapshot in-flight work: touched files, scrubbed diff vs HEAD, notes and next steps.

    Only the working-tree delta is stored (incremental snapshot) - never the whole repository.
    code_snapshot: "sync" | "async" | "off" - also record a restorable git snapshot (for rewind/undo).
    "async" keeps the Ctrl+C path fast: the memory row is written first, the code snapshot follows.
    """
    t0 = time.perf_counter()
    snap = gitctx.snapshot(root, with_diff=include_diff)
    head, br, changes = snap.head, snap.branch, snap.changes
    sid = session_id or store.ensure_session(git_head=head, branch=br)
    touched = [c.path for c in changes]
    stat = {c.path: [c.status, c.added, c.removed] for c in changes}
    diff = scrub(snap.diff).text if snap.diff else ""

    prev = store.latest_checkpoint(sid)
    if notes is None and prev is not None:
        notes = prev["notes"]
    if next_steps is None and prev is not None:
        next_steps = prev["next_steps"]
    if selected_files is None and prev is not None and prev["selected_files"]:
        selected_files = json.loads(prev["selected_files"])

    notes = scrub(notes).text if notes else notes
    next_steps = scrub(next_steps).text if next_steps else next_steps
    ckpt_id = store.add_checkpoint(
        sid, reason=reason, git_head=head, branch=br, touched_files=touched, diff=diff, diff_stat=stat,
        notes=notes, next_steps=next_steps, selected_files=selected_files or [],
    )
    elapsed = (time.perf_counter() - t0) * 1000
    store.update_checkpoint(ckpt_id, elapsed_ms=elapsed)
    store.update_session(sid, git_head=head, branch=br)
    store.log("checkpoint", "local", sid, latency_ms=elapsed, detail={"reason": reason, "files": len(touched)})
    try:
        from .learning import learn_from_session

        learn_from_session(store, sid)
    except Exception:
        pass
    try:
        from .memory import capture_checkpoint

        # only memories that are new in this checkpoint (inherited notes are already stored)
        new_notes = notes if (prev is None or notes != prev["notes"]) else None
        new_next = next_steps if (prev is None or next_steps != prev["next_steps"]) else None
        capture_checkpoint(store, sid, reason, new_notes, new_next, stat)
    except Exception:
        pass
    if code_snapshot != "off" and head is not None:
        _attach_snapshot(root, ckpt_id, reason, background=code_snapshot == "async")
    return {"id": ckpt_id, "session_id": sid, "elapsed_ms": round(elapsed, 1), "touched_files": touched,
            "reason": reason, "branch": br, "head": head}


def _attach_snapshot(root: Path, ckpt_id: int, label: str, background: bool) -> None:
    def work() -> None:
        from .snapshots import take

        ref = take(root, f"checkpoint #{ckpt_id} ({label})")
        if ref:
            with Store(root) as st:  # own connection: safe from a background thread
                st.update_checkpoint(ckpt_id, snapshot_ref=ref)

    if background:
        import threading

        threading.Thread(target=work, daemon=False).start()  # non-daemon: finishes even if the CLI exits
    else:
        work()


def _session_and_ckpt(store: Store, session_id: str | None):
    sess = store.get_session(session_id) if session_id else store.active_session()
    if sess is None:
        return None, None
    return sess, store.latest_checkpoint(sess["id"])


def _last_active_file(root: Path, touched: list[str]) -> str | None:
    best, when = None, -1.0
    for p in touched[:50]:
        try:
            m = (root / p).stat().st_mtime
        except OSError:
            continue
        if m > when:
            best, when = p, m
    return best


def recovery_bundle(root: Path, store: Store, max_tokens: int = 180, session_id: str | None = None) -> str:
    """A tiny (~180 token) state summary safe to inject into any agent context."""
    sess, ck = _session_and_ckpt(store, session_id)
    if sess is None:
        return ""
    lines = [f"[ContextPilot resume · {sess['id']}]"]
    if sess["task"]:
        lines.append(f"Task: {sess['task']}")
    if ck is not None:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ck["created_at"]))
        lines.append(f"Last checkpoint: {when} ({ck['reason']}) on {ck['branch'] or '?'}@{ck['git_head'] or '?'}")
        sel = json.loads(ck["selected_files"] or "[]")
        if sel:
            lines.append("Scope (read only these): " + ", ".join(sel))
        stat = json.loads(ck["diff_stat"] or "{}")
        if stat:
            lines.append("Modified: " + ", ".join(f"{p} (+{a}/-{r})" for p, (_, a, r) in list(stat.items())[:6]))
        if ck["notes"]:
            lines.append("Notes: " + ck["notes"])
        last = _last_active_file(root, json.loads(ck["touched_files"] or "[]"))
        if last:
            lines.append(f"Last active file: {last}")
    try:
        from .memory import session_progress

        prog = session_progress(store, sess["id"])
    except Exception:
        prog = {"done": [], "open": [], "errors": [], "decisions": []}
    if prog["done"]:
        lines.append("Completed: " + "; ".join(prog["done"]))
    if prog["errors"]:
        lines.append("Unresolved errors: " + "; ".join(prog["errors"]))
    if ck is not None and ck["next_steps"]:
        lines.append("Next: " + ck["next_steps"])
    elif prog["open"]:
        lines.append("Remaining: " + "; ".join(prog["open"]))
    for n in store.notes(sess["id"], limit=3):
        if not ck or n["text"] not in (ck["notes"] or ""):
            lines.append(f"- {n['text']}")
    budget = max_tokens * 4
    # over budget: drop the least useful lines first so "Task", "Unresolved errors" and "Next" survive
    for prefix in ("- ", "Completed:", "Last checkpoint:", "Scope", "Notes:", "Last active file:", "Modified:"):
        while len("\n".join(lines)) > budget and any(l.startswith(prefix) for l in lines):
            lines.pop(max(i for i, l in enumerate(lines) if l.startswith(prefix)))
    text = "\n".join(lines)
    if len(text) > budget:
        text = text[: budget - 1] + "…"
    return text


def full_resume(root: Path, store: Store, session_id: str | None = None, diff_bytes: int = 6000) -> str:
    sess, ck = _session_and_ckpt(store, session_id)
    if sess is None:
        return "No ContextPilot session found. Start one with `ctxly run \"<task>\"`."
    parts = [recovery_bundle(root, store, max_tokens=400, session_id=sess["id"])]
    cur_head = gitctx.head(root)
    if ck is not None and ck["git_head"] and cur_head and cur_head != ck["git_head"]:
        parts.append(f"⚠ HEAD moved since checkpoint: {ck['git_head']} → {cur_head}. Re-verify before editing.")
    if ck is not None and ck["diff"]:
        d = ck["diff"]
        if len(d) > diff_bytes:
            d = d[:diff_bytes] + "\n… [truncated; run `git diff` for the rest]"
        parts.append("In-flight diff at checkpoint (secrets redacted):\n```diff\n" + d.rstrip() + "\n```")
    try:
        from .team import bundle_lines

        team = bundle_lines(root)
    except Exception:
        team = []
    if team:
        parts.append("Teammates (from .ctxly-team):\n" + "\n".join(f"- {t}" for t in team))
    parts.append(
        "Instructions: continue from 'Next'. Do not re-read the whole repository; open only the scoped files. "
        "Never open .env, key or credential files."
    )
    return "\n\n".join(p for p in parts if p)


def resume_json(root: Path, store: Store, session_id: str | None = None) -> dict:
    sess, ck = _session_and_ckpt(store, session_id)
    if sess is None:
        return {"found": False}
    return {
        "found": True,
        "session_id": sess["id"],
        "task": sess["task"],
        "tool": sess["tool"],
        "external_session_id": sess["external_session_id"],
        "checkpoint": None if ck is None else {
            "id": ck["id"], "reason": ck["reason"], "created_at": ck["created_at"], "branch": ck["branch"],
            "head": ck["git_head"], "touched_files": json.loads(ck["touched_files"] or "[]"),
            "selected_files": json.loads(ck["selected_files"] or "[]"), "notes": ck["notes"],
            "next_steps": ck["next_steps"], "elapsed_ms": ck["elapsed_ms"],
        },
        "bundle": recovery_bundle(root, store, session_id=sess["id"]),
        "bundle_tokens_estimate": tokens.estimate(recovery_bundle(root, store, session_id=sess["id"])),
    }
