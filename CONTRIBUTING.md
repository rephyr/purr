# Contributing

Bug reports and pull requests are welcome.

- **Bugs:** open an issue with your OS, `purr --version`, the model and provider, and what you
  typed. If a session went wrong, attach its log from `~/.local/state/purr/sessions/` (check it for
  anything private first).
- **Code:** `uv sync`, then `uv run python -m unittest discover -s tests -t .`. The tests call no
  model. Keep changes small and match the code around them: plain Python, short comments that say
  why, no new dependencies.
- **Behaviour changes** (prompts, checks, nudges): say which model and task showed the problem.
  `purr bench` (docs/benchmarks.md) is how we check that a change helps.
