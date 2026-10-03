#!/usr/bin/env python3
"""Two runs on the same tasks, task by task: purr against another harness (tbench/compare.sh), or
two purr versions or variants. Tries cancelled by stopping a run were never graded and don't count.

    tbench/versus.py <job folder A> <job folder B>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish import load_job  # noqa: E402

MARK = {True: "pass", False: "fail"}


def who(config):
    agent = (config.get("agents") or [{}])[0]
    name = agent.get("name", "?").replace("tbench.purr_agent:PurrAgent", "purr")
    on = [k for k, v in (agent.get("kwargs") or {}).items() if k in ("minimal", "keep_reasoning") and v]
    return name + (f" ({', '.join(on)})" if on else "")


def graded(trials):
    """task -> its first graded try."""
    out = {}
    for t in trials:
        if not t["cancelled"] and t["task"] not in out:
            out[t["task"]] = t
    return out


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 1
    (_, ca, ta), (_, cb, tb) = load_job(argv[0]), load_job(argv[1])
    a, b = graded(ta), graded(tb)
    names = (who(ca), who(cb))
    both = sorted(set(a) & set(b))
    print(f"{'task':34} {names[0][:16]:>16} {names[1][:16]:>16}   minutes      cost")
    for task in both:
        x, y = a[task], b[task]
        px, py = (x["reward"] or 0) >= 1, (y["reward"] or 0) >= 1
        mins = f"{(x['agent_seconds'] or 0) / 60:5.1f}/{(y['agent_seconds'] or 0) / 60:<5.1f}"
        cost = f"{x['cost_usd'] or 0:.3f}/{y['cost_usd'] or 0:.3f}"
        print(f"{task[:34]:34} {MARK[px]:>16} {MARK[py]:>16}   {mins}  {cost}{'  <<' if px != py else ''}")
    for name, runs in zip(names, (a, b)):
        passed = sum((runs[t]["reward"] or 0) >= 1 for t in both)
        cost = sum(runs[t]["cost_usd"] or 0 for t in both)
        print(f"{name}: {passed} of {len(both)} ({100 * passed / max(len(both), 1):.1f}%), ${cost:.2f}")
    left = (set(a) | set(b)) - set(both)
    if left:
        print(f"not in both (stopped or not run): {', '.join(sorted(left))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
