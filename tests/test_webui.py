import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from contextpilot import webui


@pytest.fixture()
def server(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CTXLY_HOME", str(tmp_path / "home"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler("tok", 0))
    port = srv.server_address[1]
    srv.RequestHandlerClass = webui.make_handler("tok", port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", repo
    srv.shutdown()


def call(base, path, body=None, token="tok", host=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Ctxly-Token": token, "Content-Type": "application/json"})
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req) as r:
            ct = r.headers.get("Content-Type", "")
            data = r.read()
            return r.status, (json.loads(data) if "json" in ct else data.decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_page_has_token_and_security(server):
    base, _ = server
    code, html = call(base, "/")
    assert code == 200 and 'content="tok"' in html and "ContextPilot" in html
    assert call(base, "/api/state", token="wrong")[0] == 403          # other websites can't call the API
    assert call(base, "/", host="evil.example:80")[0] == 403          # DNS-rebinding blocked


def test_full_layman_flow(server):
    base, repo = server
    code, r = call(base, "/api/project", {"repo": str(repo)})
    assert code == 200 and r["files"] >= 30
    code, r = call(base, "/api/prepare", {"repo": str(repo), "task": "people get logged out, fix the jwt auth middleware 401", "smart": False})
    assert code == 200
    assert any(f["path"] == "src/auth/auth_middleware.py" for f in r["files"])
    assert all(f["friendly"] for f in r["files"])
    code, r = call(base, "/api/checkpoint", {"repo": str(repo), "notes": "expiry units wrong", "next": "fix verify_token"})
    assert code == 200 and r["elapsed_ms"] < 5000
    code, st = call(base, "/api/state?repo=" + str(repo))
    assert st["resume"]["checkpoint"]["next_steps"] == "fix verify_token"
    assert st["recent"] == [str(repo)]
    code, r = call(base, "/api/scan", {"repo": str(repo)})
    assert ".env" in r["protected"] and any(f["file"] == "src/auth/jwt_handler.py" for f in r["findings"])
    code, r = call(base, "/api/install", {"repo": str(repo), "target": "all"})
    assert code == 200 and r["changed"]
    code, st = call(base, "/api/state?repo=" + str(repo))
    assert st["installed"] == {"antigravity": True, "gemini": True}
    code, r = call(base, "/api/resume-text", {"repo": str(repo)})
    assert "fix verify_token" in r["text"]


def test_friendly_errors(server):
    base, _ = server
    code, body = call(base, "/api/prepare", {"repo": "Z:/does/not/exist", "task": "x y z"})
    assert code == 400 and "doesn't exist" in body
    code, body = call(base, "/api/prepare", {"repo": ""})
    assert code == 400 and "project folder" in body


def test_apikey_never_echoed(server, monkeypatch):
    base, _ = server
    monkeypatch.setattr("contextpilot.tokens.count_exact", lambda *a, **k: None)
    code, r = call(base, "/api/apikey", {"key": "AIzaFAKEFAKEFAKE", "remember": False})
    assert code == 200 and "AIzaFAKE" not in json.dumps(r)
    code, st = call(base, "/api/state")
    assert "AIzaFAKE" not in json.dumps(st)
    call(base, "/api/apikey", {"key": ""})


def test_v2_endpoints(server):
    from urllib.parse import quote

    base, repo = server
    q = "?repo=" + quote(str(repo))
    call(base, "/api/project", {"repo": str(repo)})
    code, r = call(base, "/api/prepare", {"repo": str(repo), "task": "fix verify_token in jwt handler", "smart": False})
    assert code == 200 and r["money"]["saved"].startswith("$")
    jwt = repo / "src/auth/jwt_handler.py"
    jwt.write_text(jwt.read_text(encoding="utf-8") + "\n# tweak\n", encoding="utf-8")
    code, e = call(base, "/api/explain", {"repo": str(repo), "smart": False})
    assert code == 200 and e.get("changed") and e["totals"]["files"] == 1, e
    code, t = call(base, "/api/timeline" + q)
    assert code == 200 and t["items"] and t["items"][0]["can_restore"]
    code, u = call(base, "/api/undo", {"repo": str(repo)})
    assert code == 200 and "# tweak" not in jwt.read_text(encoding="utf-8")
    # the undo itself can be undone from the Timeline
    items = call(base, "/api/timeline" + q)[1]["items"]
    safety = next(i for i in items if i["reason"] == "before-restore")
    assert safety["can_restore"]
    call(base, "/api/undo", {"repo": str(repo), "checkpoint_id": safety["id"]})
    assert "# tweak" in jwt.read_text(encoding="utf-8")
    call(base, "/api/undo", {"repo": str(repo)})
    code, s = call(base, "/api/settings", {"repo": str(repo), "currency": "INR", "session_budget_usd": "2"})
    assert code == 200
    code, s = call(base, "/api/savings" + q)
    assert s["all"]["saved"].startswith("₹") and len(s["series"]) == 14
    code, g = call(base, "/api/settings" + q)
    assert g["currency"] == "INR" and g["session_budget_usd"] == 2.0
    assert call(base, "/api/settings", {"repo": str(repo), "webhook_url": "https://evil.com/x"})[0] == 400
    code, m = call(base, "/api/map" + q)
    assert code == 200 and m["stats"]["files"] >= 30
    code, sh = call(base, "/api/team/share", {"repo": str(repo), "name": "Asha"})
    assert code == 200 and call(base, "/api/team" + q)[1]["items"][0]["member"] == "Asha"
    code, html = call(base, "/report" + q + "&t=tok")
    assert code == 200 and "savings report" in html
    assert call(base, "/report" + q + "&t=wrong")[0] == 403
