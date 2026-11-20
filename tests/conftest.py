import os
import subprocess
from pathlib import Path

import pytest

FILES = {
    "src/auth/auth_middleware.py": '''
from src.auth.jwt_handler import verify_token


class AuthMiddleware:
    """Validates the bearer token on every request."""

    def __init__(self, app):
        self.app = app

    def __call__(self, request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not verify_token(token):
            return {"status": 401, "error": "unauthorized"}
        return self.app(request)
''',
    "src/auth/jwt_handler.py": '''
import time

SECRET_KEY = "s3cr3tK3yValue_9f8A7b6C5d4E3f2G1h"


def verify_token(token):
    payload = decode(token)
    # BUG: exp is in seconds, time.time() * 1000 is ms
    return payload is not None and payload["exp"] > time.time() * 1000


def decode(token):
    return {"exp": 0} if token else None


def refresh_token(token):
    return token
''',
    "src/auth/token_service.py": "class TokenService:\n    def issue(self, user):\n        return 'tok'\n",
    "src/cart/cart.py": "class Cart:\n    def add_item(self, sku, qty):\n        pass\n\n    def total(self):\n        return 0\n",
    "src/cart/pricing.py": "def apply_discount(total, code):\n    return total\n",
    "src/orders/orders.py": "def create_order(cart):\n    return {'id': 1}\n",
    "src/payments/stripe_client.py": "def charge(amount):\n    return True\n",
    "web/app.ts": "export function renderCheckout(cart: any) {\n  return cart;\n}\nexport class CheckoutView {}\n",
    "tests/test_auth.py": "from src.auth.jwt_handler import verify_token\n\ndef test_verify():\n    assert verify_token('x') is False\n",
    "README.md": "# ecommerce-core\n",
    # fake key, split so code scanners (e.g. GitHub push protection) don't flag it
    ".env": "STRIPE_SECRET_KEY=sk_" "live_abcdefghijklmnopqrstuvwx\nJWT_PRIVATE_KEY=supersecretvalue123\n",
}


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def make_repo(root: Path) -> Path:
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body.lstrip("\n"), encoding="utf-8")
    # Realistic bulk: ~30 unrelated modules so the repo looks like the 42-file demo in the pitch.
    for i, area in enumerate(["catalog", "inventory", "shipping", "search", "reviews", "notifications"] * 5):
        mod = root / "src" / area / f"{area}_{i}.py"
        mod.parent.mkdir(parents=True, exist_ok=True)
        body = "\n\n".join(
            f"def {area}_handler_{i}_{j}(payload):\n    \"\"\"Process {area} payload variant {j}.\"\"\"\n"
            f"    items = [x for x in payload.get('items', []) if x]\n    return {{'count': len(items), 'v': {j}}}"
            for j in range(12)
        )
        mod.write_text(body + "\n", encoding="utf-8")
    (root / ".gitignore").write_text(".env\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    return root


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    root = make_repo(tmp_path / "ecommerce-core")
    monkeypatch.setenv("CTXLY_OFFLINE", "1")
    monkeypatch.delenv("CTXLY_REPO", raising=False)
    monkeypatch.chdir(root)
    from contextpilot import genai_client

    genai_client.reset()
    return root
