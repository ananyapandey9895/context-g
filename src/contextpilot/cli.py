"""`ctxly` command-line interface (Typer + Rich)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import __version__, genai_client
from .config import ensure_gitignored, find_repo_root, load_settings, save_settings
from .store import Store

app = typer.Typer(add_completion=False, no_args_is_help=True, rich_markup_mode="rich",
                  help="[bold]ContextPilot AI (Ctxly)[/] - persistent, surgical, cache-friendly memory for "
                       "Google Antigravity and Gemini CLI.")
cache_app = typer.Typer(help="Vertex AI / Gemini context cache.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")
con = Console(highlight=False)
err = Console(stderr=True, highlight=False)

RepoOpt = typer.Option(None, "--repo", "-C", help="Repository root (default: auto-detect).")


def _root(repo: Optional[Path]) -> Path:
    return find_repo_root(repo)


# ------------------------------------------------------------------------------------------------ setup
@app.command()
def init(repo: Optional[Path] = RepoOpt) -> None:
    """Create .ctxly/ (SQLite WAL memory + config), gitignore it and build the symbol index."""
    from .symbols import build_index

    root = _root(repo)
    with Store(root) as store:
        st = build_index(root, store)
    cfg = root / ".ctxly" / "config.json"
    if not cfg.exists():
        save_settings(root, load_settings(root))
    ensure_gitignored(root)
    con.print(f"[green]✓[/] ContextPilot initialised in [bold]{root}[/]")
    con.print(f"  indexed {st.files} files (~{st.total_tokens:,} tokens est.), parsed {st.parsed}, reused {st.reused}")
    con.print("  next: [cyan]ctxly install all[/]  ·  [cyan]ctxly run \"<task>\"[/]")


@app.command()
def install(
    target: str = typer.Argument("all", help="antigravity | gemini | all"),
    repo: Optional[Path] = RepoOpt,
    global_mcp: bool = typer.Option(False, "--global-mcp", help="Also register the MCP server in "
                                    "~/.gemini/antigravity/mcp_config.json (Antigravity)."),
    no_hooks: bool = typer.Option(False, "--no-hooks", help="Gemini CLI: skip SessionStart/End/PreCompress hooks."),
    skills_dir: Optional[Path] = typer.Option(None, help="Override skill location (default .agents/skills/contextpilot)."),
) -> None:
    """Install the Agent Skill + integrations for Google Antigravity and/or Gemini CLI."""
    from .installers import install_antigravity, install_gemini

    root = _root(repo)
    target = target.lower()
    if target not in ("antigravity", "gemini", "all"):
        raise typer.BadParameter("target must be antigravity, gemini or all")
    ensure_gitignored(root)
    written: list[Path] = []
    if target in ("antigravity", "all"):
        written += install_antigravity(root, global_mcp=global_mcp, skills_dir=skills_dir)
    if target in ("gemini", "all"):
        written += install_gemini(root, hooks=not no_hooks, skills_dir=skills_dir)
    for p in dict.fromkeys(written):
        try:
            shown = p.relative_to(root)
        except ValueError:
            shown = p
        con.print(f"  [green]+[/] {shown}")
    con.print(f"[green]✓[/] installed for [bold]{target}[/] in {root}")
    if target in ("gemini", "all"):
        con.print("  Gemini CLI: restart `gemini`, then try [cyan]/ctxly:resume[/], [cyan]/ctxly:run <task>[/], "
                  "[cyan]/mcp[/]. Wrap it for Ctrl+C capture: [cyan]ctxly wrap gemini[/]")
    if target in ("antigravity", "all"):
        con.print("  Antigravity: open this folder; the [bold]contextpilot[/] skill, rule and "
                  "[cyan]/ctxly-run[/] [cyan]/ctxly-resume[/] workflows load automatically. "
                  "Run [cyan]ctxly watch[/] in a terminal for autosave.")


@app.command()
def uninstall(repo: Optional[Path] = RepoOpt) -> None:
    """Remove the skill, workflows, commands, MCP entry and hooks from this repo (keeps .ctxly memory)."""
    from .installers import uninstall as _un

    root = _root(repo)
    for p in _un(root):
        con.print(f"  [red]-[/] {p}")
    con.print("[green]✓[/] uninstalled")


@app.command()
def doctor(repo: Optional[Path] = RepoOpt) -> None:
    """Check environment: git, SDKs, credentials, Gemini CLI / Antigravity, installed integrations."""
    from . import gitctx

    root = _root(repo)
    t = Table(show_header=False, box=None)

    def row(ok: bool | None, name: str, detail: str = "") -> None:
        mark = "[green]✓[/]" if ok else ("[yellow]•[/]" if ok is None else "[red]✗[/]")
        t.add_row(mark, name, detail)

    row(gitctx.is_repo(root), "git repository", str(root))
    for mod, label in (("google.genai", "google-genai SDK"), ("mcp", "mcp (FastMCP)")):
        try:
            __import__(mod)
            row(True, label)
        except ImportError:
            row(None, label, "optional - pip install 'contextpilot-ai[all]'")
    row(genai_client.configured() or None, "Gemini credentials",
        genai_client.backend() if genai_client.configured() else "offline: local compiler is used")
    row(bool(shutil.which("gemini")) or None, "Gemini CLI", shutil.which("gemini") or "not on PATH")
    row(bool(shutil.which("agy")) or None, "Antigravity launcher (agy)", shutil.which("agy") or "not on PATH")
    row(bool(shutil.which("ctxly")) or None, "ctxly on PATH", shutil.which("ctxly") or f"using {sys.executable} -m contextpilot")
    row((root / ".agents/skills/contextpilot/SKILL.md").exists() or None, "Agent Skill installed")
    row((root / ".gemini/settings.json").exists() or None, "Gemini CLI settings")
    row((root / ".agent/workflows/ctxly-run.md").exists() or None, "Antigravity workflows")
    con.print(t)


# ------------------------------------------------------------------------------------------------ core
def _telemetry_table(s: dict) -> Table:
    t = Table(title="Token telemetry", show_header=True, header_style="bold")
    t.add_column("metric")
    t.add_column("value", justify="right")
    t.add_column("source", style="dim")
    te = s["tokens_estimate"]
    t.add_row("naive context (read every file)", f"{te['naive']:,}", "local estimate")
    t.add_row("ContextPilot context", f"{te['optimized']:,}", "local estimate")
    t.add_row("reduction", f"[bold green]{te['reduction_pct']}%[/]", "local estimate")
    if s.get("billed_usage") and s["billed_usage"].get("prompt_tokens") is not None:
        bu = s["billed_usage"]
        t.add_row("compiler prompt tokens", f"{bu['prompt_tokens']:,}", "billed (usage_metadata)")
        t.add_row("cached tokens", f"{bu.get('cached_tokens') or 0:,}", "billed (cachedContentTokenCount)")
    if s.get("cache"):
        t.add_row("context cache", s["cache"]["status"], s["cache"]["message"][:60])
    t.add_row("compiler", s["compiler"], f"{s['latency_ms']} ms")
    return t


def _files_table(s: dict) -> Table:
    t = Table(title=f"Surgical scope ({len(s['files'])} of {s['considered_files']} files)", header_style="bold")
    t.add_column("file")
    t.add_column("lines")
    t.add_column("score", justify="right")
    t.add_column("why", style="dim")
    for f in s["files"]:
        t.add_row(f["path"], "-".join(map(str, f["lines"])) if f["lines"] else "all", str(f["score"]),
                  "; ".join(f["reasons"]))
    return t


@app.command()
def optimize(
    task: str = typer.Argument(..., help="The raw request."),
    repo: Optional[Path] = RepoOpt,
    plain: bool = typer.Option(False, "--plain", help="Print only the compiled prompt (for agents/scripts)."),
    as_json: bool = typer.Option(False, "--json", help="Print prompt + telemetry as JSON."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Use the local compiler only."),
) -> None:
    """Compile a vague request into a lean, grounded prompt scoped to 2-4 files."""
    from .engine import prepare

    p = prepare(_root(repo), task, use_llm=not no_llm)
    s = p.summary()
    if as_json:
        print(json.dumps({"prompt": p.compiled.text, **s}, indent=1, default=str))
    elif plain:
        print(p.compiled.text)
        te = s["tokens_estimate"]
        print(f"<!-- ctxly: {len(s['files'])}/{s['considered_files']} files, est. {te['naive']}→{te['optimized']} "
              f"tokens (-{te['reduction_pct']}%), compiler={s['compiler']} -->")
    else:
        con.print(_files_table(s))
        con.print(Panel(Markdown(p.compiled.text), title="Compiled prompt", border_style="cyan"))
        con.print(_telemetry_table(s))
        if s["compiler_error"]:
            err.print(f"[yellow]Gemini compiler unavailable, used local compiler:[/] {s['compiler_error']}")


@app.command("select")
def select_cmd(task: str, repo: Optional[Path] = RepoOpt) -> None:
    """Show which files ContextPilot would scope for a task (no prompt compilation)."""
    from .selector import select_files
    from .symbols import build_index

    root = _root(repo)
    s = load_settings(root)
    with Store(root) as store:
        build_index(root, store)
        sel = select_files(root, store, task, s.min_files, s.max_files)
    con.print(_files_table({"files": [{"path": c.path, "lines": c.lines, "score": round(c.score, 1),
                                       "reasons": c.reasons[:3]} for c in sel.files],
                            "considered_files": sel.considered}))


def _edit_text(path: Path) -> None:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")
    subprocess.call([*editor.split(), str(path)])


def _copy_clipboard(text: str) -> bool:
    for cmd in (["clip"], ["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, check=True, encoding="utf-16" if cmd == ["clip"] else None)
                return True
            except (OSError, subprocess.CalledProcessError):
                continue
    return False


@app.command()
def run(
    task: str = typer.Argument(..., help="Natural-language task, e.g. \"Fix the 401 in JWT auth middleware\"."),
    repo: Optional[Path] = RepoOpt,
    to: str = typer.Option("auto", "--to", help="Dispatch target: gemini | antigravity | print | auto."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the human gate (send immediately)."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Use the local compiler only."),
) -> None:
    """Full pipeline: scrub → triage → AST/Git scope → Gemini Flash compile → cache → human gate (send/edit) → dispatch."""
    from .engine import prepare
    from .signals import wrap

    root = _root(repo)
    s_cfg = load_settings(root)
    p = prepare(root, task, use_llm=not no_llm, tool=to, new_session=True)
    s = p.summary()
    con.print(f"[bold]ContextPilot[/] v{__version__} · session [cyan]{p.session_id}[/] · "
              f"task [magenta]{p.triage['label']}[/] ({p.triage['source']})")
    if p.compiled.redactions:
        con.print(f"[yellow]🛡 redacted {p.compiled.redactions} secret(s) from the task text[/]")
    con.print(_files_table(s))
    con.print(Panel(Markdown(p.compiled.text), title="Compiled prompt", border_style="cyan"))
    con.print(_telemetry_table(s))
    if s["compiler_error"]:
        err.print(f"[yellow]Gemini compiler unavailable, used local compiler:[/] {s['compiler_error']}")

    text = p.compiled.text
    if not yes:
        while True:
            choice = typer.prompt("[s]end / [e]dit / [c]ancel", default="s").strip().lower()[:1]
            if choice == "e":
                _edit_text(p.prompt_path)
                text = p.prompt_path.read_text(encoding="utf-8")
                con.print(Panel(Markdown(text), title="Edited prompt", border_style="cyan"))
                continue
            if choice == "c":
                con.print(f"cancelled - prompt saved at {p.prompt_path}")
                raise typer.Exit(0)
            if choice == "s":
                break

    target = to
    if target == "auto":
        target = "gemini" if shutil.which("gemini") else "print"
    if target == "gemini":
        if not shutil.which("gemini"):
            err.print("[red]gemini not found on PATH[/] (npm i -g @google/gemini-cli)")
            raise typer.Exit(1)
        raise typer.Exit(wrap(root, [shutil.which("gemini"), "--prompt-interactive", text], tool="gemini",
                              autosave_seconds=s_cfg.autosave_seconds))
    if target == "antigravity":
        copied = _copy_clipboard(text)
        con.print(f"[green]✓[/] prompt {'copied to clipboard' if copied else 'saved'}: {p.prompt_path}")
        con.print("  Paste it into the Antigravity agent panel (or run the [cyan]/ctxly-run[/] workflow).")
        if shutil.which("agy"):
            subprocess.Popen(["agy", str(root)])
        return
    print(text)


@app.command()
def checkpoint(
    repo: Optional[Path] = RepoOpt,
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="What you learned so far."),
    next_steps: Optional[str] = typer.Option(None, "--next", help="What to do next."),
    reason: str = typer.Option("manual", "--reason"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Save in-flight state now (touched files, scrubbed diff, notes, next steps)."""
    from .checkpoint import create_checkpoint

    root = _root(repo)
    with Store(root) as store:
        r = create_checkpoint(root, store, reason=reason, notes=notes or None, next_steps=next_steps or None)
    if not quiet:
        print(f"✓ checkpoint #{r['id']} ({r['session_id']}) saved in {r['elapsed_ms']} ms · "
              f"{len(r['touched_files'])} touched file(s)")


