# purr: the guide

Everything the [README](../README.md) leaves out.

## Using purr
```
purr                 # full-screen chat in the current folder
purr ~/projects/x    # chat in another folder
purr -m flash        # pick a model (/models lists them)
purr --plain         # simple scrolling mode, no Textual needed
purr -p "fix the bug in mathy.py"   # one task, then exit
```

Drag over any text to copy it (ctrl+c copies a selection too; with nothing selected it stops an
answer or clears the box). In the message box: enter sends, ctrl+j makes a new line, up/down bring back old messages.
`/` opens the command menu, `@` picks a file to attach, `!command` runs a shell command yourself
(the model sees the output). Esc stops an answer, ctrl+q quits. Before edits and commands a popup
asks: y, a (always), n, or type what to do instead. A plain "n" ends the turn.

A little cat above the message box shows what purr is doing: thinking, exploring (reading,
grep), building (edits), running a command, talking, waiting for you, napping when idle.
While it works you see the time so far and the speed; at the end of every task:
`✓ 1m 12s · 45 tok/s · 3.2k tokens`. The speed only counts the time spent writing, not
the wait while the model reads the prompt. The cat's frames are in `tui/cat.py`.

She's yours: she's called Mochi until you rename her (`/cat name Luna`), she cheers when a
test run passes and gets a little sad when it fails, stretches when a task wakes her up, and
purrs when you click her. `/cat` shows how often she's been petted and how many tasks you've
done together. By day she wears a bow and naps in the sun; at night (following
`theme-switch`, or 18-06) she wears a moon and the stars come out.

| Command | |
|---|---|
| `/models`, `/model <name>` | switch model (list or by name) |
| `/clear` | forget the chat |
| `/compact` | summarise the chat to free up room (happens by itself at 85% full) |
| `/resume` | carry on an earlier chat (`purr -c` carries on the last one) |
| `/undo` | put back the files the last answer changed |
| `/init` | have the model write an AGENTS.md for the project |
| `/theme`, `/theme <name>` | colours: auto (day/night), plum, strawberry-milk, lilac-dream, bubblegum-night, cotton-candy (remembered) |
| `/cat`, `/cat name <name>` | your cat's card, or rename her |
| `/mode`, `/refine`, `/plan`, `/pr` | see below |
| `/files` or **ctrl+t** | the workbench: what changed this session, the project tree, a small editor (see below) |
| `/stats` | fun numbers: chats, streak, lines written, favourite model and project, night owl or early bird |
| `/cost`, `/trust`, `/help`, `/quit` | |

**Your own commands:** a markdown file per command in `~/.config/purr/commands/` (every project)
or `<project>/.purr/commands/`. The file name is the command, the text is sent to the model,
`$ARGUMENTS` becomes whatever you type after it. A first line `description: ...` shows in the menu.

**Notes for the model:** `~/.config/purr/AGENTS.md` (every project) and `AGENTS.md` in the
project are added to the system prompt.

**Tools the model has:** read_file, list_files, grep, fetch_url, edit_file, write_file, run,
todo (a task list you see), task (a read-only helper with a fresh memory, for exploring).
Commands in `[permissions] allow_run` (in the settings) run without asking; "always" on a command
covers that kind of command only (`git status`, not all of git).

## Modes, refining and pull requests
| mode | tools | for |
|---|---|---|
| ✎ code | all | doing the work (the default) |
| ◈ ask | read, list, search: edits and commands are blocked | explaining, planning |
| ✿ learn | all | learning: purr writes the boring parts and leaves the key lines to you as `TODO(you)` |
| ⇄ pair | all | pair programming: you take turns, one small step each |
| ✦ plan | two models | a big model makes tickets, a small one does them |
| ♡ chat | none, short prompt | just talking |
| ✧ create | none, a bit more random | ideas, names, game design, writing |

