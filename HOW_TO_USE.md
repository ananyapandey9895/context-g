# How to use Ctxly (ContextPilot)

Everything you need, in plain words. Reading it takes about 5 minutes.
The same guide is inside the app: click **How to use Ctxly** at the bottom of the sidebar.

---

## What it does

AI coding assistants like **Google Antigravity** and **Gemini CLI** are powerful but forgetful. If you stop them or they run
out of memory, they start over. They also often read your whole project, which is slow and costs money.
Ctxly sits next to them and fixes that:

| | |
|---|---|
| **Remembers** | Saves what you were doing, so the AI picks up where it left off. |
| **Focuses** | Points the AI at the 2-4 files that matter, so it reads less and costs less. |
| **Protects** | Hides passwords and keys before anything is saved or sent. |

"Ctxly" is the short name. You'll also see "ContextPilot" and the command `ctxly`. They're all the same thing.

---

## Get started in 5 minutes

1. **Start the app.** Double-click **`Start ContextPilot.cmd`** (Windows) or **`start-contextpilot.command`** (Mac).
   The first time, it installs what it needs (you need Python 3.10 or newer). Your browser opens at
   `http://127.0.0.1:8765`. Keep the black window open while you use the app.
2. **Open your project.** Click **Browse for folder…** and choose the folder that holds your code, or paste its path.
3. **Connect your AI tool.** Click **Connect both** (or just the one you use), then close and reopen your AI tool so it
   notices. You can always do this later in **Setup**.
4. **Ask for something.** On **Ask AI**, describe what you want like you'd tell a colleague, then click
   **Prepare my request**. Click **Copy instructions**, paste them into your AI tool (Ctrl+V, or ⌘V on Mac) and press Enter.
5. **Check the result.** When the AI says it's done, come back and use **What did the AI change?**,
   **Check if it works**, or **Undo AI changes** if you don't like it.

The first time you open a project, the app walks you through steps 2-4 for you. You can skip it at any time.

---

## A normal day with Ctxly

1. **Describe.** Type or say (🎤) what you need. Paste a screenshot of an error if you have one.
2. **Send.** Copy the prepared instructions into your AI, or click *Send to Gemini CLI* / *Open in Antigravity*.
3. **Check.** See what changed, run your tests, and undo if needed. If tests fail, send the "fix-it" request back to the AI.
4. **Save.** Stopping for the day? Write two lines in **Save progress**. Tomorrow it's waiting for you.

**Tips for good requests:** mention the error message, the screen or feature name, and what "fixed" looks like.
*"Users get logged out after 5 minutes on the dashboard, they should stay signed in for a day"* works much better
than *"fix login"*.

---

## If the AI gets interrupted

If you started Gemini CLI from the app, pressing **Ctrl+C** or closing it saves your progress automatically.
If your AI tool is connected (step 3 above), it also saves before its memory fills up.

Next time you open the app, a **Welcome back** card shows what you were doing, what you'd figured out and the next step.
Click **Copy "where I left off" note** and paste it into the AI. It continues without you explaining everything again.

---

## What ContextPilot remembers

Open **Save progress** and scroll to **What ContextPilot remembers**. It keeps four kinds of things for the whole project:

| Kind | Example | How it gets there |
|---|---|---|
| **Decision / rule** | "Prices are stored in cents, never as decimals" | You add it, or your notes say "we decided…" |
| **To-do** | "Fix verify_token, then add a test" | Your "what's next" note. When you write a new one, the old one is marked done |
| **Problem** | "Tests failing: 2 failed" | Added when **Check if it works** fails, cleared when the tests pass again |
| **Change** | "Modified src/auth/jwt_handler.py (+3/-1)" | Saved with your progress |

The AI only gets the items that match what you're asking about, never the whole list. Repeated items are merged, and
passwords are hidden before anything is stored. Click **Done** to tick off a to-do or **×** to forget something.

---

## Going back in time

- Every time you click **Prepare my request**, Ctxly quietly saves a copy of your files: an **undo point**.
  **Undo AI changes** puts your files back to that moment.
- The **Timeline** lists every saved moment. Click **See changes** to look, or **Restore to here** to go back.
- Before going back, Ctxly always saves your current files as a **"Safety copy"** entry on the Timeline, so you can change your mind.
- Going back never touches password files (like `.env`), installed packages, or your team notes.

---

## Every screen, in one line

