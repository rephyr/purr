# purr bench

> **Not an official benchmark.** `purr bench` is for improving purr as a harness for
> **small, local models**: purr's goal is to get the most out of a ~10-35B model on your own GPU,
> on longer and vaguely worded tasks. These are purr's own small tasks (`bench/tasks/`), often run
> just once each, to debug, iterate on and improve the harness: a task or two either way is noise,
> and the tasks were written alongside purr. It's **not meant for judging large models** (big API
> models solve most of these). For official, comparable numbers see [Terminal-Bench](../terminal-bench/).
>
> **Hardware:** AMD Ryzen 9 5900X (12 cores), 32 GB RAM, NVIDIA RTX 4080 (16 GB VRAM), Arch Linux;
> local models through Ollama. Speeds and times are this machine's: yours will differ.

## Latest: 2026-10-02 · ds-flash-or · 9 tasks, vague prompts

**purr** solved 8 of 9 tasks · OpenCode solved 7 of 9 tasks

purr before 0.3 · model `ds-flash-or` (deepseek/deepseek-v4.1-flash), an API model: a check on the harness, not purr's small-model goal · **bold** = better

| | purr | OpenCode |
|---|---|---|
| tasks solved | **8/9** | 7/9 |
| hidden tests passed | **96%** | 90% |
| time per task | **19s** | 21s |
| tokens per task | 4.5k | **3.8k** |
| tool errors | 0 | 0 |
| claimed done but wasn't | **1** | 2 |

**Task by task**

| task | purr | OpenCode |
|---|---|---|
| build-order | ✓ | ✓ |
| cafe-discount | ✓ | ✓ |
| deep-bug | ✓ | ✓ |
| idle-catchup | ✗ 9/10 | ✗ 9/10 |
| lru-cache | ✓ | ✓ |
| messy-csv | ✓ | ✗ 0/2 |
| purr-meter | ✓ | ✓ |
| rename | ✓ | ✓ |
| slow-dedupe | ✓ | ✓ |

✓ every hidden test passed · ✗ 9/10: 9 of the 10 hidden tests passed

---

Every run's full results are in [`results/`](results/). Run your own: `purr bench`, then
`purr bench --publish`; play it yourself with `purr bench --you`.
