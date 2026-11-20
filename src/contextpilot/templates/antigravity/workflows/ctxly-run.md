---
description: ContextPilot - compile the request into a scoped prompt, then execute it
---

1. Run `python .agents/skills/contextpilot/scripts/run_optimize.py "<the user's request>"`.
2. Treat the output as your plan. Read ONLY the files under "Scope - read ONLY these files".
3. Implement the change, then verify it using the "Done when" checks.
4. Save progress: `python .agents/skills/contextpilot/scripts/run_checkpoint.py --notes "<what changed>" --next "<follow-up>"`.
