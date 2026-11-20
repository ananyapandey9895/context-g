"""Generates ContextPilot blocks inside GEMINI.md (Gemini CLI) and .agent/rules (Antigravity).

User content outside the managed markers is never touched.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from . import tokens
from .cache import write_prefix_file
from .checkpoint import recovery_bundle
from .store import Store
from .symbols import load_index

BEGIN = "<!-- ctxly:begin - managed by ContextPilot, edits inside this block are overwritten -->"
END = "<!-- ctxly:end -->"
_BLOCK = re.compile(re.escape(BEGIN.split(" - ")[0]) + r".*?" + re.escape(END), re.S)

MAX_IMPORT_TOKENS = 8000

PROTOCOL = """## ContextPilot protocol
1. **Start / resume:** call the `ctxly_resume` MCP tool (or run `ctxly resume`) before reading code.
2. **Scope:** call `ctxly_optimize` with the user's request; read ONLY the files it returns (2-4 files).
3. **Save progress:** call `ctxly_checkpoint` with notes + next steps after each meaningful step and before stopping.
4. **Privacy:** never open `.env*`, `*.pem`, `*.key`, credentials or secrets files."""


def upsert_block(path: Path, body: str) -> bool:
    block = f"{BEGIN}\n{body.strip()}\n{END}"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if _BLOCK.search(existing):
        new = _BLOCK.sub(lambda _: block, existing, count=1)
    else:
        new = (existing.rstrip() + "\n\n" if existing.strip() else "") + block + "\n"
    if new != existing:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
        return True
    return False


def _top_level_map(index: dict) -> str:
    groups: dict[str, int] = defaultdict(int)
    for p in index:
        groups[p.split("/")[0] if "/" in p else "."] += 1
    return "\n".join(f"- `{g}/` - {n} files" if g != "." else f"- (root) - {n} files" for g, n in sorted(groups.items()))


def gemini_md_body(root: Path, store: Store, include_session: bool = True) -> str:
    index = load_index(store)
    prefix_path, h = write_prefix_file(root, store)
    prefix_tokens = tokens.estimate(prefix_path.read_text(encoding="utf-8"))
    parts = ["# ContextPilot memory", PROTOCOL, "", "## Project map"]
    if prefix_tokens <= MAX_IMPORT_TOKENS:
        # Stable, byte-identical import => maximises implicit prefix-cache hits turn after turn.
        parts.append(f"Symbol map (stable, hash `{h}`, ~{prefix_tokens} tok):\n@.ctxly/prefix.md")
    else:
        parts.append(_top_level_map(index))
        parts.append(f"(Full symbol map is ~{prefix_tokens} tok; use the `ctxly_select_files` tool instead of browsing.)")
    if include_session:
        bundle = recovery_bundle(root, store)
        if bundle:
            parts += ["", "## Last session (auto-synced)", bundle]
    return "\n".join(parts)


def sync_gemini_md(root: Path, store: Store, hierarchical: bool = False) -> list[Path]:
    written = []
    target = root / "GEMINI.md"
    if upsert_block(target, gemini_md_body(root, store)):
        written.append(target)
    if hierarchical:
        index = load_index(store)
        by_dir: dict[str, list[str]] = defaultdict(list)
        for p, (_, syms) in index.items():
            if "/" in p and syms:
                by_dir[p.split("/")[0]].append(p)
        for d, files in by_dir.items():
            lines = [f"# ContextPilot map for `{d}/`"]
            for f in sorted(files)[:60]:
                names = ", ".join(s.qualname for s in index[f][1][:8])
                lines.append(f"- `{f}`: {names}")
            sub = root / d / "GEMINI.md"
            if upsert_block(sub, "\n".join(lines)):
                written.append(sub)
    return written


def sync_antigravity_rule(root: Path, store: Store) -> Path:
    target = root / ".agent" / "rules" / "contextpilot-memory.md"
    body = "\n".join([
        "# ContextPilot memory (Antigravity workspace rule)",
        PROTOCOL.replace("call the `ctxly_resume` MCP tool (or run `ctxly resume`)",
                         "use the `contextpilot` skill (or run `ctxly resume`)"),
        "",
        "## Last session (auto-synced)",
        recovery_bundle(root, store) or "(no checkpoint yet)",
    ])
    upsert_block(target, body)
    return target
