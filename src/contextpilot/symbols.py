"""Incremental symbol indexer: Python `ast` for .py, lightweight regex for other languages.

Files are re-parsed only when their SHA-256 changes, so repeated runs touch only changed code.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import gitctx, tokens
from .deps import imports_of
from .scrubber import is_sensitive_path
from .store import Store

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".ctxly", ".venv", "venv", "env", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", "target", ".next", ".nuxt", "out", "coverage",
    ".idea", ".vscode", ".gradle", "vendor", "Pods", ".terraform", ".agents", ".agent", ".gemini",
}
CODE_EXT = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".kts", ".scala",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".m", ".dart", ".lua", ".sh", ".ps1",
    ".sql", ".vue", ".svelte", ".ex", ".exs", ".erl", ".hs", ".clj", ".r", ".jl",
}
TEXT_EXT = CODE_EXT | {
    ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".xml", ".html", ".css", ".scss",
    ".gradle", ".properties", ".proto", ".graphql", ".tf", ".dockerfile",
}
TEXT_NAMES = {"Dockerfile", "Makefile", "Procfile", "Gemfile", "Rakefile", "Justfile"}
MAX_FILE_BYTES = 512_000

_REGEX_DEFS: list[tuple[str, re.Pattern[str]]] = [
    ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:public\s+|private\s+|internal\s+)?"
                         r"(?:class|interface|struct|enum|trait|type|record)\s+([A-Za-z_]\w*)", re.M)),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)", re.M)),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
                            r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", re.M)),
    ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M)),             # Go
    ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)", re.M)),  # Rust
    ("function", re.compile(r"^\s*def\s+([A-Za-z_]\w*[?!]?)", re.M)),                          # Ruby/Elixir
    ("function", re.compile(r"^\s*(?:public|private|protected|static|final|override|suspend|\s)+"
                            r"[\w<>\[\],.? ]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:throws [\w., ]+)?\s*\{", re.M)),
    ("function", re.compile(r"^\s*(?:fun|function)\s+([A-Za-z_]\w*)", re.M)),                  # Kotlin/PHP/Lua
]


@dataclass
class Symbol:
    name: str
    kind: str
    start: int
    end: int
    parent: str | None = None

    def to_list(self) -> list:
        return [self.name, self.kind, self.start, self.end, self.parent]

    @classmethod
    def from_list(cls, v: list) -> "Symbol":
        return cls(*v)

    @property
    def qualname(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


def is_text_file(path: str) -> bool:
    p = Path(path)
    return p.suffix.lower() in TEXT_EXT or p.name in TEXT_NAMES


def list_files(root: Path) -> list[str]:
    files = gitctx.ls_files(root)
    if files is None:
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for f in filenames:
                files.append(os.path.relpath(os.path.join(dirpath, f), root).replace("\\", "/"))
    out = []
    for f in files:
        parts = f.split("/")
        if (any(p in SKIP_DIRS for p in parts[:-1]) or is_sensitive_path(f) or not is_text_file(f)
                or gitctx.is_agent_meta(f)):
            continue
        out.append(f)
    return sorted(out)


def parse_python(src: str) -> list[Symbol]:
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return parse_regex(src)
    syms: list[Symbol] = []

    def visit(node: ast.AST, parent: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(child, ast.ClassDef) else ("method" if parent else "function")
                end = getattr(child, "end_lineno", child.lineno) or child.lineno
                start = min([child.lineno, *[d.lineno for d in child.decorator_list]])
                syms.append(Symbol(child.name, kind, start, end, parent))
                if isinstance(child, ast.ClassDef):
                    visit(child, child.name)

    visit(tree, None)
    return syms


def parse_regex(src: str) -> list[Symbol]:
    syms: dict[tuple[str, int], Symbol] = {}
    line_starts = [0]
    for i, ch in enumerate(src):
        if ch == "\n":
            line_starts.append(i + 1)
    import bisect

    for kind, pat in _REGEX_DEFS:
        for m in pat.finditer(src):
            name = m.group(1)
            if name in {"if", "for", "while", "switch", "catch", "return", "new", "else"}:
                continue
            line = bisect.bisect_right(line_starts, m.start(1))
            syms.setdefault((name, line), Symbol(name, kind, line, line))
    ordered = sorted(syms.values(), key=lambda s: s.start)
    total = src.count("\n") + 1
    for i, s in enumerate(ordered):  # approximate end = line before next symbol
        s.end = max(s.start, (ordered[i + 1].start - 1) if i + 1 < len(ordered) else total)
    return ordered


def parse_symbols(path: str, src: str) -> list[Symbol]:
    ext = Path(path).suffix.lower()
    if ext in (".py", ".pyi"):
        return parse_python(src)
    if ext in CODE_EXT:
        return parse_regex(src)
    return []


def read_text(root: Path, rel: str) -> str | None:
    p = root / rel
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return None
        data = p.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


@dataclass
class IndexStats:
    files: int = 0
    parsed: int = 0
    reused: int = 0
    removed: int = 0
    total_tokens: int = 0


def build_index(root: Path, store: Store) -> IndexStats:
    stats = IndexStats()
    files = list_files(root)
    keep: set[str] = set()
    store.conn.execute("BEGIN")
    try:
        for rel in files:
            p = root / rel
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            keep.add(rel)
            stats.files += 1
            row = store.get_indexed(rel)
            if row and row["mtime"] == st.st_mtime and row["size"] == st.st_size:
                stats.reused += 1
                stats.total_tokens += row["tokens"]
                continue
            src = read_text(root, rel)
            if src is None:
                keep.discard(rel)
                stats.files -= 1
                continue
            sha = hashlib.sha256(src.encode("utf-8", errors="replace")).hexdigest()
            if row and row["sha256"] == sha:
                old = row["deps"] if "deps" in row.keys() else None
                deps = json.loads(old) if old else imports_of(rel, src)
                store.put_indexed(rel, sha, st.st_mtime, st.st_size, row["tokens"], json.loads(row["symbols"]), deps)
                stats.reused += 1
                stats.total_tokens += row["tokens"]
                continue
            syms = [s.to_list() for s in parse_symbols(rel, src)]
            ntok = tokens.estimate(src)
            store.put_indexed(rel, sha, st.st_mtime, st.st_size, ntok, syms, imports_of(rel, src))
            stats.parsed += 1
            stats.total_tokens += ntok
        stats.removed = store.prune_index(keep)
        store.conn.execute("COMMIT")
    except Exception:
        store.conn.execute("ROLLBACK")
        raise
    return stats


def load_index(store: Store) -> dict[str, tuple[int, list[Symbol]]]:
    return {
        r["path"]: (r["tokens"], [Symbol.from_list(v) for v in json.loads(r["symbols"])])
        for r in store.all_indexed()
    }
