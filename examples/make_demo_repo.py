"""Create the 'ecommerce-core' demo repository used in the pitch (JWT 401 bug, 42 files, a .env with secrets).

    python examples/make_demo_repo.py ./demo/ecommerce-core
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import make_repo  # noqa: E402

if __name__ == "__main__":
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "demo/ecommerce-core").resolve()
    if dest.exists():
        sys.exit(f"{dest} already exists")
    print(make_repo(dest))
