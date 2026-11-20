"""Locate and invoke ContextPilot from an Agent Skill script, whichever Python the agent uses."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(args: list[str]) -> int:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # 1) Same interpreter has the package installed.
    try:
        import contextpilot  # noqa: F401

        return subprocess.call([sys.executable, "-m", "contextpilot", *args])
    except ImportError:
        pass
    # 2) Interpreter recorded by `ctxly install`.
    pinned = HERE / "ctxly_python.txt"
    if pinned.exists():
        py = pinned.read_text(encoding="utf-8").strip()
        if py and Path(py).exists():
            return subprocess.call([py, "-m", "contextpilot", *args])
    # 3) `ctxly` on PATH.
    exe = shutil.which("ctxly")
    if exe:
        return subprocess.call([exe, *args])
    print("ContextPilot is not installed. Run: pip install contextpilot-ai  (then: ctxly install all)", file=sys.stderr)
    return 1
