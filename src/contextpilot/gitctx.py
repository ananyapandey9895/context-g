"""Git intelligence: HEAD, branch, porcelain status, scoped diffs. Degrades gracefully outside a repo."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .scrubber import is_sensitive_path


AGENT_META_DIRS = (".ctxly/", ".agents/", ".agent/", ".gemini/")
AGENT_META_FILES = {"GEMINI.md", "AGENTS.md"}


def is_agent_meta(path: str) -> bool:
    """ContextPilot / agent configuration files: never treated as the user's work in flight."""
    p = path.replace("\\", "/")
    return p.startswith(AGENT_META_DIRS) or p.rsplit("/", 1)[-1] in AGENT_META_FILES


def _git(root: Path, *args: str, timeout: float = 10) -> str | None:
    try:
        p = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def is_repo(root: Path) -> bool:
    return (_git(root, "rev-parse", "--is-inside-work-tree") or "").strip() == "true"


def head(root: Path) -> str | None:
    out = _git(root, "rev-parse", "--short", "HEAD")
    return out.strip() if out else None


def branch(root: Path) -> str | None:
    out = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if out else None


@dataclass
class Change:
    status: str  # e.g. "M", "A", "D", "??", "R"
    path: str
    added: int = 0
    removed: int = 0


def status(root: Path) -> list[Change]:
    """Uncommitted changes (staged + unstaged + untracked), sensitive files excluded."""
    out = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if out is None:
        return []
    changes: list[Change] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if is_sensitive_path(path) or is_agent_meta(path):
            continue
        st = "??" if code == "??" else (code.strip() or "M")[0]
        changes.append(Change(st, path))
    stats = numstat(root)
    for c in changes:
        a, r = stats.get(c.path, (0, 0))
        c.added, c.removed = a, r
        if c.status == "??":
            try:
                c.added = sum(1 for _ in open(root / c.path, encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    return changes


def _diff_base(root: Path) -> list[str]:
    return ["HEAD"] if head(root) else []


def numstat(root: Path) -> dict[str, tuple[int, int]]:
    out = _git(root, "diff", "--numstat", *_diff_base(root))
    res: dict[str, tuple[int, int]] = {}
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            a = int(parts[0]) if parts[0].isdigit() else 0
            r = int(parts[1]) if parts[1].isdigit() else 0
            res[parts[2]] = (a, r)
    return res


def diff(root: Path, paths: list[str] | None = None, context: int = 3, max_bytes: int = 200_000) -> str:
    """Unified diff vs HEAD for the given paths (sensitive files always excluded)."""
    if paths is not None:
        paths = [p for p in paths if not is_sensitive_path(p)]
        if not paths:
            return ""
    args = ["diff", f"-U{context}", "--no-color", *_diff_base(root), "--"]
    if paths:
        args += paths
    else:
        args += [".", *[f":(exclude,glob)**/{g}" for g in (".env", ".env.*", "*.pem", "*.key", "id_rsa*")]]
    out = _git(root, *args) or ""
    if len(out) > max_bytes:
        out = out[:max_bytes] + f"\n… [diff truncated at {max_bytes} bytes]\n"
    return out


def changed_hunks(root: Path) -> dict[str, list[tuple[int, int]]]:
    """Map path -> list of (start, end) line ranges changed in the working tree."""
    out = _git(root, "diff", "-U0", "--no-color", *_diff_base(root)) or ""
    res: dict[str, list[tuple[int, int]]] = {}
    cur = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            p = line[4:]
            cur = p[2:] if p.startswith("b/") else None
        elif line.startswith("@@") and cur:
            try:
                plus = line.split("+", 1)[1].split(" ", 1)[0]
                start, _, n = plus.partition(",")
                s, cnt = int(start), int(n or 1)
                res.setdefault(cur, []).append((s, s + max(cnt, 1) - 1))
            except (IndexError, ValueError):
                pass
    return res


def ls_files(root: Path) -> list[str] | None:
    out = _git(root, "ls-files", "-co", "--exclude-standard")
    if out is None:
        return None
    return [l.strip() for l in out.splitlines() if l.strip()]


def recent_commits(root: Path, n: int = 5) -> list[str]:
    out = _git(root, "log", f"-{n}", "--pretty=format:%h %s")
    return [l for l in (out or "").splitlines() if l]


# ------------------------------------------------------------------ fast path (checkpoint, <120 ms target)
_EXCLUDES = [f":(exclude,glob)**/{g}" for g in (".env", ".env.*", "*.pem", "*.key", "id_rsa*")]


@dataclass
class Snapshot:
    head: str | None
    branch: str | None
    changes: list[Change]
    diff: str


def snapshot(root: Path, with_diff: bool = True, max_bytes: int = 200_000) -> Snapshot:
    """HEAD, branch, changes, numstat and patch with exactly two git processes, run concurrently."""
    from concurrent.futures import ThreadPoolExecutor

    def diff_job() -> str:
        args = ["diff", "--numstat", "-p", "-U3", "--no-color"]
        out = _git(root, *args, "HEAD", "--", ".", *_EXCLUDES)
        if out is None:  # no commits yet
            out = _git(root, *args, "--", ".", *_EXCLUDES) or ""
        return out

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_status = ex.submit(_git, root, "status", "--porcelain=v2", "--branch", "--untracked-files=all")
        f_diff = ex.submit(diff_job) if with_diff else None
        status_out = f_status.result() or ""
        diff_out = f_diff.result() if f_diff else ""

    hd = br = None
    changes: list[Change] = []
    for line in status_out.splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split()[-1]
            hd = None if oid == "(initial)" else oid[:7]
        elif line.startswith("# branch.head "):
            br = line.split(" ", 2)[-1]
        elif line[:2] in ("1 ", "2 ", "u "):
            parts = line.split(" ", 8 if line[0] == "1" else 9 if line[0] == "2" else 10)
            path = parts[-1].split("\t")[0]
            xy = parts[1].replace(".", "")
            changes.append(Change((xy or "M")[0], path))
        elif line.startswith("? "):
            changes.append(Change("??", line[2:]))
    changes = [c for c in changes if not is_sensitive_path(c.path) and not is_agent_meta(c.path)]

    stats: dict[str, tuple[int, int]] = {}
    patch_at = diff_out.find("diff --git")
    head_part = diff_out if patch_at < 0 else diff_out[:patch_at]
    for line in head_part.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            stats[parts[2]] = (int(parts[0]) if parts[0].isdigit() else 0, int(parts[1]) if parts[1].isdigit() else 0)
    for c in changes:
        c.added, c.removed = stats.get(c.path, (0, 0))
        if c.status == "??":
            try:
                with open(root / c.path, encoding="utf-8", errors="ignore") as fh:
                    c.added = sum(1 for _ in fh)
            except OSError:
                pass

    patch = "" if patch_at < 0 else diff_out[patch_at:]
    # Drop agent/config file hunks from the stored patch.
    if patch:
        blocks = patch.split("\ndiff --git ")
        keep = []
        for i, b in enumerate(blocks):
            b = b if i == 0 else "diff --git " + b
            first = b.split("\n", 1)[0]
            path = first.rsplit(" b/", 1)[-1] if " b/" in first else ""
            if not (is_agent_meta(path) or is_sensitive_path(path)):
                keep.append(b)
        patch = "\n".join(keep)
    if len(patch) > max_bytes:
        patch = patch[:max_bytes] + f"\n… [diff truncated at {max_bytes} bytes]\n"
    return Snapshot(hd, br, changes, patch)
