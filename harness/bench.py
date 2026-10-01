"""purr bench: the same small coding tasks, done by purr and by OpenCode with the same model.

    purr bench                         pick models from a list, then run every task
    purr bench -m qwen3-coder-32k      straight away, with this model (comma-separate more)
    purr bench -t rename,duration      only these tasks
    purr bench --harness purr          only purr (or only opencode)
    purr bench --runs 3                every task three times (models aren't the same twice)
    purr bench --vague --harness purr,purr+refine,opencode
                                       vague prompts: does refining them first help?

Every run starts from a fresh copy of the task's files. When the model is done, the task's
hidden tests are copied in and run, so the model never sees what it's graded on. Results go
to ~/.local/state/purr/bench/<date>/: results.json, report.md, and each run's files and log.

Measured per run: solved, tests passed, time, tokens written, tok/s (tokens / time the model
spent answering), model calls, tool calls, tool errors, failed edits, hallucinations (a tool
that doesn't exist, a file that doesn't exist, a tool call written as text, saying it's done
when the tests fail) and syntax errors left behind.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import ui
from .agent import STATE_DIR, Agent

TASKS_DIR = Path(__file__).resolve().parent.parent / "bench" / "tasks"
BENCH_DIR = STATE_DIR / "bench"
LEAKED_CALL = re.compile(r"<tool_call>|</tool_call>|<function=|<\|tool_call")
CLAIM = re.compile(r"\b(fixed|done|pass(es|ed|ing)?|works|working|implemented|renamed|complete[d]?|"
                   r"success(ful(ly)?)?|all good)\b", re.I)
MISSING = re.compile(r"No such file|FileNotFoundError|not found|does not exist|doesn't exist", re.I)


# ---- tasks ----

def load_tasks(names=None):
    tasks = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not (d / "task.json").exists() or (names and d.name not in names):
            continue
        spec = json.loads((d / "task.json").read_text())
        hidden = sum(f.read_text().count("    def test_") for f in (d / "check").glob("test_*.py"))
        tasks.append({"name": d.name, "dir": d, "expected": hidden, **spec})
    return tasks


def grade(task, work):
    """Copy the hidden tests in and run them. Returns (passed, total, syntax errors)."""
    syntax = 0
    for f in work.rglob("*.py"):
        try:
            compile(f.read_text(errors="replace"), str(f), "exec")
        except (SyntaxError, ValueError):
            syntax += 1
    check = work / "_bench_check"
    shutil.rmtree(check, ignore_errors=True)
    shutil.copytree(task["dir"] / "check", check)
    (check / "__init__.py").touch()
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "_bench_check", "-t", "."],
                           cwd=work, capture_output=True, text=True, timeout=120)
        out = r.stderr
    except subprocess.TimeoutExpired:
        out = ""
    total = task["expected"]
    ran = re.search(r"Ran (\d+) test", out)
    if not ran or int(ran.group(1)) < total:  # it couldn't even import the code
        return 0, total, syntax
    bad = re.search(r"FAILED \((.*?)\)", out)
    wrong = sum(int(n) for n in re.findall(r"=(\d+)", bad.group(1))) if bad else 0
    return max(0, total - wrong), total, syntax


def outside(args, work):
    """Did a tool call point at a folder outside the task's copy? (paths, or cd somewhere)"""
    for value in args.values():
        if not isinstance(value, str):
            continue
        for path in re.findall(r"(?:^|[\s'\"=])((?:/|~/)[^\s'\";|&]+)", value):
            p = Path(os.path.expanduser(path))
            if str(p).startswith(("/tmp", "/dev", "/usr", "/bin", "/proc")) and not str(p).startswith(str(work)):
                if str(p).startswith("/tmp"):
                    return True
                continue  # system paths like /usr/bin/python are fine
            if not str(p.resolve()).startswith(str(work.resolve())):
                return True
    return False


