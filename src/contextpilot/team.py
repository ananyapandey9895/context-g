"""Team memory: share session notes through the repository itself (no server needed).

`.ctxly-team/<member>.json` holds each member's latest task, notes, next steps and scoped files (secrets scrubbed,
no diffs, no code). Commit it like any other file; teammates' agents then see "who is doing what" on resume.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from .scrubber import scrub
from .store import Store

TEAM_DIR = ".ctxly-team"


def member_name(root: Path) -> str:
    try:
        out = subprocess.run(["git", "config", "user.name"], cwd=root, capture_output=True, text=True, timeout=5)
        name = out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        name = ""
    return name or "me"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "me"


def share(root: Path, store: Store, name: str | None = None) -> Path:
    name = name or member_name(root)
    sess = store.active_session()
    if sess is None:
        raise ValueError("Nothing to share yet - save some progress first.")
    ck = store.latest_checkpoint(sess["id"])
    entry = {
        "member": name,
        "updated_at": time.time(),
        "task": scrub(sess["task"] or "").text,
        "notes": ck["notes"] if ck is not None else None,
        "next_steps": ck["next_steps"] if ck is not None else None,
        "files": json.loads(ck["selected_files"] or "[]") if ck is not None else [],
        "branch": ck["branch"] if ck is not None else None,
    }
    d = root / TEAM_DIR
    d.mkdir(exist_ok=True)
    readme = d / "README.md"
    if not readme.exists():
        readme.write_text("# ContextPilot team memory\n\nShared, secret-free session notes. Commit this folder so "
                          "teammates' AI assistants know who is working on what.\n", encoding="utf-8")
    path = d / f"{_slug(name)}.json"
    path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    return path


def feed(root: Path, exclude_self: bool = False) -> list[dict]:
    d = root / TEAM_DIR
    if not d.is_dir():
        return []
    me = _slug(member_name(root))
    items = []
    for f in sorted(d.glob("*.json")):
        if exclude_self and f.stem == me:
            continue
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(items, key=lambda e: -(e.get("updated_at") or 0))


def bundle_lines(root: Path, limit: int = 3) -> list[str]:
    """Short lines for recovery bundles: what teammates are on."""
    lines = []
    for e in feed(root, exclude_self=True)[:limit]:
        bit = f"{e.get('member')}: {e.get('task') or '?'}"
        if e.get("next_steps"):
            bit += f" -> next: {e['next_steps']}"
        lines.append(bit[:200])
    return lines