@app.command()
def note(text: str, repo: Optional[Path] = RepoOpt) -> None:
    """Append a finding to the active session's memory."""
    from .scrubber import scrub

    root = _root(repo)
    with Store(root) as store:
        sid = store.ensure_session()
        store.add_note(sid, scrub(text).text)
    print("✓ noted")


@app.command()
def resume(
    repo: Optional[Path] = RepoOpt,
    fmt: str = typer.Option("full", "--format", "-f", help="bundle (≈180 tokens) | full | json"),
    session: Optional[str] = typer.Option(None, "--session", "-s"),
    launch: bool = typer.Option(False, "--launch", help="Relaunch the agent (gemini --resume) inside the signal trap."),
) -> None:
    """Restore the last session: print it for an agent, or relaunch Gemini CLI with it."""
    from .checkpoint import full_resume, recovery_bundle, resume_json
    from .signals import wrap

    root = _root(repo)
    s_cfg = load_settings(root)
    with Store(root) as store:
        if fmt == "json":
            print(json.dumps(resume_json(root, store, session), indent=1, default=str))
        elif fmt == "bundle":
            print(recovery_bundle(root, store, s_cfg.recovery_bundle_tokens, session) or "No session found.")
        else:
            print(full_resume(root, store, session))
        sess = store.get_session(session) if session else store.active_session()
    if launch:
        if not shutil.which("gemini"):
            err.print("[red]gemini not found on PATH[/]")
            raise typer.Exit(1)
        ext = sess["external_session_id"] if sess else None
        raise typer.Exit(wrap(root, [shutil.which("gemini"), "--resume", ext or "latest"], tool="gemini",
                              autosave_seconds=s_cfg.autosave_seconds))


