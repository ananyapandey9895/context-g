"""Paste a screenshot of an error -> Gemini reads it -> a clean request with the error text and file hints."""

from __future__ import annotations

import base64
import json

from . import genai_client
from .scrubber import scrub

MAX_BYTES = 8 * 1024 * 1024
ALLOWED = {"image/png", "image/jpeg", "image/webp", "image/gif"}

PROMPT = """This is a screenshot from a developer's screen, probably an error.
Return JSON: {"error_text": exact error message and stack trace lines you can read (verbatim, max 40 lines),
"files": [file paths or file names visible], "summary": one plain-English sentence of what went wrong,
"suggested_request": a one-sentence request to an AI coding assistant to fix it}.
Never transcribe passwords, API keys or tokens - write [hidden] instead."""


def read_error_image(data_url: str, model: str = "gemini-flash-lite-latest") -> dict:
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise ValueError("That doesn't look like an image.")
    mime, b64 = data_url[5:].split(";base64,", 1)
    if mime not in ALLOWED:
        raise ValueError("Please paste a PNG, JPG, WebP or GIF screenshot.")
    raw = base64.b64decode(b64)
    if len(raw) > MAX_BYTES:
        raise ValueError("That image is too big (max 8 MB).")
    client = genai_client.get_client()
    if client is None:
        raise ValueError("Reading screenshots needs Gemini smart mode. Add a free key in Setup, "
                         "or copy the error text and paste it instead.")
    from google.genai import types

    models_to_try = [model]
    for fb in ["gemini-flash-lite-latest", "gemini-3.5-flash-lite"]:
        if fb not in models_to_try:
            models_to_try.append(fb)

    resp = None
    last_exc = None
    for m in models_to_try:
        try:
            resp = client.models.generate_content(
                model=m,
                contents=[types.Part.from_bytes(data=raw, mime_type=mime), PROMPT],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0, max_output_tokens=1200),
            )
            break
        except Exception as exc:
            last_exc = exc
            continue

    if resp is None:
        raise last_exc or RuntimeError("All Gemini vision models failed")
    data = json.loads(resp.text)
    for k in ("error_text", "summary", "suggested_request"):
        if isinstance(data.get(k), str):
            data[k] = scrub(data[k]).text
    data["files"] = [str(f) for f in (data.get("files") or [])][:10]
    req = data.get("suggested_request") or "Fix this error"
    err = data.get("error_text") or ""
    data["task"] = f"{req}\n\n{err}".strip()
    return data
