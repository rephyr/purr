# purr: benchmarks

How purr is measured, and where the results are. Back to the [README](../README.md).

## Published results
- [`benchmarks/terminal-bench/`](../benchmarks/terminal-bench/): Terminal-Bench, the official numbers
  (full runs next to other harnesses on the same model, and cheap quick runs between versions).
- [`benchmarks/deepswe/`](../benchmarks/deepswe/): DeepSWE, 113 features and fixes in real projects, next
  to the same eight harnesses on the same model (`tbench/fair.sh --deepswe`).
- [`benchmarks/purr-bench/`](../benchmarks/purr-bench/): `purr bench` on purr's own small tasks.
  **Not official**: just for debugging, iterating on and improving the harness. File a run with
  `purr bench --publish` (the latest, or a folder).

## Benchmark: purr vs OpenCode
`purr bench` opens a window: tick the models (and tasks, contestants, runs), press start, and
watch a live table, a scoreboard with hearts and the cat do the work (`--plain` prints lines
instead). At the end a results page ranks everyone (♛), lists every number (tokens, calls,
each kind of mistake, repairs, cost) and shows a task-by-task grid; `s` and `l` switch between
it and the live log. Contestants: purr, opencode, purr+refine and purr-nocheck (purr without
its final check: when a model wants to stop after changing files, purr asks it once to go
through the request point by point; `final_check` in the settings). It gives purr and OpenCode the same small coding
tasks with the same model (both with everything allowed, both with the same time limit).
OpenCode runs as it ships: each run gets a config with only the model under test (the same
context and output cap purr has), none of your own OpenCode plugins, agents or models, and
`meta.json` records its version.
Every run starts from a fresh copy of the task; afterwards hidden tests decide whether it's
solved. `purr bench -m qwen3-coder-32k -t rename --runs 3` picks things up front.

It measures: solved, tests passed, time, tok/s, tokens, model calls, tool errors, failed edits,
hallucinations (a tool or file that doesn't exist, a tool call written as text, saying it's
done when the tests say no) and syntax errors left behind. A run that wanders out of its task
folder isn't counted. Results: `~/.local/state/purr/bench/<date>/report.md`. The tasks live
in `bench/tasks/` (a `task.json`, the starting `files/`, the hidden tests in `check/`, and for
the hard ones a reference `solution/` that a test checks really passes). Five are easy and six
are hard (★): subtle bugs behind passing tests, a bug hidden in a 15-file project, messy CSV
data, a dependency-order algorithm, a speed-up that mustn't change the output, and a game
inventory feature across four files. `--level hard` runs only those.