Switch with **shift+tab** or `/mode chat` (your own agents come after these: see above). Each mode can bring its own model (`[mode_models]` in your config, e.g. code → a local Qwen, ask → DeepSeek Flash, chat and create → Kimi); a model you pick with `/model` in a mode sticks to it for the session, and a model with no key keeps the current one. Refine has the model rewrite your message into a clear
task (Task / Where / Steps / Done when, from the project's file list) and shows it to you first:
ctrl+s sends it, ctrl+o sends yours. `/refine auto` (the default) does that only for a short first
message, a new task said in a few words, where it helped most in `purr bench` (Qwen3.6 IQ3 went
from 3/5 to 5/5 hard tasks from vague asks); `/refine on` refines every message, `/refine off` none.

**The workbench** (ctrl+t, `/files`, or click a change card in the chat). Left: the files changed
this session with their +/− counts, above the project tree where changed files glow. Right: a
summary of everything that changed (per file and turn by turn), or the file you pick, as a diff
against how it was before the session (**d** switches to the whole file, with changed lines
marked). **e** edits it right there (ctrl+s saves, with syntax colours from `textual[syntax]`);
**n** hands the terminal to your `$EDITOR` (nvim by default) at the first changed line and comes
back to purr when you quit it. Your edits count as yours in pair mode.

**Learn mode.** For learning by doing: purr sets things up (files, imports, wiring, tests) and
leaves the interesting 3-10 lines to you as a `TODO(you)` comment with a hint, plus a `✦ why:` line
about the idea. Say **done** and it reads your code, runs it, says what's good and asks about one or
two things to improve instead of fixing them; say **hint** for a nudge (each one a bit more
specific) or **show me** for the answer. purr keeps the model honest: it tells it where the open
`TODO(you)`s are with every message, shows `✿ your turn: file:line` after each turn, and if the model
wrote everything itself it asks it once to hand the interesting part back.

**Pair mode.** You take turns at the keyboard. The model makes one small change, then purr
hands it back to you (enforced, so a small model can't run off with the whole task): it says
what it did and what it would do next, and you say **go**, steer it, or take over. Anything it
tried to do after its step is held back and shown as a suggestion. You can edit the code
yourself any time, in any editor: with your next message purr shows the model a diff of what you
changed since its turn, so it builds on your code instead of overwriting it. Reading and
searching don't count as a step.

**Plan mode.** This is purr's whole idea in one mode: a long, vague task is better handled by a
big model that breaks it down and a small one that does each piece without losing the thread.
`/plan add wishlists to the shop` (or `/mode plan` then send your task) starts it — the planner
(`planner` under `[plan]` in your config, say `ds-pro-or`: a big, long-context model; the model you're on if you set none) reads the project and
writes a handful of tickets to `<project>/.purr/tickets/NN-title.md`. Each ticket names the one to
three files it touches, what to change and how to check it. Then purr shows you the plan and waits:
**ctrl+s** runs the tickets, **ctrl+r** reads the files again after you edit them, **esc** cancels
(and keeps the files; `/plan run` starts them later). The executor (`executor` under `[plan]`,
say a small local model; the model you're on if you set none) takes each ticket in a **fresh chat**, so its small context holds only the
ticket in front of it, while the whole request and the list of ticket titles (✓ for the ones done)
travel with it. You approve edits and commands as usual; each ticket is a normal turn, so purr's
checks and the final check still run.

purr runs the project's tests once before the first ticket and again after every ticket. A ticket
fails only if it adds failures: tests that already failed before the plan aren't blamed on it (when
purr can't tell which tests fail, the ticket is marked "not checked"). The first failing ticket
stops the plan, and purr says which one and why. Esc stops the plan too; a ticket stopped half
way isn't checked or counted. The summary is honest: `✓ plan · 3 done`, `✗ plan · 1 done, 1
failed, 1 not run` or `■ plan · 1 done, 2 stopped/not run`.
Everything a plan changes is one `/undo`, and "always" on an edit or command carries from ticket to
ticket. `/plan run 3` runs the tickets already on disk from the third onwards. Writing a new plan
doesn't delete the old one: it moves it to `.purr/tickets/old/<timestamp>/`. purr makes
`.purr/.gitignore` (`*`) the first time, so tickets never end up in a commit. The plan's own chat
and each ticket's chat are saved as sessions (`~/.local/state/purr/sessions/`). If the planner's key
or model isn't there, purr plans with the current model instead of failing.

`/pr` turns the changes into a GitHub pull request: the model drafts the title and description,
you edit them in a popup, and only then purr makes a branch, commits with a `Model: <model>`
line (and `Co-Authored-By: purr-<model>` if you set `co_author_email`), pushes and runs `gh pr create`. The title ends with the model
(`Fix the discount · qwen3-coder-32k`, so with squash merging it shows on main) and the PR gets
a pink `🐾 <model>` label. For a cute avatar on those
commits, give purr its own GitHub account and set `co_author_email` in your config.

## Your own agents
An agent is a Markdown file: a short header, then its instructions. Each becomes a mode of its own
(**shift+tab** goes through them after purr's modes, or `/agent <name>`):

```markdown
---
description: reviews code for bugs, never edits
tools: read            # all (default) | read | none | a list: [read_file, grep, run]
model: ds-flash-or     # optional: the model it switches to
temperature: 0.3       # optional
colour: cyan           # optional: a hex colour or pink/lilac/mint/peach/rose/cyan/blue
icon: ◎                # optional
---
You are a careful code reviewer. Point at the exact line, say what breaks and why.
```

Put it in `~/.config/purr/agents/` (yours, everywhere) or `.purr/agents/` in a project (that project's;
it wins over yours by the same name). Easier: **`/agent new reviews my code and points out bugs`** has
the current model write the file, and switches to it. `/agent` lists them, `/agent reload` reads them
again after you edit one. Look-only agents are enforced like ask mode, a tool list hides every other
tool, and agents that may change things get code mode's checks. Agents you already have for **Claude
Code** (`~/.claude/agents`) and **OpenCode** (`~/.config/opencode/agent`) work too, their tool names
translated; they're a `/agent <name>` away rather than in the shift+tab cycle.

## Private mode: `/private`

`/private` starts a fresh chat on a local model only: no web (`fetch_url` is gone, the Godot docs
lookup stays offline) and the chat isn't saved. `/private` again leaves it. purr never sends
telemetry, private or not.

## Free models: `/model free`
`/model free` lets purr pick the best free OpenRouter model for what you're doing, from the ranked
lists in `[free]` in `harness/defaults.toml` (code: code/learn/pair/plan, ask, talk: chat/create), skipping
models too small for the chat. When a model is rate-limited, out of free requests for the day, or
has no host, purr says so in the chat, rests it (until midnight UTC for the daily cap, a few minutes
when it's busy; remembered in `~/.local/state/purr/free.json`) and sends the same request to the
next one. The model line shows `free → laguna`. `/free` lists the models and which are resting,
`/free reset` forgets the resting; picking a model by hand turns it off.

Besides OpenRouter, purr knows the free tiers of **Groq, Cerebras, Google AI Studio (Gemini),
Mistral, NVIDIA and Cohere**, and the `[free]` rankings mix them all. A provider without a key is
simply skipped, so add keys as you get them: `purr --key groq` asks for the key without showing it
and keeps it in `~/.config/purr/keys.toml` (only you can read it; `key_page` in `harness/defaults.toml` says
where to get one). OpenRouter's daily free cap is per account, so when it's hit every OpenRouter
model rests at once and another provider takes over. `/free check` sends each model one tiny
request and rests the ones that don't work (a renamed model, a key that's wrong), so a real turn
never lands on them. Free hosts may keep your
prompts, so check a model's page before using it on private code.

## DeepSeek
Paste the key in the setup window (or `purr --key deepseek`, or `export DEEPSEEK_API_KEY=sk-...`), then `/model flash`.
DeepSeek's cache makes repeated context almost free, as long as the start of the chat never
changes. purr only ever adds to the end of the chat, so that works out of the box.

## OpenRouter
Uses the key you saved with `opencode auth login openrouter` (or `OPENROUTER_API_KEY` if set;
DeepSeek works the same way). Models: `ds-pro-or`, `ds-flash-or`, `luna`, `sonnet`, `glm`,
`qwen-flash`, `kimi`. For another one, add an entry to your `~/.config/purr/config.toml` like those in
`harness/defaults.toml`, with its `id` and `price` (both are on openrouter.ai). The cost purr shows is OpenRouter's real one.
Which hosts may answer is set once under `[providers.openrouter.body.provider]`: only well-known
hosts, no fp4 copies, the cheapest of those that do 100+ tok/s (the cheapest no-name hosts were
slow and went off the rails). OpenCode's config asks for the same, so `purr bench` stays fair.

## Beyond plain coding
Terminal-Bench showed what a coding agent also needs (11 of its 89 tasks involve images, 9 involve
interactive programs or servers):

- **`terminal`**: a session you keep talking to, for interactive programs (a VM's console, ssh, a
  REPL, a debugger) or a server you want to watch: start it, send text and keys (Enter, C-c, Up…),
  read its screen, wait for a pattern (`wait_for: "login:"`). With tmux installed the sessions are
  tmux sessions and outlive purr, so a VM or server a task needs stays up; without it they run in
  purr's own process. `run` is still for commands that finish.
- **Servers keep running:** `run` sends output to a temporary file instead of a pipe, so a
  background server (`cmd &`) no longer stalls the call until its timeout (which then killed the
  server). The final check asks the model to make sure anything that must keep running does, and
  answers.
- **`look_at_image`**: shows a png/jpg/gif/webp to models that can see (`vision = true` in
  its settings; local models Ollama says can see get it by themselves: Qwen3.6, Qwen3.5, Gemma 4, Gemini, Claude). Only the latest 3 images go back with
  each request.

## How it works
1. `harness/agent.py` sends the chat + the tool list to the model.
2. The model answers with text (done) or asks for tools.
3. `harness/tools.py` runs them (asking you before edits and commands) and adds the results to the chat.
4. Back to 1.

| File | What it does |
|---|---|
| `purr.py` | the command: starts the full-screen or plain mode |
| `tui/app.py`, `tui/purr.tcss` | the full-screen mode and its look |
| `tui/cat.py` | the cat: its moods and animation frames |
| `harness/agent.py` | the loop, system prompt, cost, compacting, sessions, helpers |
| `harness/commands.py` | the /commands (both modes use them) |
| `harness/api.py` | streams replies from any OpenAI-compatible API |
| `harness/tools.py` | the tools, permissions, undo |
| `harness/limits.py` | per-model limits, derived from the model's `context` |
| `harness/ui.py` | colours, diffs, the plain mode's view |
| `harness/defaults.toml` | purr's providers, models, prices (yours: `~/.config/purr/config.toml`) |
| `harness/settings.py`, `harness/discover.py` | settings in layers; the local servers purr finds |

Every chat is saved as JSON in `~/.local/state/purr/sessions/` (`PURR_STATE` moves it, for tests), so you can see exactly what
the model sent and got back. That's the best way to see why a model did something odd.

The agent never prints anything itself: it calls a *view* (`thinking`, `text`, `tool`, `diff`,
`note`, `ask`, `status`). `ui.PlainView` prints to the terminal, `tui.app.TuiView` draws in
Textual. A new front end only needs those methods.

Local model quirks purr works around: edits with slightly wrong indentation still apply
(`_loose_match` in tools.py), and tool calls that Qwen writes as text
(`<function=...>`) get turned into real calls (`calls_from_text` in agent.py). Tool and argument
names from other harnesses work too (`search` is grep, `old_string` is old_text, ...), an
`edit_file` with only `content` rewrites the file, `\u003c` written out instead of `<` is undone,
and when an edit's text isn't found purr says which file it is in, or shows the closest lines.
`purr bench` counts these as "repaired".

purr also checks the model's work by itself (each can be switched off in your config):
after every edit a syntax check and ruff's bug checks (undefined names, functions defined twice, ...)
go straight into the tool result (`code_checks`); a rewrite with a `# ... rest unchanged`
placeholder, or one that shrinks a big file to a fraction, is refused once; when the model wants
to stop after changing files, purr runs the project's tests itself and asks it to check the
request point by point (`final_check`, `final_check_tests`; `.purr/test_command` sets the
command); every 12 steps a checkpoint repeats the request and asks whether the approach is
getting closer (`reminders`); after 8 changes to the same file, or 6 versions of a command with
only its numbers changed, it asks for a step back and a different approach (`step_back`); edits and overwrites need the
file read first (`read_before_edit`); and an edit that removes or changes what a test checks
gets a warning to fix the code instead (part of `code_checks`). `purr bench` counts the
problems the checks caught, and `purr-bare` runs purr without any of them.

The training wheels scale with the model: local models get auto-refine, checkpoints, step backs and the
edge-case step in the final check; API models (which `purr bench` showed don't need them; with
DeepSeek V4.1 Flash they only made purr slower) get a short final check instead. Setting
`refine`, `reminders` or `edge_cases` in your config (or `/refine ...`) overrides that.

In one-shot runs (`purr -p`, benchmarks), every model gets three cheap checks: its own
checks rerun in a fresh shell before it may stop (`proof_ledger`); limits the task states
(under 1 s, at most 50 MB) measured rather than assumed (`margin_check`); and a watch on
background jobs (with a time limit) that reads their logs and says when one won't finish in the
time left or has stalled, plus a reminder to time a small piece of a long run first (`job_watch`).
Local models also get acceptance checks written from the request alone before any code
(`blind_checks`).

Big API models rarely loop, but the same task can pass in one run and fail in the next (11 of
89 Terminal-Bench tasks flipped between two DeepSeek runs). So in one-shot runs they get two
independent plans before any code, with a third call keeping the better one (`two_plans`); a
tester with a fresh chat and no view of the work, which runs checks against the request before
the run finishes and sends its FAILs back (`fresh_eyes`); and more reasoning effort for the
first step, the checks and the judging (`effort_phases`, DeepSeek and OpenRouter). In any chat,
past 160k tokens an API model goes on from a summary (`fresh_context`, `fresh_at`). Benchmark
runs take `--ak extras=false` to leave all of these out and compare with the published runs.

## Different models, different limits
purr adapts to the model it is running. A model's `context` (found by itself for local models) sets how big a
tool result may be, how many lines `read_file` returns by default, how many files or
grep matches are listed, how large an `@file` attachment may be, when the chat compacts
by itself, and how many recent tool results stay whole. A 32k local model gets small,
quick limits; a 1M API model gets much bigger ones. Any model can override a number
with a `limits = { ... }` table (`harness/limits.py` shows the keys and the defaults).

Two things that mostly bite small models:

- **Going in circles.** If the model makes the same tool call with the same arguments
  and gets nothing new, purr says so once. A failing call is stopped after 3 tries (4 on
  big models); one that works but gives the same thing (re-reading a file) gets 2 more.
  A test that fails with a different error each time is not a loop.
- **Old tool output.** Before summarising the whole chat, purr elides the contents of
  older tool results (keeping the newest ones whole), leaving a note of which call it
  was so the model can run it again. That frees room with no model
  call, so a small model spends its context on the current job rather than on output
  it has already read.

## MCP servers: helping small models understand the project
purr speaks MCP: every `[mcp.*]` table in the settings is a server whose tools are offered next to
purr's own (`harness/mcp.py`; a server is started in the project folder the first time it's
needed, and one that fails is skipped). `tools = [...]` offers only some of a server's tools, and
tools that can change things ask you first, like `run`.

purr ships two of its own in `servers/` (plain Python, no packages, any MCP client can use them):

| server | tool | what it gives the model |
|---|---|---|
| codebase | `project_overview` | tech stack and versions, how to run and test (CI commands included), folders, entry points. Put in the system prompt instead of offered as a tool (~300 tokens for small models; `overview_in_prompt = false` turns it off) |
| | `outline` | a file's classes and functions with line ranges plus who uses the file (Godot scenes and autoloads too), so the model reads just the lines it needs; a folder gives the code map |
| | `find_symbol` | where something is defined and every place it's used |
| stack | `godot_class` | a class from the Godot you have installed: exact signatures (`godot --doctool`) plus that version's docs from GitHub, cached in `~/.cache/purr/godot/`; up the inheritance chain for one member; Godot 3 names get their Godot 4 name |
| | `python_api` | a package's function, class or module from the project's own `.venv`: signature, docs, version |

Few tools with short descriptions on purpose: every tool is sent with every request, so together
they add about 300 tokens a request on top of the overview.

The stack tools only show up where they fit (`godot_class` in Godot projects, `python_api` in Python
ones), since every tool costs a small model some context. To add your own, see `servers/mcpserver.py`:
a decorated function is a tool.
