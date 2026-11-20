"""Local SQLite (WAL) memory store: sessions, checkpoints, notes, symbol index, caches, telemetry."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .config import ctxly_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  task TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  tool TEXT,
  external_session_id TEXT,
  git_head TEXT,
  branch TEXT
);
CREATE TABLE IF NOT EXISTS checkpoints(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  reason TEXT,
  git_head TEXT,
  branch TEXT,
  touched_files TEXT,
  diff TEXT,
  diff_stat TEXT,
  notes TEXT,
  next_steps TEXT,
  selected_files TEXT,
  elapsed_ms REAL
);
CREATE INDEX IF NOT EXISTS ix_ckpt_session ON checkpoints(session_id, id);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  kind TEXT NOT NULL DEFAULT 'note',
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file_index(
  path TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL,
  mtime REAL NOT NULL,
  size INTEGER NOT NULL,
  tokens INTEGER NOT NULL,
  symbols TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS caches(
  prefix_hash TEXT PRIMARY KEY,
  cache_name TEXT,
  model TEXT,
  created_at REAL,
  expires_at REAL,
  token_count INTEGER
);
CREATE TABLE IF NOT EXISTS telemetry(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  created_at REAL NOT NULL,
  event TEXT NOT NULL,
  source TEXT NOT NULL,
  baseline_tokens INTEGER,
  optimized_tokens INTEGER,
  cached_tokens INTEGER,
  latency_ms REAL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS verifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  created_at REAL NOT NULL,
  command TEXT,
  passed INTEGER,
  exit_code INTEGER,
  summary TEXT,
  output_tail TEXT,
  duration_ms REAL
);
CREATE TABLE IF NOT EXISTS file_stats(
  path TEXT PRIMARY KEY,
  suggested INTEGER NOT NULL DEFAULT 0,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS keyword_file(
  keyword TEXT NOT NULL,
  path TEXT NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(keyword, path)
);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  created_at REAL NOT NULL,
  source TEXT NOT NULL,
  model TEXT,
  input_tokens INTEGER,
  cached_tokens INTEGER,
  output_tokens INTEGER,
  cost_usd REAL
);
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  kind TEXT NOT NULL,              -- decision | task | error | change | fact
  scope TEXT NOT NULL,             -- project (long-term) | session (short-term)
  session_id TEXT,
  status TEXT NOT NULL DEFAULT 'info',  -- open | done | info
  text TEXT NOT NULL,
  files TEXT NOT NULL DEFAULT '[]',
  source TEXT,
  fingerprint TEXT NOT NULL UNIQUE,
  hits INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_mem_kind ON memories(kind, status, updated_at);
CREATE INDEX IF NOT EXISTS ix_mem_session ON memories(session_id, updated_at);
CREATE INDEX IF NOT EXISTS ix_notes_session ON notes(session_id, id);
CREATE INDEX IF NOT EXISTS ix_tel_event ON telemetry(event, created_at);
CREATE INDEX IF NOT EXISTS ix_usage_time ON usage(created_at);
CREATE INDEX IF NOT EXISTS ix_verif_time ON verifications(created_at);
"""

