"""Turn a finished Harbor run of purr into published results in the repo.

    tbench/publish.py ~/.local/state/purr/tbench/<job folder>
    tbench/publish.py <job folder> --stopped "what happened"

A run stopped part-way can be filed with --stopped and a note saying why: it goes in its own table
of stopped runs (the score over the tasks graded before it stopped), never next to the real ones.

Two benchmarks, each with its own page: Terminal-Bench (benchmarks/terminal-bench/) and DeepSWE
(benchmarks/deepswe/). Three kinds of run get published, each in its own table: the full run (every
task x 3 attempts, compared with other harnesses' published scores), the one-try run (every task x 1:
the same score with a wider margin, in a third of the time) and the quick run (a frozen set of 20
tasks x 1, compared only with other quick runs). Terminal-Bench 2.1 and the older 2.0 runs get
separate tables. Anything else is refused, so a partial run can't sneak into the tables.

Writes benchmarks/<page>/results/<purr version>.json (every trial, plus the totals) and rebuilds
benchmarks/<page>/README.md: how the run was done, a row per purr version, and the other harnesses'
published scores with the same model to compare against.
Plain Python (no packages), so it runs with any python3.
"""

import datetime
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).parent
CARD = "https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash"

# Published scores to compare against: the same model through other harnesses. Each row says where
# it's from; only rows with the same model and benchmark are compared in the table.
REFERENCE = [
    # DeepSeek's model card: N=3 per task, temperature 1.0, top_p 0.95, 1M context, max_steps 500,
    # no network in the task container
    *[{"harness": h, "model": "DeepSeek V4.1 Flash", "benchmark": "Terminal-Bench 2.1 (89 tasks)", "pass@1": s,
       "source": CARD} for h, s in (
          ("DSH Minimal (DeepSeek's own)", 90.6), ("mini-SWE-agent", 90.3), ("Claude Code", 88.0), ("Pi", 86.1),
          ("DSH Standard", 85.8), ("DSH PTC", 85.8), ("OpenCode", 85.0), ("Codex", 84.1))],
    {"harness": "Ante", "model": "DeepSeek V4.1 Flash", "benchmark": "Terminal-Bench 2.1 (89 tasks)", "pass@1": 83.9,
     "source": "https://antigma.ai/eval", "note": "5 trials per task"},
    # local models (tbench/local.sh): the makers' own runs, to set this machine's runs next to
    {"harness": "Terminus-2", "model": "Ornith-1.5-9B", "benchmark": "Terminal-Bench 2.1 (89 tasks)", "pass@1": 47.0,
     "source": "https://huggingface.co/ornith-ai/Ornith-1.5-9B", "note": "the makers' run: full weights, 128k context"},
    # the same model card, same settings, N=8 per task
    *[{"harness": h, "model": "DeepSeek V4.1 Flash", "benchmark": "DeepSWE 1.1 (113 tasks)", "pass@1": s,
       "source": CARD, "note": "8 trials per task"} for h, s in (
          ("mini-SWE-agent", 74.2), ("DSH Minimal (DeepSeek's own)", 72.6), ("DSH Standard", 70.5),
          ("Claude Code", 69.8), ("DSH PTC", 67.6), ("Pi", 66.2), ("Codex", 65.6), ("OpenCode", 65.5))],
]


