"""purr bench: the same small coding tasks, done by purr and by OpenCode with the same model.

    purr bench                         pick models from a list, then run every task
    purr bench -m qwen3-coder-32k      straight away, with this model (comma-separate more)
    purr bench -t rename,duration      only these tasks
    purr bench --level hard            only the hard tasks (or easy)
    purr bench --you                   do the tasks yourself; your row joins the latest results
    purr bench --harness purr          only purr (or only opencode)
    purr bench --runs 3                every task three times (models aren't the same twice)
    purr bench --vague --harness purr,purr+refine,opencode
                                       vague prompts: does refining them first help?

Every run starts from a fresh copy of the task's files. When the model is done, the task's
hidden tests are copied in and run, so the model never sees what it's graded on. Results go
to ~/.local/state/purr/bench/<date>/: results.json, report.md, and each run's files and log.

Measured per run: solved, tests passed, time, tokens written, tok/s (tokens / time the model
spent answering; local models only, an API's speed depends on too much else), model calls, tool calls, tool errors, failed edits, hallucinations (a tool
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
import tempfile
import sys
import threading
import time
from pathlib import Path

from . import ui
from .agent import STATE_DIR, Agent
from .limits import LOCAL

TASKS_DIR = Path(__file__).resolve().parent.parent / "bench" / "tasks"
BENCH_DIR = STATE_DIR / "bench"
LEAKED_CALL = re.compile(r"<tool_call>|</tool_call>|<function=|<\|tool_call")
CLAIM = re.compile(r"\b(fixed|done|pass(es|ed|ing)?|works|working|implemented|renamed|complete[d]?|"
                   r"success(ful(ly)?)?|all good)\b", re.I)
MISSING = re.compile(r"No such file|FileNotFoundError|not found|does not exist|doesn't exist|\bno file\b", re.I)


# ---- tasks ----

def load_tasks(names=None):
    tasks = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not (d / "task.json").exists() or (names and d.name not in names):
            continue
        spec = json.loads((d / "task.json").read_text())
        files = list((d / "check").glob("test_*.py"))
        hidden = sum(f.read_text().count("    def test_") for f in files)
        # test_spec*.py: details only the clear prompt asks for; vague runs aren't graded on them
        behaviour = sum(f.read_text().count("    def test_") for f in files if not f.name.startswith("test_spec"))
        tasks.append({"name": d.name, "dir": d, "expected": hidden, "expected_vague": behaviour, **spec})
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
    if task.get("is_vague"):  # a vague request can't be graded on details it never mentioned
        for spec in check.glob("test_spec*.py"):
            spec.unlink()
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
    # "FAILED (failures=1, errors=2, skipped=3)": skipped tests aren't wrong ones
    wrong = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", bad.group(1))) if bad else 0
    return max(0, total - wrong), total, syntax


def outside(args, work):
    """Where a tool call's absolute paths point: "outside" if one is a real place outside the
    task's copy (the run doesn't count), "made up" if one doesn't exist at all (a hallucinated
    path: a mistake, but nothing outside was touched), else None. System paths are fine."""
    found = None
    home = work.resolve()
    if home.parent.is_dir() and [p.name for p in home.parent.iterdir()] == [home.name]:
        home = home.parent  # the bench's own wrapper folder (w/03) holds only this task: not outside
    for value in args.values():
        if not isinstance(value, str):
            continue
        for path in re.findall(r"(?:^|[\s'\"=])(~(?=[\s'\";|&)]|$)|(?:/|~/)[^\s'\";|&()]+)", value):
            p = Path(os.path.expanduser(path)).resolve()
            if str(p).startswith(str(work.resolve())):
                continue
            if str(p).startswith(str(home)):  # the wrapper: a garbled path if it doesn't exist
                if not p.exists():
                    found = "made up"
                continue
            if str(p).startswith(("/dev", "/usr", "/bin", "/proc", "/etc")):
                continue  # /usr/bin/python3 and friends are fine
            if p.exists():
                return "outside"
            found = "made up"
    return found


def blank(harness, model, task):
    return {"harness": harness, "model": model, "task": task["name"], "solved": False, "passed": 0,
            "total": task["expected"], "seconds": 0.0, "out_tokens": 0, "model_seconds": 0.0,
            "tok_s": None, "calls": 0, "tool_calls": 0, "tool_errors": 0, "failed_edits": 0,
            "unknown_tool": 0, "missing_file": 0, "leaked_calls": 0, "false_claim": False,
            "syntax_errors": 0, "cost": 0.0, "timeout": False, "error": None, "final_text": "",
            "left_folder": 0, "repaired": 0, "caught": 0}


def finish(r, task, work, local=True):
    if r["left_folder"]:
        r["error"] = f"left its task folder {r['left_folder']}x: not counted (see the log)"
    r["passed"], r["total"], r["syntax_errors"] = grade(task, work)
    r["solved"] = r["total"] > 0 and r["passed"] == r["total"]
    r["false_claim"] = not r["solved"] and bool(CLAIM.search(r["final_text"] or ""))
    r["local"] = local
    if local and r["model_seconds"] > 0.3 and r["out_tokens"]:
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
        _live("text", s)

    def end_reply(self):
        text = "".join(self.reply)
        if text.strip():
            self.r["final_text"] = text
            self.r["leaked_calls"] += len(LEAKED_CALL.findall(text))
        self.reply = []

    def tool_result(self, name, args, result):
        _live("result", _short(result))
        self.r["tool_calls"] += 1
        where = outside(args, self.work)
        if where == "outside":
            self.r["left_folder"] += 1
        elif where == "made up" and (result or "").startswith("error"):
            self.r["missing_file"] += 1
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

    def thinking(self, s):
        _live("think", s)

    def tool(self, line):
        _live("tool", line)

    def note(self, s, kind="dim"):
        _live("note", s)

    def diff(self, path, before, after): pass
    def status(self, s): pass
    def todos(self, items): pass
    def activity(self, what, detail=""): pass


def run_purr(config, model, task, work, log_dir, timeout, refine=False, final_check=True, extras=True, mcp=True):
    name = ("purr+refine" if refine else "purr-bare" if not extras else "purr-nomcp" if not mcp
            else "purr" if final_check else "purr-nocheck")
    r = blank(name, model, task)
    # purr-bare: none of purr's helpers (final check, its test run, code checks, reminders)
    # purr and purr+refine use purr as it is (training wheels by model size); the others switch
    # things off on purpose
    if not extras or not mcp:  # purr-nomcp: purr as it is, minus the MCP servers (and the overview)
        config = {k: v for k, v in config.items() if k != "mcp"}
    if not extras:
        config = {**config, "final_check": False, "final_check_tests": False, "code_checks": False,
                  "reminders": False, "edge_cases": False, "read_before_edit": False}
    elif not final_check:
        config = {**config, "final_check": False}
    view = BenchView(r, work)
    try:
        agent = Agent(config, work, model, view)
    except KeyError as e:
        r["error"] = e.args[0]
        return r
    agent.tools.trust_all = True
    agent.time_limit = 0.9 * timeout  # like Terminal-Bench: purr hears its limit (less 10%) and gets reminders
    agent.set_one_shot()  # nobody answers questions during a bench run
    agent.log_path = log_dir / "purr-session.json"
    timer = threading.Timer(timeout, lambda: (r.__setitem__("timeout", True), setattr(agent, "stop_flag", True)))
    timer.start()
    threading.Thread(target=lambda: STOP.wait() and setattr(agent, "stop_flag", True), daemon=True).start()
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
    if s.get("providers"):
        r["providers"] = s["providers"]  # which OpenRouter providers answered, and how often
    r["in_tokens"], r["cached_in"] = s.get("in", 0), s.get("cached", 0)  # input sent, and how much was cached
    r["model_seconds"] = round(s.get("model_s", 0.0), 1)
    r["cost"] = round(agent.session_cost, 5)
    r["repaired"] = len(agent.tools.repairs)  # slips purr fixed by itself (search -> grep, ...)
    r["caught"] = len(agent.tools.warnings)   # problems purr's code checks pointed out
    if agent.tools.warnings:
        (log_dir / "purr-caught.txt").write_text("\n".join(agent.tools.warnings) + "\n")
    # a tool call written as text that purr turned into a real one is a repair, not a hallucination
    rescued = sum(1 for x in agent.tools.repairs if x.startswith("tool call written as text"))
    r["leaked_calls"] = max(0, r["leaked_calls"] - rescued)
    if agent.tools.repairs:
        (log_dir / "purr-repairs.txt").write_text("\n".join(agent.tools.repairs) + "\n")
    return r


# ---- opencode ----

def stock_opencode(config, model):
    """A folder to be OpenCode's XDG_CONFIG_HOME for one run: only the model under test, none of
    your OpenCode setup (plugins, agents, AGENTS.md, other models), so purr is compared with OpenCode
    as it ships. A local model gets the same context and output cap purr has; an API model uses
    OpenCode's own provider list."""
    spec = config["models"][model]
    oc = {"$schema": "https://opencode.ai/config.json"}
    if spec["provider"] in LOCAL:
        oc["providers"] = {spec["provider"]: {
            "name": spec["provider"], "package": "@opencode/ai/providers/openai-compatible",
            "settings": {"baseURL": config["providers"][spec["provider"]]["base_url"]},
            "models": {spec["id"]: {"modelID": spec["id"], "name": spec["id"],
                                    "capabilities": {"tools": True, "input": ["text"], "output": ["text"]},
                                    "limit": {"context": spec.get("context", 32768),
                                              "output": (spec.get("body") or {}).get("max_tokens", 8192)}}}}}
    folder = Path(tempfile.mkdtemp(prefix="purr-opencode-"))
    (folder / "opencode").mkdir()
    (folder / "opencode" / "opencode.json").write_text(json.dumps(oc, indent=1))
    return folder


