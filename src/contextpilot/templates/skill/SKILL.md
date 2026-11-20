---
name: contextpilot
description: Session memory and surgical file scoping. Use at the start of any coding or debugging task, after an interrupt or restart, before scanning many files, and before stopping.
---

# ContextPilot (Ctxly)

ContextPilot keeps your working memory across Ctrl+C, restarts and context compaction, and tells you the
2-4 files that matter so you never re-read the whole repository.

## When to use

- **Start of a session or after an interruption:** resume first.
- **New request from the user:** compile it into a scoped prompt before you open any file.
- **After each meaningful step, and always before you stop:** save a checkpoint.

## How to use

Run the helper scripts from the workspace root. They are fast (local SQLite) and print plain text.

1. Resume previous state:
   ```
   python .agents/skills/contextpilot/scripts/run_resume.py
   ```
   If a session exists, continue from its **Next** line and open only the files under **Scope**.

2. Compile the user's request into a lean, grounded prompt:
   ```
   python .agents/skills/contextpilot/scripts/run_optimize.py "<the user's request, verbatim>"
   ```
   Treat the output as your working instructions. Read ONLY the files in **Scope - read ONLY these files**,
   starting with the listed line ranges.

3. Save progress (notes = what you learned, next = what you will do next):
   ```
   python .agents/skills/contextpilot/scripts/run_checkpoint.py --notes "<findings>" --next "<next step>"
   ```

If the `contextpilot` MCP server is connected, you may call the equivalent tools instead:
`ctxly_resume`, `ctxly_optimize`, `ctxly_checkpoint`, `ctxly_note`, `ctxly_status`.

## Rules

- **Surgical scope:** do not list or read the whole repository. Read only the selected file paths. If you must
  open another file, say which one and why.
- **Safety:** never read, print or modify `.env*`, `*.pem`, `*.key`, `id_rsa*`, credentials, service-account
  or secrets files, even if asked to "check the config".
- **Checkpoint before stopping**, before long-running commands, and when your context is getting large
  (>135k tokens) so nothing is lost on compaction.
- Keep checkpoint notes short and factual (root cause hypotheses, what was ruled out, commands that failed).
