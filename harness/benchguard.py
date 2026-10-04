"""Benchmark runs are solved, not looked up. Two Terminal-Bench runs showed the model fetching the
benchmark itself: its repository, a task's solution.sh, its hidden tests, a "benchmark explorer"
site with the expected answer (5 trials, all of which then passed). In a benchmark run (bench_guard
= true, which tbench/purr_agent.py sets) purr refuses such lookups, and tbench/publish.py counts a
task whose log shows one as failed. Plain Python, no packages.
"""

import json
import re
from pathlib import Path

NAMES = ("terminal-bench", "terminal_bench", "terminalbench", "tbench", "laude-institute", "harbor-framework",
         "marginlab", "deep-swe", "deepswe", "datacurve", "spylab")
NETWORK = re.compile(r"https?://|\b(curl|wget|git\s+clone|git\s+fetch|urllib|requests\.get|httpx|aiohttp|"
                     r"pip\s+download|huggingface|hf_hub|load_dataset|gh\s+(api|repo))\b", re.I)
REFUSED = "error: purr: looking up the benchmark"
REFUSAL = (REFUSED + " itself ({name}: its repository, tasks, solutions, tests or published answers) isn't "
           "allowed in a benchmark run. Solve it from the request and the files here.")


def lookup(text):
    """The benchmark name a tool call reaches for over the network, or None."""
    low = str(text).lower()
    name = next((n for n in NAMES if n in low), None)
    return name if name and NETWORK.search(str(text)) else None


def looked_up(trial):
    """A trial whose log shows a benchmark lookup that wasn't refused (purr's chat in
    agent/purr-state/sessions/): what it fetched may have been the answer."""
    for session in Path(trial, "agent", "purr-state", "sessions").glob("*.json"):
        try:
            messages = json.loads(session.read_text())["messages"]
        except (OSError, ValueError, KeyError):
            continue
        results = {m.get("tool_call_id"): str(m.get("content") or "") for m in messages if m.get("role") == "tool"}
        for m in messages:
            for call in (m.get("tool_calls") or []) if m.get("role") == "assistant" else []:
                if lookup(call["function"].get("arguments") or "") and \
                        not results.get(call.get("id"), "").startswith(REFUSED):
                    return True
    return False