def blank(harness, model, task):
    return {"harness": harness, "model": model, "task": task["name"], "solved": False, "passed": 0,
            "total": task["expected"], "seconds": 0.0, "out_tokens": 0, "model_seconds": 0.0,
            "tok_s": None, "calls": 0, "tool_calls": 0, "tool_errors": 0, "failed_edits": 0,
            "unknown_tool": 0, "missing_file": 0, "leaked_calls": 0, "false_claim": False,
            "syntax_errors": 0, "cost": 0.0, "timeout": False, "error": None, "final_text": "",
            "left_folder": 0}


def finish(r, task, work):
    if r["left_folder"]:
        r["error"] = f"left its task folder {r['left_folder']}x: not counted (see the log)"
    r["passed"], r["total"], r["syntax_errors"] = grade(task, work)
    r["solved"] = r["total"] > 0 and r["passed"] == r["total"]
    r["false_claim"] = not r["solved"] and bool(CLAIM.search(r["final_text"] or ""))
    if r["model_seconds"] > 0.3 and r["out_tokens"]:
        r["tok_s"] = round(r["out_tokens"] / r["model_seconds"], 1)
    r["hallucinations"] = r["unknown_tool"] + r["missing_file"] + r["leaked_calls"] + int(r["false_claim"])
    r["final_text"] = (r["final_text"] or "")[-600:]
    return r


# ---- purr ----

class BenchView:
    """Watches a purr run instead of drawing it."""

    def __init__(self, r, work):
        self.r, self.reply, self.work = r, [], work

    def text(self, s):
        self.reply.append(s)

    def end_reply(self):
        text = "".join(self.reply)
        if text.strip():
            self.r["final_text"] = text
            self.r["leaked_calls"] += len(LEAKED_CALL.findall(text))
        self.reply = []

    def tool_result(self, name, args, result):
        self.r["tool_calls"] += 1
        if outside(args, self.work):
            self.r["left_folder"] += 1
        result = result or ""
        if result.startswith("error: there is no tool called"):
            self.r["unknown_tool"] += 1
        elif result.startswith("error"):
            if name in ("read_file", "edit_file", "list_files", "grep") and MISSING.search(result) \
                    and "old_text not found" not in result:
                self.r["missing_file"] += 1
            if name in ("edit_file", "write_file"):
                self.r["failed_edits"] += 1
        if result.startswith("error"):
            self.r["tool_errors"] += 1

    def ask(self, question, allow_always=True):
        return "y", ""  # benchmarks run with everything allowed, like opencode --auto

    def thinking(self, s): pass
    def tool(self, line): pass
    def diff(self, path, before, after): pass
    def note(self, s, kind="dim"): pass
    def status(self, s): pass
    def todos(self, items): pass
    def activity(self, what, detail=""): pass


def run_purr(config, model, task, work, log_dir, timeout, refine=False):
    r = blank("purr+refine" if refine else "purr", model, task)
    view = BenchView(r, work)
    try:
        agent = Agent(config, work, model, view)
    except KeyError as e:
        r["error"] = e.args[0]
        return r
    agent.tools.trust_all = True
    agent.log_path = log_dir / "purr-session.json"
    timer = threading.Timer(timeout, lambda: (r.__setitem__("timeout", True), setattr(agent, "stop_flag", True)))
    timer.start()
    start = time.monotonic()
    try:
        prompt = task["prompt"]
        if refine:  # the same model rewrites the request first; the bench takes its version as is
            prompt = agent.refine(prompt)
            (log_dir / "refined-prompt.txt").write_text(prompt)
        agent.turn(prompt)
    except Exception as e:  # noqa: BLE001 - a crash is a result too
        r["error"] = f"{type(e).__name__}: {e}"
    finally:
        timer.cancel()
    r["seconds"] = round(time.monotonic() - start, 1)
    s = getattr(agent, "turn_stats", {}) or {}
    r["out_tokens"], r["calls"] = s.get("out", 0), s.get("calls", 0)
    r["model_seconds"] = round(s.get("model_s", 0.0), 1)
    r["cost"] = round(agent.session_cost, 5)
    return r


# ---- opencode ----

_OC_MODELS = None