def load_job(job):
    job = Path(job).expanduser().resolve()
    config = json.loads((job / "config.json").read_text())
    trials = []
    for f in sorted(job.glob("*/result.json")):
        t = json.loads(f.read_text())
        reward = ((t.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        agent = t.get("agent_result") or {}
        ex = t.get("agent_execution") or {}
        seconds = None
        if ex.get("started_at") and ex.get("finished_at"):
            seconds = (datetime.datetime.fromisoformat(ex["finished_at"].replace("Z", "+00:00"))
                       - datetime.datetime.fromisoformat(ex["started_at"].replace("Z", "+00:00"))).total_seconds()
        exception = (t.get("exception_info") or {}).get("exception_type")
        trials.append({
            "task": t["task_name"].split("/")[-1], "trial": t["trial_name"],
            "reward": reward, "timeout": exception == "AgentTimeoutError",
            # a time limit is the task's own rule (a fail, like on the leaderboards); anything else
            # (a crash, the model server) is an error worth re-running
            "error": exception if exception not in ("AgentTimeoutError", "CancelledError") else None,
            "cancelled": exception == "CancelledError",  # the run was stopped first: never graded
            "agent_seconds": round(seconds, 1) if seconds is not None else None,
            "cost_usd": agent.get("cost_usd"), "tokens_in": agent.get("n_input_tokens"),
            "tokens_cached": agent.get("n_cache_tokens"), "tokens_out": agent.get("n_output_tokens"),
            "version": (t.get("agent_info") or {}).get("version"),
            "model": ((t.get("agent_info") or {}).get("model_info") or {}).get("name"),
        })
    return job, config, trials


def _quick(name):
    return [line.split()[0] for line in (HERE / name).read_text().splitlines()
            if line.strip() and not line.startswith("#")]


QUICK = _quick("quick-tasks.txt")

# Harbor's dataset name -> the benchmark it is. 2.1 is what the reference scores used; the first
# Terminal-Bench runs were on 2.0 (the same 89 tasks before 2.1's fixes) and keep their own tables.
# page: the folder under benchmarks/ its results go in.
BENCHES = {
    "Terminal-Bench 2.1": {"dataset": "terminal-bench/terminal-bench-2-1", "tasks": 89, "quick": QUICK,
                           "page": "terminal-bench", "flag": ""},
    "Terminal-Bench 2.0": {"dataset": "terminal-bench", "tasks": 89, "quick": QUICK, "page": "terminal-bench",
                           "old": True, "flag": ""},
    "DeepSWE 1.1": {"dataset": "datacurve/deep-swe-1-1", "tasks": 113, "quick": _quick("quick-tasks-deepswe.txt"),
                    "quick_file": "quick-tasks-deepswe.txt", "page": "deepswe", "flag": "--deepswe "},
}


def is_local(r):
    """A run on this machine's GPU (tbench/local.sh), not the hosted model the tables compare."""
    return str(r.get("model") or "").startswith("ollama/")


def bench_name(dataset):
    """ "Terminal-Bench 2.1", "Terminal-Bench 2.0", "DeepSWE 1.1", or None (a dataset we don't publish)."""
    name, _, version = str(dataset).partition("@")
    if name == "terminal-bench" and not version.startswith("2.0"):
        return None  # 4.0 and later have the same name
    return next((b for b, c in BENCHES.items() if c["dataset"] == name), None)


def profile(dataset, trials):
    """ "full", "one", "quick", or None (not a run we publish)."""
    tasks = {t["task"] for t in trials}
    per_task = len(trials) / max(len(tasks), 1)
    bench = BENCHES.get(bench_name(dataset))
    if not bench:
        return None
    if tasks == set(bench["quick"]) and per_task == 1:
        return "quick"
    if len(tasks) == bench["tasks"] and per_task == 3:
        return "full"
    if len(tasks) == bench["tasks"] and per_task == 1:
        return "one"
    return None


def summarise(config, trials):
    """pass@1 = the mean over tasks of the share of attempts that passed (an error counts as a fail,
    like on the leaderboards); ± is the standard error over tasks."""
    by_task = defaultdict(list)
    for t in trials:
        if not t.get("cancelled"):
            by_task[t["task"]].append(1.0 if (t["reward"] or 0) >= 1 else 0.0)
    shares = [sum(v) / len(v) for v in by_task.values()]
    n = len(shares)
    se = statistics.stdev(shares) / math.sqrt(n) if n > 1 else 0.0
    agent = (config.get("agents") or [{}])[0]
    versions = sorted({t["version"] for t in trials if t["version"]})
    total = lambda key: sum(t[key] or 0 for t in trials)  # noqa: E731
    times = [t["agent_seconds"] for t in trials if t["agent_seconds"] is not None]
    return {
        "purr": versions[0] if len(versions) == 1 else versions,
        "date": datetime.date.today().isoformat(),
        "model": agent.get("model_name"),
        "dataset": "@".join(str(x) for x in ((config.get("datasets") or [{}])[0].get("name"),
                                              (config.get("datasets") or [{}])[0].get("version")) if x),
        "dataset_ref": (config.get("datasets") or [{}])[0].get("ref"),
        "settings": {**(agent.get("kwargs") or {}), "attempts": round(len(trials) / max(n, 1)),
                     "agent_timeout_multiplier": config.get("agent_timeout_multiplier", 1.0)},
        "tasks": n, "trials": len(trials), "errors": sum(1 for t in trials if t["error"]),
        "timeouts": sum(1 for t in trials if t.get("timeout")),
        "pass@1": round(100 * sum(shares) / n, 1) if n else 0.0,
        "stderr": round(100 * se, 1),
        "pass@k": round(100 * sum(1 for s in shares if s > 0) / n, 1) if n else 0.0,
        "cost_usd": round(total("cost_usd"), 2), "tokens_in": total("tokens_in"),
        "tokens_cached": total("tokens_cached"), "tokens_out": total("tokens_out"),
        "median_agent_minutes": round(statistics.median(times) / 60, 1) if times else None,
    }


def short(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)


INTRO = {
    "terminal-bench": [
        "# purr on Terminal-Bench",
        "",
        "purr runs [Terminal-Bench](https://www.tbench.ai) through [Harbor](https://www.harborframework.com),",
        "the same runner the public leaderboards use (`tbench/purr_agent.py`). Every purr version that changes",
        "how it works gets a run, so you can see whether it got better. Also: [DeepSWE](../deepswe/).",
    ],
    "deepswe": [
        "# purr on DeepSWE",
        "",
        "[DeepSWE 1.1](https://huggingface.co/datasets/datacurve/deep-swe): 113 features and fixes in real",
        "open-source projects (TypeScript, Go, Python, Rust, JavaScript), checked by the projects' own tests.",
        "purr runs it through [Harbor](https://www.harborframework.com) (`tbench/purr_agent.py`), like",
        "[Terminal-Bench](../terminal-bench/). It's the other benchmark in DeepSeek's harness comparison, so",
        "the same eight harnesses have published scores with the same model.",
    ],
}
DATASET_NOTE = {
    "terminal-bench": [
        "- **dataset:** Terminal-Bench 2.1 (`terminal-bench/terminal-bench-2-1` in Harbor's registry), the",
        "  version the reference scores below used. The first runs were on 2.0 (the same 89 tasks before",
        "  2.1's fixes), so they have their own tables: don't compare across the two.",
        "- **network:** open in the task container, where DeepSeek's runs had none: tasks that need to",
        "  download something are easier here than in theirs.",
    ],
    "deepswe": [
        "- **dataset:** DeepSWE 1.1 (`datacurve/deep-swe-1-1` in Harbor's registry), each task's own time",
        "  limit (90 minutes). The reference scores used 8 attempts per task: a closer estimate of the same",
        "  pass@1, not an easier test.",
        "- **network:** none in the task container, like theirs; purr only reaches the model",
        "  (`--allow-agent-host openrouter.ai`).",
    ],
}


def readme(results, page="terminal-bench"):
    lines = INTRO[page] + [
        "",
        "## How it's run (`tbench/fair.sh`)",
        "",
        "As close as we can get to the settings DeepSeek used to run its model through other harnesses:",
        "",
        "- **model:** DeepSeek V4.1 Flash, served by DeepSeek itself (OpenRouter pinned to the `deepseek` host,",
        "  which uses prompts for training, so the account allows that; no fallback to other hosts, some of",
        "  which serve it at fp4/fp8)",
        "- **sampling:** temperature 1.0, top_p 0.95; up to 64k tokens per reply",
        "- **limits:** 1M context, 500 model calls per task, each task's own time limit",
        "- **attempts:** 3 per task (1 in one-try and quick runs); pass@1 = the average share of attempts",
        "  that passed (timeouts and errors count as fails, like on the leaderboards), ± the standard error",
        "  over tasks",
        "- **purr:** as shipped (its MCP servers on), the exact commit recorded with every trial",
    ] + DATASET_NOTE[page] + [
        "",
        "## purr, version by version",
        "",
    ]
    head = ["| purr | date | pass@1 | pass@3 | timeouts | errors | cost | tokens in / cached / out | median time |",
            "|---|---|---|---|---|---|---|---|---|"]
    quick_head = [h.replace(" pass@3 |", "").replace("---|---|---|---|---|---|---|---|---|", "---|---|---|---|---|---|---|---|")
                  for h in head]
    for bench, conf in BENCHES.items():
        if conf["page"] != page:
            continue
        everything = [r for r in sorted(results, key=key) if bench_name(r.get("dataset")) == bench]
        mine = [r for r in everything if not is_local(r)]
        local = [r for r in everything if is_local(r)]
        main = not conf.get("old")
        if not mine and not main:
            continue  # an old dataset only shows when it has runs
        n, flag = conf["tasks"], conf["flag"]
        full = [row(r, full=True) for r in mine if r.get("profile") == "full"]
        one = [row(r, full=False) for r in mine if r.get("profile") == "one"]
        quick = [row(r, full=False) for r in mine if r.get("profile") == "quick"]
        if main or full:
            lines += [f"### {bench}: full runs, all {n} tasks, 3 attempts each (`tbench/fair.sh {flag}`)".replace(" `)", "`)"),
                      ""] + head
            lines += full or ["| (none yet) |"]
            lines += [""]
        if main or one:
            lines += [f"### {bench}: one-try runs, all {n} tasks, 1 attempt each (`tbench/fair.sh {flag}--one`)", ""]
            if main:
                lines += ["The same score as a full run (pass@1) in a third of the time, with a wider margin (see ±).",
                          "Fine to set next to the reference scores below, keeping the ± in mind.", ""]
            lines += quick_head + (one or ["| (none yet) |"]) + [""]
        lines += [f"### {bench}: quick runs, the same 20 tasks (`tbench/{conf.get('quick_file', 'quick-tasks.txt')}`), "
                  f"1 attempt each (`tbench/fair.sh {flag}--quick`)", ""]
        if main:
            lines += ["Cheap and quick, for seeing whether a purr version got better or worse. With 20 tasks and one",
                      "try each the margin is wide (see ±): compare quick runs with each other, not with the full",
                      "runs or the leaderboards.", ""]
        lines += quick_head + (quick or ["| (none yet) |"]) + [""]
        if local:
            lines += [f"### {bench}: local models on this machine (`tbench/local.sh`)", "",
                      "Free runs on the RTX 4080 (Ollama, quantized, one task at a time): for purr's own target, small",
                      "models. Set them next to the same model's published score below, not the hosted rows.", "",
                      "| purr | model | run | date | pass@1 | timeouts | errors | median time |", "|---|---|---|---|---|---|---|---|"]
            lines += [f"| {r['purr']} | {str(r['model']).split('/', 1)[1]} | {r.get('profile')} | {r['date']} | "
                      f"**{r['pass@1']}%** ± {r['stderr']} | {r.get('timeouts', 0)} | {r['errors']}/{r['trials']} | "
                      f"{r['median_agent_minutes']} min |" for r in local] + [""]
        stopped = [r for r in mine if r.get("profile") == "stopped"]
        if stopped:
            lines += [f"### {bench}: stopped runs (not comparable)", "",
                      "Runs stopped part-way, kept for the record: the score is over the tasks graded before the",
                      "stop, and the note says why it stopped. Don't set these next to the reference scores.", "",
                      "| purr | date | graded | pass@1 of those | cost | why it stopped |", "|---|---|---|---|---|---|"]
            lines += [f"| {r['purr']} | {r['date']} | {r['tasks']} of {conf['tasks']} | {r['pass@1']}% | "
                      f"${r['cost_usd']} | {r.get('note', '')} |" for r in stopped] + [""]
    lines.pop()
    lines += ["", "## Published scores to compare with (other harnesses, full runs)", "",
              "| harness | model | benchmark | pass@1 | source |", "|---|---|---|---|---|"]
    names = {b for b, c in BENCHES.items() if c["page"] == page}
    for ref in REFERENCE:
        if not any(ref["benchmark"].startswith(b) for b in names):
            continue
        note = f" ({ref['note']})" if ref.get("note") else ""
        lines.append(f"| {ref['harness']} | {ref['model']} | {ref['benchmark']} | {ref['pass@1']}%{note} | "
                     f"[link]({ref['source']}) |")
    lines += ["", "Each run's every trial is in `results/`.", ""]
    return "\n".join(lines)


def key(r):
    return (r["date"], str(r["purr"]))


def row(r, full):
    third = f" {r['pass@k']}% |" if full else ""
    return (f"| {r['purr']} | {r['date']} | **{r['pass@1']}%** ± {r['stderr']} |{third} {r.get('timeouts', 0)} | "
            f"{r['errors']}/{r['trials']} | ${r['cost_usd']} | {short(r['tokens_in'])} / {short(r['tokens_cached'])} / "
            f"{short(r['tokens_out'])} | {r['median_agent_minutes']} min |")


def main(argv):
    note = None
    if len(argv) == 3 and argv[1] == "--stopped" and argv[2].strip():
        argv, note = argv[:1], argv[2].strip()
    if len(argv) != 1:
        print(__doc__)
        return 1
    job, config, trials = load_job(argv[0])
    if not trials:
        print(f"no trials in {job}")
        return 1
    summary = summarise(config, trials)
    summary["profile"] = profile(summary["dataset"], trials)
    if note:
        if not bench_name(summary["dataset"]):
            print(f"not published: {summary['dataset']} isn't a benchmark this publishes")
            return 1
        summary["profile"], summary["note"] = "stopped", note
    if not summary["profile"]:
        print(f"not published: {summary['tasks']} tasks x {summary['settings']['attempts']} on {summary['dataset']} "
              "is not a full run (every task x 3, tbench/fair.sh), a one-try run (every task x 1, --one) or a "
              "quick one (the 20 in its quick-tasks file x 1, --quick)")
        return 1
    page = BENCHES[bench_name(summary["dataset"])]["page"]
    out = ROOT / "benchmarks" / page
    version = summary["purr"] if isinstance(summary["purr"], str) else "mixed"
    if "dirty" in version:
        print(f"warning: {version} had uncommitted changes, so this result can't be tied to a commit")
    (out / "results").mkdir(parents=True, exist_ok=True)
    name = f"{version}__{summary['profile']}__{job.name}.json"
    (out / "results" / name).write_text(json.dumps({"summary": summary, "trials": trials}, indent=1) + "\n")
    results = [json.loads(f.read_text())["summary"] for f in sorted((out / "results").glob("*.json"))]
    (out / "README.md").write_text(readme(results, page))
    print(f"purr {version} ({summary['profile']} run): pass@1 {summary['pass@1']}% ± {summary['stderr']} over {summary['tasks']} tasks "
          f"({summary['trials']} trials, {summary['errors']} errors), ${summary['cost_usd']}")
    print(f"wrote {out / 'results' / name} and {out / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
