import json
import time

from contextpilot.checkpoint import create_checkpoint, full_resume, recovery_bundle, resume_json
from contextpilot.engine import prepare
from contextpilot.scrubber import is_sensitive_path, scrub
from contextpilot.selector import heuristic_triage, keywords, select_files
from contextpilot.store import Store
from contextpilot.symbols import build_index, list_files, parse_python, parse_regex


# ---------------------------------------------------------------- privacy
def test_scrub_known_secrets():
    text = (
        # fake keys are split so code scanners (e.g. GitHub push protection) don't flag them
        'api_key = "AIza' 'SyA1234567890abcdefghijklmnopqrstuv"\n'
        "aws = AKIAIOSFODNN7EXAMPLE\n"
        "STRIPE=sk_" "live_abcdefghijklmnop123456\n"
        "password: hunter2hunter2\n"
        "url = postgres://admin:pa55w0rd@db.local:5432/x\n"
    )
    r = scrub(text)
    assert "AIzaSy" not in r.text
    assert "AKIAIOSFODNN7EXAMPLE" not in r.text
    assert "sk_live_" not in r.text
    assert "hunter2hunter2" not in r.text
    assert "pa55w0rd" not in r.text
    assert r.redacted >= 5


def test_scrub_entropy_and_no_false_positives():
    r = scrub('SECRET = "s3cr3tK3yValue_9f8A7b6C5d4E3f2G1h"')
    assert "s3cr3tK3y" not in r.text
    benign = 'token = request.headers.get("Authorization")\ncommit = "9fceb02d0ae598e95dc970b74767f19372d61af8"\n'
    assert scrub(benign).redacted == 0


def test_sensitive_paths():
    for p in [".env", "config/.env.production", "id_rsa", "keys/server.pem", "service-account-prod.json", ".ssh/config"]:
        assert is_sensitive_path(p), p
    for p in [".env.example", "src/env.py", "README.md"]:
        assert not is_sensitive_path(p), p


# ---------------------------------------------------------------- indexing
def test_index_excludes_env_and_is_incremental(repo):
    with Store(repo) as st:
        s1 = build_index(repo, st)
        assert ".env" not in list_files(repo)
        assert s1.parsed == s1.files
        s2 = build_index(repo, st)
        assert s2.parsed == 0 and s2.reused == s1.files


def test_symbol_parsers():
    syms = parse_python("class A:\n    def m(self):\n        pass\n\ndef f():\n    pass\n")
    assert [(s.qualname, s.kind) for s in syms] == [("A", "class"), ("A.m", "method"), ("f", "function")]
    ts = parse_regex("export function renderCheckout() {}\nexport class CheckoutView {}\n")
    assert {s.name for s in ts} == {"renderCheckout", "CheckoutView"}


# ---------------------------------------------------------------- selection
def test_keywords():
    k = keywords("Fix the 401 in JWT auth middleware")
    assert {"jwt", "auth", "middleware", "401"} <= k


def test_surgical_selection_picks_auth_files(repo):
    with Store(repo) as st:
        build_index(repo, st)
        sel = select_files(repo, st, "Refactor JWT auth middleware, users get 401 after token refresh", 2, 4)
    paths = [c.path for c in sel.files]
    assert 2 <= len(paths) <= 4
    assert "src/auth/auth_middleware.py" in paths
    assert "src/auth/jwt_handler.py" in paths
    assert not any(p.startswith(("src/cart", "src/orders", "src/payments")) for p in paths)


def test_traceback_mention_wins(repo):
    task = 'Traceback:\n  File "src/cart/pricing.py", line 2, in apply_discount\nTypeError'
    with Store(repo) as st:
        build_index(repo, st)
        sel = select_files(repo, st, task)
    assert sel.files[0].path == "src/cart/pricing.py"


def test_triage():
    assert heuristic_triage("Fix the 401 bug in auth")["label"] == "HIGH_PRIORITY_BUGFIX"
    assert heuristic_triage("refactor cart module")["kind"] == "REFACTOR"


# ---------------------------------------------------------------- checkpoints
def test_checkpoint_fast_and_scrubbed(repo):
    (repo / "src/auth/jwt_handler.py").write_text(
        "import time\nAPI_TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'\n", encoding="utf-8")
    with Store(repo) as st:
        t0 = time.perf_counter()
        r = create_checkpoint(repo, st, reason="interrupt:SIGINT", notes="exp compared in ms vs s",
                              next_steps="fix comparison in verify_token")
        assert (time.perf_counter() - t0) * 1000 < 2000  # generous for CI; typical is well under 120ms
        assert "src/auth/jwt_handler.py" in r["touched_files"]
        ck = st.latest_checkpoint()
        assert "ghp_" not in ck["diff"]
        assert ".env" not in ck["diff"]


def test_resume_bundle_small_and_useful(repo):
    with Store(repo) as st:
        sid = st.new_session("Fix 401 in JWT middleware")
        create_checkpoint(repo, st, notes="exp compared in ms vs s", next_steps="patch verify_token",
                          selected_files=["src/auth/jwt_handler.py"], session_id=sid)
        b = recovery_bundle(repo, st, 180)
        assert len(b) <= 180 * 4
        assert "patch verify_token" in b and "jwt_handler.py" in b
        full = full_resume(repo, st)
        assert "Next:" in full
        j = resume_json(repo, st)
        assert j["found"] and j["checkpoint"]["next_steps"] == "patch verify_token"


def test_store_is_wal(repo):
    with Store(repo) as st:
        assert st.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


# ---------------------------------------------------------------- pipeline
def test_prepare_offline_pipeline(repo):
    p = prepare(repo, "hey can you fix the 401 in the JWT auth middleware? token is AIza" "SyA1234567890abcdefghijklmnopqrstuv")
    s = p.summary()
    assert s["compiler"] == "local"
    assert s["redactions"] == 1
    assert "AIzaSy" not in p.compiled.text
    assert "src/auth/auth_middleware.py" in p.compiled.text
    assert s["tokens_estimate"]["optimized"] < s["tokens_estimate"]["naive"]
    assert p.prompt_path.exists()
    with Store(repo) as st:
        ck = st.latest_checkpoint()
        assert json.loads(ck["selected_files"])