def watch_opencode(line):
    """One line of OpenCode's JSON stream, for watch mode."""
    if not live:
        return
    try:
        e = json.loads(line)
    except ValueError:
        return
    kind, part = e.get("type"), e.get("part") or {}
    if kind == "reasoning":
        _live("think", (part.get("text") or "") + "\n")
    elif kind == "text":
        _live("text", (part.get("text") or "") + "\n")
    elif kind == "tool_use":
        state = part.get("state") or {}
        args = " ".join(str(v)[:100] for v in (state.get("input") or {}).values())
        _live("tool", f"{part.get('tool', '?')} {args}")
        _live("result", _short(str(state.get("output") or state.get("error") or "")))


def end_opencode_session(events):
    """A killed `opencode run` leaves its session marked busy in OpenCode's database, and the
    background service resumes it later (it once kept working for hours, swapping models on the
    GPU). Mark it interrupted so nothing picks it up again."""
    ids = {json.loads(line).get("sessionID") for line in events.splitlines() if line.startswith("{")}
    for sid in filter(None, ids):
        try:
            subprocess.run(["opencode", "api", "session.interrupt", "--param", f"sessionID={sid}"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass


def run_opencode(config, model, task, work, log_dir, timeout):
    r = blank("opencode", model, task)
    spec = config["models"][model]
    oc = f"{spec['provider']}/{spec['id']}"
    cmd = ["opencode", "run", "--standalone", "--auto", "--thinking", "--format", "json", "-m", oc, task["prompt"]]
    start = time.monotonic()
    # its own process group, so a timeout also stops the server it starts
    # OpenCode takes its folder from $PWD, not the real working directory: set both, or it
    # works in whatever folder purr was started from
    stock = stock_opencode(config, model)
    env = {**os.environ, "PWD": str(work), "XDG_CONFIG_HOME": str(stock)}
    proc = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    lines, errs = [], []

    def read_out():  # line by line as it streams, so watch mode can show it live
        for line in proc.stdout:
            lines.append(line)
            watch_opencode(line)

    readers = [threading.Thread(target=read_out, daemon=True),
               threading.Thread(target=lambda: errs.extend(proc.stderr), daemon=True)]
    for t in readers:
        t.start()
    deadline = time.monotonic() + timeout
    while proc.poll() is None:  # wait in small steps, so a stop can end it too
        if STOP.is_set() or time.monotonic() > deadline:
            r["timeout"] = not STOP.is_set()
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # it ignored SIGTERM: make sure it goes
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            break
        time.sleep(0.2)
    for t in readers:
        t.join(timeout=5)
    out, err = "".join(lines), "".join(errs)
    shutil.rmtree(stock, ignore_errors=True)
    if r["timeout"] or STOP.is_set():
        end_opencode_session(out)
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
            cache = tokens.get("cache") or {}
            r["in_tokens"] = r.get("in_tokens", 0) + (tokens.get("input") or 0) + (cache.get("read") or 0)
            r["cached_in"] = r.get("cached_in", 0) + (cache.get("read") or 0)
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
            where = outside(state.get("input") or {}, work)
            if where == "outside":
                r["left_folder"] += 1
            elif where == "made up" and state.get("status") == "error":
                r["missing_file"] += 1
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
             "purr+refine": lambda *a: run_purr(*a, refine=True),
             "purr-nocheck": lambda *a: run_purr(*a, final_check=False),
             "purr-bare": lambda *a: run_purr(*a, extras=False),
             "purr-nomcp": lambda *a: run_purr(*a, mcp=False)}


def warm_up(config, model):
    """Load the model before anything is timed: a local model's first answer includes loading
    it onto the GPU (often 10-30 s), which would count against whoever runs first.
    Returns the seconds it took, or None if it failed."""
    try:
        agent = Agent(config, Path.cwd(), model, BenchView(blank("warmup", model, {"name": "", "expected": 0}),
                                                            Path.cwd()))
        start = time.monotonic()
        agent._call([{"role": "user", "content": "Say hi."}], tools=False, quiet=True)
        return time.monotonic() - start
    except Exception:  # noqa: BLE001 - the runs will show the real problem
        return None


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
    if r.get("disturbed"):
        bits.append(f"{ui.ROSE}⚠ disturbed by {', '.join(r['disturbed'])}{ui.RESET}")
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
                                "syntax_errors": 0, "timeouts": 0, "cost": 0.0, "repaired": 0,
                                "unknown_tool": 0, "missing_file": 0, "leaked_calls": 0, "false_claims": 0,
                                "tool_calls": 0, "caught": 0})
        s["runs"] += 1
        s["local"] = r.get("local", True)
        s["solved"] += r["solved"]
        s["passed"] += r["passed"]
        s["total"] += r["total"]
        s["seconds"] += r["seconds"]
        s["out"] += r["out_tokens"]
        s["model_s"] += r["model_seconds"]
        s["calls"] += r["calls"]
        for key in ("tool_errors", "failed_edits", "hallucinations", "syntax_errors", "cost"):
            s[key] += r[key]
        s["repaired"] += r.get("repaired", 0)
        s["caught"] += r.get("caught", 0)
        for key in ("unknown_tool", "missing_file", "leaked_calls", "tool_calls"):
            s[key] += r.get(key, 0)
        s["false_claims"] += int(r.get("false_claim", False))
        s["timeouts"] += r["timeout"]
    return list(rows.values())


