"""purr bench --you: do the bench tasks yourself, for fun, and see how you do next to the models.

    purr bench --you                    every task, your row added to the latest bench's results
    purr bench --you -t idle-catchup    just this one
    purr bench --you --level hard --vague
    purr bench --you --new              your own results folder instead

Each task gets a fresh copy of its files. You read the prompt, open the folder (enter: VS Code,
n: nvim right here), fix it (r runs the task's program, "run" in its task.json, so you can see
what it does; t runs its tests), and press enter when you're done: the same hidden tests the models
get decide, and your time is the clock from opening to done. You show up as "you" / "human".
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import ui
from .bench import BENCH_DIR, blank, grade, markdown, new_out_dir, pick_tasks, print_table, summarise


def latest_bench():
    """The newest bench folder that has results, or None."""
    found = sorted(BENCH_DIR.glob("*/results.json"), key=lambda p: p.stat().st_mtime) if BENCH_DIR.exists() else []
    return found[-1].parent if found else None


def failed_tests(task, work):
    """The hidden tests that failed, by name (after grade() has run them)."""
    check = work / "_bench_check"
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-v", "-s", "_bench_check", "-t", "."],
                       cwd=work, capture_output=True, text=True, timeout=120)
    shutil.rmtree(check, ignore_errors=True)
    # "test_x (...) ... FAIL", or with a docstring: "test_x (...)" then "the docstring ... FAIL"
    return re.findall(r"^(test_\w+) \([^)]*\)(?:\n[^\n]*?)? \.\.\. (?:FAIL|ERROR)$", r.stderr, re.M)


def visible_tests(work):
    """Run the task's own tests (the ones in its folder) and show the last lines."""
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                       cwd=work, capture_output=True, text=True, timeout=120)
    tail = (r.stderr or r.stdout).strip().splitlines()[-12:]
    ui.out(ui.DIM + "\n".join("    " + line for line in tail) + ui.RESET)


def program(task, work):
    """The command that runs the task's program: "run" in its task.json, else main.py if there is one."""
    if task.get("run"):
        return task["run"]
    return ["python3", "main.py"] if (work / "main.py").exists() else None


def run_program(task, work):
    cmd = program(task, work)
    if not cmd:
        ui.say(ui.DIM, "    this task has no program to run (t runs its tests)")
        return
    ui.say(ui.DIM, f"    $ {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=work, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        ui.say(ui.ROSE, f"    couldn't run it: {e}")


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"


def open_folder(work, how):
    if how == "n":
        subprocess.run([os.environ.get("EDITOR") or "nvim", str(work)])
    elif shutil.which("code"):
        subprocess.Popen(["code", str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        ui.say(ui.DIM, f"    (no VS Code here: open {work} in your editor)")


def play(args):
    """One task at a time; returns 0. args: the bench's parsed arguments."""
    tasks = pick_tasks(args.tasks.split(",") if args.tasks else None, args.vague, args.level)
    if not tasks:
        ui.say(ui.ROSE, "  no tasks to play")
        return 1
    out_dir = None if args.new else latest_bench()
    out_dir = out_dir or new_out_dir()
    results_file = out_dir / "results.json"
    results = json.loads(results_file.read_text()) if results_file.exists() else []
    ui.out(f"\n  {ui.PINK}/\\_/\\{ui.RESET}   {ui.LILAC}your turn! {len(tasks)} task{'s' * (len(tasks) != 1)}"
           f"{' (vague prompts)' if args.vague else ''}{ui.RESET}")
    ui.out(f"  {ui.PINK}( •ω• ){ui.RESET} {ui.DIM}same hidden tests as the models · results go to {out_dir}{ui.RESET}")
    ui.out(f"  {ui.PINK} > ^ <{ui.RESET}\n")
    played = []
    for n, task in enumerate(tasks, 1):
        work = out_dir / "you" / f"{time.strftime('%H%M%S')}_{task['name']}" / task["name"]
        shutil.copytree(task["dir"] / "files", work)
        ui.out(f"  {ui.LILAC}[{n}/{len(tasks)}] {task['name']}{ui.RESET}  {ui.DIM}{task.get('level', '')} · "
               f"{task.get('kind', '')}{ui.RESET}")
        ui.out(f"  {ui.PINK}♡{ui.RESET} {task['prompt']}")
        ui.out(f"  {ui.DIM}folder: {work}{ui.RESET}")
        runs = " · r: run the program" if program(task, work) else ""
        while True:
            how = ask(f"  {ui.DIM}enter: open in VS Code · n: nvim here{runs} · s: skip · q: quit ›{ui.RESET} ")
            if how != "r":
                break
            run_program(task, work)  # have a look at what it does before starting
        if how == "q":
            break
        if how == "s":
            continue
        start = time.monotonic()
        open_folder(work, how)
        while True:
            act = ask(f"  {ui.DIM}enter: done, grade it · t: run the visible tests{runs} · n: nvim · "
                      f"q: quit (this one isn't counted) ›{ui.RESET} ")
            if act == "t":
                visible_tests(work)
            elif act == "r":
                run_program(task, work)
            elif act == "n":
                open_folder(work, "n")
            elif act in ("", "q"):
                break
        if act == "q":
            break
        r = blank("human", "you", task)
        r["seconds"] = round(time.monotonic() - start, 1)
        r["passed"], r["total"], r["syntax_errors"] = grade(task, work)
        r["solved"] = r["total"] > 0 and r["passed"] == r["total"]
        r["local"] = False
        r["hallucinations"] = 0  # a model-only count: a person can't make up a tool
        r["run"] = 1
        failed = failed_tests(task, work) if not r["solved"] else []
        if r["solved"]:
            ui.out(f"  {ui.MINT}✓ solved in {ui.duration(r['seconds'])}! ₊˚✧{ui.RESET}\n")
        else:
            ui.out(f"  {ui.ROSE}✗ {r['passed']}/{r['total']} hidden tests in {ui.duration(r['seconds'])}{ui.RESET}")
            for name in failed:
                ui.out(f"    {ui.DIM}✗ {name.removeprefix('test_').replace('_', ' ')}{ui.RESET}")
            ui.out("")
        results.append(r)
        played.append(task)
        results_file.write_text(json.dumps(results, indent=1))  # saved after every task
    if not played:
        ui.say(ui.DIM, "  nothing played, nothing saved")
        return 0
    summary = summarise(results)
    names = {r["task"] for r in results}
    tasks_seen = [t for t in pick_tasks() if t["name"] in names]
    (out_dir / "report.md").write_text(markdown(results, summary, tasks_seen))
    print_table(summary)
    ui.say(ui.DIM, f"\n  you're the last row ♡  report: {out_dir / 'report.md'}\n")
    return 0