@app.command()
def status(repo: Optional[Path] = RepoOpt, as_json: bool = typer.Option(False, "--json")) -> None:
    """Session status + transparent token math (local estimates vs billed usage)."""
    from .report import status_dict

    root = _root(repo)
    with Store(root) as store:
        d = status_dict(root, store)
    if as_json:
        print(json.dumps(d, indent=1, default=str))
        return
    sess = d["session"] or {}
    con.print(f"[bold]{d['repo']}[/]  branch [cyan]{d['branch']}[/]@{d['head']}  backend: {d['backend']}")
    con.print(f"session [cyan]{sess.get('id', '-')}[/]  task: {sess.get('task') or '-'}  (updated {sess.get('updated', '-')})")
    if d["last_checkpoint"]:
        ck = d["last_checkpoint"]
        con.print(f"last checkpoint #{ck['id']} ({ck['reason']}, {ck['elapsed_ms']} ms) · touched: "
                  f"{', '.join(ck['touched_files']) or '-'}")
        if ck["next_steps"]:
            con.print(f"next: {ck['next_steps']}")
    e, b, c = d["estimates_local"], d["billed_usage"], d["checkpoint_latency_ms"]
    t = Table(header_style="bold")
    for col in ("", "value", "source"):
        t.add_column(col)
    t.add_row("optimizations", str(e["optimizations"]), "local")
    t.add_row("naive → optimized tokens", f"{e['naive_tokens']:,} → {e['optimized_tokens']:,}", "local estimate")
    t.add_row("reduction", f"{e['reduction_pct']}%" if e["reduction_pct"] is not None else "-", "local estimate")
    t.add_row("billed compiler calls", str(b["calls"]), "Gemini usage_metadata")
    t.add_row("billed prompt / cached tokens", f"{b['prompt_tokens']:,} / {b['cached_tokens']:,}", "Gemini usage_metadata")
    t.add_row("checkpoint latency p50 / max", f"{c['p50']} / {c['max']} ms", f"{c['count']} checkpoints")
    con.print(t)


