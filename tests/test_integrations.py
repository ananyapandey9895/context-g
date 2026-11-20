import io
import json
import subprocess
import sys
import tomllib

from typer.testing import CliRunner

from contextpilot.cli import app
from contextpilot.hooks import run_hook
from contextpilot.installers import install_antigravity, install_gemini, uninstall
from contextpilot.memory_sync import BEGIN, END, upsert_block

runner = CliRunner()


def test_install_antigravity(repo):
    install_antigravity(repo)
    skill = repo / ".agents/skills/contextpilot"
    md = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert md.startswith("---\nname: contextpilot\ndescription:")
    front = md.split("---")[1]
    assert len(front) // 4 < 60  # progressive loading: metadata stays tiny
    for s in ("run_resume.py", "run_optimize.py", "run_checkpoint.py", "_ctxly.py"):
        assert (skill / "scripts" / s).exists()
    assert (repo / ".agent/rules/contextpilot.md").exists()
    assert (repo / ".agent/workflows/ctxly-run.md").exists()


def test_skill_scripts_run(repo):
    install_antigravity(repo)
    scripts = repo / ".agents/skills/contextpilot/scripts"
    out = subprocess.run([sys.executable, str(scripts / "run_optimize.py"), "fix 401 in jwt auth middleware"],
                         cwd=repo, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert "src/auth/" in out.stdout
    out = subprocess.run([sys.executable, str(scripts / "run_checkpoint.py"), "--notes", "found it", "--next", "patch"],
                         cwd=repo, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    out = subprocess.run([sys.executable, str(scripts / "run_resume.py")], cwd=repo, capture_output=True,
                         text=True, encoding="utf-8")
    assert "Next: patch" in out.stdout


def test_install_gemini(repo):
    (repo / ".gemini").mkdir()
    (repo / ".gemini/settings.json").write_text(json.dumps({"theme": "Dracula", "hooks": {"SessionStart": [
        {"hooks": [{"name": "mine", "type": "command", "command": "echo hi"}]}]}}), encoding="utf-8")
    install_gemini(repo)
    install_gemini(repo)  # idempotent
    st = json.loads((repo / ".gemini/settings.json").read_text(encoding="utf-8"))
    assert st["theme"] == "Dracula"
    assert st["mcpServers"]["contextpilot"]["args"][-1] == "mcp"
    names = [h["name"] for g in st["hooks"]["SessionStart"] for h in g["hooks"]]
    assert names == ["mine", "ctxly-session-start"]
    assert "PreCompress" in st["hooks"] and "SessionEnd" in st["hooks"]
    for n in ("resume", "run", "checkpoint", "status"):
        data = tomllib.loads((repo / f".gemini/commands/ctxly/{n}.toml").read_text(encoding="utf-8"))
        assert data["prompt"] and data["description"]
    gm = (repo / "GEMINI.md").read_text(encoding="utf-8")
    assert gm.count("ctxly:begin") == 1 and "@.ctxly/prefix.md" in gm
    uninstall(repo)
    st = json.loads((repo / ".gemini/settings.json").read_text(encoding="utf-8"))
    assert "contextpilot" not in st.get("mcpServers", {})
    assert [h["name"] for g in st["hooks"]["SessionStart"] for h in g["hooks"]] == ["mine"]


def test_upsert_block_preserves_user_content(tmp_path):
    p = tmp_path / "GEMINI.md"
    p.write_text("# My rules\nBe nice.\n", encoding="utf-8")
    upsert_block(p, "one")
    upsert_block(p, "two")
    t = p.read_text(encoding="utf-8")
    assert t.startswith("# My rules\nBe nice.")
    assert "two" in t and "one" not in t and t.count(END) == 1


def test_gemini_hooks(repo, monkeypatch, capsys):
    runner.invoke(app, ["checkpoint", "--notes", "n1", "--next", "do x"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "abc-123", "cwd": str(repo)})))
    run_hook("session-start")
    out = json.loads(capsys.readouterr().out)
    assert "do x" in out["hookSpecificOutput"]["additionalContext"]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "abc-123", "cwd": str(repo)})))
    run_hook("pre-compress")
    r = runner.invoke(app, ["resume", "--format", "json"])
    j = json.loads(r.stdout)
    assert j["external_session_id"] == "abc-123"
    assert j["checkpoint"]["reason"] == "pre-compress"


def test_cli_end_to_end(repo):
    assert runner.invoke(app, ["init"]).exit_code == 0
    r = runner.invoke(app, ["run", "fix the 401 in JWT auth middleware", "--to", "print", "--yes"])
    assert r.exit_code == 0, r.stdout
    assert "src/auth/auth_middleware.py" in r.stdout
    r = runner.invoke(app, ["status", "--json"])
    d = json.loads(r.stdout)
    assert d["estimates_local"]["optimizations"] == 1
    r = runner.invoke(app, ["scrub", str(repo / "src/auth/jwt_handler.py")])
    assert "finding" in r.stdout


def test_mcp_server_tools(repo):
    import asyncio

    from contextpilot.mcp_server import build_server

    srv = build_server()
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert {"ctxly_resume", "ctxly_optimize", "ctxly_checkpoint", "ctxly_select_files", "ctxly_note", "ctxly_status"} <= names
    res = asyncio.run(srv.call_tool("ctxly_optimize", {"task": "fix 401 jwt auth middleware", "repo": str(repo)}))
    text = json.dumps(res, default=str)
    assert "auth_middleware.py" in text
