"""One-click local Gemma (via Ollama) for zero-cost, on-device task triage."""

from __future__ import annotations

import json
import shutil
import threading
import urllib.request

DEFAULT_MODEL = "gemma3:4b"
_progress: dict = {"state": "idle", "pct": 0, "message": ""}


def ollama_status(url: str = "http://localhost:11434") -> dict:
    installed = bool(shutil.which("ollama"))
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=2) as r:
            models = [m["name"] for m in json.loads(r.read()).get("models", [])]
        running = True
    except Exception:
        models, running = [], False
    gemma = [m for m in models if m.startswith("gemma")]
    return {"installed": installed or running, "running": running, "gemma_models": gemma, "pull": dict(_progress)}


def start_pull(model: str = DEFAULT_MODEL, url: str = "http://localhost:11434") -> dict:
    if _progress["state"] == "pulling":
        return dict(_progress)

    def work() -> None:
        _progress.update(state="pulling", pct=0, message=f"Downloading {model}…")
        try:
            req = urllib.request.Request(f"{url}/api/pull", json.dumps({"model": model, "stream": True}).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3600) as r:
                for line in r:
                    ev = json.loads(line or b"{}")
                    if ev.get("total"):
                        _progress["pct"] = round(100 * ev.get("completed", 0) / ev["total"], 1)
                    if ev.get("status"):
                        _progress["message"] = ev["status"]
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
            _progress.update(state="done", pct=100, message=f"{model} is ready")
        except Exception as e:
            _progress.update(state="error", message=str(e)[:200])

    threading.Thread(target=work, daemon=True).start()
    return dict(_progress)