@app.command()
def sessions(repo: Optional[Path] = RepoOpt, limit: int = 15) -> None:
    """List recent sessions."""
    import time as _t

    root = _root(repo)
    with Store(root) as store:
        rows = store.list_sessions(limit)
    t = Table(header_style="bold")
    for c in ("id", "updated", "tool", "checkpoints", "task"):
        t.add_column(c)
    for r in rows:
        t.add_row(r["id"], _t.strftime("%m-%d %H:%M", _t.localtime(r["updated_at"])), r["tool"] or "-",
                  str(r["n_ckpt"]), (r["task"] or "")[:60])
    con.print(t)


@app.command()
def scrub(
    path: Optional[Path] = typer.Argument(None, help="File or directory to scan (default: stdin)."),
    apply: bool = typer.Option(False, "--print", help="Print the redacted text (single file / stdin)."),
) -> None:
    """Scan text/files for secrets locally (regex + Shannon entropy). Nothing leaves the machine."""
    from .scrubber import is_sensitive_path, scrub as _scrub
    from .symbols import list_files, read_text

    def report(label: str, text: str) -> int:
        r = _scrub(text)
        for f in r.findings:
            con.print(f"  [red]![/] {label}:{f.line}  {f.kind}  {f.preview}")
        if apply:
            print(r.text)
        return r.redacted

    total = 0
    if path is None:
        total = report("<stdin>", sys.stdin.read())
    elif path.is_dir():
        root = path.resolve()
        for rel in list_files(root):
            txt = read_text(root, rel)
            if txt:
                total += report(rel, txt)
        con.print(f"[dim]sensitive files are excluded from all ContextPilot memory (e.g. .env, *.pem)[/]")
    else:
        if is_sensitive_path(path):
            con.print(f"[yellow]{path} is a sensitive file - ContextPilot never reads it into memory.[/]")
        total = report(str(path), path.read_text(encoding="utf-8", errors="replace"))
    con.print(f"{'[red]' if total else '[green]'}{total} finding(s)[/]")


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def wrap(
    ctx: typer.Context,
    repo: Optional[Path] = RepoOpt,
    autosave: Optional[int] = typer.Option(None, help="Autosave interval in seconds (0 = off)."),
) -> None:
    """Run an agent CLI inside the Ctrl+C trap, e.g. `ctxly wrap gemini` or `ctxly wrap -- gemini -m gemini-2.5-pro`."""
    from .signals import wrap as _wrap

    cmd = [a for a in ctx.args if a != "--"]
    if not cmd:
        raise typer.BadParameter("give a command to run, e.g. ctxly wrap gemini")
    root = _root(repo)
    s = load_settings(root)
    exe = shutil.which(cmd[0])
    if exe:
        cmd[0] = exe
    raise typer.Exit(_wrap(root, cmd, tool=Path(cmd[0]).stem.lower(),
                           autosave_seconds=s.autosave_seconds if autosave is None else autosave))


