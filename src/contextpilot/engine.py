"""End-to-end pipeline shared by the CLI, MCP server, Agent Skill scripts and Gemini CLI hooks.

task -> privacy scrub -> local triage -> incremental index -> surgical selection -> cache prefix
     -> Gemini Flash compile -> telemetry -> checkpoint
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import genai_client, gitctx
from .cache import CacheResult, ensure_cache
from .checkpoint import create_checkpoint, recovery_bundle
from .config import Settings, ctxly_dir, load_settings
from .optimizer import Compiled, compile_prompt
from .scrubber import scrub
from .selector import Selection, select_files, triage
from .store import Store
from .symbols import IndexStats, build_index


@dataclass
class Prepared:
    session_id: str
    task: str
    triage: dict
    index: IndexStats
    selection: Selection
    compiled: Compiled
    cache: CacheResult | None
    prompt_path: Path
    retrieval_ms: float = 0.0
    memories: list | None = None
    needs_approval: bool = False
    external_preview: str = ""

    def summary(self) -> dict:
        c = self.compiled
        saved = 1 - (c.compiled_tokens / c.raw_tokens) if c.raw_tokens else 0
        return {
            "session_id": self.session_id,
            "triage": self.triage,
            "files": [{"path": f.path, "lines": list(f.lines) if f.lines else None, "score": round(f.score, 1),
                       "reasons": f.reasons[:3]} for f in self.selection.files],
            "considered_files": self.selection.considered,
            "tokens_estimate": {"naive": c.raw_tokens, "optimized": c.compiled_tokens,
                                "reduction_pct": round(100 * saved, 1)},
            "compiler": c.source,
            "compiler_error": c.error,
            "latency_ms": round(c.latency_ms, 1),
            "redactions": c.redactions,
            "cache": None if self.cache is None else {"status": self.cache.status, "name": self.cache.cache_name,
                                                      "prefix_tokens": self.cache.token_count,
                                                      "message": self.cache.message},
            "billed_usage": c.structured.get("_usage") or None,
            "checks": c.structured.get("_checks") or {},
            "retrieval_ms": round(self.retrieval_ms, 1),
            "memories": [{"id": m["id"], "kind": m["kind"], "text": m["text"]} for m in (self.memories or [])],
            "needs_approval": self.needs_approval,
            "external_preview": self.external_preview or None,
            "prompt_path": str(self.prompt_path),
        }


def prepare(root: Path, task: str, use_llm: bool = True, use_cache: bool = True, tool: str | None = None,
            settings: Settings | None = None, store: Store | None = None, new_session: bool = False,
            approved: bool = False) -> Prepared:
    import time

    from .external import decide

    s = settings or load_settings(root)
    wants_llm = use_llm and genai_client.configured()
    use_llm, needs_approval = decide(s, wants_llm, approved)
    own = store is None
    store = store or Store(root)
    try:
        scrubbed = scrub(task)
        clean_task = scrubbed.text
        tri = triage(clean_task, s.gemma_model, s.ollama_url)
        idx = build_index(root, store)
        head, br = gitctx.head(root), gitctx.branch(root)
        if new_session:
            sid = store.new_session(clean_task, tool, head, br)
        else:
            sid = store.ensure_session(clean_task, tool, head, br)
        prev = store.latest_checkpoint(sid)
        touched = json.loads(prev["touched_files"]) if prev is not None and prev["touched_files"] else []
        t_ret = time.perf_counter()
        sel = select_files(root, store, clean_task, s.min_files, s.max_files, touched=touched)
        from .memory import recall, render as render_memory

        mems = recall(store, clean_task, [f.path for f in sel.files], sid, limit=5)
        retrieval_ms = (time.perf_counter() - t_ret) * 1000
        store.log("retrieve", "local", sid, latency_ms=retrieval_ms,
                  detail={"files": len(sel.files), "considered": sel.considered, "memories": len(mems)})
        cres = ensure_cache(root, store, s.cache_model, s.cache_ttl_seconds, s.extra.get("cache_min_tokens")) if (use_cache and use_llm) else None
        if cres is not None:
            store.log("cache_hit" if cres.status == "reused" else "cache_miss", cres.status, sid,
                      cached_tokens=cres.token_count, detail={"reason": cres.message[:200]})
        cache_name = cres.cache_name if cres and cres.cache_name and s.cache_model == s.optimizer_model else None
        bundle = recovery_bundle(root, store, s.recovery_bundle_tokens, sid) if prev is not None else ""
        mem_text = render_memory(mems, exclude=bundle)
        comp = compile_prompt(clean_task, sel, root, s.optimizer_model, use_llm, bundle, tri, cache_name, mem_text)
        preview = ""
        if needs_approval:
            from .optimizer import external_payload

            preview = external_payload(clean_task, sel)
        comp.redactions += scrubbed.redacted
        prompt_path = ctxly_dir(root) / "last_prompt.md"
        prompt_path.write_text(comp.text, encoding="utf-8")
        usage = comp.structured.get("_usage") or {}
        store.log("optimize", "estimate", sid, comp.raw_tokens, comp.compiled_tokens, None, comp.latency_ms,
                  {"compiler": comp.source, "files": [f.path for f in sel.files], "triage": tri["label"],
                   "checks": comp.structured.get("_checks")})
        if usage.get("prompt_tokens") is not None:
            import time

            from .pricing import load_prices

            pin, pcached, pout = (usage.get("prompt_tokens") or 0, usage.get("cached_tokens") or 0,
                                  usage.get("output_tokens") or 0)
            store.conn.execute(
                "INSERT INTO usage(session_id, created_at, source, model, input_tokens, cached_tokens, output_tokens,"
                " cost_usd) VALUES(?,?,?,?,?,?,?,?)",
                (sid, time.time(), "contextpilot-compiler", s.optimizer_model, pin, pcached, pout,
                 load_prices(s).cost(s.optimizer_model, pin, pcached, pout)))
            store.log("optimize_billed", genai_client.backend(), sid,
                      None, usage.get("prompt_tokens"), usage.get("cached_tokens"), comp.latency_ms, usage)
        from .learning import record_suggestion

        record_suggestion(store, [f.path for f in sel.files])
        # This checkpoint's code snapshot is the "undo point" for whatever the AI does next.
        create_checkpoint(root, store, reason="optimize", selected_files=[f.path for f in sel.files], session_id=sid)
        return Prepared(sid, clean_task, tri, idx, sel, comp, cres, prompt_path, retrieval_ms, mems,
                        needs_approval, preview)
    finally:
        if own:
            store.close()
