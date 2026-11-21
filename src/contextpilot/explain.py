"""'What did the AI change?' - a plain-English summary of changes since the last undo point."""

from __future__ import annotations

import re
from pathlib import Path

from . import genai_client
from .scrubber import scrub
from .snapshots import diff_since
from .store import Store
from .symbols import parse_symbols, read_text

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def undo_point(store: Store, session_id: str | None = None):
    """Most recent checkpoint with a code snapshot taken right before the AI was given work."""
    sess = store.get_session(session_id) if session_id else store.active_session()
    if sess is None:
        return None
    row = store.conn.execute(
        "SELECT * FROM checkpoints WHERE session_id=? AND snapshot_ref IS NOT NULL AND reason='optimize' "
        "ORDER BY id DESC LIMIT 1", (sess["id"],)).fetchone()
    return row or store.conn.execute(
        "SELECT * FROM checkpoints WHERE session_id=? AND snapshot_ref IS NOT NULL ORDER BY id DESC LIMIT 1",
        (sess["id"],)).fetchone()


def _per_file(diff: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for block in re.split(r"(?m)^diff --git ", diff):
        if not block.strip():
            continue
        first = block.split("\n", 1)[0]
        path = first.rsplit(" b/", 1)[-1].strip()
        status = "created" if "\nnew file mode" in block else "deleted" if "\ndeleted file mode" in block else "changed"
        ranges = [(int(m.group(1)), int(m.group(1)) + max(int(m.group(2) or 1), 1) - 1) for m in _HUNK.finditer(block)]
        out[path] = {"status": status, "ranges": ranges, "patch": "diff --git " + block}
    return out


def offline_summary(root: Path, files: list[dict], per_file: dict[str, dict]) -> list[str]:
    bullets = []
    for f in files:
        info = per_file.get(f["path"], {"status": "changed", "ranges": []})
        if info["status"] == "created":
            bullets.append(f"Created a new file **{f['path']}** ({f['added']} lines).")
            continue
        if info["status"] == "deleted":
            bullets.append(f"Deleted **{f['path']}**.")
            continue
        src = read_text(root, f["path"]) or ""
        syms = parse_symbols(f["path"], src)
        touched = []
        for a, b in info["ranges"]:
            for s in syms:
                if s.start <= b and s.end >= a and s.qualname not in touched and (s.end - s.start) < 400:
                    touched.append(s.qualname)
        what = f" - inside {', '.join(f'`{t}`' for t in touched[:4])}" if touched else ""
        bullets.append(f"Edited **{f['path']}**{what} (+{f['added']} / -{f['removed']} lines).")
    return bullets


def gemini_summary(diff: str, task: str | None, model: str) -> list[str] | None:
    client = genai_client.get_client()
    if client is None or not diff.strip():
        return None
    from google.genai import types

    prompt = (
        "Explain this code change to a non-programmer in 3-6 short bullet points. Say what behaviour changed "
        "and why it matters, not how the code works. No code blocks. Start each bullet with '- '.\n\n"
        + (f"The user had asked: {task}\n\n" if task else "") + "Diff:\n" + scrub(diff[:30000]).text
    )
    try:
        r = client.models.generate_content(model=model, contents=prompt,
                                           config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=500))
        bullets = [l.lstrip("-*• ").strip() for l in (r.text or "").splitlines() if l.strip().startswith(("-", "*", "•"))]
        return bullets or None
    except Exception:
        return None


def explain_changes(root: Path, store: Store, model: str = "gemini-3.8-flash", use_llm: bool = True,
                    checkpoint_id: int | None = None) -> dict:
    ck = store.get_checkpoint(checkpoint_id) if checkpoint_id else undo_point(store)
    if ck is None or not ck["snapshot_ref"]:
        return {"found": False, "message": "Nothing to compare yet. Prepare a request first - ContextPilot saves an "
                                           "undo point right before you hand work to the AI."}
    d = diff_since(root, ck["snapshot_ref"])
    if not d["files"]:
        return {"found": True, "changed": False, "since": ck["created_at"], "checkpoint_id": ck["id"],
                "message": "No changes since the undo point - the AI hasn't edited anything yet."}
    per = _per_file(d["diff"])
    sess = store.get_session(ck["session_id"])
    bullets = gemini_summary(d["diff"], sess["task"] if sess else None, model) if use_llm else None
    source = "gemini" if bullets else "local"
    if not bullets:
        bullets = offline_summary(root, d["files"], per)
    return {"found": True, "changed": True, "since": ck["created_at"], "checkpoint_id": ck["id"], "source": source,
            "bullets": bullets, "files": d["files"], "diff": scrub(d["diff"]).text,
            "totals": {"files": len(d["files"]), "added": sum(f["added"] for f in d["files"]),
                       "removed": sum(f["removed"] for f in d["files"])}}
