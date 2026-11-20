"""Project memory, recovery summary, graph retrieval, prompt checks, approval gate and metrics."""

import json

from contextpilot import memory
from contextpilot.checkpoint import create_checkpoint, recovery_bundle
from contextpilot.config import load_settings, save_settings
from contextpilot.deps import build_graph, imports_of
from contextpilot.engine import prepare
from contextpilot.metrics import collect
from contextpilot.optimizer import constraints, dedupe_lines
from contextpilot.selector import select_files
from contextpilot.store import Store
from contextpilot.symbols import build_index


def test_memory_dedupes_scrubs_and_recalls(repo):
    with Store(repo) as st:
        a = memory.remember(st, "decision", "We decided to keep JWT expiry in seconds everywhere in auth",
                            files=["src/auth/jwt_handler.py"])
        b = memory.remember(st, "decision", "we decided to keep JWT expiry in seconds everywhere in auth!")
        assert a["created"] and not b["created"] and a["id"] == b["id"]          # exact/near duplicate merged
        memory.remember(st, "fact", "api key is AIza" "SyD3adB33fC0ffee1234567890abcdefXYZ", session_id="s1")
        stored = json.dumps(memory.list_memories(st))
        assert "AIzaSyD3adB33f" not in stored                                       # secrets never stored
        memory.remember(st, "decision", "Cart totals are computed in cents, never floats", files=["src/cart/cart.py"])
        hits = memory.recall(st, "token expiry is wrong in the jwt auth", limit=3)
        assert hits and "JWT expiry" in hits[0]["text"]
        assert all("Cart totals" not in h["text"] for h in hits)                   # unrelated memory not injected
        assert memory.remember(st, "fact", "ok") is None                           # too small to be useful


def test_recovery_summary_has_progress_errors_and_next(repo):
    with Store(repo) as st:
        (repo / "src/auth/jwt_handler.py").write_text("x = 1\n", encoding="utf-8")
        create_checkpoint(repo, st, reason="agent", notes="exp compared in ms", next_steps="convert exp to seconds",
                          code_snapshot="off")
        create_checkpoint(repo, st, reason="agent", next_steps="add a regression test for refresh",
                          code_snapshot="off")
        sid = st.active_session()["id"]
        memory.capture_verification(st, sid, {"ran": True, "passed": False, "summary": "1 failed",
                                              "output_tail": "tests/test_auth.py:4 AssertionError"})
        b = recovery_bundle(repo, st, 260)
        assert "Completed: convert exp to seconds" in b                  # superseded next step = done
        assert "Unresolved errors: Tests failing: 1 failed" in b
        assert "Next: add a regression test for refresh" in b
        assert "Last active file: src/auth/jwt_handler.py" in b
        assert len(b) <= 260 * 4
        tiny = recovery_bundle(repo, st, 40)                            # budget squeeze keeps the essentials
        assert "Next:" in tiny or "Unresolved" in tiny
        memory.capture_verification(st, sid, {"ran": True, "passed": True})
        assert not memory.list_memories(st, kind="error", status="open")  # passing tests resolve it


def test_import_graph_and_test_pairing(repo):
    assert "src.auth.jwt_handler" in imports_of("src/auth/auth_middleware.py",
                                                (repo / "src/auth/auth_middleware.py").read_text(encoding="utf-8"))
    assert imports_of("web/a.ts", "import { x } from './b'\nconst y = require('../c')") == ["../c", "./b"]
    with Store(repo) as st:
        build_index(repo, st)
        g = build_graph(st.all_indexed())
        assert g["src/auth/auth_middleware.py"] == {"src/auth/jwt_handler.py"}
        sel = select_files(repo, st, "verify_token returns false for valid tokens", 2, 4)
        reasons = {c.path: " ".join(c.reasons) for c in sel.files}
        assert "src/auth/jwt_handler.py" in reasons
        assert "tests/test_auth.py" in reasons and "tests jwt_handler" in reasons["tests/test_auth.py"]


def test_constraints_survive_and_duplicates_drop():
    task = "Fix the login timeout. Don't change the public API. Keep it backwards compatible"
    assert constraints(task) == ["Don't change the public API", "Keep it backwards compatible"]
    text, n = dedupe_lines("## A\n- read the auth middleware first\n- read the auth middleware first\n## B")
    assert n == 1 and text.count("auth middleware") == 1


def test_prepare_keeps_constraints_injects_memory_and_logs_metrics(repo):
    with Store(repo) as st:
        memory.remember(st, "decision", "Auth tokens: expiry is always stored in seconds, never milliseconds",
                        files=["src/auth/jwt_handler.py"])
    task = ("people get logged out, fix the jwt auth middleware 401 " + "with lots of extra detail " * 20 +
            ". Do not change the token format.")
    p = prepare(repo, task, use_llm=False)
    text, s = p.compiled.text, p.summary()
    assert "Do not change the token format" in text                   # kept although the objective was cut
    assert "## Project memory" in text and "never milliseconds" in text
    assert s["checks"]["constraints_kept"] == s["checks"]["constraints_total"] >= 1
    assert s["checks"]["files_exist"] and s["retrieval_ms"] >= 0
    with Store(repo) as st:
        m = collect(st)
    assert m["requests"] == 1 and m["retrieval_latency_ms"]["samples"] == 1
    assert m["tokens"]["original_est"] > m["tokens"]["optimized_est"] > 0


def test_external_mode_ask_and_off(repo, monkeypatch):
    called = []
    monkeypatch.delenv("CTXLY_OFFLINE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr("contextpilot.optimizer._gemini_structured", lambda *a, **k: called.append(1) or ({}, {}))
    s = load_settings(repo)
    s.extra["external_mode"] = "ask"
    save_settings(repo, s)
    p = prepare(repo, "fix the jwt auth middleware 401", use_cache=False)
    assert not called and p.needs_approval and p.compiled.source == "local"
    assert "jwt" in p.external_preview and "s3cr3tK3y" not in p.external_preview
    prepare(repo, "fix the jwt auth middleware 401", use_cache=False, approved=True)
    assert called                                                     # sent only after approval
    s.extra["external_mode"] = "off"
    save_settings(repo, s)
    called.clear()
    p = prepare(repo, "fix the jwt auth middleware 401", use_cache=False, approved=True)
    assert not called and not p.needs_approval
