# purr ♡

A tiny coding agent for the terminal, built to learn how harnesses work.
Works with local Ollama models and the DeepSeek API. The agent itself is plain Python;
the full-screen mode uses [Textual](https://textual.textualize.io/) (`uv sync` installs it into `.venv`).

```
purr                 # full-screen chat in the current folder
purr ~/projects/x    # chat in another folder
purr -m flash        # pick a model (names in config.toml)
purr --plain         # simple scrolling mode, no Textual needed
purr -p "fix the bug in mathy.py"   # one task, then exit
```

In the message box: enter sends, ctrl+j makes a new line, up/down bring back old messages.
`/` opens the command menu, `@` picks a file to attach, `!command` runs a shell command yourself
(the model sees the output). Esc stops an answer, ctrl+q quits. Before edits and commands a popup
asks: y, a (always), n, or type what to do instead. A plain "n" ends the turn.

| Command | |
|---|---|
| `/models`, `/model <name>` | switch model (list or by name) |
| `/clear` | forget the chat |
| `/compact` | summarise the chat to free up room (happens by itself at 85% full) |
| `/resume` | carry on an earlier chat (`purr -c` carries on the last one) |
| `/undo` | put back the files the last answer changed |
| `/init` | have the model write an AGENTS.md for the project |
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
| `harness/agent.py` | the loop, system prompt, cost, compacting, sessions, helpers |
| `harness/commands.py` | the /commands (both modes use them) |
| `harness/api.py` | streams replies from any OpenAI-compatible API |
| `harness/tools.py` | the tools, permissions, undo |
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

## DeepSeek

Put the key in `~/.zshrc`: `export DEEPSEEK_API_KEY=sk-...`, open a new terminal, then `/model flash`.
DeepSeek's cache makes repeated context almost free, as long as the start of the chat never
changes. purr only ever adds to the end of the chat, so that works out of the box.

## Ideas for next steps

- images: a `look_at_image` tool for vision models (Qwen 3.6)
- per-project settings (allowed commands that never ask)
- a ticket mode that reads `docs/tickets/*.md`
