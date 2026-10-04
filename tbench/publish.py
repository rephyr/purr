"""Turn a finished Harbor run of purr into published results in the repo.

    tbench/publish.py ~/.local/state/purr/tbench/<job folder>
    tbench/publish.py <job folder> --stopped "what happened"
    tbench/publish.py --pr <job folder> [<job folder> ...]
    tbench/publish.py <job folder> --note "two tasks' graders are broken right now"

--note: a footnote under the run's row, for something a reader should know about it.

--pr: then everything not committed (the new results and pages, and anything else waiting) goes
into one pull request on a branch of its own, co-authored by purr-<report_model> (config.toml:
co_author_email, report_model), and the checkout goes back to where it was.

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
    {"harness": "Claude Code", "model": "Ornith-1.5-9B", "benchmark": "Terminal-Bench 2.1 (89 tasks)", "pass@1": 47.0,
     "source": "https://huggingface.co/ornith-ai/Ornith-1.5-9B",
     "note": "the makers' run: Claude Code 2.1.126, full weights, average of 5 runs"},
    # the same model card, same settings, N=8 per task
    *[{"harness": h, "model": "DeepSeek V4.1 Flash", "benchmark": "DeepSWE 1.1 (113 tasks)", "pass@1": s,
       "source": CARD, "note": "8 trials per task"} for h, s in (
          ("mini-SWE-agent", 74.2), ("DSH Minimal (DeepSeek's own)", 72.6), ("DSH Standard", 70.5),
          ("Claude Code", 69.8), ("DSH PTC", 67.6), ("Pi", 66.2), ("Codex", 65.6), ("OpenCode", 65.5))],
]


FINISHED = ("passed", "failed", "error", "timeout")  # graded (a cancelled try never was)


def outcome(result):
    """A trial's result.json, read once for everyone: state (passed, failed, timeout, error,
    cancelled), reward, error (Harbor's exception, not the time limit), seconds, cost."""
    ex = (result.get("exception_info") or {}).get("exception_type")
    reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    run = result.get("agent_execution") or {}
    seconds = None
    if run.get("started_at") and run.get("finished_at"):
        seconds = (datetime.datetime.fromisoformat(run["finished_at"].replace("Z", "+00:00"))
                   - datetime.datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))).total_seconds()
    if ex == "CancelledError":  # the run was stopped first: never graded, so not a fail either
        state = "cancelled"
    elif ex == "AgentTimeoutError":  # the task's own time limit: a fail, unless the files were right
        state = "passed" if (reward or 0) >= 1 else "timeout"
    elif ex and reward is None:  # broke before grading (Harbor, Docker): not purr's answer
        state = "error"
    else:
        state = "passed" if (reward or 0) >= 1 else "failed"
    return {"state": state, "reward": reward, "seconds": seconds,
            "error": ex if ex not in (None, "AgentTimeoutError", "CancelledError") else None,
            "cost": (result.get("agent_result") or {}).get("cost_usd")}


def pass_at_1(by_task):
    """{task: [1.0 or 0.0 per graded try]} -> (pass@1 %, its ± as the standard error over tasks)."""
    shares = [sum(v) / len(v) for v in by_task.values() if v]
    if not shares:
        return None, None
    se = statistics.stdev(shares) / math.sqrt(len(shares)) if len(shares) > 1 else 0.0
    return round(100 * sum(shares) / len(shares), 1), round(100 * se, 1)