@app.command()
def watch(repo: Optional[Path] = RepoOpt, interval: float = 5.0, debounce: float = 10.0) -> None:
    """IDE autosave (Antigravity editor): checkpoint whenever the working tree changes and settles."""
    from .signals import watch as _watch

    _watch(_root(repo), interval, debounce)


@app.command("sync-memory")
def sync_memory(
    repo: Optional[Path] = RepoOpt,
    hierarchical: bool = typer.Option(False, "--hierarchical", help="Also write per-directory GEMINI.md files."),
) -> None:
    """Refresh the managed ContextPilot block in GEMINI.md and the Antigravity memory rule."""
    from .memory_sync import sync_antigravity_rule, sync_gemini_md
    from .symbols import build_index

    root = _root(repo)
    with Store(root) as store:
        build_index(root, store)
        paths = sync_gemini_md(root, store, hierarchical)
        paths.append(sync_antigravity_rule(root, store))
    for p in paths:
        con.print(f"  [green]✓[/] {p.relative_to(root)}")


@app.command()
def index(repo: Optional[Path] = RepoOpt) -> None:
    """(Re)build the incremental AST symbol index. Unchanged files are skipped by SHA-256."""
    from .symbols import build_index

    root = _root(repo)
    with Store(root) as store:
        st = build_index(root, store)
    con.print(f"indexed {st.files} files: parsed {st.parsed}, reused {st.reused}, removed {st.removed}, "
              f"~{st.total_tokens:,} tokens est.")


@cache_app.command("warm")
def cache_warm(repo: Optional[Path] = RepoOpt) -> None:
    """Create (or reuse) the CachedContent for the stable project prefix."""
    from .cache import ensure_cache
    from .symbols import build_index

    root = _root(repo)
    s = load_settings(root)
    with Store(root) as store:
        build_index(root, store)
        r = ensure_cache(root, store, s.cache_model, s.cache_ttl_seconds, s.extra.get("cache_min_tokens"))
    con.print(f"cache [bold]{r.status}[/] · prefix {r.prefix_hash} · ~{r.token_count:,} tokens · {r.message}")
    if r.cache_name:
        con.print(f"name: {r.cache_name}")


@cache_app.command("status")
def cache_status(repo: Optional[Path] = RepoOpt, turns: int = typer.Option(20, help="Turns per session to model.")) -> None:
    """Show prefix stability and the modelled saving from caching it."""
    from .cache import build_prefix, prefix_hash, savings_estimate
    from .symbols import build_index
    from .tokens import estimate

    root = _root(repo)
    with Store(root) as store:
        build_index(root, store)
        prefix = build_prefix(root, store)
        row = store.get_cache(prefix_hash(prefix))
    n = estimate(prefix)
    sv = savings_estimate(n, turns)
    con.print(f"prefix hash {prefix_hash(prefix)} · ~{n:,} tokens (estimate)")
    con.print(f"explicit cache: {row['cache_name'] if row else 'none for this prefix'}")
    con.print(f"modelled over {turns} turns: {sv['uncached']:,} → {sv['cached']:,} input-token units "
              f"([green]-{sv['saved_pct']}%[/], cached tokens billed at 10%)")


