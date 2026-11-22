# ContextPilot AI (Ctxly): The Complete Guide

**Architect: Aarush Bharti ([@Aarush-bigO](https://github.com/Aarush-bigO))**  
**Version covered:** v0.3.0 (Python package `contextpilot-ai`, command `ctxly`)  
**Written as:** a joint founder and research brief explaining why we built it, how it works, how to use it, how to deploy it, and how it makes money.

---

## Part 1. Executive Summary
* **One-line Pitch:** ContextPilot is a local memory and context layer for Google's AI coding agents (Google Antigravity & Gemini CLI). It preserves session state across interruptions, reduces codebase reading bloat by 97.2%, scrubs credentials on-device, and provides verifiable undo and test checking.
* **Problem:** AI agents lose in-flight progress upon Ctrl+C, session resets, or context window compaction. They repeatedly re-read full repositories, inflating token expenses and invalidating prefix prompt caches.
* **Prototype Deliverables (v0.3.0):**
  * `ctxly` CLI (33 commands across session management, indexing, optimization, and auditing).
  * Standalone local Web UI (`http://127.0.0.1:8765`) featuring a 3-step onboarding flow, visual timeline, project tree, and token savings analytics.
  * Google Antigravity integration via `.agents/skills/contextpilot/SKILL.md`, rules, workflows, and FastMCP.
  * Gemini CLI integration via lifecycle hooks (`SessionStart`, `SessionEnd`, `PreCompress`, `BeforeModel`), commands, and `GEMINI.md` synchronization.
  * 46 automated unit and integration tests (100% passing).
* **Key Measured Metrics:**
  * Context size reduction: **97.2% average** across 8 real tasks.
  * File recall: **93% (13 of 14 target files identified)**.
  * Ctrl+C async checkpoint latency: **66 ms median / 85 ms max**.
  * Resume recovery bundle size: **73 tokens**.
  * Secret scrubber accuracy: **7 of 7 test secrets caught with zero leaks**.

---

## Part 2. Problem Statement & Theoretical Grounding
* **Five Stages of Broken AI Coding Sessions:**
  1. *Unbounded Reading:* Agent inspects dozens of files for a localized 2-file bug.
  2. *Redundant Loop Spend:* Unchanged files are re-billed on every single conversational turn.
  3. *State Destruction:* Interrupting a hallucinating agent destroys notes and execution context.
  4. *Session Amnesia:* Restarting forces developers to re-explain problem statements from scratch.
  5. *Unverified Commits:* Agent proposes plausible-looking but broken code without automated test loops.
* **Root Causes:** Stateless agent runtimes, naive keyword-based retrieval without AST/import awareness, fragile non-deterministic prompt prefixes breaking Vertex AI prefix caches, unscrubbed local credentials leaking to models.
* **Core Thesis:** A persistent, surgical, cache-friendly, private, and verifiable context layer improves agent accuracy, speeds up time-to-fix, and cuts developer token costs by up to 90% without altering base model weights.

---

## Part 3. Target User Personas
* **Individual Developers:** Crash-proof agent memory, localized prompts, instant undo snapshots.
* **Non-Technical Founders & Beginners:** GUI dashboard with 1-click test execution, plain-English diffs, and rollback safety.
* **Engineering Teams:** Shared team checkpoints via `.ctxly-team/`, unified project memory, automated Slack/Google Chat summaries.
* **Enterprise CTOs & FinOps (GCCs):** Measurable token ROI telemetry, distinct audit trails for estimated vs. Google-billed tokens, hard session spending caps.
* **Security & Compliance Teams:** 100% local SQLite storage, on-device Shannon entropy secret scrubbing, explicit human approval gates before API transmission.

---

## Part 4. Real-World Scenarios
* **Scenario A (Interrupted Bug Fix):** Priya debugs a JWT auth 401. Ctxly scopes 2 of 40 files (401 tokens vs 16,971 tokens). Upon Ctrl+C, state is flushed in 66 ms. Next morning, Gemini CLI injects a 73-token summary and resumes work seamlessly.
* **Scenario B (Non-Developer Founder):** Founder launches via `Start ContextPilot.cmd`, types a bug description, reviews plain-English explanations, and verifies fixes with automated test runners.
* **Scenario C (Context Compaction):** PreCompress hook triggers a snapshot before context window truncation, preserving dropped reasoning chains in local SQLite.
* **Scenario D (Failing Test Loop):** `ctxly verify` captures test failures, marks an open defect in project memory, and formats a focused re-prompt with runtime error traces.
* **Scenario E (Leaked Credentials):** Scrubber detects high-entropy Google API keys in stack traces and redacts them prior to storage or external API transport.
* **Scenario F (Regulated Enterprise):** "Ask me first" approval modal previews exact file paths and scrubbed prompts before network transmission.
* **Scenario G (Team Handover):** Developer syncs progress into `.ctxly-team/`. Colleague's morning resume automatically surfaces peer findings.
* **Scenario H (FinOps Budget Guard):** Per-session spending limit halts agent execution and checkpoints progress once consumption breaches threshold.

---

## Part 5. Comprehensive Feature Breakdown
* **Core Engine:** Crash-proof SQLite WAL session state, persistent project memory, surgical AST/git/traceback file selection, Gemini 2.5 Flash prompt compiler with rule preservation, byte-stable Vertex AI prefix caching, regex + Shannon entropy privacy scrubber, local Gemma 3 (4B) triage via Ollama.
* **Verification & Audit:** `ctxly verify` test harness loop, `ctxly explain` diff translator, `ctxly undo` backed by hidden `refs/ctxly/snapshots` git refs.
* **FinOps & Telemetry:** Multi-currency savings tracking (USD/INR), real Google `usage_metadata` reconciliation, session spending caps, PDF audit reports.
* **Ecosystem Surface:** Web UI (`http://127.0.0.1:8765`), Google Antigravity Skill & workflows, Gemini CLI hooks & commands, VS Code / Antigravity sidebar extension (`.vsix`), Chrome/Edge extension for GitHub issues, FastMCP server.

---

## Part 6. System Architecture & Data Flow
* **Three-Tier Architecture:**
  1. *Presentation Layer:* Web UI, CLI, Gemini CLI, Antigravity, VS Code panel, GitHub extension.
  2. *Core Execution Engine:* Privacy scrubber, retrieval engine, prompt compiler, checkpoint manager, verify/undo controller.
  3. *Local Storage & External Google AI:* Local SQLite WAL (`sessions.db`, 12 tables), hidden git snapshots (`refs/ctxly/snapshots`), optional Google AI (Gemini 2.5 Flash, Vertex AI caching, Gemma 3 on-device).
* **Interruption Handling:** Signal interception (`SIGINT`, `SIGBREAK`, `SIGTERM`, `SIGHUP`) writes SQLite state immediately (~66 ms) followed by asynchronous git snapshotting.
* **Security Architecture:** 127.0.0.1 loopback binding, session tokens (`~/.ctxly/ui_token`), DNS rebinding protection via Host header verification, strict exclusion of `.env` and credential files.

---

## Part 7. Technology Stack
* **Language & Runtime:** Python 3.10+, Typer, Rich, Python `ast`, Git plumbing, standard library HTTP server.
* **Google AI Platform:** `google-genai` SDK, Gemini 2.5 Flash, Gemini API & Vertex AI context caching, Gemma 3 (4B) via Ollama.
* **Agent Protocols:** Model Context Protocol (FastMCP), Gemini CLI hooks & slash commands, Antigravity rules & Agent Skills.
* **Data Persistence:** SQLite 3 (WAL mode), hidden Git refs (`refs/ctxly/snapshots`).
* **Frontends:** Vanilla single-file HTML/CSS/JS (zero CDN dependencies), Web Speech API, Chrome MV3 extension, VS Code webview extension.
* **Test Suite:** 46 passing pytest tests with faked Gemini transport for deterministic offline verification.

---

## Part 8. Operational Guide
* **Web UI Execution:** Launch via `start-contextpilot.command` (Mac) or `Start ContextPilot.cmd` (Windows). Opens `http://127.0.0.1:8765`.
* **Developer CLI:**
  * Setup: `ctxly init`, `ctxly install all`, `ctxly doctor`.
  * Execution: `ctxly run "<task>" --to gemini`, `ctxly wrap gemini`, `ctxly resume --launch`, `ctxly verify`, `ctxly undo`.
* **Google Antigravity Integration:** `.agents/skills/contextpilot/SKILL.md` (40-token lazy header), `.agent/rules/contextpilot.md`, `/ctxly-run`, `/ctxly-resume`, `/ctxly-checkpoint`.
* **Gemini CLI Integration:** `.gemini/settings.json` hooks, `.gemini/commands/ctxly/*.toml`, `GEMINI.md` prefix-cached managed block.

---

## Part 9. Deployment Strategies
* **Local Source Execution:** Editable virtual environment (`pip install -e ".[all]"`).
* **Package Distribution:** Planned PyPI release (`pip install contextpilot-ai[all]`).
* **IDE & Browser Extensions:** VSIX package for Antigravity/VS Code; unpacked/Store release for Chrome/Edge.
* **Enterprise & GCC Setup:** Direct Vertex AI routing via `GOOGLE_GENAI_USE_VERTEXAI=true` and Application Default Credentials (ADC) in target regions (e.g., `asia-south1`).
* **Roadmap Cloud Architecture:** Optional telemetry aggregation service on Google Cloud Run + Cloud SQL / Firestore with Identity-Aware Proxy (IAP).

---

## Part 10. Business Model & Market Sizing
* **Open-Core PLG Strategy:**
  * Free Tier ($0): Core CLI, Web UI, Agent Skill, MCP, local storage, secret scrubber, undo snapshots.
  * Pro Tier ($19/dev/mo): Team memory sync, automated savings reporting, priority smart mode.
  * Enterprise / GCC Tier ($49/seat/mo): Centralized FinOps dashboards, policy enforcement, SSO, compliance auditing.
* **Market Sizing (Team Estimates):**
  * Global TAM: $4.2 Billion (AI developer tooling & context optimization).
  * India GCC SAM: $680 Million across 2,100+ GCC facilities employing millions of software engineers.
* **Unit Economics:** <$0.40 COGS per active developer/month, yielding ~88% gross margins due to client-side compute architecture.

---

## Part 11. Empirical Benchmarks & Limitations
* **Empirical Results (8 Tasks across 2 Repositories):**
  * Average token reduction: **97.2%**.
  * File selection recall: **93%**.
  * Interruption save latency: **66 ms median / 85 ms max**.
  * Synchronous git snapshot latency: **~321 ms**.
  * Warm index preparation: **~765 ms**.
  * Secret scrubber precision: **7/7 secrets caught**.
* **Engine Limitations:**
  * Hard power loss or SIGKILL can lose edits since the last 2-minute autosave.
  * Token estimates are locally computed; Google Cloud billing uses `usage_metadata`.
  * Explicit Vertex AI caching requires minimum 2,048-token prefix thresholds.
