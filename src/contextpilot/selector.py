"""Surgical file selector: picks the 2-4 files that actually matter for a task.

Signals (all local, deterministic):
  * explicit paths / filenames / traceback frames mentioned in the task
  * symbol names (classes, functions) matching task keywords
  * path components matching task keywords
  * content keyword hits (capped)
  * files with uncommitted changes (the work in flight)
  * files touched in the last checkpoint of the active session
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import gitctx
from .symbols import Symbol, load_index, read_text
from .store import Store

STOPWORDS = set(
    """a an the and or but if then else when while for to of in on at by with from into onto is are was were be
    been being it its this that these those there here i me my we our you your he she they them their what which
    who whom how why where can could should would will shall may might must do does did done doing have has had
    not no yes please fix bug issue error problem make add update change refactor implement code file files
    function method class test tests working work broken returns return get set use using used just also like
    some any all more less new old need needs want wants help after before because so than too very via""".split()
)

SMALL_FILE_TOKENS = 600
_TRACE_PY = re.compile(r'File "([^"]+)", line (\d+)')
_TRACE_GENERIC = re.compile(r"([\w./\\-]+\.[A-Za-z]{1,5}):(\d+)(?::\d+)?")
_PATHLIKE = re.compile(r"[\w.\-/\\]+\.[A-Za-z]{1,5}\b")


def split_identifier(word: str) -> list[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", word)
    parts = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", parts)
    return [p.lower() for p in re.split(r"[\s_\-./\\]+", parts) if p]


def stem(w: str) -> str:
    for suf in ("ations", "ation", "ings", "ing", "ers", "er", "ies", "es", "ed", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def keywords(task: str) -> set[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", task)
    out: set[str] = set()
    for w in words:
        for p in split_identifier(w) + ([w.lower()] if len(w) > 2 else []):
            if len(p) > 2 and p not in STOPWORDS:
                out.add(stem(p))
    nums = re.findall(r"\b([1-5]\d\d)\b", task)  # HTTP status codes are strong hints (401, 500…)
    out.update(nums)
    return out


@dataclass
class Candidate:
    path: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    lines: tuple[int, int] | None = None
    tokens: int = 0

    def add(self, pts: float, why: str) -> None:
        self.score += pts
        if why not in self.reasons:
            self.reasons.append(why)

    @property
    def ref(self) -> str:
        return f"{self.path}:L{self.lines[0]}-{self.lines[1]}" if self.lines else self.path


@dataclass
class Selection:
    files: list[Candidate]
    considered: int
    total_tokens: int  # tokens the agent would read if it scanned every indexed file (estimate)

    @property
    def selected_tokens(self) -> int:
        return sum(c.tokens for c in self.files)


def _mentioned_paths(task: str, all_paths: list[str]) -> dict[str, int | None]:
    hits: dict[str, int | None] = {}
    norm = {p.lower(): p for p in all_paths}
    by_name: dict[str, list[str]] = {}
    for p in all_paths:
        by_name.setdefault(Path(p).name.lower(), []).append(p)

    def resolve(raw: str) -> str | None:
        r = raw.replace("\\", "/").lower().lstrip("./")
        if r in norm:
            return norm[r]
        for low, orig in norm.items():
            if low.endswith("/" + r) or r.endswith("/" + low):
                return orig
        names = by_name.get(Path(r).name)
        return names[0] if names and len(names) == 1 else None

    for rx in (_TRACE_PY, _TRACE_GENERIC):
        for m in rx.finditer(task):
            p = resolve(m.group(1))
            if p:
                hits[p] = int(m.group(2))
    for m in _PATHLIKE.finditer(task):
        p = resolve(m.group(0))
        if p and p not in hits:
            hits[p] = None
    return hits


@dataclass
class RetrievalContext:
    """What a ranking strategy can see. Strategies add points with `ctx.cand(path).add(points, reason)`."""
    root: Path
    store: Store
    task: str
    kws: set
    index: dict
    cands: dict
    cand: object

    def leaders(self, n: int = 3, min_ratio: float = 0.5) -> list:
        ranked = sorted(self.cands.values(), key=lambda c: -c.score)
        if not ranked or ranked[0].score <= 0:
            return []
        return [c for c in ranked[:n] if c.score >= ranked[0].score * min_ratio]


def graph_strategy(ctx: RetrievalContext) -> None:
    """Pull in what the leading files import (models, config, helpers) and the tests that cover them."""
    from .deps import build_graph, is_test, reverse, test_stem

    leaders = ctx.leaders()
    if not leaders:
        return
    graph = build_graph(ctx.store.all_indexed())
    rev = reverse(graph)
    for lead in leaders:
        p = lead.path
        if is_test(p):
            continue
        for dep in sorted(graph.get(p, ()))[:6]:
            if dep in ctx.index and not dep.endswith("__init__.py"):
                ctx.cand(dep).add(min(4.0, lead.score * 0.15), f"imported by {p.rsplit('/', 1)[-1]}")
        stem_ = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        tests = {t for t in rev.get(p, ()) if is_test(t)} | {t for t in ctx.index if is_test(t) and test_stem(t) == stem_}
        for t in sorted(tests)[:2]:
            ctx.cand(t).add(min(5.0, lead.score * 0.2), f"tests {p.rsplit('/', 1)[-1]}")


def memory_strategy(ctx: RetrievalContext) -> None:
    """Files that project memory links to this kind of task (decisions, unresolved errors, earlier changes)."""
    from .memory import recall

    for m in recall(ctx.store, ctx.task, limit=5):
        for f in m["files"][:4]:
            if f in ctx.index:
                ctx.cand(f).add(min(4.0, 1.0 + m["score"] * 0.2), f"in project memory ({m['kind']})")


# Order matters only for "leaders": graph runs on the ranking built so far. Append new strategies here
# (e.g. embeddings / semantic search) - each takes a RetrievalContext and adds points.
STRATEGIES = [memory_strategy, graph_strategy]


def select_files(root: Path, store: Store, task: str, min_files: int = 2, max_files: int = 4,
                 touched: list[str] | None = None) -> Selection:
    index = load_index(store)
    paths = list(index)
    kws = keywords(task)
    cands: dict[str, Candidate] = {}

    def cand(p: str) -> Candidate:
        if p not in cands:
            cands[p] = Candidate(p, tokens=index[p][0] if p in index else 0)
        return cands[p]

    # 1) explicit mentions / traceback frames
    for p, line in _mentioned_paths(task, paths).items():
        c = cand(p)
        c.add(40, "named in task" + (f" (line {line})" if line else ""))
        if line:
            for s in index.get(p, (0, []))[1]:
                if s.start <= line <= s.end:
                    c.symbols.append(s)

    # 2) work in flight
    hunks = gitctx.changed_hunks(root)
    for ch in gitctx.status(root):
        if ch.path in index:
            cand(ch.path).add(12, f"uncommitted change (+{ch.added}/-{ch.removed})")
    for p in touched or []:
        if p in index:
            cand(p).add(10, "touched in last session")

    # 3) path + symbol keyword matches.
    # Each keyword counts once per file, weighted by rarity: "jwt" in 2 files beats "handler" in 30.
    file_toks: dict[str, tuple[set[str], list[tuple[object, set[str]]]]] = {}
    df: dict[str, int] = {}
    for p, (_, syms) in index.items():
        ptoks = {stem(t) for t in split_identifier(p) if len(t) > 2}
        stoks = [(s, {stem(t) for t in split_identifier(s.name) if len(t) > 2} & kws) for s in syms]
        file_toks[p] = (ptoks, stoks)
        for k in (ptoks & kws) | set().union(*(m for _, m in stoks)):
            df[k] = df.get(k, 0) + 1
    weight = {k: min(1.0, 3.0 / df[k]) for k in df}
    for p, (ptoks, stoks) in file_toks.items():
        pm = kws & ptoks
        if pm:
            cand(p).add(6 * sum(weight[k] for k in pm), "path matches " + ", ".join(sorted(pm)))
        sym_kws: set[str] = set()
        matched = [(s, m) for s, m in stoks if m]
        for s, m in matched:
            sym_kws |= m
        if matched:
            c = cand(p)
            matched.sort(key=lambda sm: (-sum(weight[k] for k in sm[1]), sm[0].kind != "class"))
            bonus = 3 * weight.get(next(iter(matched[0][1])), 0) if matched[0][0].kind == "class" else 0
            c.add(5 * sum(weight[k] for k in sym_kws) + bonus, f"symbol {matched[0][0].qualname}")
            for s, _ in matched[:6]:
                if s not in c.symbols:
                    c.symbols.append(s)

    # 3b) learned signals: files you actually edited for similar requests
    from .learning import boosts

    for p, (pts, why) in boosts(store, kws).items():
        if p in index and pts > 0:
            cand(p).add(pts, why)

    # 3c) pluggable strategies (import graph, tests, project memory, ...) - see STRATEGIES below
    ctx = RetrievalContext(root, store, task, kws, index, cands, cand)
    for strategy in STRATEGIES:
        try:
            strategy(ctx)
        except Exception:  # a ranking helper must never break selection
            pass

    # 4) content hits only for the shortlist (keeps it fast on big repos)
    shortlist = sorted(cands.values(), key=lambda c: -c.score)[: max(12, max_files * 3)]
    if len(shortlist) < max_files and kws:
        shortlist += [cand(p) for p in paths[:400] if p not in cands]
    if kws:
        kw_re = re.compile(r"(?i)\b(" + "|".join(re.escape(k) for k in sorted(kws)) + r")\w*")
        for c in shortlist:
            src = read_text(root, c.path)
            if not src:
                continue
            n = len(kw_re.findall(src))
            if n:
                c.add(min(n, 10) * 0.8, f"{n} keyword hits")

    ranked = sorted((c for c in cands.values() if c.score > 0), key=lambda c: (-c.score, c.path))
    if not ranked:
        return Selection([], len(paths), sum(t for t, _ in index.values()))
    top = ranked[0].score
    chosen = [c for c in ranked if c.score >= top * 0.3][:max_files]
    for c in ranked:
        if len(chosen) >= min_files:
            break
        if c not in chosen:
            chosen.append(c)

    for c in chosen:
        if c.tokens <= SMALL_FILE_TOKENS:
            continue  # small files are cheaper to read whole than to risk slicing away the bug
        ranges = [(s.start, s.end) for s in c.symbols]
        ranges += hunks.get(c.path, [])
        if ranges:
            lo = max(1, min(a for a, _ in ranges) - 3)
            hi = max(b for _, b in ranges) + 3
            total_lines = max((s.end for s in index.get(c.path, (0, []))[1]), default=hi)
            c.lines = (lo, max(hi, lo))
            if total_lines and hi - lo > 0 and c.tokens:
                frac = min(1.0, (hi - lo + 1) / max(total_lines, hi))
                c.tokens = max(1, int(c.tokens * max(frac, 0.15)))
    return Selection(chosen, len(paths), sum(t for t, _ in index.values()))


# ---------------------------------------------------------------- triage (local, zero cloud cost)
TASK_KINDS = [
    ("BUGFIX", r"\b(fix|bug|error|exception|traceback|crash|fail|failing|broken|401|403|404|500|null|undefined|wrong)\b"),
    ("REFACTOR", r"\b(refactor|clean ?up|restructure|rename|extract|simplify|split|migrate)\b"),
    ("TEST", r"\b(test|tests|pytest|jest|coverage|spec)\b"),
    ("FEATURE", r"\b(add|implement|create|build|support|new|feature|endpoint)\b"),
    ("DOCS", r"\b(doc|docs|readme|comment|explain|document)\b"),
]


def heuristic_triage(task: str) -> dict:
    t = task.lower()
    kind = next((k for k, rx in TASK_KINDS if re.search(rx, t)), "GENERAL")
    urgent = bool(re.search(r"\b(prod|production|urgent|asap|outage|security|auth|payment|data loss)\b", t))
    priority = "HIGH" if urgent or kind == "BUGFIX" else ("MEDIUM" if kind in ("REFACTOR", "FEATURE") else "LOW")
    return {"kind": kind, "priority": priority, "label": f"{priority}_PRIORITY_{kind}", "source": "heuristic"}


def triage(task: str, gemma_model: str = "", ollama_url: str = "http://localhost:11434") -> dict:
    """Classify the task. Uses a local Gemma model through Ollama when configured, else heuristics."""
    base = heuristic_triage(task)
    if not gemma_model:
        return base
    import json
    import urllib.request

    prompt = (
        "Classify this coding task. Reply with JSON only: "
        '{"kind": one of BUGFIX|REFACTOR|TEST|FEATURE|DOCS|GENERAL, "priority": HIGH|MEDIUM|LOW}\n\nTask: ' + task
    )
    body = json.dumps({"model": gemma_model, "prompt": prompt, "stream": False, "format": "json"}).encode()
    try:
        req = urllib.request.Request(f"{ollama_url}/api/generate", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(json.loads(r.read())["response"])
        kind = str(data.get("kind", base["kind"])).upper()
        prio = str(data.get("priority", base["priority"])).upper()
        return {"kind": kind, "priority": prio, "label": f"{prio}_PRIORITY_{kind}", "source": f"gemma:{gemma_model}"}
    except Exception:
        return base
