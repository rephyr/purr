# purr on Terminal-Bench

purr runs [Terminal-Bench](https://www.tbench.ai) through [Harbor](https://www.harborframework.com),
the same runner the public leaderboards use (`tbench/purr_agent.py`). Every purr version that changes
how it works gets a run, so you can see whether it got better. Also: [DeepSWE](../deepswe/).

## How it's run (`tbench/fair.sh`)

As close as we can get to the settings DeepSeek used to run its model through other harnesses:

- **model:** DeepSeek V4.1 Flash, served by DeepSeek itself (OpenRouter pinned to the `deepseek` host,
  which uses prompts for training, so the account allows that; no fallback to other hosts, some of
  which serve it at fp4/fp8)
- **sampling:** temperature 1.0, top_p 0.95; up to 64k tokens per reply
- **limits:** 1M context, 500 model calls per task, each task's own time limit
- **attempts:** 3 per task (1 in one-try and quick runs); pass@1 = the average share of attempts
  that passed (timeouts and errors count as fails, like on the leaderboards), ± the standard error
  over tasks
- **purr:** as shipped (its MCP servers on), the exact commit recorded with every trial
- **dataset:** Terminal-Bench 2.1 (`terminal-bench/terminal-bench-2-1` in Harbor's registry), the
  version the reference scores below used. The first runs were on 2.0 (the same 89 tasks before
  2.1's fixes), so they have their own tables: don't compare across the two.
- **network:** open in the task container, where DeepSeek's runs had none: tasks that need to
  download something are easier here than in theirs.

## purr, version by version

### Terminal-Bench 2.1: full runs, all 89 tasks, 3 attempts each (`tbench/fair.sh`)

| purr | date | pass@1 | pass@3 | timeouts | errors | cost | tokens in / cached / out | median time |
|---|---|---|---|---|---|---|---|---|
| (none yet) |

### Terminal-Bench 2.1: one-try runs, all 89 tasks, 1 attempt each (`tbench/fair.sh --one`)

The same score as a full run (pass@1) in a third of the time, with a wider margin (see ±).
Fine to set next to the reference scores below, keeping the ± in mind.

| purr | date | pass@1 | timeouts | errors | cost | tokens in / cached / out | median time |
|---|---|---|---|---|---|---|---|
| (none yet) |

### Terminal-Bench 2.1: quick runs, the same 20 tasks (`tbench/quick-tasks.txt`), 1 attempt each (`tbench/fair.sh --quick`)

Cheap and quick, for seeing whether a purr version got better or worse. With 20 tasks and one
try each the margin is wide (see ±): compare quick runs with each other, not with the full
runs or the leaderboards.

| purr | date | pass@1 | timeouts | errors | cost | tokens in / cached / out | median time |
|---|---|---|---|---|---|---|---|
| (none yet) |

### Terminal-Bench 2.0: quick runs, the same 20 tasks (`tbench/quick-tasks.txt`), 1 attempt each (`tbench/fair.sh --quick`)

| purr | date | pass@1 | timeouts | errors | cost | tokens in / cached / out | median time |
|---|---|---|---|---|---|---|---|
| 0.3.1+3dde078 | 2026-10-02 | **75.0%** ± 9.9 | 0 | 1/20 | $0.43 | 18.5M / 17.8M / 457k | 1.7 min |

## Published scores to compare with (other harnesses, full runs)

| harness | model | benchmark | pass@1 | source |
|---|---|---|---|---|
| DSH Minimal (DeepSeek's own) | DeepSeek V4.1 Flash | Terminal-Bench 2.1 (89 tasks) | 90.6% | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| mini-SWE-agent | DeepSeek V4.1 Flash | Terminal-Bench 2.1 (89 tasks) | 90.3% | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| Claude Code | DeepSeek V4.1 Flash | Terminal-Bench 2.1 (89 tasks) | 88.0% | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| Pi | DeepSeek V4.1 Flash | Terminal-Bench 2.1 (89 tasks) | 86.1% | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| DSH Standard | DeepSeek V4.1 Flash | Terminal-Bench 2.1 (89 tasks) | 85.8% | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| DSH PTC | DeepSeek V4.1 Flash | Terminal-Bench 2.1 (89 tasks) | 85.8% | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| OpenCode | DeepSeek V4.1 Flash | Terminal-Bench 2.1 (89 tasks) | 85.0% | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| Codex | DeepSeek V4.1 Flash | Terminal-Bench 2.1 (89 tasks) | 84.1% | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| Ante | DeepSeek V4.1 Flash | Terminal-Bench 2.1 (89 tasks) | 83.9% (5 trials per task) | [link](https://antigma.ai/eval) |
| Terminus-2 | Ornith-1.5-9B | Terminal-Bench 2.1 (89 tasks) | 47.0% (the makers' run: full weights, 128k context) | [link](https://huggingface.co/ornith-ai/Ornith-1.5-9B) |

Each run's every trial is in `results/`.