def opencode_model(config, model):
    """purr's model -> OpenCode's "provider/id", if OpenCode knows it."""
    global _OC_MODELS
    if not _OC_MODELS:  # an empty list means asking failed: ask again next time
        try:
            out = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=120,
                                 stdin=subprocess.DEVNULL)
            _OC_MODELS = {line.strip() for line in (out.stdout + out.stderr).splitlines() if "/" in line}
        except (OSError, subprocess.SubprocessError):
            _OC_MODELS = set()
    spec = config["models"][model]
    oc = f"{spec['provider']}/{spec['id']}"
    # if the list couldn't be read, try anyway: OpenCode says so itself if it doesn't know the model
    return oc if oc in _OC_MODELS or not _OC_MODELS else None


def run_opencode(config, model, task, work, log_dir, timeout):
    r = blank("opencode", model, task)
    oc = opencode_model(config, model)
    if not oc:
        r["error"] = "this model isn't set up in OpenCode"
        return r
    cmd = ["opencode", "run", "--standalone", "--auto", "--format", "json", "-m", oc, task["prompt"]]
    start = time.monotonic()
    # its own process group, so a timeout also stops the server it starts
    # OpenCode takes its folder from $PWD, not the real working directory: set both, or it
    # works in whatever folder purr was started from
    env = {**os.environ, "PWD": str(work)}
    proc = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        out, err = proc.communicate()
        r["timeout"] = True
    r["seconds"] = round(time.monotonic() - start, 1)
    (log_dir / "opencode-events.jsonl").write_text(out)
    if err.strip():
        (log_dir / "opencode-stderr.txt").write_text(err)

    step_start, shell_time = None, 0.0
    for line in out.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind, part = e.get("type"), e.get("part") or {}
        if kind == "step_start":
            r["calls"] += 1
            step_start, shell_time = e.get("timestamp"), 0.0
        elif kind == "step_finish":
            tokens = part.get("tokens") or {}
            r["out_tokens"] += (tokens.get("output") or 0) + (tokens.get("reasoning") or 0)
            r["cost"] += part.get("cost") or 0.0
            if step_start:  # the step's time, minus the time its commands spent running
                r["model_seconds"] += max(0.0, (e["timestamp"] - step_start) / 1000 - shell_time)
        elif kind == "text":
            text = part.get("text") or ""
            if text.strip():
                r["final_text"] = text
                r["leaked_calls"] += len(LEAKED_CALL.findall(text))
        elif kind == "tool_use":
            r["tool_calls"] += 1
            tool, state = part.get("tool", ""), part.get("state") or {}
            if outside(state.get("input") or {}, work):
                r["left_folder"] += 1
            span = state.get("time") or {}
            if tool in ("shell", "bash") and span.get("end") and span.get("start"):
                shell_time += (span["end"] - span["start"]) / 1000
            if state.get("status") == "error" or tool == "invalid":
                r["tool_errors"] += 1
                said = json.dumps(state)
                if tool == "invalid" or "unavailable tool" in said or "not available" in said:
                    r["unknown_tool"] += 1
                elif tool in ("read", "edit", "list", "grep", "glob") and MISSING.search(said):
                    r["missing_file"] += 1
                if tool in ("edit", "write", "multiedit", "patch", "apply_patch"):
                    r["failed_edits"] += 1
        elif kind == "error":
            r["error"] = json.dumps(e)[:300]
    r["model_seconds"] = round(r["model_seconds"], 1)
    r["cost"] = round(r["cost"], 5)
    return r


# ---- running it all ----

HARNESSES = {"purr": run_purr, "opencode": run_opencode,
             "purr+refine": lambda *a: run_purr(*a, refine=True)}


def pick_models(config):
    names = list(config["models"])
    names.sort(key=lambda n: (bool(config["models"][n].get("price")), n))  # local ones first
    ui.say(ui.PINK, "\n  ₊˚✧ purr bench ✧˚₊\n")
    ui.say(ui.DIM, "  which models? numbers with spaces, like: 1 3\n")
    for i, n in enumerate(names, 1):
        spec = config["models"][n]
        paid = "paid" if spec.get("price") else "local ♡"
        ui.out(f"  {ui.LILAC}{i:>2}{ui.RESET}  {n:<18} {ui.DIM}{spec['provider']:<10}{ui.RESET} "
               f"{ui.YELLOW if spec.get('price') else ui.MINT}{paid}{ui.RESET}")
    while True:
        try:
            raw = input(f"\n  {ui.PINK}❯{ui.RESET} ").replace(",", " ").split()
        except (EOFError, KeyboardInterrupt):
            return []
        try:
            chosen = [names[int(x) - 1] for x in raw]
        except (ValueError, IndexError):
            ui.say(ui.ROSE, "  numbers from the list, please")
            continue
        if chosen:
            return chosen


