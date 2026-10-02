# purr bench

> **Not an official benchmark.** These are purr's own small tasks (`bench/tasks/`), run with
> `purr bench`, mostly on local models and often just once each, to debug, iterate on and improve
> the harness. They're nowhere near official numbers: a task or two either way is noise, and the
> tasks were written alongside purr. For official, comparable numbers see
> [Terminal-Bench](../terminal-bench/).

How to read it: each run is one `purr bench` call. *prompts* says whether the
tasks got their full prompt or the short, vague one. *solved* counts runs where every hidden test
passed; *tests* is the share of hidden tests passed. `human` rows are people playing
`purr bench --you`.

### 2026-10-02 · purr before 0.3 · 9 tasks, vague prompts

| model | harness | solved | tests | avg time | tokens | tool errors | hallucinations |
|---|---|---|---|---|---|---|---|
| ds-flash-or | purr | 8/9 | 96% | 19s | 40.7k | 0 | 1 |
| ds-flash-or | opencode | 7/9 | 90% | 21s | 34.5k | 0 | 2 |

Tasks: build-order, cafe-discount, deep-bug, idle-catchup, lru-cache, messy-csv, purr-meter, rename, slow-dedupe
