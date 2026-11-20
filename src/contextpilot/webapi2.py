"""Web app endpoints for v0.2 features: verify, explain/undo, timeline/rewind, savings, team, map, vision,
budget/settings, Gemma, webhooks and the printable report. Registered into webui's route tables on import."""

from __future__ import annotations

import html
import json
import time
from pathlib import Path


def _repo(body: dict) -> Path:
    from .webui import _repo as r

    return r(body)


def _q_repo(q: dict) -> Path:
    return _repo({"repo": (q.get("repo") or [""])[0]})


def _settings(root: Path):
    from .config import load_settings

    return load_settings(root)


def external_mode(s) -> str:
    from .external import mode

    return mode(s)


def _cmd_display(cmd: list[str] | None) -> str | None:
    if not cmd:
        return None
    return " ".join(Path(cmd[0]).name if i == 0 else a for i, a in enumerate(cmd))


# ------------------------------------------------------------------ verify / explain / undo
def api_verify(body: dict) -> dict:
    from . import verify
    from .store import Store

    root = _repo(body)
    s = _settings(root)
    with Store(root) as st:
        r = verify.run(root, st, command=s.extra.get("test_command") or None)
        if r.get("ran") and r.get("passed") is False:
            r["fix_prompt"] = verify.fix_prompt(root, st, r)
            (root / ".ctxly" / "last_prompt.md").write_text(r["fix_prompt"], encoding="utf-8")
    return r


def api_explain(body: dict) -> dict:
    from .explain import explain_changes
    from .store import Store

    root = _repo(body)
    s = _settings(root)
    with Store(root) as st:
        from .external import decide

        allowed, _ = decide(s, bool(body.get("smart", True)), approved=bool(body.get("approved")))
        return explain_changes(root, st, s.optimizer_model, use_llm=allowed,
                               checkpoint_id=body.get("checkpoint_id"))


def api_undo(body: dict) -> dict:
    from .checkpoint import create_checkpoint
    from .explain import undo_point
    from .snapshots import restore
    from .store import Store

    root = _repo(body)
    with Store(root) as st:
        ck = st.get_checkpoint(int(body["checkpoint_id"])) if body.get("checkpoint_id") else undo_point(st)
        if ck is None or not ck["snapshot_ref"]:
            raise ValueError("There's no undo point yet.")
        # A restorable Timeline entry of the files *as they are now*, so going back can itself be undone.
        create_checkpoint(root, st, reason="before-restore", session_id=ck["session_id"])
        r = restore(root, ck["snapshot_ref"])
        create_checkpoint(root, st, reason=f"restored to #{ck['id']}", session_id=ck["session_id"], code_snapshot="off")
    when = time.strftime("%d %b %H:%M", time.localtime(ck["created_at"]))
    return {**r, "message": f"Your files are back to how they were at {when}. A safety copy was saved first, "
                            "so you can undo this from the Timeline."}


# ------------------------------------------------------------------ timeline
def api_timeline(q: dict) -> dict:
    from .store import Store

    root = _q_repo(q)
    with Store(root) as st:
        rows = st.checkpoints(limit=int((q.get("limit") or ["80"])[0]))
        tasks = {r["id"]: r["task"] for r in st.list_sessions(500)}
    items = []
    for r in rows:
        stat = json.loads(r["diff_stat"] or "{}")
        items.append({
            "id": r["id"], "created_at": r["created_at"], "reason": r["reason"], "session_id": r["session_id"],
            "task": tasks.get(r["session_id"]), "notes": r["notes"], "next_steps": r["next_steps"],
            "files": [{"path": p, "status": v[0], "added": v[1], "removed": v[2]} for p, v in stat.items()],
            "can_restore": bool(r["snapshot_ref"]), "elapsed_ms": r["elapsed_ms"],
        })
    return {"items": items}


def api_checkpoint_diff(body: dict) -> dict:
    from .store import Store

    root = _repo(body)
    with Store(root) as st:
        r = st.get_checkpoint(int(body["id"]))
    if r is None:
        raise ValueError("Checkpoint not found.")
    return {"diff": r["diff"] or "", "head": r["git_head"]}


# ------------------------------------------------------------------ savings / settings / report
def api_savings(q: dict) -> dict:
    from . import verify
    from .budget import status as budget_status
    from .pricing import daily_series, load_prices, savings_summary
    from .store import Store

    root = _q_repo(q)
    s = _settings(root)
    prices = load_prices(s)
    with Store(root) as st:
        sess = st.active_session()
        return {
            "today": savings_summary(st, prices, 1), "week": savings_summary(st, prices, 7),
            "all": savings_summary(st, prices, None), "series": daily_series(st, prices, 14),
            "budget": budget_status(st, s, sess["id"]) if sess else None,
            "verifications": verify.history(st, 10),
            "prices": {"agent_model": prices.agent_model, "currency": prices.currency, "usd_inr": prices.usd_inr},
        }