# Columns added after v0.1 (applied with ALTER TABLE on open).
MIGRATIONS = [("checkpoints", "snapshot_ref", "TEXT"), ("sessions", "budget_usd", "REAL"),
              ("file_index", "deps", "TEXT")]


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = ctxly_dir(self.root) / "sessions.db"
        self.conn = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------- meta ----------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))

    # ---------- sessions ----------
    def new_session(self, task: str | None = None, tool: str | None = None,
                    git_head: str | None = None, branch: str | None = None) -> str:
        sid = "sess_" + uuid.uuid4().hex[:8]
        now = time.time()
        self.conn.execute(
            "INSERT INTO sessions(id, created_at, updated_at, task, status, tool, git_head, branch)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (sid, now, now, task, "active", tool, git_head, branch),
        )
        self.set_meta("active_session", sid)
        return sid

    def active_session(self) -> sqlite3.Row | None:
        sid = self.get_meta("active_session")
        if sid:
            row = self.get_session(sid)
            if row:
                return row
        return self.conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 1").fetchone()

    def ensure_session(self, task: str | None = None, tool: str | None = None,
                       git_head: str | None = None, branch: str | None = None) -> str:
        row = self.active_session()
        if row and row["status"] != "closed":
            if task and not row["task"]:
                self.update_session(row["id"], task=task)
            return row["id"]
        return self.new_session(task, tool, git_head, branch)

    def get_session(self, sid: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()

    def update_session(self, sid: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE sessions SET {cols} WHERE id=?", (*fields.values(), sid))

    def list_sessions(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM checkpoints c WHERE c.session_id=s.id) AS n_ckpt"
            " FROM sessions s ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()

    # ---------- checkpoints ----------
    def add_checkpoint(self, session_id: str, **data: Any) -> int:
        for k in ("touched_files", "selected_files", "diff_stat"):
            if k in data and not isinstance(data[k], str) and data[k] is not None:
                data[k] = json.dumps(data[k])
        data["session_id"] = session_id
        data.setdefault("created_at", time.time())
        cols = ", ".join(data)
        qs = ", ".join("?" for _ in data)
        cur = self.conn.execute(f"INSERT INTO checkpoints({cols}) VALUES({qs})", tuple(data.values()))
        self.update_session(session_id)
        return int(cur.lastrowid)

    def latest_checkpoint(self, session_id: str | None = None) -> sqlite3.Row | None:
        if session_id:
            return self.conn.execute(
                "SELECT * FROM checkpoints WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,)
            ).fetchone()
        return self.conn.execute("SELECT * FROM checkpoints ORDER BY id DESC LIMIT 1").fetchone()

    def update_checkpoint(self, ckpt_id: int, **fields: Any) -> None:
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            self.conn.execute(f"UPDATE checkpoints SET {cols} WHERE id=?", (*fields.values(), ckpt_id))

    # ---------- notes ----------
    def add_note(self, session_id: str, text: str, kind: str = "note") -> int:
        cur = self.conn.execute(
            "INSERT INTO notes(session_id, created_at, kind, text) VALUES(?,?,?,?)",
            (session_id, time.time(), kind, text),
        )
        self.update_session(session_id)
        return int(cur.lastrowid)

    def notes(self, session_id: str, kind: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
        if kind:
            q = "SELECT * FROM notes WHERE session_id=? AND kind=? ORDER BY id DESC LIMIT ?"
            args: tuple = (session_id, kind, limit)
        else:
            q = "SELECT * FROM notes WHERE session_id=? ORDER BY id DESC LIMIT ?"
            args = (session_id, limit)
        return list(reversed(self.conn.execute(q, args).fetchall()))

    # ---------- file index ----------
    def get_indexed(self, path: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM file_index WHERE path=?", (path,)).fetchone()

    def put_indexed(self, path: str, sha256: str, mtime: float, size: int, tokens: int, symbols: list,
                    deps: list | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO file_index(path, sha256, mtime, size, tokens, symbols, deps) VALUES(?,?,?,?,?,?,?)",
            (path, sha256, mtime, size, tokens, json.dumps(symbols), json.dumps(deps or [])),
        )

    def all_indexed(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM file_index ORDER BY path").fetchall()

    def prune_index(self, keep: set[str]) -> int:
        rows = self.conn.execute("SELECT path FROM file_index").fetchall()
        gone = [r["path"] for r in rows if r["path"] not in keep]
        self.conn.executemany("DELETE FROM file_index WHERE path=?", [(p,) for p in gone])
        return len(gone)

    # ---------- caches ----------
    def get_cache(self, prefix_hash: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM caches WHERE prefix_hash=?", (prefix_hash,)).fetchone()

    def put_cache(self, prefix_hash: str, cache_name: str | None, model: str,
                  expires_at: float, token_count: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO caches(prefix_hash, cache_name, model, created_at, expires_at, token_count)"
            " VALUES(?,?,?,?,?,?)",
            (prefix_hash, cache_name, model, time.time(), expires_at, token_count),
        )

    # ---------- telemetry ----------
    def log(self, event: str, source: str, session_id: str | None = None, baseline_tokens: int | None = None,
            optimized_tokens: int | None = None, cached_tokens: int | None = None,
            latency_ms: float | None = None, detail: Any = None) -> None:
        self.conn.execute(
            "INSERT INTO telemetry(session_id, created_at, event, source, baseline_tokens, optimized_tokens,"
            " cached_tokens, latency_ms, detail) VALUES(?,?,?,?,?,?,?,?,?)",
            (session_id, time.time(), event, source, baseline_tokens, optimized_tokens, cached_tokens,
             latency_ms, json.dumps(detail) if detail is not None else None),
        )

    def checkpoints(self, session_id: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        if session_id:
            return self.conn.execute("SELECT * FROM checkpoints WHERE session_id=? ORDER BY id DESC LIMIT ?",
                                     (session_id, limit)).fetchall()
        return self.conn.execute("SELECT * FROM checkpoints ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def get_checkpoint(self, ckpt_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM checkpoints WHERE id=?", (ckpt_id,)).fetchone()

    def telemetry(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
