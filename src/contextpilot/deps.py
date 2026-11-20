"""Import / dependency graph used by context retrieval.

Raw import specifiers are extracted when a file is (re)indexed and stored with it in SQLite, so the graph costs
nothing on unchanged files. Specifiers are resolved to repository paths at query time.
Python uses the AST; JavaScript/TypeScript use a conservative regex for relative imports only.
"""

from __future__ import annotations

import ast
import json
import posixpath
import re

_JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
_JS_RE = re.compile(r"""(?:\bfrom\s+|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"](\.{1,2}/[^'"]+)['"]""", re.M)


def imports_of(path: str, src: str) -> list[str]:
    """Raw specifiers. Python: dotted names, relative ones prefixed with dots. JS/TS: './x' style paths."""
    if path.endswith(".py"):
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            return []
        out: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = "." * (node.level or 0) + (node.module or "")
                out.append(base)
                # `from pkg import mod` may import a sub-module
                out += [f"{base}.{a.name}" if node.module else f"{base}{a.name}" for a in node.names if a.name != "*"]
        return sorted(set(o for o in out if o.strip(".")))[:200]
    if path.endswith(_JS_EXT):
        return sorted(set(_JS_RE.findall(src)))[:200]
    return []


def _resolve_py(spec: str, src_path: str, paths: set[str], roots: list[str]) -> str | None:
    level = len(spec) - len(spec.lstrip("."))
    mod = spec.lstrip(".").replace(".", "/")
    if level:
        base = posixpath.dirname(src_path)
        for _ in range(level - 1):
            base = posixpath.dirname(base)
        cands = [posixpath.join(base, mod)] if mod else []
    else:
        cands = [posixpath.join(r, mod) if r else mod for r in roots]
    for c in cands:
        for p in (c + ".py", c + "/__init__.py"):
            if p in paths:
                return p
    return None


def _resolve_js(spec: str, src_path: str, paths: set[str]) -> str | None:
    c = posixpath.normpath(posixpath.join(posixpath.dirname(src_path), spec))
    if c in paths:
        return c
    for ext in _JS_EXT:
        if c + ext in paths:
            return c + ext
        if f"{c}/index{ext}" in paths:
            return f"{c}/index{ext}"
    return None


def build_graph(rows) -> dict[str, set[str]]:
    """{file: set(files it imports)} for every indexed file. `rows` are file_index rows (path, deps)."""
    raw = {r["path"]: json.loads(r["deps"] or "[]") for r in rows if "deps" in r.keys()}
    paths = set(raw)
    roots = sorted({""} | {p.split("/")[0] for p in paths if p.startswith(("src/", "lib/", "app/"))})
    graph: dict[str, set[str]] = {}
    for p, specs in raw.items():
        out = set()
        for s in specs:
            r = _resolve_py(s, p, paths, roots) if p.endswith(".py") else _resolve_js(s, p, paths)
            if r and r != p:
                out.add(r)
        if out:
            graph[p] = out
    return graph


def reverse(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    rev: dict[str, set[str]] = {}
    for a, bs in graph.items():
        for b in bs:
            rev.setdefault(b, set()).add(a)
    return rev


_TEST = re.compile(r"(^|/)(tests?/|test_[^/]+$|[^/]+_test\.\w+$|[^/]+\.(test|spec)\.\w+$)")


def is_test(path: str) -> bool:
    return bool(_TEST.search(path))


def test_stem(path: str) -> str:
    name = posixpath.basename(path)
    name = re.sub(r"\.(test|spec)(?=\.\w+$)", "", name)
    name = re.sub(r"^test_|_test(?=\.\w+$)", "", name)
    return name.rsplit(".", 1)[0].lower()
