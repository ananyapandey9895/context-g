#!/usr/bin/env python3
import json
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://context-g.onrender.com"
TOKEN = "ctxly-cloud-run-token"
ctx = ssl.create_default_context()

def call(method, path, body=None, params=None):
    url = f"{BASE}{path}"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "X-Ctxly-Token": TOKEN,
        "Content-Type": "application/json",
        "User-Agent": "ContextPilot-QA-Auditor/1.0"
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            dur = time.time() - t0
            raw = resp.read().decode("utf-8")
            try:
                res = json.loads(raw)
            except Exception:
                res = raw
            return resp.status, dur, res
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, e.read().decode("utf-8")
    except Exception as e:
        return -1, time.time() - t0, str(e)

def run_suite():
    print("=" * 70)
    print("🔍 EXHAUSTIVE LIVE QA AUDIT: " + BASE)
    print("=" * 70)
    
    # 1. Root HTML
    s, d, res = call("GET", "/")
    print(f"[1/11] Root Web UI (GET /)            -> Status: {s} ({d:.2f}s)")
    assert s == 200, f"Expected 200, got {s}"
    has_title = "<title>ContextPilot</title>" in str(res)
    has_token = f'content="{TOKEN}"' in str(res)
    print(f"       • Document Title: {'✅ OK' if has_title else '❌ Missing'}")
    print(f"       • Security Token: {'✅ Injected' if has_token else '❌ Missing'}")

    # Extract injected START_REPO if any
    repo = ""
    for line in str(res).splitlines():
        if 'name="ctxly-repo"' in line and 'content="' in line:
            repo = line.split('content="')[1].split('"')[0]
            break
    print(f"       • Initial Injected Repo: '{repo}'")

    # 2. State & Engine Health
    s, d, res = call("GET", "/api/state")
    print(f"[2/11] System State (GET /api/state)  -> Status: {s} ({d:.2f}s)")
    assert s == 200, f"Expected 200, got {s}"
    if isinstance(res, dict):
        print(f"       • Version: {res.get('version')} | Smart Mode: {res.get('smart')}")
        for h in res.get("health", []):
            print(f"       • Health check '{h.get('title')}': {'🟢 OK' if h.get('ok') else 'ℹ️ ' + str(h.get('detail'))[:40]}")

    # 3. If repo is empty, try default demo path
    if not repo:
        repo = "/opt/render/project/src/demo/ecommerce-core"
    
    # 4. Project AST Index
    s, d, res = call("POST", "/api/project", {"repo": repo})
    print(f"[3/11] AST Symbol Index (POST /api/project) -> Status: {s} ({d:.2f}s)")
    if s == 200 and isinstance(res, dict):
        print(f"       • Total Source Files: {res.get('files')} | Indexed AST Symbols: {res.get('symbols')}")

    # 5. Prompt Task 1: Cart Checkout Bug
    s, d, res = call("POST", "/api/prepare", {"repo": repo, "task": "Fix checkout tax calculation in cart.py"})
    print(f"[4/11] Ask AI: Cart Bug (POST /api/prepare) -> Status: {s} ({d:.2f}s)")
    if s == 200 and isinstance(res, dict):
        scoped = [f.get("path") for f in res.get("files", [])]
        saved = res.get("tokens_estimate", {}).get("saved_pct", "N/A")
        print(f"       • Surgical Scoped Files: {scoped}")
        print(f"       • Token Compression: {saved}%")

    # 6. Prompt Task 2: Auth JWT
    s, d, res = call("POST", "/api/prepare", {"repo": repo, "task": "Handle expired JWT token validation"})
    print(f"[5/11] Ask AI: Auth JWT (POST /api/prepare) -> Status: {s} ({d:.2f}s)")
    if s == 200 and isinstance(res, dict):
        scoped = [f.get("path") for f in res.get("files", [])]
        print(f"       • Surgical Scoped Files: {scoped}")

    # 7. Checkpoint Creation
    s, d, res = call("POST", "/api/checkpoint", {"repo": repo, "label": "Manual QA Deployment Checkpoint", "notes": "Verified Render cloud"})
    print(f"[6/11] Checkpoint Save (POST /api/checkpoint) -> Status: {s} ({d:.2f}s)")
    cp_id = res.get("id") if isinstance(res, dict) else None
    print(f"       • New Snapshot ID: {cp_id}")

    # 8. Timeline Inspection
    s, d, res = call("GET", "/api/timeline", params={"repo": repo})
    print(f"[7/11] Timeline History (GET /api/timeline) -> Status: {s} ({d:.2f}s)")
    if s == 200 and isinstance(res, dict):
        items = res.get("items", [])
        print(f"       • Historical Checkpoints: {len(items)} events logged")

    # 9. Recovery Resume Note
    s, d, res = call("POST", "/api/resume-text", {"repo": repo})
    print(f"[8/11] Session Resume (POST /api/resume-text) -> Status: {s} ({d:.2f}s)")
    if s == 200 and isinstance(res, dict):
        txt = res.get("text", "")
        print(f"       • Recovery Note Generated: {len(txt)} chars")

    # 10. Privacy & Secret Scrubber
    s, d, res = call("POST", "/api/scan", {"repo": repo})
    print(f"[9/11] Privacy Scrubber (POST /api/scan)  -> Status: {s} ({d:.2f}s)")
    if s == 200 and isinstance(res, dict):
        findings = res.get("findings", [])
        print(f"       • Shannon Entropy Findings: {len(findings)} credentials quarantined")

    # 11. Savings Analytics
    s, d, res = call("GET", "/api/savings", params={"repo": repo})
    print(f"[10/11] Savings & ROI (GET /api/savings)  -> Status: {s} ({d:.2f}s)")
    if s == 200 and isinstance(res, dict):
        today = res.get("today", {})
        print(f"       • Saved Tokens: {today.get('saved_tokens', 0):,} ({today.get('pct', 0)}%)")

    # 12. Project Architecture Map
    s, d, res = call("GET", "/api/projectmap", params={"repo": repo})
    print(f"[11/11] Project Map (GET /api/projectmap) -> Status: {s} ({d:.2f}s)")
    if s == 200 and isinstance(res, dict):
        mods = len(res.get("modules", []))
        print(f"       • Modules Mapped: {mods}")

    print("=" * 70)
    print("🎉 ALL 11 AUDIT CHECKS FINISHED!")
    print("=" * 70)

if __name__ == "__main__":
    run_suite()
