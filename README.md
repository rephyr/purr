# purr ♡

A tiny coding agent for the terminal, built to learn how harnesses work.
Works with local Ollama models, the DeepSeek API and OpenRouter. The agent itself is plain Python;
the full-screen mode uses [Textual](https://textual.textualize.io/) (`uv sync` installs it into `.venv`).

```
purr                 # full-screen chat in the current folder
purr ~/projects/x    # chat in another folder
purr -m flash        # pick a model (names in config.toml)
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
| `/mode`, `/refine`, `/pr` | see below |
| `/cost`, `/trust`, `/help`, `/quit` | |

**Your own commands:** a markdown file per command in `~/.config/purr/commands/` (every project)
or `<project>/.purr/commands/`. The file name is the command, the text is sent to the model,
`$ARGUMENTS` becomes whatever you type after it. A first line `description: ...` shows in the menu.

**Notes for the model:** `~/.config/purr/AGENTS.md` (every project) and `AGENTS.md` in the
project are added to the system prompt.

**Tools the model has:** read_file, list_files, grep, fetch_url, edit_file, write_file, run,
todo (a task list you see), task (a read-only helper with a fresh memory, for exploring).
Commands in `[permissions] allow_run` (config.toml) run without asking; "always" on a command
covers that kind of command only (`git status`, not all of git).

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
| `config.toml` | providers, models, prices |

Every chat is saved as JSON in `~/.local/state/purr/sessions/` (`PURR_STATE` moves it, for tests), so you can see exactly what
the model sent and got back. That's the best way to see why a model did something odd.

`AGENTS.md` in the project folder is added to the system prompt automatically.

The agent never prints anything itself: it calls a *view* (`thinking`, `text`, `tool`, `diff`,
`note`, `ask`, `status`). `ui.PlainView` prints to the terminal, `tui.app.TuiView` draws in
Textual. A new front end only needs those methods.

Local model quirks purr works around: edits with slightly wrong indentation still apply
(`_loose_match` in tools.py), and tool calls that Qwen writes as text
(`<function=...>`) get turned into real calls (`calls_from_text` in agent.py).

## Different models, different limits

purr adapts to the model it is running. The `context` in `config.toml` sets how big a
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

## DeepSeek

Put the key in `~/.zshrc`: `export DEEPSEEK_API_KEY=sk-...`, open a new terminal, then `/model flash`.
DeepSeek's cache makes repeated context almost free, as long as the start of the chat never
changes. purr only ever adds to the end of the chat, so that works out of the box.

## OpenRouter

Uses the key you saved with `opencode auth login openrouter` (or `OPENROUTER_API_KEY` if set;
DeepSeek works the same way). Models: `ds-pro-or`, `ds-flash-or`, `luna`, `sonnet`, `glm`,
`qwen-flash`, `kimi`. For another one, copy an entry in `config.toml` and change `id` and `price`
(both are on openrouter.ai). The cost purr shows is OpenRouter's real one.

## Modes, refining and pull requests

| mode | tools | for |
|---|---|---|
| ✎ code | all | doing the work (the default) |
| ◈ ask | read, list, search: edits and commands are blocked | explaining, planning |
| ♡ chat | none, short prompt | just talking |
| ✧ create | none, a bit more random | ideas, names, game design, writing |

Switch with **shift+tab** or `/mode chat`. `/refine on` makes the model rewrite each message into
a clear task (Task / Where / Steps / Done when, from the project's file list) and shows it to
you first: ctrl+s sends it, ctrl+o sends yours.

`/pr` turns the changes into a GitHub pull request: the model drafts the title and description,
you edit them in a popup, and only then purr makes a branch, commits with
`Co-Authored-By: purr-<model>`, pushes and runs `gh pr create`. The title ends with the model
(`Fix the discount · qwen3-coder-32k`, so with squash merging it shows on main) and the PR gets
a pink `🐾 <model>` label. For a cute avatar on those
commits, give purr its own GitHub account and set `co_author_email` in `config.toml`.

## Benchmark: purr vs OpenCode

`purr bench` asks which models to test, then gives purr and OpenCode the same small coding
tasks with the same model (both with everything allowed, both with the same time limit).
Every run starts from a fresh copy of the task; afterwards hidden tests decide whether it's
solved. `purr bench -m qwen3-coder-32k -t rename --runs 3` picks things up front.

It measures: solved, tests passed, time, tok/s, tokens, model calls, tool errors, failed edits,
hallucinations (a tool or file that doesn't exist, a tool call written as text, saying it's
done when the tests say no) and syntax errors left behind. A run that wanders out of its task
folder isn't counted. Results: `~/.local/state/purr/bench/<date>/report.md`. The tasks live
in `bench/tasks/` (a `task.json`, the starting `files/`, and the hidden tests in `check/`).

## Tests

`python3 -m unittest discover tests` (no model is called).

## Ideas for next steps

- images: a `look_at_image` tool for vision models (Qwen 3.6)
- per-project settings (allowed commands that never ask)
- a ticket mode that reads `docs/tickets/*.md`
