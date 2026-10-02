# purr on DeepSWE

[DeepSWE 1.1](https://huggingface.co/datasets/datacurve/deep-swe): 113 features and fixes in real
open-source projects (TypeScript, Go, Python, Rust, JavaScript), checked by the projects' own tests.
purr runs it through [Harbor](https://www.harborframework.com) (`tbench/purr_agent.py`), like
[Terminal-Bench](../terminal-bench/). It's the other benchmark in DeepSeek's harness comparison, so
the same eight harnesses have published scores with the same model.

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
- **dataset:** DeepSWE 1.1 (`datacurve/deep-swe-1-1` in Harbor's registry), each task's own time
  limit (90 minutes). The reference scores used 8 attempts per task: a closer estimate of the same
  pass@1, not an easier test.
- **network:** none in the task container, like theirs; purr only reaches the model
  (`--allow-agent-host openrouter.ai`).

## purr, version by version

### DeepSWE 1.1: full runs, all 113 tasks, 3 attempts each (`tbench/fair.sh --deepswe`)

| purr | date | pass@1 | pass@3 | timeouts | errors | cost | tokens in / cached / out | median time |
|---|---|---|---|---|---|---|---|---|
| (none yet) |

### DeepSWE 1.1: one-try runs, all 113 tasks, 1 attempt each (`tbench/fair.sh --deepswe --one`)

The same score as a full run (pass@1) in a third of the time, with a wider margin (see ±).
Fine to set next to the reference scores below, keeping the ± in mind.

| purr | date | pass@1 | timeouts | errors | cost | tokens in / cached / out | median time |
|---|---|---|---|---|---|---|---|
| (none yet) |

### DeepSWE 1.1: quick runs, the same 20 tasks (`tbench/quick-tasks-deepswe.txt`), 1 attempt each (`tbench/fair.sh --deepswe --quick`)

Cheap and quick, for seeing whether a purr version got better or worse. With 20 tasks and one
try each the margin is wide (see ±): compare quick runs with each other, not with the full
runs or the leaderboards.

| purr | date | pass@1 | timeouts | errors | cost | tokens in / cached / out | median time |
|---|---|---|---|---|---|---|---|
| (none yet) |

## Other harnesses, same model (published, full runs)

| harness | model | benchmark | pass@1 | source |
|---|---|---|---|---|
| mini-SWE-agent | DeepSeek V4.1 Flash | DeepSWE 1.1 (113 tasks) | 74.2% (8 trials per task) | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| DSH Minimal (DeepSeek's own) | DeepSeek V4.1 Flash | DeepSWE 1.1 (113 tasks) | 72.6% (8 trials per task) | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| DSH Standard | DeepSeek V4.1 Flash | DeepSWE 1.1 (113 tasks) | 70.5% (8 trials per task) | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| Claude Code | DeepSeek V4.1 Flash | DeepSWE 1.1 (113 tasks) | 69.8% (8 trials per task) | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| DSH PTC | DeepSeek V4.1 Flash | DeepSWE 1.1 (113 tasks) | 67.6% (8 trials per task) | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| Pi | DeepSeek V4.1 Flash | DeepSWE 1.1 (113 tasks) | 66.2% (8 trials per task) | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| Codex | DeepSeek V4.1 Flash | DeepSWE 1.1 (113 tasks) | 65.6% (8 trials per task) | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| OpenCode | DeepSeek V4.1 Flash | DeepSWE 1.1 (113 tasks) | 65.5% (8 trials per task) | [link](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |

Each run's every trial is in `results/`.
