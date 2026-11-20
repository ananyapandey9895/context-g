"""Code snapshots for timeline rewind, safe-undo and "what changed?".

A snapshot is an ordinary git commit object built from a *temporary* index, so it captures tracked and
untracked (non-ignored) files without touching your branch, your index or your working tree. It's kept
alive by a ref under refs/ctxly/ (invisible to `git log`/`git branch`). Sensitive files are never included.
Every restore first takes a safety snapshot, so a restore can itself be undone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .gitctx import is_agent_meta
from .scrubber import is_sensitive_path

_EXCL = [":(exclude,glob).ctxly/**", *[f":(exclude,glob)**/{g}" for g in
         (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*.p12", "*.pfx", "credentials*.json", "*.tfvars")]]
_RM = [":(glob).ctxly/**", *[f":(glob)**/{g}" for g in
        (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*.p12", "*.pfx", "credentials*.json", "*.tfvars")]]
_IDENT = ["-c", "user.name=ContextPilot", "-c", "user.email=contextpilot@localhost", "-c", "core.quotepath=off"]
# Never part of a snapshot and never deleted by a restore: build junk, dependencies, team notes, secrets.
_EXTRA_IGNORE = """.ctxly/
.ctxly-team/
__pycache__/
*.pyc
node_modules/
.venv/
venv/
.pytest_cache/
.mypy_cache/
.DS_Store
.env
.env.*
*.pem
*.key
id_rsa*
*.p12
*.pfx
credentials*.json
*.tfvars
"""


def _ignore_args(root: Path) -> list[str]:
    """`-c core.excludesFile=…` that keeps the user's global ignores and adds ours (no repo files are modified)."""
    f = root / ".ctxly" / "snapshot-ignore"
    try:
        glob = ""
        for cand in (os.environ.get("XDG_CONFIG_HOME") and Path(os.environ["XDG_CONFIG_HOME"]) / "git" / "ignore",
                     Path.home() / ".config" / "git" / "ignore"):
            if cand and cand.is_file():
                glob = cand.read_text(encoding="utf-8", errors="replace")
                break
        cfg = subprocess.run(["git", "config", "--get", "core.excludesFile"], cwd=root, capture_output=True, text=True)
        if cfg.returncode == 0 and cfg.stdout.strip():
            p = Path(os.path.expanduser(cfg.stdout.strip()))
            if p.is_file():
                glob = p.read_text(encoding="utf-8", errors="replace")
        f.parent.mkdir(parents=True, exist_ok=True)
        content = glob.rstrip("\n") + "\n" + _EXTRA_IGNORE
        if not f.exists() or f.read_text(encoding="utf-8") != content:
            f.write_text(content, encoding="utf-8")
    except OSError:
        return []
    return ["-c", f"core.excludesFile={f.as_posix()}"]


def _git(root: Path, *args: str, env: dict | None = None, check: bool = False) -> str | None:
    try:
        p = subprocess.run(["git", *_IDENT, *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        if check:
            raise RuntimeError(p.stderr.strip()[:400] or f"git {' '.join(args)} failed")
        return None
    return p.stdout


def _git_dir(root: Path) -> Path | None:
    out = _git(root, "rev-parse", "--absolute-git-dir")
    return Path(out.strip()) if out else None


def _tree_commit(root: Path, label: str) -> str | None:
    """Commit object of the current working tree (tracked + untracked, ignored/sensitive excluded)."""
    gd = _git_dir(root)
    if gd is None:
        return None
    tmp = root / ".ctxly" / f"snap-{os.getpid()}-{time.monotonic_ns()}.index"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        if (gd / "index").exists():
            shutil.copyfile(gd / "index", tmp)  # start from the real index: only changed files get re-hashed
        env = {**os.environ, "GIT_INDEX_FILE": str(tmp)}
        # Exclude pathspecs that hit .gitignored paths make `git add` fail, so add everything (ignored files are
        # skipped anyway) and then drop sensitive/ContextPilot paths from the temporary index.
        if _git(root, *_ignore_args(root), "add", "-A", "--", ".", env=env) is None:
            return None
        _git(root, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", *_RM, env=env)
        tree = (_git(root, "write-tree", env=env) or "").strip()
        if not tree:
            return None
        head = (_git(root, "rev-parse", "-q", "--verify", "HEAD") or "").strip()
        args = ["commit-tree", tree, "-m", f"ContextPilot snapshot: {label}"]
        if head:
            args[2:2] = ["-p", head]
        return (_git(root, *args) or "").strip() or None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def take(root: Path, label: str) -> str | None:
    commit = _tree_commit(root, label)
    if commit:
        _git(root, "update-ref", f"refs/ctxly/snapshots/{int(time.time() * 1000)}", commit)
    return commit


def _files_in(root: Path, commit: str) -> set[str]:
    out = _git(root, "ls-tree", "-r", "--name-only", commit) or ""
    return {l for l in out.splitlines() if l}


def _current_files(root: Path) -> set[str]:
    out = _git(root, *_ignore_args(root), "ls-files", "-co", "--exclude-standard") or ""
    return {l for l in out.splitlines()
            if l and not l.startswith((".ctxly/", ".ctxly-team/")) and not is_sensitive_path(l)}


def diff_since(root: Path, commit: str, max_bytes: int = 120_000) -> dict:
    """Changes between a snapshot and *now* (including brand-new files)."""
    now = _tree_commit(root, "now")
    if not now:
        return {"diff": "", "files": []}
    diff = _git(root, "diff", "--no-color", "-U3", commit, now, "--", ".", *_EXCL) or ""
    stat = _git(root, "diff", "--numstat", commit, now, "--", ".", *_EXCL) or ""
    files = []
    for line in stat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and not is_agent_meta(parts[2]):
            files.append({"path": parts[2], "added": int(parts[0]) if parts[0].isdigit() else 0,
                          "removed": int(parts[1]) if parts[1].isdigit() else 0})
    if len(diff) > max_bytes:
        diff = diff[:max_bytes] + "\n… [diff truncated]\n"
    return {"diff": diff, "files": files}


def restore(root: Path, commit: str) -> dict:
    """Make the working tree match the snapshot. HEAD/branch are untouched; a safety snapshot is taken first."""
    if _git(root, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        raise ValueError("That snapshot no longer exists.")
    safety = take(root, "before restore")
    if not safety:
        raise RuntimeError("Couldn't take a safety snapshot - nothing was changed.")
    target = _files_in(root, commit)
    extra = sorted(_current_files(root) - target)
    _git(root, "checkout", commit, "--", ".", ":(exclude).ctxly-team", check=True)
    _git(root, "reset", "-q")  # put the index back to HEAD; working tree keeps the restored files
    removed = []
    for rel in extra:
        p = root / rel
        if p.is_file() and not is_sensitive_path(rel):
            p.unlink()
            removed.append(rel)
    return {"safety_ref": safety, "restored_files": len(target), "removed": removed}
