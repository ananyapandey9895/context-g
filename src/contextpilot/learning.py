"""Gets smarter with use: remembers which suggested files were actually edited, and which files each keyword led to.

All local (SQLite). Signals feed back into the surgical selector as small, capped boosts.
"""

from __future__ import annotations

import json

from .store import Store


def record_suggestion(store: Store, paths: list[str]) -> None:
    store.conn.executemany(
        "INSERT INTO file_stats(path, suggested, used) VALUES(?,1,0) "
        "ON CONFLICT(path) DO UPDATE SET suggested=suggested+1", [(p,) for p in paths])


def learn_from_session(store: Store, session_id: str) -> int:
    """Called after a checkpoint: files edited in this session teach the ranker. Idempotent per checkpoint."""
    from .selector import keywords

    sess = store.get_session(session_id)
    if sess is None or not sess["task"]:
        return 0
    ck = store.latest_checkpoint(session_id)
    if ck is None:
        return 0
    key = f"learned:{session_id}"
    done = set(json.loads(store.get_meta(key, "[]")))
    touched = [p for p in json.loads(ck["touched_files"] or "[]") if p not in done]
    if not touched:
        return 0
    kws = keywords(sess["task"])
    rows = [(p,) for p in touched]
    store.conn.executemany("INSERT INTO file_stats(path, suggested, used) VALUES(?,0,1) "
                           "ON CONFLICT(path) DO UPDATE SET used=used+1", rows)
    store.conn.executemany("INSERT INTO keyword_file(keyword, path, hits) VALUES(?,?,1) "
                           "ON CONFLICT(keyword, path) DO UPDATE SET hits=hits+1",
                           [(k, p) for k in kws for p in touched])
    store.set_meta(key, json.dumps(sorted(done | set(touched))))
    return len(touched)


def boosts(store: Store, kws: set[str]) -> dict[str, tuple[float, str]]:
    """path -> (points, reason). Capped so learning nudges, never overrides, explicit signals."""
    out: dict[str, tuple[float, str]] = {}
    if kws:
        q = "SELECT path, SUM(hits) h FROM keyword_file WHERE keyword IN (%s) GROUP BY path" % ",".join("?" * len(kws))
        for r in store.conn.execute(q, tuple(kws)):
            out[r["path"]] = (min(r["h"], 4) * 3.0, f"you edited this for similar requests ({r['h']}×)")
    for r in store.conn.execute("SELECT path, suggested, used FROM file_stats WHERE used>0"):
        rate = r["used"] / max(1, r["suggested"] + r["used"])
        pts, why = out.get(r["path"], (0.0, ""))
        out[r["path"]] = (pts + 4.0 * rate, why or "often edited after suggestions")
    return out