@app.command()
def ui(
    repo: Optional[Path] = typer.Option(None, "--repo", "-C", help="Open this project directly."),
    port: int = typer.Option(8765, help="Local port (next free port is used if busy)."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open the browser automatically."),
    shortcut: bool = typer.Option(False, "--shortcut", help="Create a double-click 'ContextPilot' launcher on the desktop."),
) -> None:
    """Open the friendly local web app (http://127.0.0.1:8765) - no terminal knowledge needed."""
    from .webui import create_shortcut, serve

    if shortcut:
        con.print(f"[green]✓[/] desktop launcher created: {create_shortcut()}")
    serve(port=port, open_browser=not no_browser, repo=repo.resolve() if repo else None)


@app.command()
def launch(
    target: str = typer.Argument("gemini", help="gemini"),
    repo: Optional[Path] = RepoOpt,
) -> None:
    """Start Gemini CLI with the last prepared prompt, inside the Ctrl+C trap (used by the web app)."""
    from .signals import wrap as _wrap

    root = _root(repo)
    prompt_file = root / ".ctxly" / "last_prompt.md"
    if not prompt_file.exists():
        err.print("No prepared prompt yet - run `ctxly optimize \"<task>\"` or use `ctxly ui`.")
        raise typer.Exit(1)
    if target != "gemini":
        raise typer.BadParameter("only 'gemini' can be launched from the terminal")
    exe = shutil.which("gemini")
    if not exe:
        err.print("[red]gemini not found on PATH[/] (npm i -g @google/gemini-cli)")
        raise typer.Exit(1)
    s = load_settings(root)
    raise typer.Exit(_wrap(root, [exe, "--prompt-interactive", prompt_file.read_text(encoding="utf-8")],
                           tool="gemini", autosave_seconds=s.autosave_seconds))


@app.command()
def mcp() -> None:
    """Run the FastMCP server on stdio (used by Gemini CLI and Antigravity)."""
    try:
        from .mcp_server import serve
    except ImportError:
        err.print("pip install 'contextpilot-ai[mcp]'")
        raise typer.Exit(1)
    serve()


# ------------------------------------------------------------------------------------------------ v0.2 features
team_app = typer.Typer(help="Team memory: share what you're working on via .ctxly-team/.", no_args_is_help=True)
app.add_typer(team_app, name="team")


@app.command("verify")
def verify_cmd(repo: Optional[Path] = RepoOpt,
               command: Optional[str] = typer.Option(None, "--command", help="Test command (default: auto-detect)."),
               copy: bool = typer.Option(False, "--copy", help="Copy the fix-it request to the clipboard on failure.")) -> None:
    """Run the project's tests; on failure build a fix-it request with the same scope + the error."""
    from . import verify

    root = _root(repo)
    with Store(root) as st:
        r = verify.run(root, st, command=command or load_settings(root).extra.get("test_command") or None)
        if not r.get("ran"):
            con.print(f"[yellow]{r.get('message')}[/]")
            raise typer.Exit(2)
        icon = "[green]✓ passed[/]" if r["passed"] else "[yellow]? no tests ran[/]" if r["passed"] is None else "[red]✗ failed[/]"
        con.print(f"{icon} · {r['summary']} · {r['duration_ms'] / 1000:.1f}s · {r['command']}")
        if r["passed"] is False:
            fp = verify.fix_prompt(root, st, r)
            (root / ".ctxly" / "last_prompt.md").write_text(fp, encoding="utf-8")
            con.print("Fix-it request written to [bold].ctxly/last_prompt.md[/]"
                      + (" and copied." if copy and _copy_clipboard(fp) else "."))
            raise typer.Exit(1)


@app.command()
def explain(repo: Optional[Path] = RepoOpt, smart: bool = typer.Option(True, "--smart/--offline"),
            diff: bool = typer.Option(False, "--diff", help="Also print the full diff.")) -> None:
    """Plain-English summary of what changed since the last undo point."""
    from .explain import explain_changes

    root = _root(repo)
    with Store(root) as st:
        r = explain_changes(root, st, load_settings(root).optimizer_model, use_llm=smart)
    if not r.get("found") or not r.get("changed"):
        con.print(r.get("message", "No changes."))
        return
    t = r["totals"]
    con.print(f"[bold]{t['files']} file(s) changed, +{t['added']}/-{t['removed']}[/] ({r['source']})")
    for b in r["bullets"]:
        con.print(Markdown("- " + b))
    if diff:
        print(r["diff"])


@app.command()
def undo(repo: Optional[Path] = RepoOpt,
         to: Optional[int] = typer.Option(None, "--to", help="Checkpoint id from `ctxly timeline` (default: last undo point)."),
         yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation.")) -> None:
    """Put your files back to the last undo point (or any checkpoint). A safety snapshot is taken first."""
    from .webapi2 import api_undo

    root = _root(repo)
    if not yes and not typer.confirm("Restore files? (a safety snapshot is taken first)"):
        raise typer.Exit(1)
    r = api_undo({"repo": str(root), **({"checkpoint_id": to} if to else {})})
    con.print(f"[green]↩ {r['message']}[/]")
    if r["removed"]:
        con.print("Removed new files: " + ", ".join(r["removed"]))


@app.command()
def timeline(repo: Optional[Path] = RepoOpt, limit: int = 30) -> None:
    """Every saved moment, newest first. Restore one with `ctxly undo --to <id>`."""
    import time as _t

    from .webapi2 import api_timeline

    root = _root(repo)
    items = api_timeline({"repo": [str(root)], "limit": [str(limit)]})["items"]
    tb = Table("id", "when", "reason", "files", "restorable", "notes")
    for it in items:
        tb.add_row(str(it["id"]), _t.strftime("%d %b %H:%M", _t.localtime(it["created_at"])), it["reason"],
                   ", ".join(f["path"] for f in it["files"][:3]) + (" …" if len(it["files"]) > 3 else ""),
                   "yes" if it["can_restore"] else "-", (it["notes"] or "")[:50])
    con.print(tb)


@app.command()
def savings(repo: Optional[Path] = RepoOpt,
            report: Optional[Path] = typer.Option(None, "--report", help="Write a printable HTML report here."),
            send: bool = typer.Option(False, "--send", help="Post the report to the Slack/Google Chat webhook.")) -> None:
    """Money saved (estimate) and actually billed, plus test results."""
    from .webapi2 import api_notify, api_savings, render_report

    root = _root(repo)
    d = api_savings({"repo": [str(root)]})
    tb = Table("", "saved (est.)", "requests", "billed by Gemini")
    for k in ("today", "week", "all"):
        s = d[k]
        tb.add_row(k, s["saved"], str(s["requests"]), f"{s['billed']} ({s['billed_calls']} calls)")
    con.print(tb)
    b = d.get("budget")
    if b and b.get("budget_usd"):
        con.print(f"Session budget: {b['spent']} of {b['budget']} ({b['pct']}%)")
    if report:
        report.write_text(render_report(root), encoding="utf-8")
        con.print(f"Report written to {report} - open it and use Print → Save as PDF.")
    if send:
        con.print(api_notify({"repo": str(root)})["message"])


@app.command()
def budget(usd: Optional[float] = typer.Argument(None, help="Limit per session in USD; 0 removes it."),
           repo: Optional[Path] = RepoOpt) -> None:
    """Show or set the per-session spending limit (Gemini CLI pauses and checkpoints when it's reached)."""
    from .webapi2 import api_savings, api_settings

    root = _root(repo)
    if usd is not None:
        api_settings({"repo": str(root), "session_budget_usd": usd})
    b = api_savings({"repo": [str(root)]}).get("budget")
    s = load_settings(root).extra.get("session_budget_usd")
    con.print(f"Limit: {'$%.2f' % s if s else 'none'}" + (f" · this session: {b['spent']}" if b else ""))


@team_app.command("share")
def team_share(name: Optional[str] = typer.Option(None, "--name", help="Your name (default: git user.name)."),
               repo: Optional[Path] = RepoOpt) -> None:
    """Share your current task/notes/files (never code or secrets) in .ctxly-team/."""
    from .team import share

    root = _root(repo)
    with Store(root) as st:
        p = share(root, st, name)
    con.print(f"✓ Shared → {p.relative_to(root).as_posix()} (commit it so teammates see it)")


@team_app.command("feed")
def team_feed(repo: Optional[Path] = RepoOpt) -> None:
    """What everyone is working on."""
    from .team import feed

    for e in feed(_root(repo)):
        con.print(f"[bold]{e.get('member')}[/] · {e.get('task') or '-'}")
        if e.get("next_steps"):
            con.print(f"   next: {e['next_steps']}")


@app.command("map")
def map_cmd(repo: Optional[Path] = RepoOpt, depth: int = 2) -> None:
    """Plain-English map of the project."""
    from rich.tree import Tree

    from .projectmap import build_map
    from .symbols import build_index

    root = _root(repo)
    with Store(root) as st:
        build_index(root, st)
        m = build_map(root, st)

    def add(node, branch, level):
        for c in node.get("children", []):
            label = c["name"] + (f" [dim]· {c['about']}[/]" if c.get("about") else "") + f" [dim]({c['tokens']} tok)[/]"
            sub = branch.add(label)
            if c["kind"] == "dir" and level < depth:
                add(c, sub, level + 1)

    t = Tree(f"[bold]{root.name}[/] · {m['stats']['files']} files")
    add(m["tree"], t, 1)
    con.print(t)


@app.command()
def gemma(pull: bool = typer.Option(False, "--pull", help="Download gemma3:4b via Ollama and use it for triage."),
          repo: Optional[Path] = RepoOpt) -> None:
    """Local Gemma (via Ollama) for free, on-device task triage."""
    import time as _t

    from .gemma import DEFAULT_MODEL, ollama_status, start_pull

    s = ollama_status()
    if not s["installed"] or not s["running"]:
        con.print("Ollama isn't " + ("running" if s["installed"] else "installed") + " - get it at https://ollama.com/download")
        raise typer.Exit(1)
    if pull and not s["gemma_models"]:
        start_pull(DEFAULT_MODEL)
        while (p := ollama_status()["pull"])["state"] == "pulling":
            con.print(f"\r{p['message']}", end="")
            _t.sleep(2)
        con.print()
        s = ollama_status()
    if s["gemma_models"]:
        root = _root(repo)
        st = load_settings(root)
        if pull or not st.gemma_model:
            st.gemma_model = s["gemma_models"][0]
            save_settings(root, st)
        con.print(f"Gemma models: {', '.join(s['gemma_models'])} · triage uses: {st.gemma_model or 'heuristics'}")
    else:
        con.print("No Gemma model yet. Run `ctxly gemma --pull`.")


@app.command(hidden=True)
def hook(event: str) -> None:
    """Gemini CLI hook entry point: session-start | session-end | pre-compress."""
    from .hooks import run_hook

    raise typer.Exit(run_hook(event))


@app.command()
def version() -> None:
    """Print the version."""
    print(f"ContextPilot AI (ctxly) {__version__}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
        except (AttributeError, ValueError):
            pass
    app()


if __name__ == "__main__":
    main()


# ------------------------------------------------------------------------------------------------ project memory
memory_app = typer.Typer(help="Project memory: decisions, tasks, errors and changes kept across sessions.",
                         no_args_is_help=True)
app.add_typer(memory_app, name="memory")


@memory_app.command("list")
def memory_list(kind: Optional[str] = typer.Option(None, help="decision | task | error | change | fact"),
                status: Optional[str] = typer.Option(None, help="open | done | info"),
                limit: int = 30, repo: Optional[Path] = RepoOpt, as_json: bool = typer.Option(False, "--json")) -> None:
    """Show what ContextPilot remembers about this project."""
    from .memory import list_memories

    with Store(_root(repo)) as st:
        rows = list_memories(st, kind, status, limit=limit)
    if as_json:
        con.print_json(json.dumps(rows))
        return
    t = Table("id", "kind", "status", "memory", "files", box=None)
    for m in rows:
        t.add_row(str(m["id"]), m["kind"], m["status"], m["text"][:90], ", ".join(m["files"][:3]))
    con.print(t if rows else "[dim]No memories yet.[/]")


@memory_app.command("add")
def memory_add(text: str, kind: str = typer.Option("decision", help="decision | task | error | fact"),
               file: List[str] = typer.Option([], "--file", "-f"), repo: Optional[Path] = RepoOpt) -> None:
    """Remember something, e.g. an architectural decision. Secrets are redacted; duplicates are merged."""
    from .memory import remember

    with Store(_root(repo)) as st:
        sess = st.active_session()
        r = remember(st, kind, text, files=file, session_id=sess["id"] if sess else None)
    con.print("[dim]Too short to remember.[/]" if r is None else
              f"[green]✓[/] {'Remembered' if r['created'] else 'Already known - updated'} #{r['id']}")


@memory_app.command("done")
def memory_done(memory_id: int, repo: Optional[Path] = RepoOpt) -> None:
    """Mark a task or error as done / resolved."""
    from .memory import set_status

    with Store(_root(repo)) as st:
        con.print("[green]✓[/] done" if set_status(st, memory_id, "done") else "[red]not found[/]")


@memory_app.command("forget")
def memory_forget(memory_id: int, repo: Optional[Path] = RepoOpt) -> None:
    """Delete one memory."""
    from .memory import forget

    with Store(_root(repo)) as st:
        con.print("[green]✓[/] forgotten" if forget(st, memory_id) else "[red]not found[/]")


@memory_app.command("recall")
def memory_recall(task: str, repo: Optional[Path] = RepoOpt) -> None:
    """Which memories would be given to the AI for this task (and why)."""
    from .memory import recall

    with Store(_root(repo)) as st:
        for m in recall(st, task, limit=8):
            con.print(f"[bold]{m['score']:>5}[/] {m['kind']:<8} {m['text'][:100]}")


@app.command()
def metrics(days: int = typer.Option(30, help="Window in days (0 = all time)."), repo: Optional[Path] = RepoOpt,
            as_json: bool = typer.Option(False, "--json")) -> None:
    """Measured token reduction, cache hits/misses and retrieval latency (no extrapolation)."""
    from .metrics import collect

    with Store(_root(repo)) as st:
        m = collect(st, days or None)
    if as_json:
        con.print_json(json.dumps(m))
        return
    tk, ca, rl, q = m["tokens"], m["cache"], m["retrieval_latency_ms"], m["quality"]
    dash = lambda v: "-" if v is None else v  # noqa: E731
    con.print(Panel.fit(
        f"requests: {m['requests']}\n"
        f"tokens (est.): {tk['original_est']:,} → {tk['optimized_est']:,}  ({dash(tk['reduction_pct'])}% less)\n"
        f"cache: {ca['hits']} hits / {ca['misses']} misses  ({dash(ca['hit_rate_pct'])}% hit rate)\n"
        f"retrieval latency: p50 {dash(rl['p50'])} ms · p95 {dash(rl['p95'])} ms  ({rl['samples']} samples)\n"
        f"constraints kept: {q['constraints_kept']}/{q['constraints_total']} · "
        f"duplicate lines removed: {q['duplicate_lines_removed']}\n"
        f"billed by Google: {m['billed']['calls']} calls, {m['billed']['input_tokens']:,} input tokens "
        f"({m['billed']['cached_tokens']:,} cached)",
        title=f"ContextPilot metrics · last {m['window_days'] or 'all'} days"))