def api_get_settings(q: dict) -> dict:
    from . import verify

    root = _q_repo(q)
    s = _settings(root)
    return {"currency": s.extra.get("currency", "USD"), "agent_model": s.extra.get("agent_model") or "",
            "usd_inr": s.extra.get("usd_inr") or "", "session_budget_usd": s.extra.get("session_budget_usd") or "",
            "test_command": s.extra.get("test_command") or "",
            "detected_test_command": _cmd_display(verify.detect_command(root, s.extra.get("test_command") or None)),
            "webhook_set": bool(s.extra.get("webhook_url")), "gemma_model": s.gemma_model,
            "external_mode": external_mode(s)}


def api_settings(body: dict) -> dict:
    from .config import save_settings
    from .notify import validate

    root = _repo(body)
    s = _settings(root)
    if "currency" in body:
        if body["currency"] not in ("USD", "INR"):
            raise ValueError("Currency must be USD or INR.")
        s.extra["currency"] = body["currency"]
    for key in ("agent_model", "test_command"):
        if key in body:
            s.extra[key] = str(body[key] or "").strip()
    if body.get("usd_inr") not in (None, ""):
        s.extra["usd_inr"] = float(body["usd_inr"])
    if "session_budget_usd" in body:
        v = body["session_budget_usd"]
        s.extra["session_budget_usd"] = float(v) if v not in ("", None) and float(v) > 0 else None
    if "webhook_url" in body:
        s.extra["webhook_url"] = validate(body["webhook_url"]) if body["webhook_url"] else ""
    if "gemma_model" in body:
        s.gemma_model = str(body["gemma_model"] or "")
    if "external_mode" in body:
        from .external import MODES

        if body["external_mode"] not in MODES:
            raise ValueError("Choose auto, ask or off.")
        s.extra["external_mode"] = body["external_mode"]
    save_settings(root, s)
    return {"ok": True, "message": "Saved."}


def report_data(root: Path) -> dict:
    from . import verify
    from .pricing import load_prices, savings_summary
    from .store import Store
    from .team import feed

    prices = load_prices(_settings(root))
    with Store(root) as st:
        return {"week": savings_summary(st, prices, 7), "all": savings_summary(st, prices, None),
                "verifications": verify.history(st, 50), "team": feed(root), "project": root.name}


def api_notify(body: dict) -> dict:
    from .notify import report_text, send

    root = _repo(body)
    url = _settings(root).extra.get("webhook_url")
    if not url:
        raise ValueError("Add a Slack or Google Chat webhook in Setup first.")
    d = report_data(root)
    send(url, report_text(d["project"], d["week"], d["all"], d["verifications"], d["team"]))
    return {"message": "Report sent to your team chat."}


def render_report(root: Path) -> str:
    """Printable savings report - the browser's 'Save as PDF' turns it into the CTO-ready audit PDF."""
    d = report_data(root)
    w, a = d["week"], d["all"]
    e = html.escape
    ver = "".join(
        f"<tr><td>{time.strftime('%d %b %H:%M', time.localtime(v['created_at']))}</td><td>{e(v['summary'] or '')}</td>"
        f"<td>{'✅ passed' if v['passed'] == 1 else '❌ failed' if v['passed'] == 0 else '-'}</td></tr>"
        for v in d["verifications"][:15]) or "<tr><td colspan=3>No test runs yet</td></tr>"
    team = "".join(f"<li><b>{e(t.get('member') or '')}</b>: {e(t.get('task') or '-')}</li>" for t in d["team"]) \
        or "<li>No shared notes yet</li>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>ContextPilot report</title>