def line(r):
    """One finished run, for the progress list."""
    if r.get("left_folder"):
        return f"{ui.ROSE}✗ left its task folder, not counted{ui.RESET}"
    mark = f"{ui.MINT}✓ solved{ui.RESET}" if r["solved"] else f"{ui.ROSE}✗ {r['passed']}/{r['total']}{ui.RESET}"
    if r["error"] and not r["calls"]:
        mark = f"{ui.ROSE}skipped{ui.RESET}"
    bits = [f"{r['seconds']:.0f}s"]
    if r["tok_s"]:
        bits.append(f"{r['tok_s']:.0f} tok/s")
    if r["hallucinations"]:
        bits.append(f"{ui.ROSE}{r['hallucinations']} hallucination{'s' * (r['hallucinations'] != 1)}{ui.RESET}")
    if r["timeout"]:
        bits.append(f"{ui.ROSE}timed out{ui.RESET}")
    if r["error"] and not r["calls"]:
        bits = [r["error"][:60]]
    return f"{mark}  {ui.DIM}{' · '.join(bits)}{ui.RESET}"


def summarise(results):
    """Per model and harness: the totals for the report."""
    rows = {}
    for r in results:
        if (r["error"] and not r["calls"]) or r.get("left_folder"):
            continue
        k = (r["model"], r["harness"])
        s = rows.setdefault(k, {"model": k[0], "harness": k[1], "runs": 0, "solved": 0, "passed": 0,
                                "total": 0, "seconds": 0.0, "out": 0, "model_s": 0.0, "calls": 0,
                                "tool_errors": 0, "failed_edits": 0, "hallucinations": 0,
                                "syntax_errors": 0, "timeouts": 0, "cost": 0.0})
        s["runs"] += 1
        s["solved"] += r["solved"]
        s["passed"] += r["passed"]
        s["total"] += r["total"]
        s["seconds"] += r["seconds"]
        s["out"] += r["out_tokens"]
        s["model_s"] += r["model_seconds"]
        s["calls"] += r["calls"]
        for key in ("tool_errors", "failed_edits", "hallucinations", "syntax_errors", "cost"):
            s[key] += r[key]
        s["timeouts"] += r["timeout"]
    return list(rows.values())


COLUMNS = ["model", "harness", "solved", "tests", "avg time", "tok/s", "tokens", "calls",
           "tool errors", "failed edits", "hallucinations", "syntax errors"]


def table_rows(summary):
    out = []
    for s in summary:
        out.append([s["model"], s["harness"], f"{s['solved']}/{s['runs']}",
                    f"{100 * s['passed'] // max(s['total'], 1)}%", ui.duration(s["seconds"] / s["runs"]),
                    f"{s['out'] / s['model_s']:.0f}" if s["model_s"] > 0.3 and s["out"] else "-",
                    ui.short(s["out"]), str(s["calls"]), str(s["tool_errors"]), str(s["failed_edits"]),
                    str(s["hallucinations"]), str(s["syntax_errors"])])
    return out


