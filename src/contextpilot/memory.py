"""Persistent project memory (local SQLite, table `memories`).

Four kinds of memory, kept apart so each can be retrieved for the right reason:

  scope   kind       what it holds                                      lifetime
  ------- ---------- -------------------------------------------------- -------------------------------
  project decision   architectural decisions ("use JWT in cookies, not localStorage")  long-term, high priority
  project error      unresolved errors / failing tests                  until resolved
  session task       pending / completed steps for the current task     task-specific
  session change     what code changed in a session                     task-specific
  session fact       findings while debugging                           short-term

Everything is secret-scrubbed before it is written, and near-duplicates are merged instead of stored twice
(the duplicate only bumps `hits` / `updated_at`), so the store does not fill with repetition.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time

from .scrubber import scrub
from .store import Store

KINDS = ("decision", "task", "error", "change", "fact")
DEFAULT_SCOPE = {"decision": "project", "error": "project", "task": "session", "change": "session", "fact": "session"}
KIND_WEIGHT = {"decision": 2.5, "error": 2.0, "task": 1.5, "change": 0.8, "fact": 1.0}
_DECISION_HINT = re.compile(r"(?i)\b(decided|decision|we will|we'll always|always use|never use|instead of|architecture|"
                            r"convention|must (?:always|never)|standardi[sz]e)\b")
_WORD = re.compile(r"[a-z0-9_]{3,}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s/.-]", " ", text.lower())).strip()


def _words(text: str) -> set[str]:
    return set(_WORD.findall(_norm(text)))


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def looks_like_decision(text: str) -> bool:
    return bool(_DECISION_HINT.search(text or ""))


def remember(store: Store, kind: str, text: str, files: list[str] | tuple = (), session_id: str | None = None,
             scope: str | None = None, status: str | None = None, source: str = "user",
             fingerprint: str | None = None) -> dict | None:
    """Store one memory. Returns {"id", "created": bool} or None when the text is too small to be useful."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    text = scrub(re.sub(r"\s+", " ", text or "").strip()).text[:1200]
    if len(text) < 6:
        return None
    scope = scope or DEFAULT_SCOPE[kind]
    status = status or ("open" if kind in ("task", "error") else "info")
    files = sorted({f for f in files if f})[:20]
    fp = fingerprint or hashlib.sha1(f"{kind}|{scope}|{session_id if scope == 'session' else ''}|{_norm(text)}".encode()).hexdigest()
    now = time.time()
    row = store.conn.execute("SELECT * FROM memories WHERE fingerprint=?", (fp,)).fetchone()
    if row is None:
        # near-duplicate of a recent memory of the same kind? merge instead of storing it twice
        words = _words(text)
        for r in store.conn.execute("SELECT * FROM memories WHERE kind=? AND scope=? ORDER BY updated_at DESC LIMIT 200",
                                    (kind, scope)):
            if (scope == "project" or r["session_id"] == session_id) and _jaccard(words, _words(r["text"])) >= 0.85:
                row = r
                break
    if row is not None:
        merged = sorted(set(json.loads(row["files"] or "[]")) | set(files))[:20]
        new_text = text if fingerprint else row["text"]  # keyed memories (e.g. a session's changes) are rewritten
        store.conn.execute("UPDATE memories SET updated_at=?, hits=hits+1, files=?, text=?, status=CASE WHEN ?='open' "
                           "THEN 'open' ELSE status END WHERE id=?",
                           (now, json.dumps(merged), new_text, status, row["id"]))
        return {"id": row["id"], "created": False}
    cur = store.conn.execute(
        "INSERT INTO memories(created_at, updated_at, kind, scope, session_id, status, text, files, source, fingerprint)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)", (now, now, kind, scope, session_id, status, text, json.dumps(files), source, fp))
    return {"id": cur.lastrowid, "created": True}


def set_status(store: Store, memory_id: int, status: str) -> bool:
    if status not in ("open", "done", "info"):
        raise ValueError("status must be open, done or info")
    return store.conn.execute("UPDATE memories SET status=?, updated_at=? WHERE id=?",
                              (status, time.time(), memory_id)).rowcount > 0


def forget(store: Store, memory_id: int) -> bool:
    return store.conn.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0


def as_dict(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "scope": r["scope"], "status": r["status"], "text": r["text"],
            "files": json.loads(r["files"] or "[]"), "session_id": r["session_id"], "hits": r["hits"],
            "created_at": r["created_at"], "updated_at": r["updated_at"], "source": r["source"]}


def list_memories(store: Store, kind: str | None = None, status: str | None = None, session_id: str | None = None,
                  limit: int = 100) -> list[dict]:
    q, a = "SELECT * FROM memories WHERE 1=1", []
    for col, val in (("kind", kind), ("status", status), ("session_id", session_id)):
        if val:
            q += f" AND {col}=?"
            a.append(val)
    q += " ORDER BY updated_at DESC LIMIT ?"
    return [as_dict(r) for r in store.conn.execute(q, (*a, limit))]


