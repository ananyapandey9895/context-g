"""Send ContextPilot reports to Slack or Google Chat through an incoming webhook (outbound only)."""

from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlparse

ALLOWED_HOSTS = ("hooks.slack.com", "chat.googleapis.com")


def validate(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme != "https" or not any(u.hostname == h or (u.hostname or "").endswith("." + h) for h in ALLOWED_HOSTS):
        raise ValueError("Use a Slack (https://hooks.slack.com/…) or Google Chat (https://chat.googleapis.com/…) webhook URL.")
    return url.strip()


def send(url: str, text: str) -> None:
    url = validate(url)
    req = urllib.request.Request(url, json.dumps({"text": text}).encode("utf-8"),
                                 {"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=15) as r:
        if r.status >= 300:
            raise RuntimeError(f"Webhook returned {r.status}")


def report_text(project: str, week: dict, total: dict, verifications: list[dict], team: list[dict]) -> str:
    passed = sum(1 for v in verifications if v.get("passed") == 1)
    failed = sum(1 for v in verifications if v.get("passed") == 0)
    lines = [
        f"*ContextPilot weekly report - {project}*",
        f"• Requests prepared this week: {week['requests']}",
        f"• Estimated savings this week: {week['saved']} (all time: {total['saved']})",
        f"• Billed by Gemini this week: {week['billed']} across {week['billed_calls']} calls"
        + (f", cache saved {week['billed_cache_saved']}" if week.get("billed_cache_saved_usd") else ""),
        f"• Test checks: {passed} passed, {failed} failed",
    ]
    for t in team[:5]:
        lines.append(f"• {t.get('member')}: {t.get('task') or '-'}" + (f" (next: {t['next_steps']})" if t.get("next_steps") else ""))
    lines.append("_Savings are local estimates; billed amounts come from Google's usage data._")
    return "\n".join(lines)
