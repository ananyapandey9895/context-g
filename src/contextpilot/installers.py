"""Installers for Google Antigravity and Gemini CLI.

Shared:       .agents/skills/contextpilot/   (open-standard Agent Skill: SKILL.md + scripts/)
Antigravity:  .agent/rules/contextpilot.md, .agent/workflows/ctxly-*.md,
              optional MCP entry in ~/.gemini/antigravity/mcp_config.json (--global)
Gemini CLI:   .gemini/settings.json (mcpServers.contextpilot + SessionStart/SessionEnd/PreCompress hooks),
              .gemini/commands/ctxly/*.toml (/ctxly:resume, /ctxly:run, /ctxly:checkpoint, /ctxly:status),
              GEMINI.md managed memory block
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from importlib import resources
from pathlib import Path

SKILL_DIR = Path(".agents/skills/contextpilot")


def _tpl(*parts: str):
    return resources.files("contextpilot").joinpath("templates", *parts)


def ctxly_command() -> tuple[str, list[str]]:
    """(executable, prefix args) that runs ContextPilot regardless of PATH."""
    exe = shutil.which("ctxly")
    if exe:
        return exe, []
    return sys.executable, ["-m", "contextpilot"]


def ctxly_shell() -> str:
    if shutil.which("ctxly"):
        return "ctxly"
    exe, args = ctxly_command()
    exe = exe.replace("\\", "/")  # forward slashes work on Windows and need no escaping in TOML/JSON
    q = f'"{exe}"' if " " in exe else exe
    return " ".join([q, *args])


def _write(path: Path, content: str, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
        written.append(path)


def _merge_json(path: Path, mutate) -> bool:
    data = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"{path} is not valid JSON ({e}); fix it or remove it, then re-run.") from e
    before = json.dumps(data, sort_keys=True)
    mutate(data)
    if json.dumps(data, sort_keys=True) == before:
        return False
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + f".bak-{int(time.time())}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def install_skill(root: Path, skills_dir: Path | None = None) -> list[Path]:
    written: list[Path] = []
    dest = root / (skills_dir or SKILL_DIR)
    _write(dest / "SKILL.md", _tpl("skill", "SKILL.md").read_text(encoding="utf-8"), written)
    for name in ("_ctxly.py", "run_resume.py", "run_optimize.py", "run_checkpoint.py"):
        _write(dest / "scripts" / name, _tpl("skill", "scripts", name).read_text(encoding="utf-8"), written)
    _write(dest / "scripts" / "ctxly_python.txt", sys.executable + "\n", written)
    return written


def _mcp_entry(root: Path | None) -> dict:
    exe, args = ctxly_command()
    entry = {"command": exe, "args": [*args, "mcp"]}
    if root is not None:
        entry["env"] = {"CTXLY_REPO": str(root)}
    return entry


def install_antigravity(root: Path, global_mcp: bool = False, skills_dir: Path | None = None) -> list[Path]:
    written = install_skill(root, skills_dir)
    rel = (skills_dir or SKILL_DIR).as_posix()
    rule = _tpl("antigravity", "rules", "contextpilot.md").read_text(encoding="utf-8").replace(SKILL_DIR.as_posix(), rel)
    _write(root / ".agent" / "rules" / "contextpilot.md", rule, written)
    for name in ("ctxly-resume.md", "ctxly-run.md", "ctxly-checkpoint.md"):
        wf = _tpl("antigravity", "workflows", name).read_text(encoding="utf-8").replace(SKILL_DIR.as_posix(), rel)
        _write(root / ".agent" / "workflows" / name, wf, written)
    if global_mcp:
        cfg = Path.home() / ".gemini" / "antigravity" / "mcp_config.json"

        def mutate(d: dict) -> None:
            # Global config: no pinned repo; tools take a `repo` argument (the workspace path).
            d.setdefault("mcpServers", {})["contextpilot"] = _mcp_entry(None)

        if _merge_json(cfg, mutate):
            written.append(cfg)
    return written


HOOK_EVENTS = {"SessionStart": "session-start", "SessionEnd": "session-end", "PreCompress": "pre-compress",
               "AfterModel": "after-model", "BeforeModel": "before-model"}


def install_gemini(root: Path, hooks: bool = True, skills_dir: Path | None = None) -> list[Path]:
    written = install_skill(root, skills_dir)
    shell = ctxly_shell()
    for name in ("resume.toml", "run.toml", "checkpoint.toml", "status.toml"):
        body = _tpl("gemini", "commands", name).read_text(encoding="utf-8").replace("!{ctxly ", "!{" + shell + " ")
        _write(root / ".gemini" / "commands" / "ctxly" / name, body, written)

    settings = root / ".gemini" / "settings.json"

    def mutate(d: dict) -> None:
        d.setdefault("mcpServers", {})["contextpilot"] = {**_mcp_entry(root), "trust": False}
        if hooks:
            hk = d.setdefault("hooks", {})
            for event, arg in HOOK_EVENTS.items():
                groups = [g for g in hk.get(event, []) if not any(
                    h.get("name", "").startswith("ctxly") for h in g.get("hooks", []))]
                groups.append({"hooks": [{"name": f"ctxly-{arg}", "type": "command",
                                          "command": f"{shell} hook {arg}", "timeout": 10000}]})
                hk[event] = groups

    if _merge_json(settings, mutate):
        written.append(settings)

    from .memory_sync import sync_gemini_md
    from .store import Store
    from .symbols import build_index

    with Store(root) as store:
        build_index(root, store)
        written += sync_gemini_md(root, store)
    return written


def uninstall(root: Path) -> list[Path]:
    removed: list[Path] = []
    for p in [root / SKILL_DIR, root / ".gemini" / "commands" / "ctxly", root / ".agent" / "rules" / "contextpilot.md",
              *[root / ".agent" / "workflows" / n for n in ("ctxly-resume.md", "ctxly-run.md", "ctxly-checkpoint.md")]]:
        if p.is_dir():
            shutil.rmtree(p)
            removed.append(p)
        elif p.exists():
            p.unlink()
            removed.append(p)
    settings = root / ".gemini" / "settings.json"
    if settings.exists():
        def mutate(d: dict) -> None:
            d.get("mcpServers", {}).pop("contextpilot", None)
            for ev in list(d.get("hooks", {})):
                d["hooks"][ev] = [g for g in d["hooks"][ev]
                                  if not any(h.get("name", "").startswith("ctxly") for h in g.get("hooks", []))]
                if not d["hooks"][ev]:
                    del d["hooks"][ev]
        if _merge_json(settings, mutate):
            removed.append(settings)
    return removed
