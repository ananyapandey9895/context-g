"""'Explain my project' - a navigable map built from the local symbol index (no code leaves the machine)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .store import Store
from .symbols import load_index

AREA_HINTS = {
    "auth": "Sign-in, passwords and access control", "login": "Sign-in", "user": "User accounts", "api": "API endpoints",
    "route": "Web routes", "view": "Screens / pages", "ui": "User interface", "component": "UI building blocks",
    "model": "Data models", "db": "Database", "migration": "Database changes", "test": "Automated tests",
    "payment": "Payments", "cart": "Shopping cart", "order": "Orders", "ship": "Shipping", "inventory": "Stock levels",
    "search": "Search", "notification": "Emails & notifications", "review": "Reviews", "catalog": "Product catalogue",
    "config": "Settings", "util": "Shared helpers", "lib": "Shared code", "doc": "Documentation", "script": "Scripts",
    "web": "Website front end", "src": "Source code", "app": "Application", "service": "Business logic",
}


def describe(name: str) -> str | None:
    low = name.lower()
    hits = [k for k in AREA_HINTS if k in low]
    return AREA_HINTS[max(hits, key=len)] if hits else None  # longest key wins: "reviews" -> review, not view


def build_map(root: Path, store: Store) -> dict:
    index = load_index(store)
    tree: dict = {"name": root.name, "path": "", "kind": "dir", "children": {}, "tokens": 0, "files": 0}
    for path, (ntok, syms) in index.items():
        node = tree
        parts = path.split("/")
        for i, part in enumerate(parts[:-1]):
            node["tokens"] += ntok
            node["files"] += 1
            node = node["children"].setdefault(part, {"name": part, "path": "/".join(parts[: i + 1]), "kind": "dir",
                                                      "children": {}, "tokens": 0, "files": 0})
        node["tokens"] += ntok
        node["files"] += 1
        node["children"][parts[-1]] = {
            "name": parts[-1], "path": path, "kind": "file", "tokens": ntok, "files": 1,
            "symbols": [{"name": s.qualname, "kind": s.kind, "line": s.start} for s in syms if s.kind != "method"][:25],
        }

    def finish(n: dict) -> dict:
        if n["kind"] == "dir":
            kids = sorted(n["children"].values(), key=lambda c: (c["kind"] != "dir", -c["tokens"], c["name"]))
            n["children"] = [finish(c) for c in kids]
            n["about"] = describe(n["name"])
        else:
            n["about"] = describe(n["name"])
        return n

    finish(tree)
    by_kind: dict[str, int] = defaultdict(int)
    for _, syms in index.values():
        for s in syms:
            by_kind[s.kind] += 1
    exts: dict[str, int] = defaultdict(int)
    for p in index:
        exts[Path(p).suffix or Path(p).name] += 1
    return {"tree": tree, "stats": {"files": len(index), "tokens": sum(t for t, _ in index.values()),
                                    "symbols": dict(by_kind), "languages": dict(sorted(exts.items(), key=lambda x: -x[1])[:8])}}
