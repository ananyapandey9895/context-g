---
description: ContextPilot - resume the last interrupted session
---

1. Run `python .agents/skills/contextpilot/scripts/run_resume.py` in the workspace root.
2. Read the recovered Task, Scope, Notes and Next lines.
3. Open ONLY the files under Scope (start at the listed line ranges).
4. Continue from the Next step. Save progress with
   `python .agents/skills/contextpilot/scripts/run_checkpoint.py --notes "..." --next "..."`.
