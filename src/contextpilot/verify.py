"""Verification loop: did the AI actually fix it?

Detects the project's test command, runs it, summarises pass/fail in plain language, and on failure builds a
"fix it" prompt that re-sends the failure with the *same* scoped files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .scrubber import scrub
from .store import Store


def detect_command(root: Path, override: str | None = None) -> list[str] | None:
    if override:
        import shlex

        return shlex.split(override, posix=os.name != "nt")
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, ValueError):
            scripts = {}
        test = scripts.get("test", "")
        if test and "no test specified" not in test:
            npm = shutil.which("npm") or "npm"
            return [npm, "test", "--silent"]
    py_markers = ["pytest.ini", "conftest.py", "tox.ini", "setup.cfg"]
    pyproject = root / "pyproject.toml"
    has_py_tests = (root / "tests").is_dir() or (root / "test").is_dir() or any((root / m).exists() for m in py_markers) \
        or (pyproject.exists() and "pytest" in pyproject.read_text(encoding="utf-8", errors="ignore"))
    if has_py_tests:
        py = _project_python(root)
        return [py, "-m", "pytest", "-q", "--no-header", "-x", "--tb=short"]
    if (root / "go.mod").exists():
        return ["go", "test", "./..."]
    if (root / "Cargo.toml").exists():
        return ["cargo", "test", "--quiet"]
    if (root / "pom.xml").exists():
        return ["mvn", "-q", "test"]
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        gw = root / ("gradlew.bat" if os.name == "nt" else "gradlew")
        return [str(gw) if gw.exists() else "gradle", "test", "-q"]
    return None


def _project_python(root: Path) -> str:
    for cand in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python",
                 root / "venv" / "Scripts" / "python.exe", root / "venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


_PYTEST = re.compile(r"(?:(\d+) failed)?[, ]*(?:(\d+) passed)?[, ]*.*?(?:(\d+) errors?)?\s+in [\d.]+s")


def summarise(output: str, code: int) -> str:
    tail = output.strip().splitlines()[-30:]
    for line in reversed(tail):
        l = line.strip("= ").strip()
        if re.search(r"\b(passed|failed|error)\b.*\bin [\d.]+s", l):  # pytest
            return l
        if re.match(r"^(Tests?|Test Suites):", l):  # jest
            return l
        if l.startswith(("ok ", "FAIL", "PASS")):  # go
            return l
        if l.startswith("test result:"):  # cargo
            return l
    return "All checks passed" if code == 0 else f"Tests failed (exit code {code})"


def run(root: Path, store: Store, command: str | None = None, timeout: int = 600, session_id: str | None = None) -> dict:
    cmd = detect_command(root, command)
    if not cmd:
        return {"ran": False, "message": "No tests found in this project, so there's nothing automatic to check. "
                                        "You can set a test command in Setup."}
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env={**os.environ, "CI": "1", "PYTHONIOENCODING": "utf-8"})
        out, code = (p.stdout or "") + (p.stderr or ""), p.returncode
    except FileNotFoundError:
        return {"ran": False, "message": f"Couldn't run `{cmd[0]}` - is it installed?"}
    except subprocess.TimeoutExpired as e:
        out, code = ((e.stdout or "") if isinstance(e.stdout, str) else "") + f"\n[timed out after {timeout}s]", 124
    dur = (time.perf_counter() - t0) * 1000
    passed = code == 0
    if "no tests ran" in out.lower() or code == 5:  # pytest exit code 5: no tests collected
        passed, summary = None, "No tests were collected"
    else:
        summary = summarise(out, code)
    tail = scrub("\n".join(out.strip().splitlines()[-80:])).text
    sid = session_id or store.ensure_session()
    store.conn.execute(
        "INSERT INTO verifications(session_id, created_at, command, passed, exit_code, summary, output_tail, duration_ms)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (sid, time.time(), " ".join(cmd), None if passed is None else int(passed), code, summary, tail, dur))
    res = {"ran": True, "passed": passed, "summary": summary, "output_tail": tail}
    try:
        from .memory import capture_verification

        capture_verification(store, sid, res)
    except Exception:
        pass
    return {"ran": True, "passed": passed, "summary": summary, "exit_code": code, "command": " ".join(Path(cmd[0]).name if i == 0 else a for i, a in enumerate(cmd)),
            "output_tail": tail, "duration_ms": round(dur)}


def fix_prompt(root: Path, store: Store, result: dict) -> str:
    """A follow-up prompt for the AI: same scope, plus the failing output."""
    sess = store.active_session()
    ck = store.latest_checkpoint(sess["id"]) if sess else None
    scope = json.loads(ck["selected_files"] or "[]") if ck is not None else []
    lines = ["## Objective", "The previous change did not pass the tests. Fix the failure below with a minimal change."]
    if sess and sess["task"]:
        lines += ["", "## Original request", sess["task"]]
    if scope:
        lines += ["", "## Scope - read ONLY these files"] + [f"- `{p}`" for p in scope]
    lines += ["", f"## Test result: {result.get('summary', '')}", "```", result.get("output_tail", "")[-4000:], "```",
              "", "## Done when", f"- `{result.get('command', 'the test command')}` passes",
              "", "## Rules", "- Do not rewrite unrelated code. Never read .env, key or credential files."]
    return "\n".join(lines) + "\n"


def history(store: Store, limit: int = 20) -> list[dict]:
    return [dict(r) for r in store.conn.execute("SELECT id, session_id, created_at, command, passed, summary, duration_ms "
                                                "FROM verifications ORDER BY id DESC LIMIT ?", (limit,))]
