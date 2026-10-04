# purr ♡

A cute coding agent for your terminal, built to get more out of **small local models** on long,
vague tasks. Works with API models too.

![purr fixing a bug with Qwen3.6 on a local GPU (2x speed)](docs/demo.gif)

## Install

```
curl -LsSf https://raw.githubusercontent.com/rephyr/purr/main/install.sh | sh
```

Or with uv: `uv tool install git+https://github.com/rephyr/purr`.

**Arch:** the AUR package is waiting for AUR sign-ups to reopen. Until then:

```
git clone https://github.com/rephyr/purr && cd purr/packaging/aur/purr-agent-git && makepkg -si
```

**Windows:** run purr inside WSL (`wsl --install`). For Ollama on the Windows side, turn on WSL's
mirrored networking (`networkingMode=mirrored` in `.wslconfig`) or set `OLLAMA_HOST` to its address.
Ollama on another machine works the same way.

## First start

Run `purr` in a project folder. A setup window walks you through it:

- **Local models** are found automatically: Ollama, llama.cpp, LM Studio and vLLM.
- **API providers** need only a key: DeepSeek, OpenRouter, or the free tiers of Groq, Cerebras,
  Gemini, Mistral, NVIDIA and Cohere. Any OpenAI-compatible server works too.
- Pick your model and name your cat.

`purr setup` (or `/setup`) opens it again. Settings live in `~/.config/purr/config.toml`.

## Use

```
purr                      # chat in this folder
purr -m flash             # pick a model
purr -p "fix the tests"   # one task, then exit
purr --plain              # no full-screen window
```

| key | |
|---|---|
| `/` | commands (`/models`, `/undo`, `/resume`, `/pr`, ...) |
| `@file` | attach a file |
| `!cmd` | run a shell command yourself |
| shift+tab | switch mode |
| ctrl+t | see what changed, edit files |
| esc / ctrl+q | stop / quit |

## Modes and your own agents

| mode | |
|---|---|
| ✎ code | does the work (default) |
| ◈ ask | looks, never changes anything |
| ✿ learn | leaves the key lines for you to write |
| ⇄ pair | you take turns, one step each |
| ✦ plan | a big model writes tickets, a small one does them |
| ♡ chat / ✧ create | just talking, or ideas and writing |

**Your own agent** is a Markdown file in `~/.config/purr/agents/` (or `.purr/agents/` in a project):

```markdown
---
description: reviews code for bugs, never edits
tools: read
colour: cyan
---
You are a careful code reviewer. Point at the exact line and say what breaks.
```

Or let purr write it: `/agent new reviews my code and points out bugs`. Agents from Claude Code
and OpenCode work too.

## Why small models do better in purr

- **A lean prompt** and limits that fit the model's context.
- **Slips get repaired:** fuzzy edits still apply, and tool calls written as text still run.
- **Work gets checked:** syntax and lint after every edit, and the tests before it says "done".
- **Loops get caught:** reworking the same file over and over triggers a step back.
- **Plan mode** splits big tasks into small tickets.

## Benchmarks

On Terminal-Bench's quick set (DeepSeek V4.1 Flash, one try each), purr solved **16 of 17** tasks
and OpenCode **14**. Published runs: [benchmarks/](benchmarks/) · how they're run: [docs/benchmarks.md](docs/benchmarks.md).

## More

- [docs/guide.md](docs/guide.md): every command, mode and setting
- [packaging/](packaging/): releases, PyPI, the AUR
- Tests: `python3 -m unittest discover -s tests -t .` (no model is called)

MIT licensed.
