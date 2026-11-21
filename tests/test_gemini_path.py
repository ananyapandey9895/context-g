"""Exercise the Gemini / Vertex code paths against the real google-genai types with a fake transport."""

import json
from types import SimpleNamespace

from contextpilot import cache, genai_client
from contextpilot.engine import prepare
from contextpilot.store import Store
from contextpilot.symbols import build_index


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        body = {
            "objective": "Fix 401: verify_token compares exp (seconds) against milliseconds",
            "files": [{"path": "src/auth/jwt_handler.py", "lines": "6-9", "why": "exp comparison"},
                      {"path": "src/invented/nope.py", "lines": "", "why": "hallucinated"}],
            "steps": ["Compare exp to time.time()"], "acceptance": ["pytest tests/test_auth.py"], "risks": [],
        }
        um = SimpleNamespace(prompt_token_count=96, cached_content_token_count=2048, candidates_token_count=80)
        return SimpleNamespace(text=json.dumps(body), usage_metadata=um)

    def count_tokens(self, model, contents):
        return SimpleNamespace(total_tokens=42)


class FakeCaches:
    def __init__(self):
        self.created = []

    def create(self, model, config):
        self.created.append((model, config))
        return SimpleNamespace(name="cachedContents/abc123", usage_metadata=SimpleNamespace(total_token_count=5000))


def _fake(monkeypatch):
    client = SimpleNamespace(models=FakeModels(), caches=FakeCaches())
    monkeypatch.delenv("CTXLY_OFFLINE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(genai_client, "get_client", lambda: client)
    return client


def test_gemini_compiler_and_cache(repo, monkeypatch):
    from google.genai import types

    client = _fake(monkeypatch)
    monkeypatch.setattr(cache, "MIN_CACHE_TOKENS", 10)
    p = prepare(repo, "users get 401 after refresh in jwt auth middleware")
    s = p.summary()
    assert s["compiler"] in ("gemini:gemini-3.8-flash", "gemini:gemini-2.5-flash")
    assert "src/invented/nope.py" not in p.compiled.text  # hallucinated paths are dropped
    assert "src/auth/jwt_handler.py" in p.compiled.text
    assert s["billed_usage"]["cached_tokens"] == 2048
    assert s["cache"]["status"] == "created"
    model, cfg = client.caches.created[0]
    assert isinstance(cfg, types.CreateCachedContentConfig) and cfg.ttl == "3600s"
    _, _, gen_cfg = client.models.calls[0]
    assert isinstance(gen_cfg, types.GenerateContentConfig)
    assert gen_cfg.cached_content == "cachedContents/abc123" and gen_cfg.system_instruction is None

    # Second run with unchanged code reuses the cache (prefix hash is stable).
    p2 = prepare(repo, "users get 401 after refresh in jwt auth middleware")
    assert p2.summary()["cache"]["status"] == "reused"
    assert len(client.caches.created) == 1


def test_prefix_is_deterministic_and_secret_free(repo):
    with Store(repo) as st:
        build_index(repo, st)
        a, b = cache.build_prefix(repo, st), cache.build_prefix(repo, st)
    assert a == b
    assert "sk_live" not in a and "s3cr3t" not in a and ".env" not in a.split()


def test_gemini_failure_falls_back_to_local(repo, monkeypatch):
    client = _fake(monkeypatch)

    def boom(**kw):
        raise RuntimeError("quota")

    client.models.generate_content = boom
    p = prepare(repo, "fix jwt auth middleware 401")
    s = p.summary()
    assert s["compiler"] == "local" and "quota" in s["compiler_error"]
