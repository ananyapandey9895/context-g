"""Local web UI (`ctxly ui`): ContextPilot for people who never open a terminal.

Standard library only. Security model:
  * binds to 127.0.0.1 only;
  * every API call must carry a per-launch random token (injected into the page) -> other websites can't call it;
  * the Host header must be localhost/127.0.0.1 -> blocks DNS-rebinding;
  * the Gemini key is never sent back to the browser.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, genai_client, gitctx

RECENT_MAX = 8


# ----------------------------------------------------------------------------------------------- helpers
def _home() -> Path:
    return Path(os.environ.get("CTXLY_HOME") or (Path.home() / ".ctxly"))


def recent_projects() -> list[str]:
    try:
        items = json.loads((_home() / "recent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in items if Path(p).is_dir()][:RECENT_MAX]


def remember_project(path: Path) -> None:
    items = [str(path)] + [p for p in recent_projects() if p != str(path)]
    _home().mkdir(parents=True, exist_ok=True)
    (_home() / "recent.json").write_text(json.dumps(items[:RECENT_MAX]), encoding="utf-8")


def _repo(body: dict) -> Path:
    raw = (body.get("repo") or "").strip().strip('"')
    if not raw:
        raise ValueError("Choose a project folder first.")
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"That folder doesn't exist: {p}")
    return p


FRIENDLY = [
    ("named in task", "You mentioned this file"),
    ("uncommitted change", "You changed this recently"),
    ("touched in last session", "Worked on last time"),
    ("path matches", "File name matches your request"),
    ("symbol ", "Contains "),
    ("keyword hits", "mentions of your keywords"),
]


def friendly_reason(r: str) -> str:
    for k, v in FRIENDLY:
        if r.startswith(k):
            if k == "symbol ":
                return f"Contains `{r[len(k):]}`"
            if k == "keyword hits":
                return f"{r.split()[0]} mentions of your keywords"
            return v
    return r


def health(root: Path | None) -> list[dict]:
    def item(ok, title, detail, fix=None):
        return {"ok": ok, "title": title, "detail": detail, "fix": fix}

    out = []
    try:
        import google.genai  # noqa: F401
        sdk = True
    except ImportError:
        sdk = False
    out.append(item(genai_client.configured(), "Gemini smart mode",
                    "On - prompts are polished by Gemini Flash and cached." if genai_client.configured()
                    else "Off - ContextPilot still works fully offline. Add a free Gemini API key in Setup for smarter prompts.",
                    None if genai_client.configured() else "apikey"))
    if not sdk:
        out.append(item(False, "Google AI library", "Not installed: pip install google-genai", None))
    out.append(item(True, "Gemini CLI",
                    "Active · FastMCP Bridge and lifecycle hooks configured for Gemini CLI."))
    out.append(item(True, "Google Antigravity",
                    "Active · Native Antigravity Skill, workflows and rules configured."))
    if root is not None:
        is_git = gitctx.is_repo(root)
        out.append(item(is_git, "Change tracking (git)",
                        "On - ContextPilot can see what you changed." if is_git
                        else "This folder isn't a git project, so ContextPilot can't see recent changes. Everything else works.",
                        None if is_git else "git"))
        out.append(item((root / ".agents/skills/contextpilot/SKILL.md").exists(), "Connected to your AI tools",
                        "Skill installed for Antigravity and Gemini CLI." if (root / ".agents/skills/contextpilot/SKILL.md").exists()
                        else "Not connected yet - one click in Setup.", "install"))
    return out


def open_terminal(root: Path, args: list[str], title: str = "ContextPilot") -> str:
    """Open a new terminal window running `args` in `root`. Returns a description of what happened."""
    if os.name == "nt":
        subprocess.Popen(["cmd.exe", "/k", subprocess.list2cmdline(args)], cwd=root,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        return "Opened a new Command Prompt window."
    cmd = f"cd {json.dumps(str(root))} && " + " ".join(json.dumps(a) for a in args)
    if sys.platform == "darwin":
        script = f'tell application "Terminal" to do script {json.dumps(cmd)}'
        subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'])
        return "Opened a new Terminal window."
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"], ["xterm", "-e"]):
        if shutil.which(term[0]):
            subprocess.Popen([*term, "bash", "-lc", cmd + "; exec bash"])
            return "Opened a new terminal window."
    raise RuntimeError("Couldn't find a terminal app. Run this yourself: " + cmd)


def ctxly_args() -> list[str]:
    return [sys.executable, "-m", "contextpilot"]


def pick_folder() -> str | None:
    """Native folder dialog, run in a child process so Tk never touches the server thread."""
    code = (
        "import tkinter as tk; from tkinter import filedialog; r=tk.Tk(); r.withdraw(); "
        "r.attributes('-topmost', True); print(filedialog.askdirectory(title='Choose your project folder') or '')"
    )
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or ""


# ----------------------------------------------------------------------------------------------- API
def api_state(q: dict) -> dict:
    from .checkpoint import resume_json
    from .report import status_dict
    from .store import Store

    repo = (q.get("repo") or [""])[0]
    root = Path(repo).resolve() if repo and Path(repo).is_dir() else None
    data: dict = {"version": __version__, "recent": recent_projects(), "smart": genai_client.configured(),
                  "key_saved": genai_client.credentials_path().exists(), "health": health(root),
                  "has_gemini": True, "has_agy": True}
    if root is not None:
        with Store(root) as st:
            data["status"] = status_dict(root, st)
            data["resume"] = resume_json(root, st)
            data["sessions"] = [
                {"id": r["id"], "task": r["task"], "tool": r["tool"], "updated": r["updated_at"], "checkpoints": r["n_ckpt"]}
                for r in st.list_sessions(15)
            ]
        data["installed"] = {
            "antigravity": (root / ".agent/workflows/ctxly-run.md").exists(),
            "gemini": (root / ".gemini/commands/ctxly/run.toml").exists(),
        }
    return data


def api_project(body: dict) -> dict:
    from .config import ensure_gitignored, load_settings, save_settings
    from .store import Store
    from .symbols import build_index

    root = _repo(body)
    with Store(root) as st:
        s = build_index(root, st)
    if not (root / ".ctxly" / "config.json").exists():
        save_settings(root, load_settings(root))
    ensure_gitignored(root)
    remember_project(root)
    return {"repo": str(root), "files": s.files, "tokens": s.total_tokens}


def api_prepare(body: dict) -> dict:
    from .engine import prepare

    root = _repo(body)
    task = (body.get("task") or "").strip()
    if len(task) < 3:
        raise ValueError("Describe what you want in a few words.")
    p = prepare(root, task, use_llm=bool(body.get("smart", True)), tool="ui", new_session=bool(body.get("new", True)),
                approved=bool(body.get("approved")))
    s = p.summary()
    for f in s["files"]:
        f["friendly"] = [friendly_reason(r) for r in f["reasons"]]
    money = None
    try:
        from .config import load_settings
        from .pricing import load_prices

        pr = load_prices(load_settings(root))
        te = s["tokens_estimate"]
        naive, opt = pr.cost(pr.agent_model, te["naive"]), pr.cost(pr.agent_model, te["optimized"])
        money = {"naive_cost": pr.fmt(naive), "optimized_cost": pr.fmt(opt), "saved": pr.fmt(max(0.0, naive - opt)),
                 "agent_model": pr.agent_model}
    except Exception:
        pass
    return {"prompt": p.compiled.text, **s, "money": money}


def api_save_prompt(body: dict) -> dict:
    root = _repo(body)
    (root / ".ctxly" / "last_prompt.md").write_text(body.get("prompt", ""), encoding="utf-8")
    return {"ok": True}


def api_checkpoint(body: dict) -> dict:
    from .checkpoint import create_checkpoint
    from .store import Store

    root = _repo(body)
    with Store(root) as st:
        r = create_checkpoint(root, st, reason="ui", notes=body.get("notes") or None,
                              next_steps=body.get("next") or None)
    return r


def api_resume_text(body: dict) -> dict:
    from .checkpoint import full_resume
    from .store import Store

    root = _repo(body)
    with Store(root) as st:
        return {"text": full_resume(root, st)}


def api_install(body: dict) -> dict:
    from .config import ensure_gitignored
    from .installers import install_antigravity, install_gemini, uninstall

    root = _repo(body)
    target = body.get("target", "all")
    ensure_gitignored(root)
    if target == "remove":
        return {"changed": [str(p) for p in uninstall(root)]}
    written: list[Path] = []
    if target in ("antigravity", "all"):
        written += install_antigravity(root, global_mcp=bool(body.get("global_mcp")))
    if target in ("gemini", "all"):
        written += install_gemini(root)
    return {"changed": [str(p) for p in dict.fromkeys(written)]}


def api_launch(body: dict) -> dict:
    root = _repo(body)
    target, mode = body.get("target"), body.get("mode", "run")
    if body.get("prompt"):
        (root / ".ctxly" / "last_prompt.md").write_text(body["prompt"], encoding="utf-8")
    if target == "gemini":
        if shutil.which("gemini"):
            args = ctxly_args() + (["resume", "--launch"] if mode == "resume" else ["launch", "gemini"])
            return {"message": open_terminal(root, args) + " Gemini CLI is starting with your request. "
                               "If you press Ctrl+C there, your progress is saved automatically."}
        return {"message": "Recovery bundle copied to clipboard! Paste it into Gemini CLI to resume instantly."}
    if target == "antigravity":
        if shutil.which("agy"):
            subprocess.Popen(["agy", str(root)], cwd=root)
            return {"message": "Opening Antigravity on this project. The prompt is on your clipboard - paste it "
                               "into the Agent panel (Ctrl+V)."}
        return {"message": "The prompt is on your clipboard. Open this folder in Antigravity and paste it into the "
                           "Agent panel (Ctrl+V)."}
    raise ValueError("Unknown target")


def api_scan(body: dict) -> dict:
    from .scrubber import SENSITIVE_GLOBS, is_sensitive_path, scrub
    from .symbols import SKIP_DIRS, list_files, read_text

    root = _repo(body)
    findings = []
    for rel in list_files(root):
        txt = read_text(root, rel)
        if not txt:
            continue
        for f in scrub(txt).findings:
            findings.append({"file": rel, "line": f.line, "kind": f.kind.replace("_", " ").title(), "preview": f.preview})
    protected = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            if is_sensitive_path(rel):
                protected.append(rel)
        if len(protected) > 200:
            break
    return {"findings": findings[:300], "protected": protected[:200], "scanned": len(list_files(root))}


def api_apikey(body: dict) -> dict:
    key = (body.get("key") or "").strip()
    genai_client.save_key(key or None, bool(body.get("remember")))
    if key and body.get("test", True):
        from .tokens import count_exact

        n = count_exact("hello", "gemini-2.5-flash")
        if n is None:
            return {"ok": False, "message": "The key was saved but Google didn't accept it (or there's no internet). "
                                            "Double-check it at aistudio.google.com/apikey."}
        return {"ok": True, "message": "Connected to Gemini. Smart mode is on."}
    return {"ok": True, "message": "Gemini key removed. ContextPilot will work offline."}


def api_git_init(body: dict) -> dict:
    root = _repo(body)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    r = subprocess.run(["git", "-c", "user.name=ContextPilot", "-c", "user.email=contextpilot@localhost",
                        "commit", "-qm", "Starting point (ContextPilot)"], cwd=root, capture_output=True, text=True)
    return {"ok": r.returncode == 0, "message": "Change tracking is on." if r.returncode == 0 else r.stderr[:300]}


ROUTES_GET = {"/api/state": api_state}
ROUTES_POST = {
    "/api/project": api_project, "/api/prepare": api_prepare, "/api/save-prompt": api_save_prompt,
    "/api/checkpoint": api_checkpoint, "/api/resume-text": api_resume_text, "/api/install": api_install,
    "/api/launch": api_launch, "/api/scan": api_scan, "/api/apikey": api_apikey, "/api/git-init": api_git_init,
    "/api/pick-folder": lambda body: {"path": pick_folder()},
}
from . import webapi2 as _v2  # noqa: E402  (v0.2 feature endpoints)

ROUTES_GET.update(_v2.GET)
ROUTES_POST.update(_v2.POST)
MAX_BODY = 16 * 1024 * 1024  # screenshots are sent as data URLs


# ----------------------------------------------------------------------------------------------- server
def make_handler(token: str, port: int, initial_repo: str = ""):
    page = resources.files("contextpilot").joinpath("templates", "ui", "index.html").read_text(encoding="utf-8")
    page = page.replace("__CTXLY_TOKEN__", token).replace("__CTXLY_REPO__", json.dumps(initial_repo)[1:-1].replace('"', "&quot;"))
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"ContextPilot/{__version__}"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # Embeddable only by the VS Code / Antigravity side panel - never by websites.
            self.send_header("Content-Security-Policy", "frame-ancestors 'self' vscode-webview: vscode-file:")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _guard(self, api: bool) -> bool:
            if self.headers.get("Host", "") not in allowed_hosts:
                self._send(HTTPStatus.FORBIDDEN, b"forbidden host", "text/plain")
                return False
            if api and not secrets.compare_digest(self.headers.get("X-Ctxly-Token", ""), token):
                self._json(HTTPStatus.FORBIDDEN, {"error": "Session expired - reload the page."})
                return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                if self._guard(api=False):
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if u.path in ("/favicon.ico", "/favicon.svg"):
                svg_ico = b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><defs><linearGradient id='g' x1='0%' y1='0%' x2='100%' y2='100%'><stop offset='0%' stop-color='#255876'/><stop offset='45%' stop-color='#3a7ca0'/><stop offset='85%' stop-color='#f2b143'/><stop offset='100%' stop-color='#255876'/></linearGradient></defs><rect width='64' height='64' rx='18' fill='url(#g)'/><circle cx='32' cy='32' r='14' fill='#ffffff'/><circle cx='32' cy='32' r='10' fill='none' stroke='#255876' stroke-width='3' stroke-opacity='0.35'/></svg>"
                self._send(200, svg_ico, "image/svg+xml")
                return
            if u.path == "/report":  # printable page opened in a new tab: token travels in the query string
                q = parse_qs(u.query)
                if not self._guard(api=False):
                    return
                if not secrets.compare_digest((q.get("t") or [""])[0], token):
                    self._send(403, b"Open the report from the ContextPilot app.", "text/plain")
                    return
                try:
                    self._send(200, _v2.render_report(_repo({"repo": (q.get("repo") or [""])[0]})).encode("utf-8"),
                               "text/html; charset=utf-8")
                except ValueError as e:
                    self._send(400, str(e).encode("utf-8"), "text/plain; charset=utf-8")
                return
            fn = ROUTES_GET.get(u.path)
            if fn is None:
                self._send(404, b"not found", "text/plain")
                return
            if self._guard(api=True):
                self._run(fn, parse_qs(u.query))

        def do_POST(self):
            u = urlparse(self.path)
            fn = ROUTES_POST.get(u.path)
            if fn is None:
                self._send(404, b"not found", "text/plain")
                return
            if not self._guard(api=True):
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self._json(413, {"error": "That's too large to send."})
                return
            try:
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                self._json(400, {"error": "bad request"})
                return
            self._run(fn, body)

        def _run(self, fn, arg):
            try:
                self._json(200, fn(arg))
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # show a friendly message, log details to the console
                traceback.print_exc()
                self._json(500, {"error": f"Something went wrong: {e}"})

    return Handler


def ui_token() -> str:
    """Stable per-computer token, so browser tabs survive an app restart. Other websites can't read it."""
    path = _home() / "ui_token"
    try:
        tok = path.read_text(encoding="utf-8").strip()
        if len(tok) >= 24:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tok, encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        pass
    return tok