def recall(store: Store, task: str, files: list[str] | tuple = (), session_id: str | None = None,
           limit: int = 6) -> list[dict]:
    """Memories relevant to `task`, best first. Relevance = shared words + shared files, weighted by kind,
    recency, and how often the memory recurred. Completed tasks and resolved errors only come back when they
    match strongly; architectural decisions and open errors are favoured."""
    from .selector import keywords, stem

    kws = keywords(task or "")
    fset = set(files)
    now = time.time()
    scored = []
    for r in store.conn.execute("SELECT * FROM memories ORDER BY updated_at DESC LIMIT 2000"):
        mwords = {stem(w) for w in _words(r["text"])}
        mfiles = set(json.loads(r["files"] or "[]"))
        word_hit = len(kws & mwords)
        file_hit = len(fset & mfiles) + sum(1 for f in mfiles for k in kws if k in f.lower())
        same_session = session_id is not None and r["session_id"] == session_id
        if not (word_hit or file_hit or same_session):
            continue
        if r["scope"] == "session" and not same_session and not (word_hit >= 2 or file_hit):
            continue  # someone else's short-term notes only when clearly related
        if r["status"] == "done" and word_hit + file_hit < 2:
            continue
        age_days = (now - r["updated_at"]) / 86400
        score = (2.0 * word_hit + 1.5 * min(file_hit, 3) + (2.0 if same_session else 0)) * KIND_WEIGHT[r["kind"]]
        score *= 0.5 + 0.5 * math.exp(-age_days / 21)          # gently prefer recent memories
        score *= 1 + 0.15 * math.log(r["hits"])                 # things that kept coming up matter more
        if r["status"] == "open":
            score *= 1.3
        scored.append((score, r))
    scored.sort(key=lambda t: -t[0])
    return [{**as_dict(r), "score": round(s, 2)} for s, r in scored[:limit]]


def render(memories: list[dict], exclude: str = "") -> str:
    """Compact, de-duplicated lines for a prompt. Lines already present in `exclude` are dropped."""
    label = {"decision": "Decision", "error": "Unresolved error", "task": "Task", "change": "Changed", "fact": "Finding"}
    seen, out = _norm(exclude), []
    for m in memories:
        if _norm(m["text"]) and _norm(m["text"]) in seen:
            continue
        seen += " " + _norm(m["text"])
        tag = label[m["kind"]] + (" (done)" if m["status"] == "done" and m["kind"] in ("task", "error") else "")
        out.append(f"- {tag}: {m['text']}")
    return "\n".join(out)


# ---------------------------------------------------------------- automatic capture
def capture_checkpoint(store: Store, session_id: str, reason: str, notes: str | None, next_steps: str | None,
                       stat: dict) -> None:
    """Called from create_checkpoint: turns notes / next steps / diffs into typed memories."""
    if next_steps:
        remember(store, "task", next_steps, session_id=session_id, status="open", source="checkpoint")
    if notes:
        if looks_like_decision(notes):
            remember(store, "decision", notes, session_id=session_id, source="checkpoint")
        else:
            remember(store, "fact", notes, session_id=session_id, source="checkpoint")
    if stat and reason in ("agent", "manual", "ui", "pre-compress", "compaction-defense") or (stat and reason.startswith("session-end")):
        summary = ", ".join(f"{p} (+{a}/-{r})" for p, (_, a, r) in list(stat.items())[:8])
        remember(store, "change", f"Modified {summary}", files=list(stat), session_id=session_id, source="checkpoint",
                 fingerprint=hashlib.sha1(f"change|{session_id}".encode()).hexdigest())
    # a new "next step" supersedes the previous open one in this session
    if next_steps:
        rows = store.conn.execute("SELECT id, text FROM memories WHERE kind='task' AND status='open' AND session_id=? "
                                  "ORDER BY updated_at DESC", (session_id,)).fetchall()
        for r in rows[1:]:
            if _norm(r["text"]) != _norm(scrub(next_steps).text):
                set_status(store, r["id"], "done")


def capture_verification(store: Store, session_id: str | None, result: dict) -> None:
    """Failing tests become an open error memory; a later pass resolves open test errors."""
    if not result.get("ran") or result.get("passed") is None:
        return
    if result["passed"] is False:
        files = sorted(set(re.findall(r"([\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb))(?::\d+)?", result.get("output_tail") or "")))[:8]
        remember(store, "error", f"Tests failing: {result.get('summary') or 'see output'}", files=files,
                 session_id=session_id, source="tests", status="open")
    else:
        store.conn.execute("UPDATE memories SET status='done', updated_at=? WHERE kind='error' AND source='tests' "
                           "AND status='open'", (time.time(),))


def session_progress(store: Store, session_id: str) -> dict:
    """Completed / remaining tasks and open errors for the recovery summary."""
    rows = list_memories(store, session_id=session_id, limit=200)
    errs = [m for m in list_memories(store, kind="error", status="open", limit=5)]
    return {"done": [m["text"] for m in rows if m["kind"] == "task" and m["status"] == "done"][:3],
            "open": [m["text"] for m in rows if m["kind"] == "task" and m["status"] == "open"][:3],
            "errors": [m["text"] for m in errs][:2],
            "decisions": [m["text"] for m in list_memories(store, kind="decision", limit=3)]}
