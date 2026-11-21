"""Lazy Google GenAI SDK client (Gemini API or Vertex AI, configured purely by environment).

Gemini API:   set GEMINI_API_KEY (or GOOGLE_API_KEY)
Vertex AI:    set GOOGLE_GENAI_USE_VERTEXAI=true, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION
              and authenticate with `gcloud auth application-default login`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_client = None
_tried = False


def credentials_path() -> Path:
    return Path(os.environ.get("CTXLY_HOME") or (Path.home() / ".ctxly")) / "credentials.json"


def _load_saved_key() -> None:
    """A key saved from the local UI ("remember on this computer") - environment variables always win."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return
    try:
        key = json.loads(credentials_path().read_text(encoding="utf-8")).get("gemini_api_key")
    except (OSError, ValueError):
        return
    if key:
        os.environ["GEMINI_API_KEY"] = key


def save_key(key: str | None, remember: bool) -> None:
    """Use a Gemini API key for this process; optionally persist it (user-only file) for next time."""
    if key:
        os.environ["GEMINI_API_KEY"] = key
    else:
        os.environ.pop("GEMINI_API_KEY", None)
    path = credentials_path()
    if remember and key:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"gemini_api_key": key}), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    elif not key and path.exists():
        path.unlink()
    reset()


def configured() -> bool:
    if os.environ.get("CTXLY_OFFLINE"):
        return False
    _load_saved_key()
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return True
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes") and bool(
        os.environ.get("GOOGLE_CLOUD_PROJECT")
    )


def backend() -> str:
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes"):
        return "vertex"
    return "gemini-api"


def get_client():
    global _client, _tried
    if _client is not None or _tried:
        return _client
    _tried = True
    if not configured():
        return None
    try:
        from google import genai
    except ImportError:
        return None
    try:
        _client = genai.Client(http_options={"api_version": "v1beta", "timeout": 8000})
    except Exception:
        try:
            _client = genai.Client(http_options={"timeout": 8000})
        except Exception:
            _client = None
    return _client


def reset() -> None:
    global _client, _tried
    _client, _tried = None, False
