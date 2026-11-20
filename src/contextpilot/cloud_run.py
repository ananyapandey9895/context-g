"""
ContextPilot AI - Google Cloud Run Entrypoint
Serves the live interactive prototype on 0.0.0.0:$PORT with Google Cloud optimizations.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, webapi2 as _v2, webui


def make_cloud_handler(token: str, port: int, initial_repo: str = ""):
    raw_page = resources.files("contextpilot").joinpath("templates", "ui", "index.html").read_text(encoding="utf-8")
    page = raw_page.replace("__CTXLY_TOKEN__", token).replace(
        "__CTXLY_REPO__", json.dumps(initial_repo)[1:-1].replace('"', "&quot;")
    )
    # Inject a clean Google Cloud Run header badge
    cloud_badge = """
    <div style="background: linear-gradient(90deg, #1a73e8, #4285f4); color: white; padding: 8px 16px; font-size: 13px; font-weight: 600; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
      <div style="display: flex; align-items: center; gap: 8px;">
        <span>☁️ <b>Google Cloud Run</b> Live Deployment</span>
        <span style="background: rgba(255,255,255,0.25); padding: 2px 8px; rounded: 12px; border-radius: 12px; font-size: 11px;">Team Infinity</span>
        <span style="background: rgba(52,168,83,0.3); padding: 2px 8px; border-radius: 12px; font-size: 11px;">✓ Vertex AI Context Caching Active</span>
      </div>
      <div style="display: flex; gap: 14px;">
        <a href="https://github.com/ananyapandey9895/context-g" target="_blank" style="color: white; text-decoration: underline;">GitHub Repository</a>
        <a href="https://github.com/ananyapandey9895/context-g/blob/main/ContextPilot_AI_Startup_Buildathon_Round2.pdf" target="_blank" style="color: white; text-decoration: underline;">Pitch Deck (PDF)</a>
      </div>
    </div>
    """
    if "<body>" in page:
        page = page.replace("<body>", f"<body>\n{cloud_badge}\n")

    class CloudHandler(BaseHTTPRequestHandler):
        server_version = f"ContextPilot-CloudRun/{__version__}"

        def log_message(self, fmt, *args):
            sys.stderr.write(f"[CloudRun] {self.address_string()} - {fmt % args}\n")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _guard(self, api: bool) -> bool:
            # Allow any public host on Google Cloud Run (e.g. *.a.run.app)
            if api and not secrets.compare_digest(self.headers.get("X-Ctxly-Token", ""), token):
                self._json(HTTPStatus.FORBIDDEN, {"error": "Session token mismatch - please reload the page."})
                return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                if self._guard(api=False):
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if u.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            if u.path == "/healthz" or u.path == "/_health":
                self._send(200, b"OK", "text/plain")
                return
            if u.path == "/report":
                q = parse_qs(u.query)
                if not self._guard(api=False):
                    return
                try:
                    repo_path = webui._repo({"repo": (q.get("repo") or [""])[0]})
                    self._send(200, _v2.render_report(repo_path).encode("utf-8"), "text/html; charset=utf-8")
                except Exception as e:
                    self._send(500, f"Report generation error: {e}".encode("utf-8"), "text/plain")
                return
            if u.path.startswith("/api/"):
                if not self._guard(api=True):
                    return
                q = parse_qs(u.query)
                if u.path == "/api/state":
                    self._run(webui.api_state, q)
                    return
                if u.path == "/api/summary":
                    self._run(_v2.api_summary, q)
                    return
                if u.path == "/api/map":
                    self._run(_v2.api_map, q)
                    return
                if u.path == "/api/recent":
                    self._json(200, {"recent": webui.recent_projects()})
                    return
            self._send(404, b"Not found", "text/plain")

        def do_POST(self):
            if not self._guard(api=True):
                return
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self._json(400, {"error": "Malformed JSON payload"})
                return

            routes = {
                "/api/project": webui.api_project,
                "/api/prepare": webui.api_prepare,
                "/api/save_prompt": webui.api_save_prompt,
                "/api/checkpoint": webui.api_checkpoint,
                "/api/resume_text": webui.api_resume_text,
                "/api/scan": webui.api_scan,
                "/api/timeline": _v2.api_timeline,
                "/api/explain": _v2.api_explain,
                "/api/undo": _v2.api_undo,
                "/api/restore": _v2.api_restore,
                "/api/memory": _v2.api_memory,
                "/api/save_memory": _v2.api_save_memory,
                "/api/budget": _v2.api_budget,
                "/api/team": _v2.api_team,
                "/api/share": _v2.api_share,
                "/api/setup": _v2.api_setup,
                "/api/save_setup": _v2.api_save_setup,
                "/api/verify": _v2.api_verify,
            }
            fn = routes.get(u.path)
            if fn is None:
                self._send(404, b"Not found", "text/plain")
                return
            self._run(fn, body)

        def _run(self, fn, arg):
            try:
                self._json(200, fn(arg))
            except Exception as e:
                self._json(500, {"error": str(e)})

    return CloudHandler


def run_cloud_server():
    port = int(os.environ.get("PORT", "8080"))
    root = Path(__file__).resolve().parent.parent.parent
    demo_repo = root / "demo" / "ecommerce-core"
    
    # Ensure demo repo exists
    if not demo_repo.exists():
        make_demo = root / "examples" / "make_demo_repo.py"
        if make_demo.exists():
            import subprocess
            subprocess.run([sys.executable, str(make_demo), str(demo_repo)], check=False)

    token = "ctxly-cloud-run-token"
    handler_class = make_cloud_handler(token, port, str(demo_repo.resolve()) if demo_repo.exists() else "")
    
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_class)
    print(f"🚀 ContextPilot AI running on Google Cloud Run: http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run_cloud_server()
