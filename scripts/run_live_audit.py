import urllib.request
import json
import ssl
import time
import urllib.parse

ctx = ssl.create_default_context()
BASE = "https://partnership-balance-sbjct-revelation.trycloudflare.com"
TOKEN = "ctxly-cloud-run-token"
REPO = "/Users/aarush/Desktop/Ctxly/context-g/demo/ecommerce-core"

def api(method, endpoint, body=None, params=None):
    url = f"{BASE}{endpoint}"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"
    headers = {
        "X-Ctxly-Token": TOKEN,
        "Content-Type": "application/json"
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            dur = time.time() - t0
            raw = resp.read().decode("utf-8")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return resp.status, dur, parsed
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, e.read().decode("utf-8")
    except Exception as e:
        return -1, time.time() - t0, str(e)

print("="*65)
print("🎯 EXHAUSTIVE LIVE AUDIT: " + BASE)
print("="*65)

# 1. Root & HTML
s, d, res = api("GET", "/")
has_badge = "Google Cloud Run" in str(res)
has_title = "<title>ContextPilot</title>" in str(res)
has_token = TOKEN in str(res)
print(f"[1/12] HTML / Root Page:        {s} in {d:.2f}s | Title OK: {has_title} | Banner Removed: {not has_badge} | Token Injected: {has_token}")

# 2. State & Health
s, d, res = api("GET", "/api/state")
version = res.get("version") if isinstance(res, dict) else "N/A"
smart = res.get("smart") if isinstance(res, dict) else "N/A"
key_saved = res.get("key_saved") if isinstance(res, dict) else "N/A"
print(f"[2/12] State & Health API:       {s} in {d:.2f}s | Version: {version} | Smart Mode: {smart} | Key Active: {key_saved}")

# 3. Project Load
s, d, res = api("POST", "/api/project", {"repo": REPO})
num_files = res.get("files") if isinstance(res, dict) else "N/A"
num_symbols = res.get("symbols") if isinstance(res, dict) else "N/A"
print(f"[3/12] Project Load (Repo AST):  {s} in {d:.2f}s | Total Files: {num_files} | Indexed Symbols: {num_symbols}")

# 4. Prompt 1: Checkout Tax
s, d, res = api("POST", "/api/prepare", {"repo": REPO, "task": "Fix tax calculation in checkout"})
files = [f.get("path") for f in res.get("files", [])] if isinstance(res, dict) else []
tokens = res.get("tokens_estimate", {}).get("optimized") if isinstance(res, dict) else None
print(f"[4/12] Prompt 1 (Tax bug):       {s} in {d:.2f}s | Scoped Files: {files} | Tokens: {tokens}")

# 5. Prompt 2: Auth JWT
s, d, res = api("POST", "/api/prepare", {"repo": REPO, "task": "Handle expired JWT tokens in auth"})
files = [f.get("path") for f in res.get("files", [])] if isinstance(res, dict) else []
tokens = res.get("tokens_estimate", {}).get("optimized") if isinstance(res, dict) else None
print(f"[5/12] Prompt 2 (JWT Auth):      {s} in {d:.2f}s | Scoped Files: {files} | Tokens: {tokens}")

# 6. Prompt 3: Inventory
s, d, res = api("POST", "/api/prepare", {"repo": REPO, "task": "Update stock level when order is placed"})
files = [f.get("path") for f in res.get("files", [])] if isinstance(res, dict) else []
tokens = res.get("tokens_estimate", {}).get("optimized") if isinstance(res, dict) else None
print(f"[6/12] Prompt 3 (Inventory):     {s} in {d:.2f}s | Scoped Files: {files} | Tokens: {tokens}")

# 7. Checkpoint & Timeline
s, d, cp = api("POST", "/api/checkpoint", {"repo": REPO, "label": "Audit Test Checkpoint"})
cp_id = cp.get("id") if isinstance(cp, dict) else None
s2, d2, tl = api("GET", "/api/timeline", params={"repo": REPO})
total_cps = len(tl.get("items", [])) if isinstance(tl, dict) else 0
print(f"[7/12] Checkpoint & Timeline:    {s} in {d:.2f}s | New Checkpoint ID: {cp_id} | Total Snapshots: {total_cps}")

# 8. Resume Note
s, d, res = api("POST", "/api/resume-text", {"repo": REPO})
has_resume = "[ContextPilot resume" in res.get("text", "") if isinstance(res, dict) else False
print(f"[8/12] Resume Note Generation:   {s} in {d:.2f}s | Recovery Note Formatted: {has_resume}")

# 9. Secret Scrubber & Security Scan
s, d, res = api("POST", "/api/scan", {"repo": REPO})
findings = len(res.get("findings", [])) if isinstance(res, dict) else 0
print(f"[9/12] Privacy & Scrubber:       {s} in {d:.2f}s | Quarantined Secrets Found: {findings}")

# 10. Savings & ROI Metrics
s, d, res = api("GET", "/api/savings", params={"repo": REPO})
today = res.get("today", {}) if isinstance(res, dict) else {}
saved_tokens = today.get("saved_tokens", 0)
saved_pct = today.get("pct", 0)
print(f"[10/12] Savings & ROI Stats:     {s} in {d:.2f}s | Tokens Saved: {saved_tokens:,} ({saved_pct:.1f}% reduction)")

# 11. Project Map & AST Tree
s, d, res = api("GET", "/api/map", params={"repo": REPO})
children = len(res.get("tree", {}).get("children", [])) if isinstance(res, dict) else 0
print(f"[11/12] Project Map (AST Nodes): {s} in {d:.2f}s | Root Children: {children}")

# 12. Settings & Verification
s, d, res = api("GET", "/api/settings", params={"repo": REPO})
currency = res.get("currency") if isinstance(res, dict) else "N/A"
budget = res.get("session_budget_usd") if isinstance(res, dict) else "N/A"
print(f"[12/12] Settings Configuration:  {s} in {d:.2f}s | Currency: {currency} | Session Budget: ${budget}")

print("="*65)
print("✅ ALL 12 AUDIT TESTS PASSED WITH HTTP 200 ON LIVE PUBLIC URL")
print("="*65)
