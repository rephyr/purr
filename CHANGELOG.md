# Changelog

## Unreleased

- Big API models: two independent plans before coding, a fresh-eyes tester before finishing,
  more reasoning effort where it counts, and a fresh context for very long chats.
- Every model: its own checks rerun in a fresh shell, stated limits measured, background jobs
  watched against the time limit.
- Small models: acceptance checks written from the request before any code.
- Long command output is kept in files; made-up tool names get repaired instead of crashing.
- Benchmark runs can no longer look the benchmark up.
- Windows: a clear "use WSL" message. `OLLAMA_HOST` is honoured.
- No personal settings in purr's defaults; copying works with pbcopy, xclip and xsel too.
- CI runs the tests on Linux and macOS; search works without ripgrep installed.

## 0.5.0 (2026-10)

First release: first-run setup that finds Ollama, llama.cpp, LM Studio and vLLM; your own agents;
modes (code, ask, learn, pair, plan, chat, create); MCP; `/pr`; the free-tier router; `purr bench`.
