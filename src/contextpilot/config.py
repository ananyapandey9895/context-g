"""Repository discovery and settings for ContextPilot."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CTXLY_DIR = ".ctxly"

DEFAULTS = {
    # Gemini model used to compile prompts (fast + cheap).
    "optimizer_model": "gemini-3.8-flash",
    # Model the context cache is created for (must match the model you query).
    "cache_model": "gemini-3.8-flash",
    "cache_ttl_seconds": 3600,
    # Surgical selection bounds.
    "min_files": 2,
    "max_files": 4,
    # Save state before the agent's context is compacted.
    "compaction_threshold_tokens": 135_000,
    # Max size of the recovery bundle injected on resume.
    "recovery_bundle_tokens": 260,
    # Autosave interval for `ctxly wrap` / `ctxly watch` (seconds).
    "autosave_seconds": 120,
    # Optional local Gemma triage through Ollama (empty = heuristic triage).
    "gemma_model": "",
    "ollama_url": "http://localhost:11434",
}


@dataclass
class Settings:
    optimizer_model: str = DEFAULTS["optimizer_model"]
    cache_model: str = DEFAULTS["cache_model"]
    cache_ttl_seconds: int = DEFAULTS["cache_ttl_seconds"]
    min_files: int = DEFAULTS["min_files"]
    max_files: int = DEFAULTS["max_files"]
    compaction_threshold_tokens: int = DEFAULTS["compaction_threshold_tokens"]
    recovery_bundle_tokens: int = DEFAULTS["recovery_bundle_tokens"]
    autosave_seconds: int = DEFAULTS["autosave_seconds"]
    gemma_model: str = DEFAULTS["gemma_model"]
    ollama_url: str = DEFAULTS["ollama_url"]
    extra: dict = field(default_factory=dict)


def find_repo_root(start: Path | str | None = None) -> Path:
    """Walk upward for a .ctxly or .git directory; fall back to the start dir."""
    env = os.environ.get("CTXLY_REPO")
    if env and start is None:
        return Path(env).resolve()
    cur = Path(start or os.getcwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / CTXLY_DIR).is_dir() or (p / ".git").exists():
            return p
    return cur


def ctxly_dir(root: Path) -> Path:
    d = root / CTXLY_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_settings(root: Path) -> Settings:
    path = root / CTXLY_DIR / "config.json"
    s = Settings()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for k, v in data.items():
            if hasattr(s, k) and k != "extra":
                setattr(s, k, v)
            else:
                s.extra[k] = v
    for k in DEFAULTS:
        env = os.environ.get(f"CTXLY_{k.upper()}")
        if env:
            cur = getattr(s, k)
            setattr(s, k, type(cur)(env) if not isinstance(cur, str) else env)
    return s


def save_settings(root: Path, s: Settings) -> Path:
    path = ctxly_dir(root) / "config.json"
    data = asdict(s)
    data.update(data.pop("extra") or {})
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def ensure_gitignored(root: Path) -> None:
    """Make sure .ctxly/ is never committed (it holds local session memory)."""
    gi = root / ".gitignore"
    line = f"{CTXLY_DIR}/"
    try:
        existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    except OSError:
        return
    if any(l.strip() in (line, CTXLY_DIR) for l in existing.splitlines()):
        return
    sep = "" if not existing or existing.endswith("\n") else "\n"
    gi.write_text(f"{existing}{sep}# ContextPilot local memory\n{line}\n", encoding="utf-8")
