"""v0.2 features: verify loop, money, snapshots/undo/timeline, explain, learning, team, map, vision, budget, notify."""

import base64
import io
import json
import sys
from types import SimpleNamespace

import pytest

from contextpilot import genai_client, verify
from contextpilot.budget import find_usage, record
from contextpilot.checkpoint import create_checkpoint
from contextpilot.config import load_settings, save_settings
from contextpilot.engine import prepare
from contextpilot.explain import explain_changes, undo_point
from contextpilot.hooks import run_hook
from contextpilot.learning import boosts, learn_from_session
from contextpilot.notify import report_text, validate
from contextpilot.pricing import load_prices, savings_summary
from contextpilot.projectmap import build_map
from contextpilot.selector import keywords, select_files
from contextpilot.snapshots import restore, take
from contextpilot.store import Store
from contextpilot.symbols import build_index
from contextpilot.team import bundle_lines, feed, share

JWT = "src/auth/jwt_handler.py"


# ---------------------------------------------------------------- snapshots / undo / explain
def test_snapshot_restore_roundtrip(repo):
    (repo / ".gitignore").write_text(".ctxly/\n.env\n", encoding="utf-8")  # ignored paths must not break snapshots
    ref = take(repo, "before")
    from contextpilot.snapshots import _files_in
    files = _files_in(repo, ref)
    assert JWT in files and ".env" not in files and not any(f.startswith(".ctxly") for f in files)
    (repo / JWT).write_text("BROKEN\n", encoding="utf-8")
    (repo / "src/auth/new_file.py").write_text("x = 1\n", encoding="utf-8")
    r = restore(repo, ref)
    assert "def verify_token" in (repo / JWT).read_text(encoding="utf-8")
    assert not (repo / "src/auth/new_file.py").exists()
    assert "src/auth/new_file.py" in r["removed"]
    assert (repo / ".env").exists()  # sensitive files are never touched
    # the safety snapshot lets you undo the undo
    restore(repo, r["safety_ref"])
    assert (repo / JWT).read_text(encoding="utf-8") == "BROKEN\n"


def test_restore_keeps_team_notes_and_ignores_junk(repo):
    ref = take(repo, "before")
    (repo / ".ctxly-team").mkdir()
    (repo / ".ctxly-team/asha.json").write_text("{}", encoding="utf-8")
    (repo / "src/__pycache__").mkdir()
    (repo / "src/__pycache__/x.pyc").write_bytes(b"\0")
    (repo / "node_modules/pkg").mkdir(parents=True)
    (repo / "node_modules/pkg/index.js").write_text("x", encoding="utf-8")
    (repo / "src/scratch.py").write_text("y = 2\n", encoding="utf-8")
    from contextpilot.snapshots import diff_since
    changed = {f["path"] for f in diff_since(repo, ref)["files"]}
    assert changed == {"src/scratch.py"}
    r = restore(repo, ref)
    assert r["removed"] == ["src/scratch.py"]
    assert (repo / ".ctxly-team/asha.json").exists() and (repo / "node_modules/pkg/index.js").exists()


def test_undo_point_and_plain_english_explanation(repo):
    prepare(repo, "fix the 401 in jwt auth middleware", use_llm=False)
    p = repo / JWT
    p.write_text(p.read_text(encoding="utf-8").replace("time.time() * 1000", "time.time()"), encoding="utf-8")
    with Store(repo) as st:
        assert undo_point(st)["snapshot_ref"]
        e = explain_changes(repo, st, use_llm=False)
    assert e["changed"] and e["source"] == "local"
    assert any("jwt_handler.py" in b and "verify_token" in b for b in e["bullets"])
    assert e["totals"]["files"] == 1


# ---------------------------------------------------------------- verification loop
def test_verify_detects_failure_and_builds_fix_prompt(repo):
    prepare(repo, "fix verify_token in jwt handler", use_llm=False)
    (repo / JWT).write_text("def verify_token(t):\n    raise ValueError('boom')\n", encoding="utf-8")
    with Store(repo) as st:
        assert verify.detect_command(repo)[1:3] == ["-m", "pytest"]
        r = verify.run(repo, st)
        assert r["ran"] and r["passed"] is False
        assert "failed" in r["summary"]
        fp = verify.fix_prompt(repo, st, r)
    assert "boom" in fp and JWT in fp.replace("\\", "/")
    with Store(repo) as st:
        assert verify.history(st)[0]["passed"] == 0


def test_verify_pass(repo):
    with Store(repo) as st:
        r = verify.run(repo, st)
    assert r["passed"] is True and "passed" in r["summary"]


