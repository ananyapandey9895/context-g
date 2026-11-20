"""FastMCP bridge: exposes ContextPilot to Gemini CLI (`gemini mcp add`) and Antigravity (mcp_config.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .checkpoint import create_checkpoint, full_resume, resume_json
from .config import find_repo_root, load_settings
from .scrubber import scrub
from .store import Store


def _root(repo: str | None) -> Path:
    return find_repo_root(repo or os.environ.get("CTXLY_REPO") or os.getcwd())


def build_server():
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server

    mcp = Server("contextpilot", instructions=(
        "ContextPilot is a persistent memory + surgical context layer. Call ctxly_resume first, then "
        "ctxly_optimize with the user's request and read ONLY the returned files. Call ctxly_checkpoint with "
        "notes and next steps after each meaningful step. Never read .env, key or credential files."
    ))

    @mcp.tool()
    def ctxly_resume(repo: str | None = None, full: bool = True) -> str:
        """Restore the last saved session (task, scoped files, notes, next steps, in-flight diff).
        Call this at the start of every session or after an interruption. `repo` = workspace path."""
        root = _root(repo)
        with Store(root) as store:
            return full_resume(root, store) if full else json.dumps(resume_json(root, store), indent=1)

    @mcp.tool()
    def ctxly_optimize(task: str, repo: str | None = None, use_llm: bool = True) -> str:
        """Compile a vague request into a lean, grounded prompt that names the 2-4 files (with line ranges)
        that matter. Read ONLY those files afterwards."""
        from .engine import prepare

        p = prepare(_root(repo), task, use_llm=use_llm, tool="mcp")
        s = p.summary()
        t = s["tokens_estimate"]
        return (p.compiled.text + f"\n---\nContextPilot: {len(s['files'])} of {s['considered_files']} files selected; "
                f"est. tokens {t['naive']} → {t['optimized']} (-{t['reduction_pct']}%), compiler={s['compiler']}.")

    @mcp.tool()
    def ctxly_select_files(task: str, repo: str | None = None) -> str:
        """Return only the ranked file list (path, line range, reasons) for a task, as JSON."""
        from .selector import select_files
        from .symbols import build_index

        root = _root(repo)
        s = load_settings(root)
        with Store(root) as store:
            build_index(root, store)
            sel = select_files(root, store, scrub(task).text, s.min_files, s.max_files)
        return json.dumps([{"path": c.path, "lines": c.lines, "reasons": c.reasons[:3]} for c in sel.files], indent=1)

    @mcp.tool()
    def ctxly_checkpoint(notes: str = "", next_steps: str = "", repo: str | None = None) -> str:
        """Save debugging progress: what you learned (notes) and what to do next. Secrets are redacted."""
        root = _root(repo)
        with Store(root) as store:
            r = create_checkpoint(root, store, reason="agent", notes=notes or None, next_steps=next_steps or None)
        return f"Checkpoint #{r['id']} saved in {r['elapsed_ms']} ms ({len(r['touched_files'])} touched files)."

    @mcp.tool()
    def ctxly_note(text: str, repo: str | None = None) -> str:
        """Append a short finding to session memory (e.g. 'token expiry compared in seconds vs ms')."""
        root = _root(repo)
        with Store(root) as store:
            sid = store.ensure_session()
            store.add_note(sid, scrub(text).text)
        return "noted"

    @mcp.tool()
    def ctxly_status(repo: str | None = None) -> str:
        """Session status and token telemetry (local estimates vs billed usage, clearly separated)."""
        from .report import status_dict

        root = _root(repo)
        with Store(root) as store:
            return json.dumps(status_dict(root, store), indent=1, default=str)

    @mcp.tool()
    def ctxly_remember(text: str, kind: str = "decision", files: list[str] | None = None, repo: str | None = None) -> str:
        """Store long-term project memory. kind: decision (architecture/conventions), task (pending work),
        error (unresolved problem), fact (finding). Duplicates are merged; secrets are redacted."""
        from .memory import remember

        root = _root(repo)
        with Store(root) as store:
            sess = store.active_session()
            r = remember(store, kind, text, files=files or [], session_id=sess["id"] if sess else None, source="agent")
        return "too short to store" if r is None else f"memory #{r['id']} {'stored' if r['created'] else 'updated'}"

    @mcp.tool()
    def ctxly_recall(task: str, repo: str | None = None) -> str:
        """Project memories relevant to a task (decisions, unresolved errors, pending tasks, earlier changes)."""
        from .memory import recall, render

        root = _root(repo)
        with Store(root) as store:
            return render(recall(store, task, limit=8)) or "No relevant memories."

    @mcp.tool()
    def ctxly_task_done(memory_id: int, repo: str | None = None) -> str:
        """Mark a remembered task or error as completed/resolved."""
        from .memory import set_status

        root = _root(repo)
        with Store(root) as store:
            return "done" if set_status(store, memory_id, "done") else "not found"

    return mcp


def serve() -> None:
    build_server().run()
