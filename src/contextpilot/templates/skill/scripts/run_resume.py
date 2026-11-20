"""Agent Skill helper: print the last ContextPilot session (task, scope, notes, next steps, in-flight diff)."""

import sys

from _ctxly import run

if __name__ == "__main__":
    sys.exit(run(["resume", "--format", "full", *sys.argv[1:]]))