<style>body{{font:15px/1.5 system-ui,"Segoe UI",Arial,sans-serif;color:#1b1f2a;background:#fff;max-width:820px;margin:32px auto;padding:0 16px}}
h1{{margin:0}}.sub{{color:#5d6475}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0}}
.k{{border:1px solid #dfe3ec;border-radius:12px;padding:14px}}.k b{{display:block;font-size:24px}}
table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #dfe3ec;padding:8px;text-align:left}}
.note{{color:#5d6475;font-size:13px;margin-top:24px}}button{{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid #1a73e8;background:#1a73e8;color:#fff;cursor:pointer}}
@media print{{.noprint{{display:none}}}}@media (max-width:640px){{.grid{{grid-template-columns:1fr}}}}</style></head><body>
<p class="noprint"><button onclick="print()">Save as PDF / Print</button></p>
<h1>ContextPilot savings report</h1>
<p class="sub">Project <b>{e(d['project'])}</b> · generated {time.strftime('%d %b %Y, %H:%M')}</p>
<div class="grid"><div class="k"><b>{w['saved']}</b>estimated saved this week</div>
<div class="k"><b>{a['saved']}</b>estimated saved all time</div><div class="k"><b>{a['requests']}</b>requests prepared</div></div>
<div class="grid"><div class="k"><b>{a['naive_tokens']:,}</b>tokens without ContextPilot (est.)</div>
<div class="k"><b>{a['optimized_tokens']:,}</b>tokens with ContextPilot (est.)</div>
<div class="k"><b>{a['billed']}</b>actually billed by Gemini ({a['billed_calls']} calls)</div></div>
<h2>Did the fixes work?</h2><table><tr><th>When</th><th>Result</th><th></th></tr>{ver}</table>
<h2>Team</h2><ul>{team}</ul>
<p class="note">Estimates compare reading the whole project with reading only the files ContextPilot selected, priced at
{e(a['agent_model'])} list input prices (editable in Setup). Billed amounts come from Google's usage data and are shown separately.</p>
</body></html>"""


# ------------------------------------------------------------------ team / map / vision / gemma
def api_team(q: dict) -> dict:
    from .team import feed, member_name

    root = _q_repo(q)
    return {"me": member_name(root), "items": feed(root)}


def api_team_share(body: dict) -> dict:
    from .store import Store
    from .team import share

    root = _repo(body)
    with Store(root) as st:
        p = share(root, st, (body.get("name") or "").strip() or None)
    return {"path": p.relative_to(root).as_posix(),
            "message": "Shared! Commit the .ctxly-team folder so your teammates' AI tools see it."}


def api_map(q: dict) -> dict:
    from .projectmap import build_map
    from .store import Store
    from .symbols import build_index

    root = _q_repo(q)
    with Store(root) as st:
        build_index(root, st)
        return build_map(root, st)


def api_vision(body: dict) -> dict:
    from .vision import read_error_image

    root = _repo(body)
    if external_mode(_settings(root)) == "off":
        raise ValueError("Sending to Gemini is turned off in Setup, so screenshots can't be read.")
    return read_error_image(body.get("image") or "", _settings(root).optimizer_model)


def api_gemma(q: dict) -> dict:
    from .gemma import ollama_status

    return ollama_status()


def api_gemma_pull(body: dict) -> dict:
    from .gemma import DEFAULT_MODEL, ollama_status, start_pull

    if not ollama_status()["running"]:
        raise ValueError("Ollama isn't running. Install it from ollama.com, open it, then try again.")
    return start_pull(body.get("model") or DEFAULT_MODEL)


# ------------------------------------------------------------------ project memory / metrics
def api_memory(q: dict) -> dict:
    from .memory import list_memories
    from .store import Store

    root = _q_repo(q)
    with Store(root) as st:
        return {"items": list_memories(st, limit=int((q.get("limit") or ["60"])[0]))}


def api_memory_post(body: dict) -> dict:
    from .memory import forget, remember, set_status
    from .store import Store

    root = _repo(body)
    action = body.get("action") or "add"
    with Store(root) as st:
        if action == "add":
            sess = st.active_session()
            r = remember(st, body.get("kind") or "decision", body.get("text") or "", files=body.get("files") or [],
                         session_id=sess["id"] if sess else None)
            if r is None:
                raise ValueError("Write a little more so it's worth remembering.")
            return {**r, "message": "Remembered." if r["created"] else "ContextPilot already knew that, so it updated it."}
        mid = int(body.get("id") or 0)
        ok = forget(st, mid) if action == "forget" else set_status(st, mid, "done" if action == "done" else "open")
    if not ok:
        raise ValueError("That memory doesn't exist any more.")
    return {"ok": True}


def api_metrics(q: dict) -> dict:
    from .metrics import collect
    from .store import Store

    root = _q_repo(q)
    days = int((q.get("days") or ["30"])[0])
    with Store(root) as st:
        return collect(st, days or None)


GET = {"/api/memory": api_memory, "/api/metrics": api_metrics, "/api/timeline": api_timeline, "/api/savings": api_savings, "/api/settings": api_get_settings,
       "/api/team": api_team, "/api/map": api_map, "/api/gemma": api_gemma}
POST = {"/api/memory": api_memory_post, "/api/verify": api_verify, "/api/explain": api_explain, "/api/undo": api_undo,
        "/api/checkpoint-diff": api_checkpoint_diff, "/api/settings": api_settings, "/api/notify": api_notify,
        "/api/team/share": api_team_share, "/api/vision": api_vision, "/api/gemma/pull": api_gemma_pull}