def markdown(results, summary, tasks):
    lines = [f"# purr bench, {datetime.date.today()}", "",
             "Same tasks, same model, both harnesses with everything allowed. Hidden tests decide "
             "if a task is solved. tok/s = tokens written / time the model spent answering.", "",
             "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(row) + " |" for row in table_rows(summary)]
    lines += ["", "## every run", "",
              "| model | harness | task | result | time | tok/s | calls | tool errors | hallucinations |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        res = "skipped: " + r["error"] if r["error"] and not r["calls"] else \
            ("✓" if r["solved"] else f"✗ {r['passed']}/{r['total']}") + (" (timed out)" if r["timeout"] else "")
        lines.append(f"| {r['model']} | {r['harness']} | {r['task']} | {res} | {r['seconds']:.0f}s | "
                     f"{r['tok_s'] or '-'} | {r['calls']} | {r['tool_errors']} | {r['hallucinations']} |")
    lines += ["", "Tasks: " + ", ".join(f"**{t['name']}** ({t['kind']})" for t in tasks), ""]
    return "\n".join(lines)


def print_table(summary):
    rows = [COLUMNS] + table_rows(summary)
    widths = [max(len(r[i]) for r in rows) for i in range(len(COLUMNS))]
    ui.out("")
    for n, row in enumerate(rows):
        cells = [c.ljust(w) for c, w in zip(row, widths)]
        colour = ui.LILAC if n == 0 else (ui.PINK if row[1].startswith("purr") else ui.RESET)
        ui.out("  " + colour + "  ".join(cells) + ui.RESET)


def main(argv, config):
    ap = argparse.ArgumentParser(prog="purr bench", description="benchmark purr against OpenCode")
    ap.add_argument("-m", "--models", help="comma-separated model names (from config.toml)")
    ap.add_argument("-t", "--tasks", help="comma-separated task names (default: all)")
    ap.add_argument("--harness", default="purr,opencode", help="purr, opencode or both (default)")
    ap.add_argument("--runs", type=int, default=1, help="how often to run every task")
    ap.add_argument("--timeout", type=int, default=600, help="seconds per run (default 600)")
    ap.add_argument("--vague", action="store_true",
                    help="use the tasks' short, vague prompts (try with --harness purr,purr+refine,opencode)")
    args = ap.parse_args(argv)

    tasks = load_tasks(set(args.tasks.split(",")) if args.tasks else None)
    if args.vague:
        tasks = [{**t, "prompt": t["vague"]} for t in tasks if t.get("vague")]
    harnesses = [h for h in args.harness.split(",") if h in HARNESSES]
    models = args.models.split(",") if args.models else pick_models(config)
    unknown = [m for m in models if m not in config["models"]]
    if unknown or not models or not tasks or not harnesses:
        ui.say(ui.ROSE, f"  nothing to run{': no model called ' + ', '.join(unknown) if unknown else ''}")
        return 1

    out_dir = BENCH_DIR / datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir.mkdir(parents=True)
    total = len(models) * len(tasks) * len(harnesses) * args.runs
    ui.say(ui.DIM, f"\n  {len(models)} model(s) × {len(tasks)} tasks × {'+'.join(harnesses)}"
                   f"{f' × {args.runs} runs' if args.runs > 1 else ''} = {total} runs   → {out_dir}\n")
    results, n = [], 0
    for model in models:  # model by model, so a local model only loads once
        ui.say(ui.PINK, f"  ♡ {model}")
        for task in tasks:
            for run in range(args.runs):
                for harness in harnesses:
                    n += 1
                    run_dir = out_dir / "runs" / f"{model}__{task['name']}__{harness}{f'__{run + 1}' if args.runs > 1 else ''}"
                    work = run_dir / "work"
                    shutil.copytree(task["dir"] / "files", work)
                    label = f"    {ui.DIM}[{n}/{total}]{ui.RESET} {task['name']:<15} {harness:<12}"
                    ui.out(label + f"{ui.DIM}working…{ui.RESET}", end="\r")
                    r = HARNESSES[harness](config, model, task, work, run_dir, args.timeout)
                    if not (r["error"] and not r["calls"]):
                        r = finish(r, task, work)
                    else:
                        r["hallucinations"] = 0
                    r["run"] = run + 1
                    results.append(r)
                    ui.out("\033[2K" + label + line(r))
                    (out_dir / "results.json").write_text(json.dumps(results, indent=1))

    summary = summarise(results)
    (out_dir / "report.md").write_text(markdown(results, summary, tasks))
    print_table(summary)
    ui.say(ui.DIM, f"\n  report: {out_dir / 'report.md'}\n")
    return 0