**Chat bench:** `purr bench --chat` measures what everyday use looks like: a chat of a few turns
instead of one request. Each scenario in `bench/chat/<name>/` is a `scenario.json` (its turns,
`files_from` to start from a bench task's files, and `checks_from_task` to be graded on that
task's hidden tests too), a `files/` overlay, the hidden tests in `check/final/` that decide
"solved" on the end state, optional `check/turnN/` graded on a copy after a turn (they show where
the chat slipped, they don't change "solved"), an optional `check/user/` (the user's own run: a
follow-up like "it still fails: {output}" is only sent when it fails, with its output), and a
reference `solution/` (with `solution_turnN/` for a turn the end state moved on from) that a
test checks really passes. Between turns the bench does what you would: edits, appends to, writes
or deletes files, runs a command (`keep` marks text the model must not write over; for a command,
`in` names its file) or calls `/undo`; a turn can also get a steer mid-way (`steer.at_step`).
Nine scenarios: a follow-up after a failing run, a hand edit to keep through a rename, `/undo`
then a different request, a steer on an easy feature, a rule from the first turn that matters in
the third, tests that a later turn breaks, a vague "make it faster" (and "still slow" when it
isn't), a refactor across two turns, and a rename the user did with `sed` between turns.

Contestants: `purr`, and `purr-nochat`: purr without its notes for files changed behind its back
and for tests that passed earlier and fail now (`outside_changes` and `regression_note` in the
settings). Without `outside_changes` the files you changed also stay "read", so purr-nochat
doesn't get `read_before_edit`'s request for a fresh read either. OpenCode comes later. Each turn
gets `--turn-timeout` seconds (420; a scenario's own `timeout` for a turn replaces it) and the
whole chat `--timeout` (840, also the most either may be); a turn leaves 30 seconds for each turn
still to come, and a turn's commands are cut to fit its time. `--level easy` or `hard` and `-t`
pick scenarios as usual. Besides the usual table, the report has a chat table (turn checks passed,
counting a skipped turn's as failed; user edits lost; follow-ups needed; steers that came after
the turn ended, or were lost for lack of time; outside-change and regression notes; "Decided:"
lines after a green final check; `/undo` that left something behind; runs not counted because a
hand edit couldn't apply) and every turn's result and events. Plain mode only for now (`--watch`
works); results go to `~/.local/state/purr/bench/<date>_chat/`, and `--publish` takes the newest
run, chat or not (`--you` never adds your row to a chat run).

**Play it yourself:** `purr bench --you` hands you the tasks one by one: a fresh copy of the
files and the prompt (**enter** opens VS Code, **n** nvim; **r** runs the task's program so you can see what it does, **t** runs the visible tests, **enter**
again when you're done). The same hidden tests grade you, failures are named so you learn what
you missed, and your row ("you / human") is added at the end of the latest bench's results
(`--new` for a folder of your own). `--level hard`, `--vague` and `-t` work as usual. The hardest
task, **idle-catchup** (from desk-pets' idle core), is aimed at top models: exact catch-up where
one big step must equal any split, ties at a buff's line, and float crumbs that add up to coins.

**The real ones:** `purr bench --real` (or **r** on the setup screen) runs Terminal-Bench 2.1 or
DeepSWE 1.1 the way other harnesses are scored (`tbench/fair.sh` underneath, so the same fair
settings and checks). Pick the benchmark and how much (the quick 20, every task once, or ×3) and
it shows the time, cost and scores to beat; then a table of every task (setting up, purr working,
grading, ✓ / ✗ / out of time / broke) next to the live log of the one you pick (**f** follows the
newest), the score so far and the cost. Mochi does what the purr she's watching does, cheers or
sniffles as tasks get graded, and at the end reacts to the score: next to the published harnesses
for a whole run, next to the last quick run for a quick one. **p** publishes it to `benchmarks/`.
`purr bench --real deepswe --size quick --jobs 8` skips the picking; `--plain` just runs fair.sh.

**For the leaderboard:** `tbench/fair.sh --submit` runs the official Terminal-Bench 2.0 leaderboard's
own dataset (Harbor Hub: `terminal-bench/terminal-bench-2`) the way a submission has to be run: every
task 5 times, the tasks' own time limits, no overrides, and purr's web tool off (the rules: no looking
at the benchmark's site or repo). `tbench/submit.py <job>` then checks the finished run against every
rule the leaderboard's bot and reviewers check (and that no API key is anywhere in it), writes the
`metadata.yaml`, and says the two steps that need your accounts: `harbor upload <job> --public`, and
the submission itself. With DeepSeek V4.1 Flash that's 445 tries, about $5 off-peak (evenings and
nights here) and a night of running.

## Real benchmarks: Terminal-Bench
`tbench/` plugs purr into [Harbor](https://www.harborframework.com), the runner behind
**Terminal-Bench** (and other benchmarks in its registry), so purr is graded on the same tasks as
OpenCode, Claude Code, Codex and Aider on the public leaderboards. Each task runs in its own Docker
container: the adapter (`tbench/purr_agent.py`) installs uv + Python 3.12 there, copies purr in
(plain mode only), writes a config for the model Harbor picks (using purr's own provider settings
and model details) and runs `purr -p <task> --yes` once.

    uv tool install harbor                          # once
    tbench/run.sh -i terminal-bench/html-js-filter  # one task
    tbench/run.sh -l 10 -n 4                        # 10 tasks, 4 at a time (API models)
    tbench/run.sh -l 10 --ak mcp=false              # without purr's MCP servers
    PURR_MODEL=openrouter/<model id> tbench/run.sh  # default: DeepSeek V4.1 Flash on OpenRouter

Each task's own time limit is passed on (less 10%, `purr --time-limit`): purr tells the model up
front and reminds it at half and four-fifths time to get a working version in place, since an
unfinished deliverable scores nothing. `--ak edge_cases=true` turns on the edge-case step of the
final check, which purr leaves off for API models, to measure whether it helps here.

`run.sh` hands purr's saved OpenRouter key to Harbor and passes everything else to `harbor run`.
Results land in `~/.local/state/purr/tbench/`, with purr's own log (`agent/purr.txt`) and saved
chat (`agent/purr-state/`) per task.
