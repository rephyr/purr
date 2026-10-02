"""purr bench --real: Terminal-Bench and DeepSWE, the benchmarks other harnesses publish scores on.

The run itself is tbench/fair.sh (the fair settings, the checks before it starts), started as a
process; this reads what it leaves in Harbor's job folder while it goes: every task's state, purr's
live log, the score, and what Mochi makes of it at the end. No textual here, so it can be tested
(and the window is tui/realbench_app.py).
"""

import importlib.util
import json
import math
import os
import re
import signal
import statistics
import subprocess
import time
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

SUITES = {
    "terminal-bench": {"bench": "Terminal-Bench 2.1", "flag": [], "page": "terminal-bench",
                       "what": "89 tasks in a Linux terminal: servers, builds, ML, security, VMs"},
    "deepswe": {"bench": "DeepSWE 1.1", "flag": ["--deepswe"], "page": "deepswe",
                "what": "113 features and fixes in real projects (TS, Go, Python, Rust, JS), offline"},
}
SIZES = {"quick": {"flag": ["--quick"], "what": "the quick 20, one try each", "attempts": 1},
         "one": {"flag": ["--one"], "what": "every task, one try each", "attempts": 1},
         "full": {"flag": [], "what": "every task, three tries each", "attempts": 3}}
# (time, cost) with DeepSeek V4.1 Flash, from the runs so far; None: not measured yet
ESTIMATES = {("terminal-bench", "quick"): ("about 1 hour", "under $0.50"),
             ("terminal-bench", "one"): ("about 2 hours", "about $2"),
             ("terminal-bench", "full"): ("4-5 hours", "about $6")}


def tasks_in(suite, size):
    bench = PUBLISH.BENCHES[SUITES[suite]["bench"]]
    return 20 if size == "quick" else bench["tasks"]


def trials_in(suite, size):
    return tasks_in(suite, size) * SIZES[size]["attempts"]


def references(suite):
    """The published scores with the same model: [(harness, pass@1)], best first."""
    bench = SUITES[suite]["bench"]
    return sorted(((r["harness"], r["pass@1"]) for r in PUBLISH.REFERENCE if r["benchmark"].startswith(bench)),
                  key=lambda x: -x[1])


def previous(suite, size):
    """Earlier published runs of the same kind: [(purr version, date, pass@1)], oldest first."""
    out = []
    for f in sorted((ROOT / "benchmarks" / SUITES[suite]["page"] / "results").glob("*.json")):
        try:
            s = json.loads(f.read_text())["summary"]
        except (OSError, ValueError, KeyError):
            continue
        if s.get("profile") == size and PUBLISH.bench_name(s.get("dataset")) == SUITES[suite]["bench"]:
            out.append((str(s["purr"]), s["date"], s["pass@1"]))
    return out


def command(suite, size, edge_cases=False):
    cmd = [str(ROOT / "tbench" / "fair.sh"), *SUITES[suite]["flag"], *SIZES[size]["flag"]]
    return cmd + (["--ak", "edge_cases=true"] if edge_cases else [])


class Run:
    """fair.sh going in the background: its own output (the checks, Harbor's lines) and its job folder."""

    def __init__(self, suite, size, jobs=6, edge_cases=False):
        self.suite, self.size = suite, size
        self.cmd = command(suite, size, edge_cases)
        self.jobs = jobs
        self.started = time.time()
        self.lines = []  # fair.sh's output, ANSI stripped
        self.proc = None
        self.job = None
        self.stopping = False

    def start(self):
        env = {**os.environ, "PURR_JOBS": str(self.jobs), "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"}
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
        """The job folder Harbor made for this run: the newest one started after it."""
        if self.job is None and JOBS.is_dir():
            new = [d for d in JOBS.iterdir() if d.is_dir() and d.stat().st_mtime >= self.started - 2
                   and (d / "config.json").exists()]
            if new:
                self.job = max(new, key=lambda d: d.stat().st_mtime)
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
            ex = (d.get("exception_info") or {}).get("exception_type")
            reward = ((d.get("verifier_result") or {}).get("rewards") or {}).get("reward")
            t["reward"] = reward
            t["cost"] = (d.get("agent_result") or {}).get("cost_usd")
            t["seconds"] = _seconds(d.get("agent_execution") or {})
            if ex == "CancelledError":  # the run was stopped: never graded, so not a fail either
                t["state"] = "cancelled"
            elif ex == "AgentTimeoutError":
                t["state"] = "passed" if (reward or 0) >= 1 else "timeout"
            elif ex and reward is None:
                t["state"], t["error"] = "error", ex
            else:
                t["state"] = "passed" if (reward or 0) >= 1 else "failed"
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


def _seconds(ex):
    import datetime
    if not (ex.get("started_at") and ex.get("finished_at")):
        return None
    p = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return (p(ex["finished_at"]) - p(ex["started_at"])).total_seconds()


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
        if t["state"] in ("passed", "failed", "error", "timeout"):
            by_task.setdefault(t["task"], []).append(1.0 if t["state"] == "passed" else 0.0)
    if not by_task:
        return None, None
    shares = [sum(v) / len(v) for v in by_task.values()]
    se = statistics.stdev(shares) / math.sqrt(len(shares)) if len(shares) > 1 else 0.0
    return round(100 * sum(shares) / len(shares), 1), round(100 * se, 1)


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

def verdict(suite, size, pct, finished, total, stopped=False, name="Mochi"):
    """(mood, headline, detail) for the end: next to the published harnesses for a whole run, next
    to the last quick run for a quick one."""
    if not finished or pct is None:
        return "oops", f"{name} didn't get to see anything finish", "no task got graded: the run log says why"
    part = f" ({finished} of {total} graded)" if finished < total else ""
    if stopped or finished < total:
        return "startled", f"{name} saw {pct:.1f}% before it stopped{part}", "a stopped run isn't comparable"
    if size == "quick":
        before = previous(suite, size)
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
    refs = references(suite)
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
