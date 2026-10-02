"""purr bench --real: Terminal-Bench and DeepSWE, the benchmarks other harnesses publish scores on.

The run itself is tbench/fair.sh (the fair settings, the checks before it starts), started as a
process; this reads what it leaves in Harbor's job folder while it goes: every task's state, purr's
live log, the score, and what Mochi makes of it at the end. No textual here, so it can be tested
(and the window is tui/realbench_app.py).
"""

import importlib.util
import json
import os
import re
import signal
import subprocess
import time
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = Path.home() / ".local" / "state" / "purr" / "tbench"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _publish():
    """tbench/publish.py: the benchmarks, their quick sets and the published scores live there."""
    spec = importlib.util.spec_from_file_location("purr_tbench_publish", ROOT / "tbench" / "publish.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PUBLISH = _publish()
FINISHED = PUBLISH.FINISHED  # graded: the window counts these

SUITES = {
    "terminal-bench": {"bench": "Terminal-Bench 2.1", "flag": [], "page": "terminal-bench",
                       "what": "89 tasks in a Linux terminal: servers, builds, ML, security, VMs"},
    # the official leaderboard's own version (Harbor Hub), where purr can get an entry
    "terminal-bench-2": {"bench": "Terminal-Bench 2.0", "flag": [], "page": "terminal-bench",
                         "env": {"PURR_DATASET": "terminal-bench/terminal-bench-2"},
                         "what": "the same 89 tasks, the version the public leaderboard uses"},
    "deepswe": {"bench": "DeepSWE 1.1", "flag": ["--deepswe"], "page": "deepswe",
                "what": "113 features and fixes in real projects (TS, Go, Python, Rust, JS), offline"},
}
SIZES = {"quick": {"flag": ["--quick"], "what": "the quick 20, one try each", "attempts": 1},
         "one": {"flag": ["--one"], "what": "every task, one try each", "attempts": 1},
         "full": {"flag": [], "what": "every task, three tries each", "attempts": 3},
         # a leaderboard entry: 5 tries, purr's web tool off, then tbench/submit.py (Terminal-Bench 2.0)
         "submit": {"flag": ["--submit"], "what": "every task ×5 (leaderboard)", "attempts": 5,
                    "only": "terminal-bench-2"}}
# (time, cost) with DeepSeek V4.1 Flash, from the runs so far; None: not measured yet
# purr 0.4.0 (its extra checks use more steps than 0.3.1's runs did), off-peak; weekday peak hours cost 2x
ESTIMATES = {**{(s, "quick"): ("about 1 hour", "about $0.70") for s in ("terminal-bench", "terminal-bench-2")},
             **{(s, "one"): ("about 2 hours", "about $3") for s in ("terminal-bench", "terminal-bench-2")},
             **{(s, "full"): ("5-6 hours", "about $8") for s in ("terminal-bench", "terminal-bench-2")},
             ("terminal-bench-2", "submit"): ("9-11 hours", "about $12-15"),
             ("deepswe", "quick"): ("about 1 hour", "about $3"),
             ("deepswe", "one"): ("4-5 hours", "about $18-23"),
             ("deepswe", "full"): ("about 14 hours", "about $55-70")}

# who answers: DeepSeek V4.1 Flash on OpenRouter (tbench/fair.sh, the fair settings) or a model on
# this machine (tbench/local.sh: Ollama, one task at a time, free)
DEEPSEEK = "deepseek"
LOCAL_SIZES = ("quick", "one")  # local.sh runs the quick set or every task once
LOCAL_SUITES = ("terminal-bench", "terminal-bench-2")  # DeepSWE needs its offline network setup: fair.sh only
LOCAL_ESTIMATES = {"quick": ("4-7 hours (one task at a time)", "free: your GPU"),
                   "one": ("about a day (one task at a time)", "free: your GPU")}
LOCAL_MIN_CONTEXT = 65536  # Terminal-Bench chats run long: a 32k copy fills up and loses the task
OLLAMA = "http://127.0.0.1:11434/api/tags"

# how purr runs: as it is, or one of the bench options in tbench/purr_agent.py
VARIANTS = {"purr": {"what": "purr as it is", "ak": []},
            "minimal": {"what": "minimal: DSH Minimal's setup", "ak": ["--ak", "minimal=true"]},
            "thinking": {"what": "purr, all thinking kept", "ak": ["--ak", "keep_reasoning=all"]}}


def local_models():
    """The Ollama models purr's config lists with room for a benchmark (64k+ context) that Ollama
    has right now: their Ollama names, the one local.sh uses by default (ornith) first. [] without Ollama."""
    try:
        with urllib.request.urlopen(OLLAMA, timeout=2) as resp:
            have = {m["name"].removesuffix(":latest") for m in json.loads(resp.read())["models"]}
        config = tomllib.loads((ROOT / "config.toml").read_text())
    except (OSError, ValueError, KeyError):
        return []
    names = {spec["id"] for spec in config.get("models", {}).values()
             if spec.get("provider") == "ollama" and spec.get("context", 0) >= LOCAL_MIN_CONTEXT
             and spec.get("id", "").removesuffix(":latest") in have}
    return sorted(names, key=lambda n: (not n.startswith("ornith"), n))


def problem(suite, size, model=DEEPSEEK):
    """Why this pick can't run, or None."""
    only = SIZES[size].get("only")
    if only and suite != only:
        return f"{size} is for {SUITES[only]['bench']} only"
    if model != DEEPSEEK and (suite not in LOCAL_SUITES or size not in LOCAL_SIZES):
        return "a local model runs Terminal-Bench, quick or one"
    return None


def estimate(suite, size, model=DEEPSEEK):
    """(time, cost) as words."""
    if model != DEEPSEEK:
        return LOCAL_ESTIMATES.get(size, ("not measured yet", "free: your GPU"))
    return ESTIMATES.get((suite, size), ("not measured yet", "not measured yet"))


def model_name(model):
    return "DeepSeek V4.1 Flash" if model == DEEPSEEK else model


def tasks_in(suite, size):
    bench = PUBLISH.BENCHES[SUITES[suite]["bench"]]
    return len(bench["quick"]) if size == "quick" else bench["tasks"]


def trials_in(suite, size):
    return tasks_in(suite, size) * SIZES[size]["attempts"]


def leaderboard():
    """The Terminal-Bench 2.0 leaderboard as it stood (tbench/leaderboard-tb2.json): entries, as_of."""
    try:
        data = json.loads((ROOT / "tbench" / "leaderboard-tb2.json").read_text())
    except (OSError, ValueError):
        return [], None
    return data.get("entries") or [], data.get("as_of")


def place(pct):
    """Where a score would land on that leaderboard: (rank, out of)."""
    entries, _ = leaderboard()
    return 1 + sum(e["score"] > pct for e in entries), len(entries) + 1


def references(suite, model=DEEPSEEK):
    """The published scores with the same model: [(harness, pass@1)], best first. A local model's
    are its makers' (Ornith-1.5-9B for ornith-9b-128k)."""
    bench = SUITES[suite]["bench"]

    def same(ref):
        if model == DEEPSEEK:
            return ref["model"] == "DeepSeek V4.1 Flash"
        return ref["model"].split("-")[0].lower() in model.lower()
    return sorted(((r["harness"], r["pass@1"]) for r in PUBLISH.REFERENCE
                   if r["benchmark"].startswith(bench) and same(r)), key=lambda x: -x[1])


def previous(suite, size, model=DEEPSEEK):
    """Earlier published runs of the same kind with the same model: [(purr version and variant,
    date, pass@1)], oldest first."""
    out = []
    for f in sorted((ROOT / "benchmarks" / SUITES[suite]["page"] / "results").glob("*.json")):
        try:
            s = json.loads(f.read_text())["summary"]
        except (OSError, ValueError, KeyError):
            continue
        same_model = (not PUBLISH.is_local(s) if model == DEEPSEEK else s.get("model") == f"ollama/{model}")
        if s.get("profile") == size and PUBLISH.bench_name(s.get("dataset")) == SUITES[suite]["bench"] and same_model:
            out.append((PUBLISH.label(s), s["date"], s["pass@1"]))
    return out


def command(suite, size, edge_cases=False, model=DEEPSEEK, variant="purr"):
    why = problem(suite, size, model)
    if why:
        raise ValueError(why)
    extra = VARIANTS[variant]["ak"] + (["--ak", "edge_cases=true"] if edge_cases else [])
    if model == DEEPSEEK:
        return [str(ROOT / "tbench" / "fair.sh"), *SUITES[suite]["flag"], *SIZES[size]["flag"], *extra]
    return [str(ROOT / "tbench" / "local.sh"), *(["--one"] if size == "one" else []), *extra]


class Run:
    """fair.sh (or local.sh) going in the background: its own output (the checks, Harbor's lines)
    and its job folder."""

    def __init__(self, suite, size, jobs=6, edge_cases=False, model=DEEPSEEK, variant="purr"):
        self.suite, self.size, self.model, self.variant = suite, size, model, variant
        self.cmd = command(suite, size, edge_cases, model, variant)
        self.jobs = jobs
        self.started = time.time()
        self.lines = []  # fair.sh's output, ANSI stripped
        self.proc = None
        self.job = None
        # fair.sh names the job after this (PURR_JOB), so the window finds its own run's folder
        self.job_name = time.strftime("%Y-%m-%d__%H-%M-%S", time.localtime(self.started)) + "__window"
        self.stopping = False

    def start(self):
        env = {**os.environ, **SUITES[self.suite].get("env", {}), "PURR_JOBS": str(self.jobs),
               "PURR_JOB": self.job_name, "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"}
        if self.model != DEEPSEEK:
            env["PURR_LOCAL_MODEL"] = self.model
        self.proc = subprocess.Popen(self.cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        return self.proc

    def read_output(self):
        """Blocks: call it in a thread. Keeps fair.sh's lines (Harbor redraws its progress with \\r)."""
        buf = b""
        while True:
            chunk = self.proc.stdout.read1(4096) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            *done, buf = re.split(rb"[\r\n]", buf)
            for raw in done:
                line = ANSI.sub("", raw.decode("utf-8", "replace")).rstrip()
                if line.strip():
                    self.lines.append(line)
                    del self.lines[:-400]
        self.proc.wait()

    def find_job(self):
        """The job folder Harbor made for this run: the one named after it (PURR_JOB)."""
        if self.job is None and (JOBS / self.job_name / "config.json").exists():
            self.job = JOBS / self.job_name
        return self.job

    @property
    def done(self):
        return self.proc is not None and self.proc.poll() is not None

    def stop(self):
        """Like ctrl+c: Harbor cancels the trials and cleans up its containers."""
        if self.proc and not self.done:
            self.stopping = True
            try:
                os.killpg(self.proc.pid, signal.SIGINT)
            except ProcessLookupError:
                pass


# ---- reading the job folder ----

STATES = {"setup": "setting up", "working": "purr is working", "grading": "grading",
          "passed": "passed", "failed": "failed", "error": "error", "timeout": "out of time",
          "cancelled": "cancelled"}


def trial(path):
    """One trial folder -> what it's doing or how it ended."""
    path = Path(path)
    name = path.name
    t = {"name": name, "task": name.rsplit("__", 1)[0], "log": path / "agent" / "purr.txt",
         "seconds": None, "cost": None, "reward": None, "error": None}
    result = path / "result.json"
    if result.exists():
        try:
            d = json.loads(result.read_text())
        except (OSError, ValueError):
            d = None
        if d is not None:
            t["task"] = str(d.get("task_name", t["task"])).split("/")[-1]
            o = PUBLISH.outcome(d)  # the same reading the published tables use
            t.update(state=o["state"], reward=o["reward"], cost=o["cost"], seconds=o["seconds"],
                     error=o["error"] if o["state"] == "error" else None)
            return t
    verifier = path / "verifier"
    if verifier.is_dir() and any(verifier.iterdir()):
        t["state"] = "grading"
    elif t["log"].exists():
        t["state"] = "working"
    else:
        t["state"] = "setup"
    try:
        t["seconds"] = time.time() - path.stat().st_ctime
    except OSError:
        pass
    return t


def scan(job):
    if not job or not Path(job).is_dir():
        return []
    return sorted((trial(d) for d in Path(job).iterdir() if d.is_dir() and "__" in d.name),
                  key=lambda t: (t["state"] in ("setup", "working", "grading"), t["name"]))


def score(trials):
    """pass@1 like the leaderboards (mean over tasks of the share of tries passed, errors and
    timeouts fail) and its ± (standard error over tasks). None until something finished."""
    by_task = {}
    for t in trials:
        if t["state"] in FINISHED:
            by_task.setdefault(t["task"], []).append(1.0 if t["state"] == "passed" else 0.0)
    return PUBLISH.pass_at_1(by_task)


def last_tool(lines):
    """The newest tool purr's log shows ('◆ run ...' -> 'run'), for Mochi's mood."""
    for line in reversed(lines):
        m = re.match(r"\s*◆ (\w+)", ANSI.sub("", line))
        if m:
            return m.group(1)
    return None


def tail(path, size=48_000):
    """The end of a log, as lines (with their colours)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - size))
            data = f.read()
    except OSError:
        return [], 0
    lines = data.decode("utf-8", "replace").split("\n")
    return (lines[1:] if end > size else lines), end


# ---- how it went ----

def verdict(suite, size, pct, finished, total, stopped=False, name="Mochi", model=DEEPSEEK):
    """(mood, headline, detail) for the end: next to the published harnesses for a whole run, next
    to the last quick run for a quick one."""
    if not finished or pct is None:
        return "oops", f"{name} didn't get to see anything finish", "no task got graded: the run log says why"
    part = f" ({finished} of {total} graded)" if finished < total else ""
    if stopped or finished < total:
        return "startled", f"{name} saw {pct:.1f}% before it stopped{part}", "a stopped run isn't comparable"
    if size == "quick":
        before = previous(suite, size, model)
        if not before:
            return "happy", f"{pct:.1f}% on the quick set: {name}'s first one", "this is the baseline to beat ♡"
        version, _, last = before[-1]
        diff = pct - last
        if diff >= 5:
            return "celebrating", f"{pct:.1f}%: up {diff:.0f} points since purr {version}!", \
                "quick runs are noisy, but that's a real jump ♡"
        if diff > -5:
            return "purring", f"{pct:.1f}%: about the same as purr {version} ({last:.1f}%)", \
                "within the noise of 20 tasks"
        return "sad", f"{pct:.1f}%: down {-diff:.0f} points from purr {version}", \
            "worth reading which tasks broke"
    if suite == "terminal-bench-2" and model == DEEPSEEK:  # the leaderboard is the comparison
        rank, out_of = place(pct)
        where = f"#{rank} of {out_of} on the Terminal-Bench 2.0 leaderboard"
        if rank <= 10:
            return "celebrating", f"{pct:.1f}%: {where}!", f"top ten ♡ {name} can't sit still"
        if rank <= 30:
            return "proud", f"{pct:.1f}%: {where}", "among the big names"
        if rank <= out_of // 2:
            return "happy", f"{pct:.1f}%: {where}", "the top half ♡"
        return "purring", f"{pct:.1f}%: {where}", "the failed tasks say what to fix next"
    refs = references(suite, model)
    beaten = [h for h, s in refs if s < pct]
    if refs and len(beaten) == len(refs):
        return "celebrating", f"{pct:.1f}%: purr beat every harness on the list!", \
            f"{name} is doing zoomies ♡ (keep the ± in mind)"
    if refs and len(beaten) * 2 >= len(refs):
        return "proud", f"{pct:.1f}%: purr beat {len(beaten)} of {len(refs)} harnesses", \
            f"ahead of {', '.join(beaten[:3])}{'…' if len(beaten) > 3 else ''}"
    if beaten:
        return "happy", f"{pct:.1f}%: purr beat {', '.join(beaten)}", f"{len(refs) - len(beaten)} still ahead ♡"
    if refs:
        lowest, low = refs[-1]
        gap = low - pct
        if gap <= 5:
            return "purring", f"{pct:.1f}%: {gap:.1f} points behind {lowest}", "so close ♡ within the margin"
        return "sad", f"{pct:.1f}%: {gap:.1f} points behind {lowest}", "the failed tasks say what to fix next"
    return "happy", f"{pct:.1f}%", ""
