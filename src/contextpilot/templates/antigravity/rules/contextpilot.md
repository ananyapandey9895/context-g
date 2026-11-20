# ContextPilot (always on)

This workspace uses ContextPilot for session memory and surgical context.

- At the start of every task, use the `contextpilot` skill: run
  `python .agents/skills/contextpilot/scripts/run_resume.py`, then
  `python .agents/skills/contextpilot/scripts/run_optimize.py "<request>"`.
- Read ONLY the files the optimizer returns (2-4 files). Do not scan the whole repository.
- Spawned subagents get the same scope: pass them the selected paths, never the full tree.
- Save progress with `python .agents/skills/contextpilot/scripts/run_checkpoint.py --notes "..." --next "..."`
  after each meaningful step and before you stop.
- Never open `.env*`, keys, credentials or secrets files.