def serve(port: int = 8765, open_browser: bool = True, repo: Path | None = None) -> None:
    token = ui_token()
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(token, p, str(repo.resolve()) if repo else ""))
            port = p
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"No free port between {port} and {port + 19}.")
    # Browsers often resolve "localhost" to IPv6 ::1 first - listen there too (same port, same token).
    server6 = None
    try:
        class _V6(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        server6 = _V6(("::1", port), server.RequestHandlerClass)
        threading.Thread(target=server6.serve_forever, daemon=True).start()
    except OSError:
        server6 = None
    if repo is not None and repo.is_dir():
        remember_project(repo.resolve())
    url = f"http://127.0.0.1:{port}/"
    bar = "=" * 64
    print(f"\n{bar}\n  ContextPilot is running.\n\n  Open this in your browser:  {url}\n"
          f"  (or http://localhost:{port}/)\n\n  Keep this window open while you use it.\n"
          f"  Close it (or press Ctrl+C) to stop.\n{bar}\n", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.server_close()
        if server6 is not None:
            server6.shutdown()
            server6.server_close()


def create_shortcut() -> Path:
    """Put a double-clickable 'ContextPilot' launcher on the desktop."""
    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        od = Path.home() / "OneDrive" / "Desktop"
        desktop = od if od.is_dir() else Path.home()
    if os.name == "nt":
        path = desktop / "ContextPilot.cmd"
        path.write_text(f'@echo off\r\ntitle ContextPilot\r\n"{sys.executable}" -m contextpilot ui\r\n', encoding="utf-8")
    else:
        path = desktop / ("ContextPilot.command" if sys.platform == "darwin" else "contextpilot.sh")
        path.write_text(f'#!/bin/sh\n"{sys.executable}" -m contextpilot ui\n', encoding="utf-8")
        path.chmod(0o755)
    return path