| Screen | What it's for |
|---|---|
| **Ask AI** | Describe a task, get focused instructions for your AI, then check its work. |
| **Timeline** | Every saved moment. See changes or restore any of them. |
| **Savings** | Money saved, a 14-day chart, a spending limit and a printable report. |
| **Project map** | Your project explained folder by folder. "Ask" about any file. |
| **Team** | Share what you're working on so teammates' AIs know too. |
| **Save progress** | Two lines: what you figured out, and what's next. |
| **Privacy** | See which files are never read and which secrets get hidden. |
| **Setup** | Connect tools, add a Gemini key, money, tests, team chat, local AI. |

---

## Where your data lives

Everything stays **on your computer**. Your saved work (tasks, notes, next steps, the list of changed files, test results,
savings) is kept in a small local **SQLite database** inside your project: **`.ctxly/sessions.db`**.

| Where | What |
|---|---|
| `.ctxly/sessions.db` | Your memory: sessions, saved moments, notes, test results, savings. |
| `.ctxly/config.json` | Your settings for this project. |
| Undo copies | Stored compactly by git, hidden from your normal history. |
| `.ctxly-team/` | Only what you choose to share with your team. |
| `~/.ctxly/credentials.json` | Your Gemini key, only if you ticked "Remember on this computer". |

Nothing is sent anywhere unless you turn on **Gemini smart mode**. In **Setup → What may leave your computer** you choose
**Automatically**, **Ask me first** (you see exactly what would be sent and click Send) or **Never**. Then only the text needed for that feature
(your request, the small parts of code it picked, or a screenshot you added) goes to Google's Gemini, with passwords
and keys hidden first. Password files like `.env`, private keys and credential files are never read at all.

---

## How the money numbers work

AI tools charge by how much text they read. Ctxly compares "the AI reads your whole project" with "the AI reads only the
files Ctxly picked" and turns the difference into money at Google's list prices. These are **estimates**. When smart mode is
on, the Savings page also shows what Google **actually billed** for Ctxly's own Gemini calls.

You can switch between dollars and rupees, and set a **spending limit**: Gemini CLI then pauses and saves your progress
when a session gets too expensive.

---

## Optional extras

| Extra | What it adds |
|---|---|
| **Gemini smart mode** | Free key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey): sharper instructions, reads screenshots, plain-English change summaries. |
| **Local AI (Gemma)** | Sorts requests on your own computer for free, using [Ollama](https://ollama.com/download). |
| **Editor side panel** | This app inside VS Code or Antigravity: see `integrations/vscode/README.md`. |
| **GitHub button** | "ContextPilot it" on any GitHub issue: see `integrations/browser/README.md`. |
| **Team chat** | Post weekly savings and test results to Slack or Google Chat (Setup → Team chat). |
| **Voice** | Click the microphone on Ask AI and just say what you need. |

---

## Common questions

**Do I need to know git or programming?**
No. If your folder doesn't use git yet, Setup → Health check offers a "Turn on" button that sets it up for you. Your files aren't changed.

**Is my code uploaded anywhere?**
Not unless you turn on Gemini smart mode, and even then only the small parts needed, with secrets hidden.

**Does it work without internet?**
Yes. Everything except Gemini smart mode works offline.

**Why is "Send to Gemini CLI" greyed out?**
Gemini CLI isn't installed on this computer. Use **Copy instructions** and paste into any AI chat instead.

**The page says "ContextPilot was restarted".**
Close the tab and open `http://127.0.0.1:8765` again.

**The page won't open.**
Make sure the black ContextPilot window is still open. If you closed it, double-click **Start ContextPilot.cmd** again.
Use the address `http://127.0.0.1:8765`, not a file on your disk.

**I undid something by mistake.**
Open **Timeline**, find the "Safety copy" entry just before it and click **Restore to here**.

**"Check if it works" says no tests were found.**
Your project doesn't have automated tests yet, or uses an unusual command. Ask the AI "write tests for …", or type your
test command in Setup → Tests.

---

## Words you might see

| Word | Meaning |
|---|---|
| **Undo point** | A copy of your files taken just before the AI starts, so you can go back. |
| **Saved moment** | One entry on the Timeline: what you were doing and how your files looked. |
| **Session** | One task from start to finish, possibly over several days. |
| **Tokens** | How AI tools measure text. Roughly 4 letters = 1 token. More tokens means slower and more expensive. |
| **Smart mode** | Optional: uses Google's Gemini to polish requests and explain changes. |
| **Secret** | A password, API key or similar. Ctxly hides these automatically. |
| **Tests** | Small programs that check your code still works. "Check if it works" runs them. |

---

### For developers

Prefer the terminal? Everything in the app is also a command: `ctxly --help`. The full reference is in [README.md](README.md).
