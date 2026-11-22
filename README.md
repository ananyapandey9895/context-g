# ContextPilot AI (Ctxly)

[![Live Prototype](https://img.shields.io/badge/Live_Prototype-Try_Demo-4285F4?style=for-the-badge&logo=google-cloud&logoColor=white)](https://jean-forty-champion-essence.trycloudflare.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)

> 🚀 **Live Interactive Prototype**: [https://jean-forty-champion-essence.trycloudflare.com](https://jean-forty-champion-essence.trycloudflare.com)

**A persistent, surgical, cache-friendly memory layer for Google Antigravity and Gemini CLI.**

Autonomous coding sessions lose their state when interrupted. Each restart wipes the debugging context, breaks
prefix caches, and the agent re-reads code that hasn't changed. ContextPilot fixes this:

| Problem | ContextPilot feature | Where |
|---|---|---|
| 1. AI reads everything | **Surgical file selector**: AST symbols + git diff + traceback frames → the 2–4 files that matter, with line ranges | `selector.py`, `symbols.py`, `gitctx.py` |
| 2. Burning tokens in loops | **Stable cache prefix** + Vertex AI / Gemini **CachedContent** reused while the code structure is unchanged | `cache.py`, `memory_sync.py` |
| 3. Ctrl+C memory wipe | **Signal trap** (`ctxly wrap`): SIGINT/SIGBREAK/SIGTERM/SIGHUP → checkpoint in about 60–85 ms (measured on Windows) | `signals.py`, `checkpoint.py` |
| 4. Starting over from zero | **1-click resume**: a 180-token recovery bundle; SessionStart hook; `gemini --resume` mapping | `checkpoint.py`, `hooks.py` |
| 5. Near-miss fixes | **Gemini Flash prompt compiler**: vague request → objective, scoped files, steps, done-when checks | `optimizer.py` |
| Privacy | **Local secret scrubber** (regex + Shannon entropy); `.env`, keys and credentials are never read | `scrubber.py` |
| Trust | **Human gate** `[s]end / [e]dit / [c]ancel` · **transparent token math** that keeps local estimates separate from billed usage | `cli.py`, `report.py` |

All memory stays on your machine in `.ctxly/sessions.db` (SQLite, WAL mode), and `.ctxly/` is gitignored automatically.

> **New here? Read [How to use Ctxly](HOW_TO_USE.md)**: a plain-English guide for everyone (also inside the app).

## The easy way: the ContextPilot app (no terminal needed)

**Windows:** double-click **`Start ContextPilot.cmd`**. **macOS/Linux:** double-click or run `start-contextpilot.command`.
The first run installs everything (Python 3.10+ is required). Your browser then opens at `http://127.0.0.1:8765`.

| Screen | What a non-developer does there |
|---|---|
| **Welcome** | *Browse for folder…* (a native picker), paste a path, or click a recent project |
| **Ask AI** | Type a request in plain English → **Prepare my request** → see "2 of 40 files" with plain-language reasons → **Copy instructions**, **Send to Gemini CLI** (opens a terminal that saves progress if you press Ctrl+C), or **Open in Antigravity** (copies the instructions and opens the app) |
| **Welcome back** card | Shows the last task, what was figured out and the next step. **Copy "where I left off" note**, or continue in Gemini CLI or Antigravity. |
| **Check the AI's work** (Step 3) | **What did the AI change?** (plain-English bullets + the exact diff), **Check if it works** (runs your tests; on failure, one click sends a fix-it request with the same files + the error), **Undo AI changes** (back to the moment before the AI started) |
| **Voice & screenshots** | 🎤 speak your request (browser speech recognition); paste or drop a screenshot of an error and Gemini reads it into a request (smart mode) |
| **Timeline** | Every saved moment (Ctrl+C, before the AI started, manual saves) with **See changes** and **Restore to here** - a safety copy is always taken first |
| **Savings** | Money saved today / this week / all time (USD or ₹), a 14-day chart, Google's real billed amounts, test results, a per-session **spending limit**, a printable **report (Save as PDF)** and **Send to team chat** |
| **Project map** | Plain-English map of folders and files ("Sign-in, passwords and access control"), size bars, classes/functions, and **Ask** on any file |
| **Team** | Share your task/notes/files (never code or secrets) in `.ctxly-team/` - teammates' resume notes include it |
| **Save progress** | Two boxes: *what have you figured out?* and *what's next?* → Save (optionally share with the team) |
| **Privacy** | Lists the files that are never read (`.env`, keys) and the secrets found in code (masked, hidden automatically) |
| **Setup** | **Connect both** (Antigravity + Gemini CLI), Gemini API key, currency and agent model, test command, Slack/Google Chat webhook, one-click **local Gemma** (via Ollama), and a health checklist with one-click fixes |

From a terminal it's `ctxly ui` (add `--repo <folder>` to open a project directly, or `--shortcut` to put a launcher on the desktop).
The app only listens on `127.0.0.1`. A per-user secret token (in `~/.ctxly/ui_token`) means other websites can't call it, and a Host-header check blocks DNS rebinding.
The Gemini key is never sent back to the browser. "Remember on this computer" stores it in `~/.ctxly/credentials.json`.

### Editor panel and browser button

- **VS Code / Antigravity side panel** - `integrations/vscode/` (packaged as `contextpilot-0.2.0.vsix`: *Extensions → ⋯ → Install from VSIX…*). Shows the app in the sidebar, starts `ctxly ui` for the open folder, and adds *Ask AI about selection* to the editor's right-click menu.
- **"ContextPilot it" on GitHub issues** - `integrations/browser/` (Chrome/Edge: `chrome://extensions` → Developer mode → *Load unpacked*). Opens the app with the issue pre-filled; the text only goes to `127.0.0.1`.
- **Team chat** - savings and test results are posted to a Slack or Google Chat *incoming webhook* (`ctxly savings --send` or the Savings tab). It's a one-way report, not an interactive bot.
- **Spending limit** - in Gemini CLI, the `BeforeModel` hook stops the session and saves a checkpoint once the session's estimated cost passes your limit; the `AfterModel` hook records real token usage and checkpoints before the context fills up.

## Install (developers)

```bash
pip install -e ".[all]"          # from this folder; installs the `ctxly` command
cd /path/to/your/repo
ctxly init                       # creates .ctxly/ and builds the incremental symbol index
ctxly install all                # or: ctxly install antigravity | ctxly install gemini
ctxly doctor                     # checks git, SDKs, credentials, gemini/agy, and installed integrations
```

Optional Gemini credentials enable the Flash compiler, exact token counts and context caching. Without them,
everything still works with the deterministic local compiler.

```bash
# Gemini API
export GEMINI_API_KEY=...
# or Vertex AI
export GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=my-proj GOOGLE_CLOUD_LOCATION=us-central1
gcloud auth application-default login
```

## Google Antigravity

`ctxly install antigravity` writes:

| File | Purpose |
|---|---|
| `.agents/skills/contextpilot/SKILL.md` | Open-standard Agent Skill. Only its YAML front-matter (about 40 tokens) loads at startup. |
| `.agents/skills/contextpilot/scripts/run_resume.py` · `run_optimize.py` · `run_checkpoint.py` | On-demand helper scripts that run only when the agent uses the skill |
| `.agent/rules/contextpilot.md` | Always-on workspace rule: resume first, read only the scoped files, checkpoint before stopping, never open `.env` files |
| `.agent/workflows/ctxly-run.md` · `ctxly-resume.md` · `ctxly-checkpoint.md` | `/ctxly-run`, `/ctxly-resume`, `/ctxly-checkpoint` workflows in the agent panel |
| `~/.gemini/antigravity/mcp_config.json` *(only with `--global-mcp`)* | Registers the `contextpilot` MCP server (a backup of the old file is kept) |

In the IDE, run `ctxly watch` in a terminal. It saves a checkpoint whenever the working tree settles.
For terminal sessions, use `ctxly run "<task>" --to antigravity`, which compiles the prompt, copies it to the clipboard and opens `agy`.

> If your Antigravity build looks for skills in `.agent/skills/`, use
> `ctxly install antigravity --skills-dir .agent/skills/contextpilot`.

## Gemini CLI

`ctxly install gemini` writes:

| File | Purpose |
|---|---|
| `.agents/skills/contextpilot/` | The same Agent Skill (Gemini CLI also discovers skills here) |
| `.gemini/settings.json` → `mcpServers.contextpilot` | FastMCP bridge with the tools `ctxly_resume`, `ctxly_optimize`, `ctxly_select_files`, `ctxly_checkpoint`, `ctxly_note`, `ctxly_status` |
| `.gemini/settings.json` → `hooks` | `SessionStart` injects the recovery bundle and records the Gemini session id. `SessionEnd` and `PreCompress` save checkpoints (compaction defense). Your existing hooks and settings are preserved, and a `.bak` backup is written. |
| `.gemini/commands/ctxly/*.toml` | `/ctxly:run <task>`, `/ctxly:resume`, `/ctxly:checkpoint <notes>`, `/ctxly:status` |
| `GEMINI.md` | Managed `<!-- ctxly:begin/end -->` block with the protocol, a byte-stable `@.ctxly/prefix.md` project map (good for implicit caching) and the last session. Text outside the block is never touched. |

Recommended launch, which captures Ctrl+C:

```bash
ctxly wrap gemini               # any CLI works: ctxly wrap -- gemini -m gemini-2.5-pro
ctxly resume --launch           # after a crash: gemini --resume <recorded id | latest>, still trapped
```

> Hooks require a Gemini CLI version with hooks support. If yours predates it, use `ctxly install gemini --no-hooks`
> and rely on `ctxly wrap` plus the MCP tools.

## CLI reference

```text
ctxly run "<task>" [--to gemini|antigravity|print] [-y] [--no-llm]   full pipeline + human gate + dispatch
ctxly optimize "<task>" [--plain|--json]                            compiled prompt only
ctxly select "<task>"                                               which files, and why
ctxly checkpoint --notes "..." --next "..."                         save progress now
ctxly note "<finding>"                                              append to session memory
ctxly resume [--format bundle|full|json] [--launch]                 restore
ctxly status [--json] · ctxly sessions                              telemetry and history
ctxly wrap <cmd...> · ctxly watch                                   Ctrl+C trap / IDE autosave
ctxly scrub [path|-] [--print]                                      local secret scan
ctxly cache warm|status · ctxly index · ctxly sync-memory [--hierarchical]
ctxly install|uninstall|doctor · ctxly mcp
ctxly verify [--copy]                                               run tests; on failure write a fix-it request
ctxly explain [--offline] [--diff]                                  what changed since the undo point
ctxly undo [--to <checkpoint id>] [-y] · ctxly timeline             safe undo / rewind (safety snapshot first)
ctxly savings [--report out.html] [--send] · ctxly budget [usd]     money saved, report, spending limit
ctxly team share [--name X] · ctxly team feed · ctxly map           team memory, project map
ctxly gemma [--pull]                                                local Gemma triage via Ollama
ctxly memory list|add|done|forget|recall                            project memory (decisions, to-dos, errors, changes)
ctxly metrics [--days N] [--json]                                   measured token reduction, cache hits/misses, latency
```

MCP tools for agents: `ctxly_resume`, `ctxly_optimize`, `ctxly_select_files`, `ctxly_checkpoint`, `ctxly_note`,
`ctxly_status`, `ctxly_remember`, `ctxly_recall`, `ctxly_task_done`.

Settings live in `.ctxly/config.json`, and each one can be overridden with a `CTXLY_<NAME>` environment variable:
`optimizer_model` (default `gemini-2.5-flash`), `cache_model`, `cache_ttl_seconds`, `min_files`/`max_files` (2/4),
`compaction_threshold_tokens` (135000), `recovery_bundle_tokens` (260), `autosave_seconds`, `external_mode`
(`auto` | `ask` | `off`: whether smart features may call Gemini, need approval first, or never do), and
`gemma_model` (for example `gemma3:4b`, which enables local Gemma triage through Ollama; heuristic triage is used otherwise).

## Demo (the pitch scenario)

```bash
python examples/make_demo_repo.py demo/ecommerce-core     # 40 indexed files, JWT 401 bug, a .env containing secrets
cd demo/ecommerce-core && ctxly init && ctxly install all
ctxly run "hey can you fix the 401 in the JWT auth middleware? users get logged out after refresh" --to print
```

Measured offline on this repo: **2 of 40 files selected** (`jwt_handler.py`, `auth_middleware.py`), context
**~16,980 → ~550 tokens (local estimate)**, and checkpoints written in **60–85 ms** including a trapped Ctrl+Break.

## How context is chosen and remembered (v0.3)

- **Project memory** (`memories` table in `.ctxly/sessions.db`, WAL mode, indexed): *decisions* and *unresolved errors*
  are long-term project memory; *to-dos*, *changes* and *findings* are task/session memory. Everything is secret-scrubbed
  before it is written and near-duplicates are merged. Failing `ctxly verify` runs become open errors and passing runs
  resolve them. A new "next step" marks the previous one done.
- **Recovery summary** (Gemini CLI `SessionStart` hook, `ctxly resume`, the app's Welcome back card): task, last
  checkpoint, modified files, last active file, completed steps, unresolved errors and the next step. When it's over the
  token budget, the least important lines are dropped first. Ctrl+C / closing a wrapped CLI saves gracefully, and periodic
  autosave covers hard crashes up to the last interval. A power cut or killed process can still lose the last few minutes.
- **Retrieval** = explicit mentions, git changes, rarity-weighted path/symbol matches (Python AST, regex for other
  languages), learned edits, then pluggable strategies in `selector.STRATEGIES`: **import graph** (imports are stored per
  file at index time, so this costs nothing for unchanged files), **test pairing**, and **project memory**. Add a strategy
  (e.g. embeddings) by appending a function that takes a `RetrievalContext`.
- **Prompt compiler** checks its own output: user constraints ("don't…", "must…", "keep…") are carried over verbatim if
  the objective lost them, duplicate lines are removed, the final text is scrubbed again, and compiled file paths must come
  from the selection. Results are logged per request and summarised by `ctxly metrics`.

## Honest numbers

- The token "reduction" shown by `ctxly` is a **local estimate**: the ~4 chars/token estimate for reading every
  indexed file compared with the compiled prompt plus the scoped files. Billed numbers (`prompt_token_count`,
  `cached_content_token_count`) come from Gemini `usage_metadata` and are shown in a separate column. The two are never blended.
- Explicit context caching needs a minimum prefix size: 2,048 tokens for Gemini 2.5 Flash/Pro per Google's current docs
  (older docs said 1,024/4,096; override with `cache_min_tokens`). Smaller repos skip it, say so, and still benefit from
  Gemini's automatic implicit caching because the prefix is byte-stable. Every hit and miss is logged (`ctxly metrics`).
- Cached-token pricing (about 10% of the input price) follows Google's published Gemini 2.5+ pricing. Check the current rates for your model.

## Tests

```bash
pip install -e ".[dev]" && pytest -q      # 46 tests: scrubber, index, selector, checkpoints, installers, web app + v0.2
                                          # endpoints, snapshots/undo, verify loop, money, learning, team, map,
                                          # budget hooks, MCP tools, CLI end-to-end, Gemini paths (faked transport),
                                          # project memory, recovery summary, import graph, approval gate, metrics
```
