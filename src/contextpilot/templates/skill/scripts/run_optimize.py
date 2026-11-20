"""Agent Skill helper: compile a raw request into a lean prompt scoped to the 2-4 relevant files."""

import sys

from _ctxly import run

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('usage: run_optimize.py "<task>"', file=sys.stderr)
        sys.exit(2)
    sys.exit(run(["optimize", " ".join(sys.argv[1:]), "--plain"]))