def load_job(job):
    job = Path(job).expanduser().resolve()
    config = json.loads((job / "config.json").read_text())
    trials = []
    sys.path.insert(0, str(ROOT))
    from harness import benchguard
    for f in sorted(job.glob("*/result.json")):
        t = json.loads(f.read_text())
        agent = t.get("agent_result") or {}
        o = outcome(t)
        looked = benchguard.looked_up(f.parent)  # it fetched the benchmark itself: not solved, whatever it scored
        if looked and o["state"] == "passed":
            o = {**o, "state": "failed", "reward": 0.0}
        trials.append({
            "task": t["task_name"].split("/")[-1], "trial": t["trial_name"],
            "reward": o["reward"], "timeout": o["state"] == "timeout",
            # a time limit is the task's own rule (a fail, like on the leaderboards); anything else
            # (a crash, the model server) is an error worth re-running
            "error": o["error"],
            "cancelled": o["state"] == "cancelled",  # the run was stopped first: never graded
            "agent_seconds": round(o["seconds"], 1) if o["seconds"] is not None else None,
            "cost_usd": agent.get("cost_usd"), "tokens_in": agent.get("n_input_tokens"),
            "tokens_cached": agent.get("n_cache_tokens"), "tokens_out": agent.get("n_output_tokens"),
            "version": (t.get("agent_info") or {}).get("version"),
            "looked_up": looked,
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
    # the registry's 2.0, and the same 89 tasks as Harbor Hub's leaderboard package
    "Terminal-Bench 2.0": {"dataset": "terminal-bench/terminal-bench-2", "tasks": 89, "quick": QUICK,
                           "aliases": ("terminal-bench@2.0",), "page": "terminal-bench", "old": True, "flag": ""},
    "DeepSWE 1.1": {"dataset": "datacurve/deep-swe-1-1", "tasks": 113, "quick": _quick("quick-tasks-deepswe.txt"),
                    "quick_file": "quick-tasks-deepswe.txt", "page": "deepswe", "flag": "--deepswe "},
}


# what tbench/fair.sh runs with, the same as DeepSeek's harness comparison: a hosted run with other
# settings would sit next to their scores without being comparable (local runs have their own table)
FAIR = {"model": "openrouter/deepseek/deepseek-v4.1-flash", "hosts": "deepseek", "temperature": 1.0,
        "top_p": 0.95, "max_tokens": 65536, "max_steps": 500}


def unfair(summary):
    """The fair settings this run didn't use: [] when it did (or it's a local run)."""
    if is_local(summary):
        return []
    got = {"model": summary.get("model"), **(summary.get("settings") or {})}
    wrong = []
    for key, want in FAIR.items():
        have = got.get(key)
        same = have is not None and (float(have) == float(want) if isinstance(want, (int, float)) else str(have) == want)
        if not same:
            wrong.append(f"{key}={have} (fair: {want})")
    return wrong


def is_local(r):
    """A run on this machine's GPU (tbench/local.sh), not the hosted model the tables compare."""
    return str(r.get("model") or "").startswith("ollama/")


def bench_name(dataset):
    """ "Terminal-Bench 2.1", "Terminal-Bench 2.0", "DeepSWE 1.1", or None (a dataset we don't publish)."""
    name, _, version = str(dataset).partition("@")
    for bench, conf in BENCHES.items():
        for alias in (conf["dataset"], *conf.get("aliases", ())):
            alias_name, _, alias_version = alias.partition("@")
            # terminal-bench@4.0 shares the registry name with 2.0: the version decides
            if name == alias_name and (not alias_version or version.startswith(alias_version)):
                return bench
    return None


def profile(dataset, trials):
    """ "full" (3 tries), "submit" (5, the leaderboard's), "one", "quick", or None (not a run we
    publish). Tries cancelled by stopping the run were never graded, so they don't count."""
    graded = [t for t in trials if not t.get("cancelled")]
    tasks = {t["task"] for t in graded}
    per_task = len(graded) / max(len(tasks), 1)
    bench = BENCHES.get(bench_name(dataset))
    if not bench:
        return None
    if tasks == set(bench["quick"]) and per_task == 1:
        return "quick"
    if len(tasks) == bench["tasks"] and per_task == 3:
        return "full"
    if len(tasks) == bench["tasks"] and per_task == 5:
        return "submit"
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
    pct, se = pass_at_1(by_task)
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
        "settings": {**(agent.get("kwargs") or {}),
                     "attempts": round(sum(not t.get("cancelled") for t in trials) / max(n, 1)),
                     "agent_timeout_multiplier": config.get("agent_timeout_multiplier", 1.0)},
        "tasks": n, "trials": len(trials), "errors": sum(1 for t in trials if t["error"]),
        "timeouts": sum(1 for t in trials if t.get("timeout")),
        "pass@1": pct if n else 0.0,
        "stderr": se if n else 0.0,
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
        full = [row(r, full=True) for r in mine if r.get("profile") == "full"] + footnotes(mine, "full")
        one = [row(r, full=False) for r in mine if r.get("profile") == "one"] + footnotes(mine, "one")
        quick = [row(r, full=False) for r in mine if r.get("profile") == "quick"] + footnotes(mine, "quick")
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
            if conf.get("quick_file", "quick-tasks.txt") == "quick-tasks.txt":  # Terminal-Bench's set
                lines += ["Since 2026-10-03 the set has git-multibranch instead of qemu-alpine-ssh, whose grader broke",
                          "(even the task's reference solution scores 0): earlier quick runs scored it 0.", ""]
        lines += quick_head + (quick or ["| (none yet) |"]) + [""]
        if local:
            lines += [f"### {bench}: local models on this machine (`tbench/local.sh`)", "",
                      "Free runs on the RTX 4080 (Ollama, quantized, one task at a time): for purr's own target, small",
                      "models. Set them next to the same model's published score below, not the hosted rows.", "",
                      "| purr | model | run | date | pass@1 | timeouts | errors | median time |", "|---|---|---|---|---|---|---|---|"]
            lines += [f"| {r['purr']} | {str(r['model']).split('/', 1)[1]} | {r.get('profile')} | {r['date']} | "
                      f"**{r['pass@1']}%** ± {r['stderr']} | {r.get('timeouts', 0)} | {r['errors']}/{r['trials']} | "
                      f"{r['median_agent_minutes']} min |" for r in local] + [""]
        submits = [row(r, full=True) for r in mine if r.get("profile") == "submit"]
        if submits:
            lines += [f"### {bench}: leaderboard runs, all {n} tasks, 5 attempts each (`tbench/fair.sh --submit`)", ""]
            lines += [h.replace("pass@3", "pass@5") for h in head] + submits + [""]
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


# options that change how purr works, named in its row: a minimal run isn't purr's usual harness
VARIANTS = {"minimal": "minimal", "keep_reasoning": "all thinking kept"}


def label(r):
    """The version, and the variant it ran as: 0.4.0+abc (minimal)."""
    settings = r.get("settings") or {}
    on = [name for key, name in VARIANTS.items()
          if str(settings.get(key, "")).lower() not in ("", "false", "0", "no", "off", "none")]
    if str(settings.get("extras", "")).lower() in ("false", "0", "no", "off"):
        on.append("without the newer checks")
    more_time = float(settings.get("agent_timeout_multiplier") or 1.0)
    if more_time != 1.0:
        on.append(f"×{more_time:g} time")
    return f"{r['purr']} ({', '.join(on)})" if on else str(r["purr"])


def row(r, full):
    third = f" {r['pass@k']}% |" if full else ""
    mark = " †" if r.get("note") and r.get("profile") != "stopped" else ""
    return (f"| {label(r)}{mark} | {r['date']} | **{r['pass@1']}%** ± {r['stderr']} |{third} {r.get('timeouts', 0)} | "
            f"{r['errors']}/{r['trials']} | ${r['cost_usd']} | {short(r['tokens_in'])} / {short(r['tokens_cached'])} / "
            f"{short(r['tokens_out'])} | {r['median_agent_minutes']} min |")


REPORT_MODEL = "claude-opus-5.5"  # the co-author model on report PRs (config.toml report_model)


def report_pr(summaries):
    """One pull request with everything not committed: the runs just published, their pages, and
    whatever else was waiting. Returns its URL; raises RuntimeError when it can't."""
    sys.path.insert(0, str(ROOT))
    from harness import pr
    from harness import settings
    config = settings.load(discover=False)
    model = config.get("report_model", REPORT_MODEL)
    files = pr.changed_files(ROOT) or []
    runs = [f"{bench_name(s['dataset'])} {s['profile']} {s['pass@1']}%" for s in summaries]
    title = (f"Benchmark: {runs[0]} (purr {label(summaries[0])})" if len(runs) == 1
             else f"Benchmarks: {len(runs)} runs ({', '.join(runs)})")[:90]
    lines = [f"- **{bench_name(s['dataset'])}, {s['profile']}** · {s['model']} · purr {label(s)}: "
             f"**{s['pass@1']}%** ± {s['stderr']} over {s['tasks']} tasks ({s['trials']} trials, "
             f"{s['errors']} errors, {s.get('timeouts', 0)} out of time), ${s['cost_usd']}, "
             f"median {s['median_agent_minutes']} min a task" for s in summaries]
    body = ("\n".join(lines) + "\n\nEverything that wasn't committed:\n```\n" + "\n".join(files) + "\n```")
    return pr.open_pr(ROOT, config, model, title, body, new_branch=True, back=True)


def footnotes(results, kind):
    """The notes of a table's runs, under it: "† 0.5.0+abc: ..." (a blank line ends the table first)."""
    out = []
    for r in results:
        if r.get("profile") == kind and r.get("note"):
            out += ["", f"† {label(r)}: {r['note']}"]
    return out


def main(argv):
    """publish.py [--pr] <job>... [--note "..."] | publish.py <job> --stopped "note"."""
    make_pr = "--pr" in argv
    argv = [a for a in argv if a != "--pr"]
    extra = None
    if "--note" in argv:
        at = argv.index("--note")
        if at + 1 >= len(argv) or not argv[at + 1].strip():
            print(__doc__)
            return 1
        extra = argv[at + 1].strip()
        argv = argv[:at] + argv[at + 2:]
    note = None
    if len(argv) == 3 and argv[1] == "--stopped" and argv[2].strip():
        argv, note = argv[:1], argv[2].strip()
    if not argv or (len(argv) > 1 and not make_pr):
        print(__doc__)
        return 1
    done = []
    for job in argv:
        summary = publish(job, note, extra)
        if summary is None:
            return 1
        done.append(summary)
    if make_pr:
        try:
            print(f"pull request: {report_pr(done)}")
        except RuntimeError as e:
            print(f"published, but no pull request: {e}")
            return 1
    return 0


def publish(job, note=None, extra=None):
    """One job into its page. Returns its summary, or None (and says why) when it isn't published.
    note: a stopped run's reason (its own table); extra: a footnote on any run."""
    job, config, trials = load_job(job)
    agent = (config.get("agents") or [{}])[0].get("name", "")
    if agent and agent != "tbench.purr_agent:PurrAgent":
        print(f"not published: {agent} isn't purr (tbench/compare.sh); tbench/versus.py sets it next to a purr run")
        return None
    if not trials:
        print(f"no trials in {job}")
        return None
    summary = summarise(config, trials)
    summary["profile"] = profile(summary["dataset"], trials)
    if note:
        if not bench_name(summary["dataset"]):
            print(f"not published: {summary['dataset']} isn't a benchmark this publishes")
            return None
        summary["profile"], summary["note"] = "stopped", note
    elif extra:
        summary["note"] = extra
    looked = sorted({t["task"] for t in trials if t.get("looked_up")})
    if looked:  # said in the row, so nobody wonders where the points went
        said = (f"{len(looked)} task{'s' * (len(looked) != 1)} looked up the benchmark itself and count as "
                f"failed: {', '.join(looked)}")
        summary["note"] = f"{summary['note']} {said}" if summary.get("note") else said
    if not summary["profile"]:
        print(f"not published: {summary['tasks']} tasks x {summary['settings']['attempts']} on {summary['dataset']} "
              "is not a full run (every task x 3, tbench/fair.sh), a one-try run (every task x 1, --one) or a "
              "quick one (the 20 in its quick-tasks file x 1, --quick)")
        return None
    wrong = unfair(summary)
    if wrong:
        print("not published: not run with the fair settings (tbench/fair.sh): " + ", ".join(wrong))
        return None
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
    return summary


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
