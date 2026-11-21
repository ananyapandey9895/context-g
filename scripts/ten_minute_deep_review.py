import urllib.request
import urllib.parse
import json
import ssl
import time
import sys
import subprocess
import os

ctx = ssl.create_default_context()
BASE = "https://partnership-balance-sbjct-revelation.trycloudflare.com"
TOKEN = "ctxly-cloud-run-token"
REPO = "/Users/aarush/Desktop/Ctxly/context-g/demo/ecommerce-core"

TASKS = [
    ("Minute 01 - Authentication", "Fix expired JWT refresh token bug in auth handler"),
    ("Minute 02 - Cart Checkout", "Calculate tiered volume discount on multi-item cart"),
    ("Minute 03 - Payment Processing", "Handle Stripe webhook idempotency key collisions"),
    ("Minute 04 - Product Catalog", "Add full-text search indexing to catalog database"),
    ("Minute 05 - Inventory Mgmt", "Lock inventory rows during concurrent order placement"),
    ("Minute 06 - Order Fulfillment", "Send order confirmation email notification via webhook"),
    ("Minute 07 - Security Audit", "Validate and sanitize user input in product reviews"),
    ("Minute 08 - Performance & Cache", "Implement LRU in-memory cache for pricing calculations"),
    ("Minute 09 - Database Migration", "Add indices on customer_id and created_at columns"),
    ("Minute 10 - Final Sign-off", "Run comprehensive end-to-end regression check")
]

def req(method, endpoint, body=None, params=None):
    url = f"{BASE}{endpoint}"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"
    headers = {"X-Ctxly-Token": TOKEN, "Content-Type": "application/json"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, context=ctx, timeout=20) as resp:
            dur = time.time() - t0
            raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except Exception:
                data = raw
            return resp.status, dur, data
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, e.read().decode("utf-8")
    except Exception as e:
        return -1, time.time() - t0, str(e)

print("="*75)
print("⏱️ STARTING OFFICIAL 10-MINUTE CONTINUOUS LIVE STABILITY REVIEW")
print(f"Target URL: {BASE}")
print(f"Start Time: {time.strftime('%Y-%m-%d %H:%M:%S IST', time.localtime())}")
print("="*75)
sys.stdout.flush()

overall_success = True
all_latencies = []

for idx, (label, task_desc) in enumerate(TASKS, start=1):
    epoch_start = time.time()
    print(f"\n>>> [EPOCH {idx:02d}/10] {label} (Targeting ~60s interval)")
    sys.stdout.flush()
    
    # 1. Health & Tunnel Check
    s_root, d_root, _ = req("GET", "/")
    s_state, d_state, r_state = req("GET", "/api/state")
    smart_on = r_state.get("smart") if isinstance(r_state, dict) else False
    
    # 2. Prompt Compilation with Gemini 3.8 Flash
    s_prep, d_prep, r_prep = req("POST", "/api/prepare", {"repo": REPO, "task": task_desc})
    files = [f.get("path") for f in r_prep.get("files", [])] if isinstance(r_prep, dict) else []
    toks = r_prep.get("tokens_estimate", {}).get("optimized", 0) if isinstance(r_prep, dict) else 0
    pct = r_prep.get("tokens_estimate", {}).get("reduction_pct", 0) if isinstance(r_prep, dict) else 0
    
    # 3. Checkpoint Creation
    s_cp, d_cp, r_cp = req("POST", "/api/checkpoint", {"repo": REPO, "label": f"Review Epoch {idx}"})
    cp_id = r_cp.get("id") if isinstance(r_cp, dict) else None
    
    # 4. Timeline verification
    s_tl, d_tl, r_tl = req("GET", "/api/timeline", params={"repo": REPO})
    total_cps = len(r_tl.get("items", [])) if isinstance(r_tl, dict) else 0

    all_latencies.append(d_prep)
    
    epoch_ok = (s_root == 200 and s_state == 200 and s_prep == 200 and s_cp == 200 and s_tl == 200)
    if not epoch_ok:
        overall_success = False
        print(f"  ❌ Epoch {idx} Failure: Root={s_root}, State={s_state}, Prepare={s_prep}, CP={s_cp}")
    else:
        print(f"  ✅ Tunnel Connectivity: HTTP {s_root} in {d_root:.2f}s | Smart Mode: {smart_on}")
        print(f"  ✅ Prompt Engine:       HTTP {s_prep} in {d_prep:.2f}s | Scoped: {len(files)} files | Tokens: {toks} ({pct}% reduction)")
        print(f"  ✅ State Checkpointing: HTTP {s_cp} in {d_cp:.2f}s | Checkpoint #{cp_id} (Total: {total_cps})")
    
    sys.stdout.flush()
    
    # Sleep remaining seconds of the 60-second window
    elapsed = time.time() - epoch_start
    sleep_time = max(0.0, 60.0 - elapsed)
    if idx < 10:
        print(f"  ⏳ Epoch {idx:02d} verified. Sustaining load & waiting {sleep_time:.1f}s for next epoch...")
        sys.stdout.flush()
        time.sleep(sleep_time)

print("\n" + "="*75)
print("🏁 10-MINUTE CONTINUOUS REVIEW FINISHED")
print(f"End Time: {time.strftime('%Y-%m-%d %H:%M:%S IST', time.localtime())}")
avg_l = sum(all_latencies)/len(all_latencies) if all_latencies else 0
print(f"Summary: 10/10 Epochs Tested | Avg Prompt Latency: {avg_l:.2f}s | Overall Verdict: {'PASS' if overall_success else 'FAIL'}")
print("="*75)
sys.stdout.flush()
