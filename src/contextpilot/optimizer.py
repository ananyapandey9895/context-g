"""Prompt compiler: turns a vague request into a lean, grounded instruction with exact file paths.

Uses Gemini Flash (via google-genai) when credentials are configured; otherwise a deterministic local
compiler produces the same structure. Every result reports which path produced it.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import genai_client, gitctx, tokens
from .scrubber import scrub
from .selector import Selection

SYSTEM_INSTRUCTION = """You are ContextPilot's prompt compiler for an autonomous coding agent.
Rewrite the developer's raw request into a precise, minimal instruction.
Rules:
- Use ONLY the candidate files provided; never invent paths. Keep 2-4 files.
- Be terse. No pleasantries. Imperative steps.
- Include concrete acceptance checks (tests/commands) when inferable.
- Never request access to .env files, keys or credentials.
Return JSON: {"objective": str, "files": [{"path": str, "lines": str, "why": str}],
"steps": [str], "acceptance": [str], "risks": [str]}"""


@dataclass
class Compiled:
    text: str
    source: str  # "gemini:<model>" | "local"
    raw_tokens: int
    compiled_tokens: int
    latency_ms: float
    structured: dict = field(default_factory=dict)
    redactions: int = 0
    error: str | None = None


def _clean_objective(task: str) -> str:
    t = re.sub(r"\s+", " ", task).strip()
    t = re.sub(r"(?i)^(hey|hi|hello|ok|so|please|can you|could you|would you|i need you to|i want you to)[,!\s]+", "", t)
    t = re.sub(r"(?i)^(please|can you|could you)\s+", "", t)
    t = re.sub(r"\?(?=\s|$)", ".", t)  # questions become instructions
    if len(t) > 300:
        t = t[:297] + "…"
    return t[:1].upper() + t[1:]


def _local_structured(task: str, sel: Selection, triage: dict | None) -> dict:
    files = [
        {"path": c.path, "lines": f"{c.lines[0]}-{c.lines[1]}" if c.lines else "",
         "why": "; ".join(c.reasons[:2])}
        for c in sel.files
    ]
    kind = (triage or {}).get("kind", "GENERAL")
    steps = {
        "BUGFIX": ["Reproduce the failure from the description", "Locate the root cause within the scoped lines",
                   "Apply the minimal fix", "Add or update a regression test"],
        "REFACTOR": ["Map current behaviour of the scoped symbols", "Refactor without changing public behaviour",
                     "Keep diffs minimal and run existing tests"],
        "TEST": ["Identify untested paths in scoped files", "Write focused tests", "Run the test suite"],
        "FEATURE": ["Extend the scoped modules following existing patterns", "Wire it up end-to-end",
                    "Add tests for the new behaviour"],
        "DOCS": ["Update documentation in the scoped files only"],
    }.get(kind, ["Inspect the scoped files", "Make the minimal change", "Verify"])
    tests = [c.path for c in sel.files if re.search(r"(^|/)(test_|tests?/|.*\.test\.|.*_test\.)", c.path)]
    acceptance = [f"Tests pass (e.g. run {tests[0]})"] if tests else ["Existing tests pass", "No unrelated files modified"]
    return {"objective": _clean_objective(task), "files": files, "steps": steps, "acceptance": acceptance, "risks": []}


_CONSTRAINT = re.compile(r"(?i)\b(don'?t|do not|never|must|mustn'?t|should(?:n'?t)?|only|without|keep|avoid|make sure|"
                         r"no (?:new|breaking|changes?)|backwards?[- ]compatible|preserve|instead of)\b")


def constraints(task: str) -> list[str]:
    """Sentences of the request that limit HOW the work is done - these must reach the agent verbatim."""
    parts = re.split(r"(?<=[.!?;])\s+|\n+", task or "")
    return [p.strip().rstrip(".") for p in parts if _CONSTRAINT.search(p) and 6 <= len(p.strip()) <= 400][:8]


def _n(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def dedupe_lines(text: str) -> tuple[str, int]:
    """Drop repeated content lines (headings and blank lines are kept)."""
    seen, out, dropped = set(), [], 0
    for line in text.splitlines():
        k = _n(line.lstrip("-*0123456789. "))
        if line.strip() and not line.startswith("#") and len(k) > 12:
            if k in seen:
                dropped += 1
                continue
            seen.add(k)
        out.append(line)
    return "\n".join(out), dropped


def render(structured: dict, root: Path, resume_bundle: str = "", triage: dict | None = None,
           memory_text: str = "", task: str = "") -> str:
    out = ["## Objective", structured.get("objective", "").strip()]
    if triage:
        out[1] += f"  `[{triage.get('label', '')}]`"
    files = structured.get("files") or []
    if files:
        out += ["", "## Scope - read ONLY these files"]
        for f in files:
            loc = f"{f['path']}" + (f" (lines {f['lines']})" if f.get("lines") else "")
            out.append(f"- `{loc}`" + (f" - {f['why']}" if f.get("why") else ""))
    br, hd = gitctx.branch(root), gitctx.head(root)
    changes = gitctx.status(root)
    if br or changes:
        out += ["", "## Repo state"]
        if br:
            out.append(f"- branch `{br}` @ `{hd}`")
        if changes:
            a = sum(c.added for c in changes)
            r = sum(c.removed for c in changes)
            out.append(f"- uncommitted: {len(changes)} file(s), +{a}/-{r}")
    if resume_bundle:
        out += ["", "## Resumed context", resume_bundle]
    if memory_text:
        out += ["", "## Project memory (relevant to this task)", memory_text]
    so_far = _n("\n".join(out + list(structured.get("steps") or []) + list(structured.get("acceptance") or [])))
    missing = [c for c in constraints(task) if _n(c) not in so_far]
    if missing:
        out += ["", "## Constraints (from the request - keep these)"] + [f"- {c}" for c in missing]
    if structured.get("steps"):
        out += ["", "## Steps"] + [f"{i}. {s}" for i, s in enumerate(structured["steps"], 1)]
    if structured.get("acceptance"):
        out += ["", "## Done when"] + [f"- {a}" for a in structured["acceptance"]]
    if structured.get("risks"):
        out += ["", "## Watch out"] + [f"- {r}" for r in structured["risks"]]
    out += ["", "## Rules",
            "- Do not scan the repository; open other files only if strictly required, and say why.",
            "- Never read .env, key, or credential files.",
            "- Before stopping, record progress: `ctxly checkpoint --notes \"…\" --next \"…\"`."]
    return "\n".join(out).strip() + "\n"


def external_payload(task: str, sel: Selection) -> str:
    """Exactly what is sent to Gemini to compile a prompt: the request plus file names and symbol names (no file
    bodies), secret-scrubbed. Shown to the user verbatim when approval is required."""
    cands = [
        {"path": c.path, "lines": f"{c.lines[0]}-{c.lines[1]}" if c.lines else "", "signals": c.reasons[:3],
         "symbols": [s.qualname for s in c.symbols[:6]]}
        for c in sel.files
    ]
    return scrub(f"Raw request:\n{task}\n\nCandidate files (ranked):\n{json.dumps(cands, indent=1)}").text


def _gemini_structured(task: str, sel: Selection, model: str, cache_name: str | None) -> tuple[dict, dict]:
    from google.genai import types

    client = genai_client.get_client()
    if client is None:
        raise RuntimeError("Gemini client not configured")
    user = external_payload(task, sel)
    cfg = dict(response_mime_type="application/json", temperature=0.0, max_output_tokens=800)
    if cache_name:
        cfg["cached_content"] = cache_name  # system instruction lives inside the cache
    else:
        cfg["system_instruction"] = SYSTEM_INSTRUCTION
    resp = client.models.generate_content(model=model, contents=user, config=types.GenerateContentConfig(**cfg))
    data = json.loads(resp.text)
    um = getattr(resp, "usage_metadata", None)
    usage = {
        "prompt_tokens": getattr(um, "prompt_token_count", None),
        "cached_tokens": getattr(um, "cached_content_token_count", None),
        "output_tokens": getattr(um, "candidates_token_count", None),
    }
    valid = {c.path for c in sel.files}
    data["files"] = [f for f in data.get("files", []) if f.get("path") in valid] or _local_structured(task, sel, None)["files"]
    return data, usage


def compile_prompt(task: str, sel: Selection, root: Path, model: str = "gemini-2.5-flash", use_llm: bool = True,
                   resume_bundle: str = "", triage: dict | None = None, cache_name: str | None = None,
                   memory_text: str = "") -> Compiled:
    t0 = time.perf_counter()
    clean = scrub(task)
    raw_tokens = tokens.estimate(task) + sel.total_tokens  # naive: raw prompt + agent reading every file
    structured, source, err, usage = None, "local", None, {}
    if use_llm and genai_client.configured():
        try:
            structured, usage = _gemini_structured(clean.text, sel, model, cache_name)
            source = f"gemini:{model}"
        except Exception as e:  # fall back, but say so
            err = f"{type(e).__name__}: {e}"[:300]
    if structured is None:
        structured = _local_structured(clean.text, sel, triage)
    text = render(structured, root, resume_bundle, triage, memory_text, clean.text)
    text, dupes = dedupe_lines(text)
    final = scrub(text)  # generated context is checked too - memory and diffs must never leak a secret
    text = final.text
    compiled_tokens = tokens.estimate(text) + sel.selected_tokens
    valid = {c.path for c in sel.files}
    cons = constraints(clean.text)
    checks = {
        "files_exist": all((root / f["path"]).exists() for f in structured.get("files") or []),
        "files_from_selection": all(f.get("path") in valid for f in structured.get("files") or []) if valid else True,
        "constraints_total": len(cons),
        "constraints_kept": sum(1 for c in cons if _n(c) in _n(text)),
        "duplicate_lines_removed": dupes,
        "secrets_redacted_in_output": final.redacted,
        "prompt_tokens": tokens.estimate(text),
    }
    c = Compiled(text, source, raw_tokens, compiled_tokens, (time.perf_counter() - t0) * 1000, structured,
                 clean.redacted, err)
    c.structured["_usage"] = usage
    c.structured["_checks"] = checks
    return c
