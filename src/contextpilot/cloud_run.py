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

        def do_HEAD(self):
            self.do_GET()

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
                fn = webui.ROUTES_GET.get(u.path)
                if fn:
                    self._run(fn, parse_qs(u.query))
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

            fn = webui.ROUTES_POST.get(u.path)
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
