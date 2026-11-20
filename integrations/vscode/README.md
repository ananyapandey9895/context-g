# ContextPilot side panel (VS Code & Google Antigravity)

Puts the ContextPilot app in your editor's sidebar, so you can prepare focused requests, check the AI's work,
undo it, and see your savings without leaving the editor. Antigravity is built on VS Code, so the same extension works there.

## Install

1. Install ContextPilot itself (`pip install contextpilot-ai`, or use **Start ContextPilot.cmd** once).
2. Install the extension:
   - From the packaged file: **Extensions → ⋯ → Install from VSIX…** and pick `contextpilot-0.2.0.vsix`.
   - Or build it: `npx @vscode/vsce package` in this folder.
3. Click the ContextPilot icon in the activity bar.

The panel starts `ctxly ui` for your open folder if it isn't running yet. If `ctxly` isn't on your PATH,
set **Settings → ContextPilot: Command** to its full path (for example `C:\path\to\.venv\Scripts\ctxly.exe`).

## Commands

- **ContextPilot: Open in editor tab** - the full app in a wide tab.
- **ContextPilot: Ask AI about selection** - right-click selected code; it's pre-filled as a request.
- **ContextPilot: Reload panel**

Everything stays on your computer: the panel only talks to `http://127.0.0.1:<port>`.