COLUMNS = ["model", "harness", "solved", "tests", "avg time", "tok/s", "tokens", "calls",
           "tool errors", "failed edits", "hallucinations", "syntax errors", "repaired"]


def speed(s):
    """A summary row's tok/s, or "" for API models (their speed isn't yours to measure)."""
    return f"{s['out'] / s['model_s']:.0f}" if s.get("local", True) and s["model_s"] > 0.3 and s["out"] else ""


def table_rows(summary):
    out = []
    for s in summary:
        out.append([s["model"], s["harness"], f"{s['solved']}/{s['runs']}",
                    f"{100 * s['passed'] // max(s['total'], 1)}%", ui.duration(s["seconds"] / s["runs"]),
                    speed(s) or "-",
                    ui.short(s["out"]), str(s["calls"]), str(s["tool_errors"]), str(s["failed_edits"]),
                    str(s["hallucinations"]), str(s["syntax_errors"]),
                    str(s["repaired"]) if s["harness"].startswith("purr") else "-"])
    return out


def markdown(results, summary, tasks):
    lines = [f"# purr bench, {datetime.date.today()}", "",
             "Same tasks, same model, both harnesses with everything allowed. Hidden tests decide "
             "if a task is solved. tok/s = tokens written / time the model spent answering (local models only). "
             "repaired = model slips purr fixed by itself (a tool name from another harness, an edit "
             "aimed at the wrong file, ...); they don't count as errors.", "",
             "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(row) + " |" for row in table_rows(summary)]
    lines += ["", "## every run", "",
              "| model | harness | task | result | time | tok/s | calls | tool errors | hallucinations |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        res = "skipped: " + r["error"] if r["error"] and not r["calls"] else \
            ("✓" if r["solved"] else f"✗ {r['passed']}/{r['total']}") + (" (timed out)" if r["timeout"] else "") \
            + (f" ⚠ disturbed by {', '.join(r['disturbed'])}" if r.get("disturbed") else "")
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
        colour = (ui.LILAC if n == 0 else ui.MINT if row[1] == "human"
                  else ui.PINK if row[1].startswith("purr") else ui.RESET)
        ui.out("  " + colour + "  ".join(cells) + ui.RESET)


STOP = threading.Event()  # set it to cancel the run going on and stop after it
live = None  # live(kind, text): what the run going on is doing right now (watch mode)


def _live(kind, text):
    if live and text:
        live(kind, text)


def _short(text, lines=2, width=160):
    out = [line[:width] for line in (text or "").strip().splitlines()[:lines]]
    return "\n".join(out) + (" …" if len((text or "").strip().splitlines()) > lines else "")


def ollama_loaded(config):
    """The models Ollama has loaded right now (empty if it can't be asked)."""
    base = (config.get("providers", {}).get("ollama", {}).get("base_url") or "http://127.0.0.1:11434/v1")
    try:
        import urllib.request
        with urllib.request.urlopen(base.rsplit("/v1", 1)[0] + "/api/ps", timeout=3) as resp:
            return {m["name"].removesuffix(":latest") for m in json.load(resp).get("models", [])}
    except Exception:  # noqa: BLE001 - no Ollama, or an API model: nothing to watch
        return set()


def unreachable(config, model):
    """Why the model's server can't be used right now, or None. Checked before a model's runs,
    so a server that isn't running doesn't fail every run one by one."""
    spec = config["models"][model]
    base = (config["providers"].get(spec["provider"]) or {}).get("base_url", "")
    if not base.startswith(("http://127.", "http://localhost")):
        return None  # an API: its own errors are clear enough
    try:
        import urllib.request
        urllib.request.urlopen(base.rstrip("/") + "/models", timeout=5).close()
        return None
    except Exception:  # noqa: BLE001
        hint = "start it with `purr-turbo start`" if spec["provider"] == "llamacpp" else "is it running?"
        return f"{spec['provider']} at {base} doesn't answer: {hint}"


def disturbed_by(config, model):
    """Other models on the GPU next to a local model under test: something else is using
    Ollama, so it swaps models and every step gets slow. Returns their names."""
    spec = config["models"][model]
    if spec.get("provider") != "ollama":
        return set()
    return ollama_loaded(config) - {spec["id"].removesuffix(":latest")}


def tool_version(name):
    """`<name> --version`'s first line, for the record (None when it isn't installed)."""
    try:
        out = subprocess.run([name, "--version"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        return (out.stdout or out.stderr).strip().splitlines()[0][:80]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def run_all(config, models, tasks, harnesses, runs, timeout, out_dir, events):
    """Run everything, model by model. events(kind, info) hears about it: "warming", "warm",
    "start", "done", "finished". Returns (results, summary)."""
    global live
    live = lambda kind, text: events("live", {"kind": kind, "text": text})  # noqa: E731
    total = len(models) * len(tasks) * len(harnesses) * runs
    from . import full_version
    (out_dir / "meta.json").write_text(json.dumps({  # what this run was, for --publish
        "purr": full_version(), "date": datetime.date.today().isoformat(), "models": models,
        "harnesses": harnesses, "tasks": [t["name"] for t in tasks], "vague": any(t.get("is_vague") for t in tasks),
        "runs": runs, "timeout": timeout, "opencode": tool_version("opencode") if "opencode" in harnesses else None,
        "ollama": tool_version("ollama"), "opencode_config": "stock (only the model under test)"}, indent=1))
    results, n = [], 0
    for model in models:  # model by model, so a local model only loads once
        if STOP.is_set():
            break
        why = unreachable(config, model)
        if why:  # skip this model instead of failing all its runs
            events("unreachable", {"model": model, "why": why})
            n += len(tasks) * len(harnesses) * runs
            continue
        events("warming", {"model": model})
        events("warm", {"model": model, "seconds": warm_up(config, model)})
        for t, task in enumerate(tasks):
            for run in range(runs):
                # take turns going first, so neither harness always gets the fresher start
                order = harnesses if (t + run) % 2 == 0 else list(reversed(harnesses))
                for harness in order:
                    if STOP.is_set():
                        break
                    n += 1
                    run_dir = out_dir / "runs" / f"{model}__{task['name']}__{harness}{f'__{run + 1}' if runs > 1 else ''}"
                    run_dir.mkdir(parents=True)
                    # the model works alone in a private temp folder named like a real project: next
                    # to other runs it could find their answers (an OpenCode run once read purr's
                    # solution from the folder beside it). Its files are kept in w/NN afterwards.
                    sandbox = Path(tempfile.mkdtemp(prefix="purr-bench-"))
                    work = sandbox / task["name"]
                    shutil.copytree(task["dir"] / "files", work)
                    kept = out_dir / "w" / f"{n:02d}" / task["name"]
                    before = disturbed_by(config, model)
                    if before:
                        events("disturbed", {"model": model, "others": sorted(before)})
                    events("start", {"n": n, "total": total, "model": model, "task": task["name"], "harness": harness})
                    _live("start", f"{model} · {task['name']} · {harness}")
                    r = HARNESSES[harness](config, model, task, work, run_dir, timeout)
                    others = before | disturbed_by(config, model)
                    if others:  # its time and speed don't mean much: say so
                        r["disturbed"] = sorted(others)
                    if STOP.is_set():
                        r["error"], r["calls"] = "stopped", 0  # cut short: not counted
                    if not (r["error"] and not r["calls"]):
                        r = finish(r, task, work, config["models"][model].get("provider") in LOCAL)
                    else:
                        r["hallucinations"] = 0
                    shutil.copytree(work, kept, symlinks=True)  # keep what it did, then tidy up
                    (run_dir / "work").symlink_to(kept)
                    shutil.rmtree(sandbox, ignore_errors=True)
                    r["run"] = run + 1
                    results.append(r)
                    events("done", {"n": n, "total": total, "result": r})
                    (out_dir / "results.json").write_text(json.dumps(results, indent=1))
                    # the report too, so quitting (or a crash) never loses what's done
                    (out_dir / "report.md").write_text(markdown(results, summarise(results), tasks))
    summary = summarise(results)
    report = out_dir / "report.md"
    report.write_text(markdown(results, summary, tasks))
    events("finished", {"results": results, "summary": summary, "report": report})
    return results, summary


def printer(watch=False):
    """events() for the plain text mode. With watch, the runs' live stream is printed too."""
    label = {"live": None}
    looks = {"think": ui.DIM + "\033[3m", "text": ui.RESET, "tool": ui.LILAC, "result": ui.DIM,
             "note": ui.DIM, "start": ui.PINK}

    def events(kind, info):
        if kind == "live":
            if not watch:
                return
            k, text = info["kind"], info["text"]
            if k in ("think", "text"):  # streamed in pieces
                if label["live"] != k:
                    ui.out(f"\n{looks[k]}      ", end="")
                ui.out(looks[k] + text.replace("\n", "\n      ") + ui.RESET, end="")
            else:
                prefix = {"tool": "◈ ", "result": "  ↳ ", "note": "", "start": "── "}[k]
                ui.out(f"\n{looks[k]}      {prefix}" + text.replace("\n", "\n        ") + ui.RESET)
            label["live"] = k
            return
        if kind == "warming":
            ui.out(f"  {ui.PINK}♡ {info['model']}{ui.RESET}  {ui.DIM}warming up…{ui.RESET}", end="\r")
        elif kind == "warm":
            s = info["seconds"]
            ui.out("\033[2K" + f"  {ui.PINK}♡ {info['model']}{ui.RESET}  {ui.DIM}"
                   + (f"loaded in {s:.0f}s (not counted)" if s is not None else "couldn't warm it up") + ui.RESET)
        elif kind == "start":
            label["now"] = f"    {ui.DIM}[{info['n']}/{info['total']}]{ui.RESET} {info['task']:<15} {info['harness']:<12}"
            ui.out(label["now"] + f"{ui.DIM}working…{ui.RESET}", end="\r")
        elif kind == "done":
            ui.out("\033[2K" + label["now"] + line(info["result"]))
        elif kind == "unreachable":
            ui.out(f"  {ui.ROSE}♡ {info['model']}: skipped, {info['why']}{ui.RESET}")
        elif kind == "disturbed":
            ui.out(f"    {ui.ROSE}⚠ {', '.join(info['others'])} is also loaded in Ollama: something else is using "
                   f"the GPU, so this run will be slow{ui.RESET}")
        elif kind == "finished":
            print_table(info["summary"])
            ui.say(ui.DIM, f"\n  report: {info['report']}\n")
    return events


def parse(argv):
    ap = argparse.ArgumentParser(prog="purr bench", description="benchmark purr against OpenCode")
    ap.add_argument("-m", "--models", help="comma-separated model names (from config.toml)")
    ap.add_argument("-t", "--tasks", help="comma-separated task names (default: all)")
    ap.add_argument("--harness", default="purr,opencode", help="purr, opencode or both (default)")
    ap.add_argument("--runs", type=int, default=1, help="how often to run every task")
    ap.add_argument("--timeout", type=int, default=600, help="seconds per run (default 600)")
    ap.add_argument("--vague", action="store_true",
                    help="use the tasks' short, vague prompts (try with --harness purr,purr+refine,opencode)")
    ap.add_argument("--level", choices=["easy", "hard"], help="only the easy or only the hard tasks")
    ap.add_argument("--watch", action="store_true",
                    help="show what each run is doing and thinking, live (in the window: w toggles it)")
    ap.add_argument("--plain", action="store_true", help="print lines instead of the full-screen window")
    ap.add_argument("--publish", nargs="?", const="latest", metavar="FOLDER",
                    help="file a finished run (default: the latest) under benchmarks/purr-bench/ in the repo")
    ap.add_argument("--real", nargs="?", const="pick", choices=["pick", "terminal-bench", "deepswe"],
                    help="the real benchmarks instead (Terminal-Bench, DeepSWE), the way other harnesses are scored")
    ap.add_argument("--size", choices=["quick", "one", "full"], help="with --real: the quick 20, every task once, or x3")
    ap.add_argument("--jobs", type=int, default=6, help="with --real: tasks at once (default 6)")
    ap.add_argument("--edge-cases", action="store_true", help="with --real: purr's extra edge-case check on")
    ap.add_argument("--you", action="store_true", help="do the tasks yourself, for fun (your row joins the latest results)")
    ap.add_argument("--new", action="store_true", help="with --you: a results folder of your own")
    return ap.parse_args(argv)


def pick_tasks(names=None, vague=False, level=None):
    tasks = [t for t in load_tasks(set(names) if names else None) if not level or t.get("level") == level]
    return [{**t, "prompt": t["vague"], "is_vague": True, "expected": t["expected_vague"]}
            for t in tasks if t.get("vague")] if vague else tasks


def new_out_dir():
    out = BENCH_DIR / datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out.mkdir(parents=True)
    return out


PUBLISHED = Path(__file__).resolve().parent.parent / "benchmarks" / "purr-bench"

NOTE = """> **Not an official benchmark.** `purr bench` is for improving purr as a harness for
> **small, local models**: purr's goal is to get the most out of a ~10-35B model on your own GPU,
> on longer and vaguely worded tasks. These are purr's own small tasks (`bench/tasks/`), often run
> just once each, to debug, iterate on and improve the harness: a task or two either way is noise,
> and the tasks were written alongside purr. It's **not meant for judging large models** (big API
> models solve most of these). For official, comparable numbers see [Terminal-Bench](../terminal-bench/).
>
> **Hardware:** AMD Ryzen 9 5900X (12 cores), 32 GB RAM, NVIDIA RTX 4080 (16 GB VRAM), Arch Linux;
> local models through Ollama. Speeds and times are this machine's: yours will differ."""


HARNESS_NAMES = {"purr": "purr", "opencode": "OpenCode", "human": "you", "purr-nomcp": "purr (no MCP)",
                 "purr-bare": "purr (bare)", "purr+refine": "purr + refine", "purr-nocheck": "purr (no final check)"}
# each row of the side-by-side table: label, the number (None: doesn't apply), how to show it, higher is better
MEASURES = [
    ("tasks solved", lambda s: s["solved"] / max(s["runs"], 1), lambda s: f"{s['solved']}/{s['runs']}", True),
    ("hidden tests passed", lambda s: s["passed"] / max(s["total"], 1),
     lambda s: f"{100 * s['passed'] // max(s['total'], 1)}%", True),
    ("time per task", lambda s: s["seconds"] / max(s["runs"], 1), lambda s: ui.duration(s["seconds"] / max(s["runs"], 1)), False),
    ("tokens per task", lambda s: s["out"] / max(s["runs"], 1) if s["harness"] != "human" else None,
     lambda s: ui.short(round(s["out"] / max(s["runs"], 1))), False),
    ("tool errors", lambda s: s["tool_errors"] if s["harness"] != "human" else None, lambda s: str(s["tool_errors"]), False),
    ("claimed done but wasn't", lambda s: s["false_claims"] if s["harness"] != "human" else None,
     lambda s: str(s["false_claims"]), False),
]


def _label(s, many_models):
    name = HARNESS_NAMES.get(s["harness"], s["harness"])
    return name if many_models is False or s["harness"] == "human" else f"{name} · {s['model']}"


def _model_name(key):
    """`key` (its id), and where it ran: on this machine's GPU, or an API model."""
    try:
        from harness import settings
        spec = settings.load(discover=False)["models"].get(key, {})
    except (OSError, ValueError, SystemExit):
        spec = {}
    name = f"`{key}` ({spec['id']})" if spec.get("id") and spec["id"] != key else f"`{key}`"
    if spec.get("provider") in LOCAL:
        return name + ", a local model on the RTX 4080"
    if spec.get("provider"):
        return name + ", an API model: a check on the harness, not purr's small-model goal"
    return name


def _run_section(p, heading):
    """One published run: a headline, the side-by-side table (best in bold) and a task-by-task grid."""
    m, summary, rows = p["meta"], [s for s in p["summary"] if s["runs"]], p["results"]
    models = sorted({s["model"] for s in summary if s["harness"] != "human"})
    many = len(models) > 1
    prompts = "vague" if m.get("vague") else "full"
    runs = m.get("runs", 1)
    title = f"{m['date']} · {', '.join(models) or 'people'} · {len(m['tasks'])} tasks, {prompts} prompts"
    ranked = sorted(summary, key=lambda s: -s["solved"] / max(s["runs"], 1))
    unit = "tasks" if runs == 1 else "runs"
    headline = " · ".join(f"{'**' + _label(s, many) + '**' if i == 0 else _label(s, many)} solved "
                          f"{s['solved']} of {s['runs']} {unit}" for i, s in enumerate(ranked))
    out = [f"{heading} {title}", "", headline, "",
           f"purr {m['purr']}" + (f" · model {_model_name(models[0])}" if len(models) == 1 else "")
           + (f" · {runs} runs per task" if runs > 1 else "") + " · **bold** = better", ""]
    out += ["| | " + " | ".join(_label(s, many) for s in summary) + " |", "|---|" + "---|" * len(summary)]
    for label, number, show, higher in MEASURES:
        values = [number(s) for s in summary]
        real = [v for v in values if v is not None]
        best = (max(real) if higher else min(real)) if len(real) > 1 and len(set(real)) > 1 else None
        cells = ["—" if v is None else (f"**{show(s)}**" if v == best else show(s)) for s, v in zip(summary, values)]
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    out += ["", "**Task by task**", "", "| task | " + " | ".join(_label(s, many) for s in summary) + " |",
            "|---|" + "---|" * len(summary)]
    for task in m["tasks"]:
        cells = []
        for s in summary:
            mine = [r for r in rows if r["task"] == task and r["harness"] == s["harness"] and r["model"] == s["model"]]
            if not mine:
                cells.append("—")
            elif len(mine) == 1:
                r = mine[0]
                cells.append("✓" if r["solved"] else ("⏱ timed out" if r.get("timeout") else f"✗ {r['passed']}/{r['total']}"))
            else:
                won = sum(1 for r in mine if r["solved"])
                cells.append(f"{'✓' if won else '✗'} {won}/{len(mine)}")
        out.append(f"| {task} | " + " | ".join(cells) + " |")
    out += ["", "✓ every hidden test passed · ✗ 9/10: 9 of the 10 hidden tests passed"
            + (" · with several runs: how many of them solved it" if runs > 1 else ""), ""]
    return out


def rebuild_readme():
    """benchmarks/purr-bench/README.md from every published run: the latest in full, the rest folded."""
    published = []
    for f in sorted((PUBLISHED / "results").glob("*.json"), reverse=True):
        try:
            published.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    lines = ["# purr bench", "", NOTE, ""]
    if not published:
        lines.append("Nothing published yet: `purr bench`, then `purr bench --publish`.")
    else:
        lines += _run_section(published[0], "## Latest:")
        if len(published) > 1:
            lines += ["## Earlier runs", ""]
            for p in published[1:]:
                section = _run_section(p, "###")
                lines += [f"<details><summary>{section[0].removeprefix('### ')}</summary>", "", *section[1:], "</details>", ""]
    lines += ["---", "", "Every run's full results are in [`results/`](results/). Run your own: `purr bench`, then",
              "`purr bench --publish`; play it yourself with `purr bench --you`.", ""]
    (PUBLISHED / "README.md").write_text("\n".join(lines))


def publish(folder="latest"):
    """purr bench --publish [FOLDER]: copy a finished run's results into the repo and rebuild the
    README there (every published run, newest first)."""
    if folder == "latest":
        runs = sorted(BENCH_DIR.glob("*/results.json"), key=lambda p: p.stat().st_mtime) if BENCH_DIR.exists() else []
        if not runs:
            ui.say(ui.ROSE, "  no bench runs to publish")
            return 1
        out = runs[-1].parent
    else:
        out = Path(folder).expanduser().resolve()
    try:
        results = json.loads((out / "results.json").read_text())
    except (OSError, ValueError):
        ui.say(ui.ROSE, f"  no results.json in {out}")
        return 1
    meta = {}
    if (out / "meta.json").exists():
        meta = json.loads((out / "meta.json").read_text())
    meta.setdefault("purr", "before 0.3")  # runs from before meta.json existed
    meta.setdefault("date", out.name[:10])
    meta.setdefault("tasks", sorted({r["task"] for r in results}))
    if "vague" not in meta:  # older runs: a vague prompt is graded on fewer tests (no spec tests)
        specs = {t["name"]: t for t in load_tasks()}
        meta["vague"] = any(r["task"] in specs and r["total"] == specs[r["task"]]["expected_vague"]
                            != specs[r["task"]]["expected"] for r in results)
    keep = [{k: v for k, v in r.items() if k != "final_text"} for r in results]  # the rows, minus the long texts
    (PUBLISHED / "results").mkdir(parents=True, exist_ok=True)
    (PUBLISHED / "results" / f"{out.name}.json").write_text(
        json.dumps({"meta": meta, "summary": summarise(results), "results": keep}, indent=1) + "\n")
    rebuild_readme()
    ui.say(ui.MINT, f"  published {out.name} (purr {meta['purr']}) → {PUBLISHED / 'README.md'}")
    return 0


def real(config, args, window):
    """Terminal-Bench / DeepSWE: the window watches tbench/fair.sh; plain mode just runs it."""
    from . import realbench
    suite = args.real if args.real in realbench.SUITES else None
    if window:
        from tui.realbench_app import RealBenchApp
        return RealBenchApp(config, suite, args.size, args.jobs, args.edge_cases).run() or 0
    cmd = realbench.command(suite or "terminal-bench", args.size or "quick", args.edge_cases)
    ui.say(ui.DIM, f"  {' '.join(cmd)}   (PURR_JOBS={args.jobs})")
    return subprocess.call(cmd, cwd=realbench.ROOT, env={**os.environ, "PURR_JOBS": str(args.jobs)})


def main(argv, config, window=False):
    args = parse(argv)
    if args.publish:
        return publish(args.publish)
    if args.you:  # you play: no models, nothing on the GPU
        from .play import play
        return play(args)
    if args.real:
        return real(config, args, window and not args.plain)
    if window and not args.plain:
        from tui.bench_app import BenchApp
        if BenchApp(config, args).run() == "real":  # r on its setup screen
            return real(config, args, True)
        return 0

    tasks = pick_tasks(args.tasks.split(",") if args.tasks else None, args.vague, args.level)
    harnesses = [h for h in args.harness.split(",") if h in HARNESSES]
    models = args.models.split(",") if args.models else pick_models(config)
    unknown = [m for m in models if m not in config["models"]]
    if unknown or not models or not tasks or not harnesses:
        ui.say(ui.ROSE, f"  nothing to run{': no model called ' + ', '.join(unknown) if unknown else ''}")
        return 1
    out_dir = new_out_dir()
    total = len(models) * len(tasks) * len(harnesses) * args.runs
    ui.say(ui.DIM, f"\n  {len(models)} model(s) × {len(tasks)} tasks × {'+'.join(harnesses)}"
                   f"{f' × {args.runs} runs' if args.runs > 1 else ''} = {total} runs   → {out_dir}\n")
    try:
        run_all(config, models, tasks, harnesses, args.runs, args.timeout, out_dir, printer(args.watch))
    except KeyboardInterrupt:
        STOP.set()
        ui.say(ui.ROSE, f"\n  stopped. finished runs are in {out_dir}")
    return 0
