"""Background jobs the model starts (`nohup train.py > run.log 2>&1 &`), watched from their logs:
progress lines give an estimate of when each will finish, and purr says so once when that is past
the deadline, or when the log stopped moving. Two Terminal-Bench runs lost their task to one long
job that couldn't finish in the time left (26 minutes of sampling, 25 epochs of training).
Plain Python, no packages.
"""

import re
import time
from pathlib import Path

BACKGROUND = re.compile(r"&\s*(?:;|$)|^\s*(nohup|setsid)\b", re.M)
LOG = re.compile(r"(?:>>?|&>)\s*([^\s;&|<>]+)")
PROGRESS = (  # each gives (done, total)
    re.compile(r"epoch\s+(\d+)\s*(?:/|of)\s*(\d+)", re.I),
    re.compile(r"chain\s+\d+:\s*(\d+(?:\.\d+)?)\s*%", re.I),  # Stan: a percentage
    re.compile(r"(\d+(?:\.\d+)?)\s*%"),
    re.compile(r"\b(\d+)\s*/\s*(\d+)\b"),
)
STALL = 300  # seconds without the log growing


def progress(text):
    """The fraction done the latest progress line in a log shows, or None."""
    for line in reversed(text.splitlines()[-200:]):
        for pattern in PROGRESS:
            found = pattern.findall(line)
            if not found:
                continue
            last = found[-1]
            if isinstance(last, tuple) and len(last) == 2 and last[1]:
                done, total = float(last[0]), float(last[1])
                if 0 < total and 0 <= done <= total:
                    return done / total
            elif isinstance(last, str) and last:
                share = float(last) / 100
                if 0 <= share <= 1:
                    return share
    return None


class Jobs:
    def __init__(self, root):
        self.root = Path(root)
        self.jobs = []  # {command, log, started, said}

    def saw(self, command):
        """A run command: if it starts a background job with a log, watch it."""
        if not BACKGROUND.search(command or ""):
            return
        logs = [m for m in LOG.findall(command) if m not in ("/dev/null", "&1", "2", "1")]
        if not logs:
            return
        log = Path(logs[-1])
        log = log if log.is_absolute() else self.root / log
        if any(j["log"] == log for j in self.jobs):
            return
        self.jobs.append({"command": command.strip()[:120], "log": log, "started": time.time(), "said": set()})

    def notes(self, seconds_left, now=None):
        """What to tell the model now: [lines], each said once per job (the estimate is past the
        deadline; the log stopped moving)."""
        now = now or time.time()
        out = []
        for job in self.jobs:
            try:
                stat = job["log"].stat()
                text = job["log"].read_text(errors="replace")[-20000:]
            except OSError:
                continue
            elapsed = now - job["started"]
            share = progress(text)
            if share and 0.02 <= share < 1 and seconds_left is not None and "late" not in job["said"]:
                eta = elapsed / share * (1 - share)
                if eta > seconds_left - 60:
                    job["said"].add("late")
                    out.append(f"`{job['command']}` is about {share:.0%} done after {elapsed / 60:.0f} min: at "
                               f"this pace it needs about {eta / 60:.0f} more, and you have about "
                               f"{max(0, seconds_left) / 60:.0f}. Stop it and use a smaller run (fewer epochs, "
                               "samples or data) that fits, or have a result in place before it ends.")
            if now - stat.st_mtime > STALL and (share is None or share < 1) and "stalled" not in job["said"]:
                job["said"].add("stalled")
                out.append(f"`{job['command']}`: its log ({job['log'].name}) hasn't changed for "
                           f"{(now - stat.st_mtime) / 60:.0f} min. Check it's still running and not stuck.")
        return out
