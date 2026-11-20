"""Signal wrapper + autosave watcher.

`ctxly wrap gemini` runs the agent as a child process. Every Ctrl+C (SIGINT), SIGTERM, SIGBREAK (Windows) or
SIGHUP is trapped and a checkpoint is written before the terminal closes. The child still receives the
interrupt normally (it shares the console), so the agent behaves exactly as without the wrapper.
"""

from __future__ import annotations

import hashlib
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from . import gitctx
from .checkpoint import create_checkpoint
from .store import Store


def _fingerprint(root: Path) -> str:
    st = gitctx.status(root)
    raw = "|".join(f"{c.status}:{c.path}:{c.added}:{c.removed}" for c in st)
    return hashlib.sha1(raw.encode()).hexdigest()


def _install(handler: Callable) -> dict:
    prev = {}
    names = ["SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP"]
    for n in names:
        sig = getattr(signal, n, None)
        if sig is None:
            continue
        try:
            prev[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
    return prev


def _restore(prev: dict) -> None:
    for sig, h in prev.items():
        try:
            signal.signal(sig, h)
        except (ValueError, OSError):
            pass


def wrap(root: Path, cmd: list[str], tool: str | None = None, autosave_seconds: int = 120,
         on_event: Callable[[str], None] | None = None) -> int:
    say = on_event or (lambda m: print(m, file=sys.stderr))
    store = Store(root)
    sid = store.ensure_session(tool=tool or Path(cmd[0]).stem, git_head=gitctx.head(root), branch=gitctx.branch(root))
    store.update_session(sid, tool=tool or Path(cmd[0]).stem, status="active")
    state = {"last_sig": 0.0, "interrupted": False, "fp": _fingerprint(root)}

    def handler(signum, _frame):
        now = time.monotonic()
        state["interrupted"] = True
        if now - state["last_sig"] < 1.0:  # debounce double Ctrl+C
            return
        state["last_sig"] = now
        name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        try:
            r = create_checkpoint(root, store, reason=f"interrupt:{name}", session_id=sid, code_snapshot="async")
            say(f"\n[ctxly] ⏸ {name} trapped - checkpoint #{r['id']} saved in {r['elapsed_ms']:.0f} ms "
                f"({len(r['touched_files'])} touched files). Resume: ctxly resume")
        except Exception as e:  # never break the user's terminal
            say(f"\n[ctxly] checkpoint failed: {e}")

    prev = _install(handler)
    try:
        try:
            proc = subprocess.Popen(cmd, cwd=root)
        except FileNotFoundError:
            say(f"[ctxly] command not found: {cmd[0]}")
            return 127
        say(f"[ctxly] ⚡ signal trap active for `{' '.join(cmd)}` (session {sid}); autosave every {autosave_seconds}s")
        last_auto = time.monotonic()
        while True:
            try:
                rc = proc.wait(timeout=0.1)  # short waits keep signal handlers responsive on Windows
                break
            except subprocess.TimeoutExpired:
                pass
            if autosave_seconds and time.monotonic() - last_auto >= autosave_seconds:
                last_auto = time.monotonic()
                fp = _fingerprint(root)
                if fp != state["fp"]:
                    state["fp"] = fp
                    create_checkpoint(root, store, reason="autosave", session_id=sid)
        r = create_checkpoint(root, store, reason="interrupted-exit" if state["interrupted"] else "exit", session_id=sid)
        say(f"[ctxly] ✓ session state saved (checkpoint #{r['id']}, {r['elapsed_ms']:.0f} ms). Resume: ctxly resume")
        return rc
    finally:
        _restore(prev)
        store.close()


def watch(root: Path, interval: float = 5.0, debounce: float = 10.0,
          on_event: Callable[[str], None] | None = None) -> None:
    """IDE-friendly autosave (e.g. for Antigravity's editor): checkpoint whenever the working tree settles."""
    say = on_event or print
    store = Store(root)
    sid = store.ensure_session(tool="watch", git_head=gitctx.head(root), branch=gitctx.branch(root))
    fp, changed_at, pending = _fingerprint(root), 0.0, False
    say(f"[ctxly] 👀 watching {root} (session {sid}). Ctrl+C to stop.")
    try:
        while True:
            time.sleep(interval)
            cur = _fingerprint(root)
            if cur != fp:
                fp, changed_at, pending = cur, time.monotonic(), True
            elif pending and time.monotonic() - changed_at >= debounce:
                r = create_checkpoint(root, store, reason="watch", session_id=sid)
                say(f"[ctxly] checkpoint #{r['id']} ({len(r['touched_files'])} files, {r['elapsed_ms']:.0f} ms)")
                pending = False
    except KeyboardInterrupt:
        r = create_checkpoint(root, store, reason="interrupt:watch", session_id=sid)
        say(f"\n[ctxly] final checkpoint #{r['id']} saved in {r['elapsed_ms']:.0f} ms")
    finally:
        store.close()