# ---------------------------------------------------------------- money
def test_prices_and_savings(repo):
    s = load_settings(repo)
    p = load_prices(s)
    assert p.cost("gemini-2.5-flash-lite", 1_000_000) == pytest.approx(0.10)
    assert p.cost("gemini-2.5-pro", 1_000_000, cached_tokens=1_000_000) == pytest.approx(0.125)
    prepare(repo, "fix the 401 in jwt auth middleware", use_llm=False)
    with Store(repo) as st:
        sv = savings_summary(st, p, 7)
    assert sv["requests"] == 1 and sv["saved_usd"] > 0 and sv["saved"].startswith("$")
    s.extra["currency"] = "INR"
    save_settings(repo, s)
    assert load_prices(load_settings(repo)).fmt(1) == "₹88.00"


# ---------------------------------------------------------------- learning
def test_learning_boosts_files_you_actually_edit(repo):
    with Store(repo) as st:
        build_index(repo, st)
        sid = st.new_session("speed up the checkout discount")
        (repo / "src/cart/pricing.py").write_text("def apply_discount(total, code):\n    return total * 0.9\n", encoding="utf-8")
        create_checkpoint(repo, st, session_id=sid)
        assert learn_from_session(st, sid) == 0  # already learned during the checkpoint (idempotent)
        b = boosts(st, keywords("checkout discount slow"))
        assert "src/cart/pricing.py" in b
        sel = select_files(repo, st, "checkout discount is slow")
        assert sel.files[0].path == "src/cart/pricing.py"
        assert any("similar requests" in r for r in sel.files[0].reasons)


# ---------------------------------------------------------------- team
def test_team_memory(repo):
    with Store(repo) as st:
        sid = st.new_session("Fix 401 with password=hunter2hunter2")
        create_checkpoint(repo, st, notes="exp in ms", next_steps="patch it", session_id=sid)
        path = share(repo, st, "Asha")
        share(repo, st, "Ravi")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["member"] == "Asha" and "hunter2" not in json.dumps(data)
    assert {e["member"] for e in feed(repo)} == {"Asha", "Ravi"}
    assert any("patch it" in l for l in bundle_lines(repo))


# ---------------------------------------------------------------- map
def test_project_map(repo):
    with Store(repo) as st:
        build_index(repo, st)
        m = build_map(repo, st)
    src = next(c for c in m["tree"]["children"] if c["name"] == "src")
    auth = next(c for c in src["children"] if c["name"] == "auth")
    assert auth["about"].startswith("Sign-in")
    mw = next(c for c in auth["children"] if c["name"] == "auth_middleware.py")
    assert mw["symbols"][0]["name"] == "AuthMiddleware"
    assert m["stats"]["files"] >= 30


# ---------------------------------------------------------------- budget + hooks
def test_usage_parsing_and_budget_stop(repo, monkeypatch, capsys):
    payload = {"session_id": "g1", "cwd": str(repo), "llm_response": {"model": "gemini-2.5-pro",
               "usageMetadata": {"promptTokenCount": 200_000, "cachedContentTokenCount": 150_000, "candidatesTokenCount": 1000}}}
    assert find_usage(payload)["promptTokenCount"] == 200_000
    s = load_settings(repo)
    s.extra["session_budget_usd"] = 0.05
    save_settings(repo, s)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    run_hook("after-model")
    out = json.loads(capsys.readouterr().out)
    assert "saved before compaction" in out["systemMessage"]  # 200k > 135k ceiling
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    run_hook("before-model")
    out = json.loads(capsys.readouterr().out)
    assert out["continue"] is False and "budget reached" in out["stopReason"]


# ---------------------------------------------------------------- vision (fake Gemini)
def test_screenshot_to_request(repo, monkeypatch):
    from contextpilot import vision

    class Models:
        def generate_content(self, model, contents, config):
            return SimpleNamespace(text=json.dumps({
                "error_text": "TypeError in src/cart/pricing.py line 2, key sk_" "live_abcdefghijklmnop123456",
                "files": ["src/cart/pricing.py"], "summary": "Discount crashes",
                "suggested_request": "Fix the TypeError in apply_discount"}))

    monkeypatch.setattr(genai_client, "get_client", lambda: SimpleNamespace(models=Models()))
    img = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()
    r = vision.read_error_image(img)
    assert r["task"].startswith("Fix the TypeError") and "sk_live" not in r["task"]
    with pytest.raises(ValueError):
        vision.read_error_image("data:text/html;base64,PGh0bWw+")


# ---------------------------------------------------------------- notify
def test_webhook_validation_and_text():
    assert validate("https://hooks.slack.com/services/T/B/x")
    assert validate("https://chat.googleapis.com/v1/spaces/x/messages?key=k")
    for bad in ("http://hooks.slack.com/x", "https://evil.com/hooks.slack.com", "https://hooks.slack.com.evil.com/x"):
        with pytest.raises(ValueError):
            validate(bad)
    wk = {"requests": 3, "saved": "$1.20", "billed": "$0.01", "billed_calls": 2, "billed_cache_saved_usd": 0}
    t = report_text("shop", wk, {"saved": "$9.00"}, [{"passed": 1}, {"passed": 0}], [{"member": "Asha", "task": "x"}])
    assert "$1.20" in t and "1 passed, 1 failed" in t and "Asha" in t
