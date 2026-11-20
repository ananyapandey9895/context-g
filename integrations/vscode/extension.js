// ContextPilot side panel for VS Code and Google Antigravity (a VS Code-based editor).
// Shows the local ContextPilot app (http://127.0.0.1:<port>) inside the editor and starts it if needed.
const vscode = require("vscode");
const http = require("http");
const cp = require("child_process");

let server = null; // child process we started (if any)
let views = [];    // live webviews to refresh

function cfg() {
  const c = vscode.workspace.getConfiguration("contextpilot");
  return { command: c.get("command") || "ctxly", port: c.get("port") || 8765 };
}
function workspaceRoot() {
  const f = vscode.workspace.workspaceFolders;
  return f && f.length ? f[0].uri.fsPath : "";
}
function ping(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/", timeout: 1500 }, (res) => {
      let body = "";
      res.on("data", (d) => (body += d));
      res.on("end", () => resolve(res.statusCode === 200 && body.includes("ctxly-token")));
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => { req.destroy(); resolve(false); });
  });
}
async function ensureServer(out) {
  const { command, port } = cfg();
  if (await ping(port)) return true;
  if (!server) {
    const args = ["ui", "--no-browser", "--port", String(port)];
    const root = workspaceRoot();
    if (root) args.push("--repo", root);
    out.appendLine(`Starting: ${command} ${args.join(" ")}`);
    try {
      server = cp.spawn(command, args, { cwd: root || undefined, shell: process.platform === "win32", windowsHide: true });
      server.stdout.on("data", (d) => out.append(d.toString()));
      server.stderr.on("data", (d) => out.append(d.toString()));
      server.on("exit", (code) => { out.appendLine(`ContextPilot exited (${code})`); server = null; });
    } catch (e) {
      out.appendLine(String(e));
      return false;
    }
  }
  for (let i = 0; i < 30; i++) {
    if (await ping(port)) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function html(webview, port, extra) {
  const q = new URLSearchParams();
  const root = workspaceRoot();
  if (root) q.set("repo", root);
  for (const [k, v] of Object.entries(extra || {})) q.set(k, v);
  const src = `http://127.0.0.1:${port}/?${q.toString()}`;
  return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src http://127.0.0.1:${port}; style-src 'unsafe-inline';">
<style>html,body,iframe{margin:0;padding:0;border:0;width:100%;height:100%;overflow:hidden;background:transparent}</style></head>
<body><iframe src="${src}" allow="clipboard-read; clipboard-write; microphone"></iframe></body></html>`;
}
function errorHtml(command) {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  return `<!doctype html><html><body style="font-family:var(--vscode-font-family);padding:12px;color:var(--vscode-foreground)">
<h3>ContextPilot isn't running</h3>
<p>Couldn't start it with <code>${esc(command)}</code>.</p>
<ol><li>Install it: <code>pip install contextpilot-ai</code> (or run <b>Start ContextPilot.cmd</b>).</li>
<li>If <code>ctxly</code> isn't on your PATH, set <b>ContextPilot: Command</b> in Settings to its full path.</li>
<li>Run <b>ContextPilot: Reload panel</b>.</li></ol>
<p>Details are in the <b>ContextPilot</b> output channel.</p></body></html>`;
}

async function fill(webview, out, extra) {
  webview.options = { enableScripts: false };
  webview.html = `<!doctype html><html><body style="font-family:var(--vscode-font-family);padding:12px;color:var(--vscode-foreground)">Starting ContextPilot…</body></html>`;
  const { command, port } = cfg();
  webview.html = (await ensureServer(out)) ? html(webview, port, extra) : errorHtml(command);
}

function activate(context) {
  const out = vscode.window.createOutputChannel("ContextPilot");

  const provider = {
    resolveWebviewView(view) {
      views.push(view.webview);
      view.onDidDispose(() => { views = views.filter((w) => w !== view.webview); });
      fill(view.webview, out);
    },
  };
  context.subscriptions.push(
    out,
    vscode.window.registerWebviewViewProvider("contextpilot.panel", provider, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.commands.registerCommand("contextpilot.open", () => {
      const panel = vscode.window.createWebviewPanel("contextpilot.tab", "ContextPilot", vscode.ViewColumn.Beside, { retainContextWhenHidden: true });
      fill(panel.webview, out);
    }),
    vscode.commands.registerCommand("contextpilot.reload", () => views.forEach((w) => fill(w, out))),
    vscode.commands.registerCommand("contextpilot.askSelection", () => {
      const ed = vscode.window.activeTextEditor;
      if (!ed) return;
      const rel = vscode.workspace.asRelativePath(ed.document.uri);
      const sel = ed.document.getText(ed.selection).slice(0, 4000);
      const line = ed.selection.start.line + 1;
      const task = `About ${rel}:${line}:\n${sel}\n\nWhat I want: `;
      const panel = vscode.window.createWebviewPanel("contextpilot.tab", "ContextPilot", vscode.ViewColumn.Beside, { retainContextWhenHidden: true });
      fill(panel.webview, out, { task });
    }),
  );
}

function deactivate() {
  if (server) { try { server.kill(); } catch (_) { /* ignore */ } }
}

module.exports = { activate, deactivate };
