"""Turn a finished Harbor run of purr into published results in the repo.

    tbench/publish.py ~/.local/state/purr/tbench/<job folder>

Two kinds of run get published, each in its own table: the full run (all 89 Terminal-Bench 2.0
tasks x 3 attempts, compared with other harnesses' published scores) and the quick run (the 20
tasks in tbench/quick-tasks.txt x 1, compared only with other quick runs). Anything else is
refused, so a partial run can't sneak into the tables.

Writes benchmarks/terminal-bench/results/<purr version>.json (every trial, plus the totals) and
rebuilds benchmarks/terminal-bench/README.md: how the run was done, a row per purr version, and
the other harnesses' published scores with the same model to compare against.
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
OUT = ROOT / "benchmarks" / "terminal-bench"

# Published scores to compare against: the same model through other harnesses. Each row says where
# it's from; only rows with the same model and benchmark are compared in the table.
REFERENCE = [
    # DeepSeek's model card: N=3 per task, temperature 1.0, top_p 0.95, 1M context, max_steps 500
    *[{"harness": h, "model": "DeepSeek V4.1 Flash", "benchmark": "Terminal-Bench 2.1 (89 tasks)", "pass@1": s,
       "source": "https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash"}
      for h, s in (("DSH Minimal (DeepSeek's own)", 90.6), ("mini-SWE-agent", 90.3), ("Claude Code", 88.0),
                   ("Pi", 86.1), ("DSH Standard", 85.8), ("DSH PTC", 85.8), ("OpenCode", 85.0), ("Codex", 84.1))],
    {"harness": "Ante", "model": "DeepSeek V4.1 Flash", "benchmark": "Terminal-Bench 2.1 (89 tasks)", "pass@1": 83.9,
     "source": "https://antigma.ai/eval", "note": "5 trials per task"},
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
            "error": exception if exception != "AgentTimeoutError" else None,
            "agent_seconds": round(seconds, 1) if seconds is not None else None,
            "cost_usd": agent.get("cost_usd"), "tokens_in": agent.get("n_input_tokens"),
            "tokens_cached": agent.get("n_cache_tokens"), "tokens_out": agent.get("n_output_tokens"),
            "version": (t.get("agent_info") or {}).get("version"),
            "model": ((t.get("agent_info") or {}).get("model_info") or {}).get("name"),
        })
    return job, config, trials


QUICK = [line.split()[0] for line in (Path(__file__).parent / "quick-tasks.txt").read_text().splitlines()
         if line.strip() and not line.startswith("#")]


def profile(dataset, trials):
    """ "full", "quick", or None (not a run we publish)."""
    tasks = {t["task"] for t in trials}
    per_task = len(trials) / max(len(tasks), 1)
    if not dataset.startswith("terminal-bench@2.0"):
        return None
    if tasks == set(QUICK) and per_task == 1:
        return "quick"
    if len(tasks) == 89 and per_task == 3:
        return "full"
    return None


def summarise(config, trials):
    """pass@1 = the mean over tasks of the share of attempts that passed (an error counts as a fail,
    like on the leaderboards); ± is the standard error over tasks."""
    by_task = defaultdict(list)
    for t in trials:
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


def readme(results):
    lines = [
        "# purr on Terminal-Bench",
        "",
        "purr runs [Terminal-Bench](https://www.tbench.ai) through [Harbor](https://www.harborframework.com),",
        "the same runner the public leaderboards use (`tbench/purr_agent.py`). Every purr version that changes",
        "how it works gets a run, so you can see whether it got better.",
        "",
        "## How it's run (`tbench/fair.sh`)",
        "",
        "As close as we can get to the settings DeepSeek used to run its model through other harnesses:",
        "",
        "- **model:** DeepSeek V4.1 Flash, served by DeepSeek itself (OpenRouter pinned to the `deepseek` host,\n  which uses prompts for training, so the account allows that;",
        "  no fallback to other hosts, some of which serve it at fp4/fp8)",
        "- **sampling:** temperature 1.0, top_p 0.95; up to 64k tokens per reply",
        "- **limits:** 1M context, 500 model calls per task, each task's own time limit",
        "- **attempts:** 3 per task; pass@1 = the average share of attempts that passed (timeouts and",
        "  errors count as fails, like on the leaderboards), ± the standard error over tasks",
        "- **purr:** as shipped (its MCP servers on), the exact commit recorded with every trial",
        "- **dataset:** `terminal-bench@2.0` from Harbor's registry. The reference scores below are on",
        "  Terminal-Bench 2.1, which has the same 89 tasks with fixes and isn't in the registry, so the",
        "  comparison is close but not exact.",
        "",
        "## purr, version by version",
        "",
    ]
    head = ["| purr | date | pass@1 | pass@3 | timeouts | errors | cost | tokens in / cached / out | median time |",
            "|---|---|---|---|---|---|---|---|---|"]
    quick_head = [h.replace(" pass@3 |", "").replace("---|---|---|---|---|---|---|---|---|", "---|---|---|---|---|---|---|---|")
                  for h in head]
    lines += ["### Full runs: all 89 tasks, 3 attempts each", ""] + head
    lines += [row(r, full=True) for r in sorted(results, key=key) if r.get("profile") == "full"] or ["| (none yet) |"]
    lines += ["", "### Quick runs: the same 20 tasks (`tbench/quick-tasks.txt`), 1 attempt each", "",
              "Cheap (well under $1 with DeepSeek V4.1 Flash) and quick, for seeing whether a purr version got better or worse. With 20",
              "tasks and one try each the margin is wide (see ±): compare quick runs with each other, not",
              "with the full runs or the leaderboards.", ""] + quick_head
    lines += [row(r, full=False) for r in sorted(results, key=key) if r.get("profile") == "quick"] or ["| (none yet) |"]
    lines += ["", "## Other harnesses, same model (published, full runs)", "",
              "| harness | model | benchmark | pass@1 | source |", "|---|---|---|---|---|"]
    for ref in REFERENCE:
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
    if len(argv) != 1:
        print(__doc__)
        return 1
    job, config, trials = load_job(argv[0])
    if not trials:
        print(f"no trials in {job}")
        return 1
    summary = summarise(config, trials)
    summary["profile"] = profile(summary["dataset"], trials)
    if not summary["profile"]:
        print(f"not published: {summary['tasks']} tasks x {summary['settings']['attempts']} on {summary['dataset']} "
              "is neither the full run (89 x 3, tbench/fair.sh) nor the quick one (the 20 in quick-tasks.txt x 1, "
              "tbench/fair.sh --quick)")
        return 1
    version = summary["purr"] if isinstance(summary["purr"], str) else "mixed"
    if "dirty" in version:
        print(f"warning: {version} had uncommitted changes, so this result can't be tied to a commit")
    (OUT / "results").mkdir(parents=True, exist_ok=True)
    name = f"{version}__{summary['profile']}__{job.name}.json"
    (OUT / "results" / name).write_text(json.dumps({"summary": summary, "trials": trials}, indent=1) + "\n")
    results = [json.loads(f.read_text())["summary"] for f in sorted((OUT / "results").glob("*.json"))]
    (OUT / "README.md").write_text(readme(results))
    print(f"purr {version} ({summary['profile']} run): pass@1 {summary['pass@1']}% ± {summary['stderr']} over {summary['tasks']} tasks "
          f"({summary['trials']} trials, {summary['errors']} errors), ${summary['cost_usd']}")
    print(f"wrote {OUT / 'results' / name} and {OUT / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
