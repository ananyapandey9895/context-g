"""Agent Skill helper: save progress. Usage: run_checkpoint.py --notes "..." --next "..." """

import sys

from _ctxly import run

if __name__ == "__main__":
    sys.exit(run(["checkpoint", "--reason", "agent", *sys.argv[1:]]))
