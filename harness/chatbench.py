"""purr bench --chat: chats instead of one request each.

    purr bench --chat -m gpt-oss-64k               every scenario in bench/chat/, purr as it is
    purr bench --chat --harness purr,purr-nochat   ... and purr without its chat helpers
    purr bench --chat -t still-fails --watch       one scenario, watching it work

Everyday use is a chat: a few turns, the user editing files in between, /undo, "it still fails".
A scenario (bench/chat/<name>/scenario.json) is a list of turns. Before a turn the bench can edit,
append, write or delete files like you in your editor, run a command, or /undo; a turn can be sent
only when the user's own run (check/user) fails, with its output; a steer can arrive mid-turn.
Hidden tests on the end state (check/final, and the base task's check/ with checks_from_task)
decide "solved"; check/turnN, graded on a copy after a turn, show where the chat slipped. Results
go next to purr bench's, in <date>_chat/.
"""

import functools
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import bench, prompts, ui
from .agent import Agent
from .tools import child_env

CHAT_DIR = Path(__file__).resolve().parent.parent / "bench" / "chat"
MIN_LEFT = 30      # seconds: every turn still to come keeps this much; with less, the rest are skipped
OUTPUT_LINES = 25  # of the user's failing run, quoted in an if_fails turn's {output}
ACTIONS = ("edit", "append", "write", "delete", "undo", "run")
COUNTERS = ("tool_calls", "tool_errors", "failed_edits", "asked_to_continue")  # added up in the row as it goes
DECIDED = re.compile(r"^\s*[*_]*Decided[*_]*:.*$", re.M)  # "Decided: ..." or "**Decided:** ..."
# the final check a turn ended with, by the words purr sent (the text before its {services})
FINAL_CHECKS = [(p.split("{services}")[0], kind) for p, kind in (
    (prompts.FINAL_CHECK_GREEN, "green"), (prompts.FINAL_CHECK_RED, "red"),
    (prompts.FINAL_CHECK, "other"), (prompts.FINAL_CHECK_LIGHT, "other"))]


# ---- scenarios ----

def _start_text(d, files_from, path):
    """A file as the scenario starts (its own files over the base task's), or None."""
    for root in (d / "files", bench.TASKS_DIR / files_from / "files" if files_from else None):
        if root and (root / path).is_file():
            return (root / path).read_text()
    return None


def load_scenario(d):
    """One scenario folder, checked, as a task-shaped dict (bench's helpers take it as it is)."""
    d = Path(d)
    spec = json.loads((d / "scenario.json").read_text())

    def bad(why):
        raise ValueError(f"chat scenario {d.name}: {why}")

    turns = spec.get("turns") or bad("no turns")
    base = spec.get("files_from")
    if base and not (bench.TASKS_DIR / base / "files").is_dir():
        bad(f"files_from: no bench task called {base}")
    if spec.get("checks_from_task") and not base:
        bad("checks_from_task needs files_from")
    folders = ["final"]
    for k, turn in enumerate(turns, 1):
        if not turn.get("user"):
            bad(f"turn {k} has no user message")
        folders += [turn[key] for key in ("check", "if_fails") if turn.get(key)]
        steer = turn.get("steer")
        if steer and (not steer.get("text") or steer.get("at_step", 0) < 2):
            bad(f"turn {k}: a steer needs text and at_step 2 or later (step 1 is the message itself)")
        if turn.get("timeout", 0) > bench.CHAT_CAP:
            bad(f"turn {k}: a timeout of at most {bench.CHAT_CAP}")
        for action in turn.get("before", []):
            kind = next((key for key in ACTIONS if key in action), None)
            if not kind:
                bad(f"turn {k}: an action that isn't one of {', '.join(ACTIONS)}: {action}")
            if kind == "edit":
                if "old" not in action or "new" not in action:
                    bad(f"turn {k}: an edit needs old and new")
                # hand edits only touch text the request leaves alone: it must be there, once
                if (_start_text(d, base, action["edit"]) or "").count(action["old"]) != 1:
                    bad(f"turn {k}: the edit's old text isn't in {action['edit']} exactly once")
            if kind == "run" and action.get("keep") and not action.get("in"):
                bad(f"turn {k}: a run's keep needs in (the file it's kept in)")
    for name in folders:
        if not (d / "check" / name).is_dir():
            bad(f"no check/{name}")
    # with checks_from_task the base task's hidden tests decide too, so the copies can't drift apart
    checks = ([bench.TASKS_DIR / base / "check"] if spec.get("checks_from_task") else []) + [d / "check" / "final"]
    return {"name": d.name, "dir": d, "kind": spec.get("kind", ""), "level": spec.get("level"), "turns": turns,
            "files_from": base, "check_dirs": checks, "expected": bench.count_tests(checks),
            "turn_checks": {name: bench.count_tests(d / "check" / name) for name in dict.fromkeys(folders)}}


def load_scenarios(names=None, level=None):
    scenarios = [load_scenario(d) for d in sorted(CHAT_DIR.iterdir())
                 if (d / "scenario.json").exists() and (not names or d.name in names)]
    return [sc for sc in scenarios if not level or sc["level"] == level]


def fill(sc, work):
    """The scenario's starting files: the base task's, then its own over them."""
    work.mkdir(parents=True, exist_ok=True)
    if sc.get("files_from"):
        shutil.copytree(bench.TASKS_DIR / sc["files_from"] / "files", work, dirs_exist_ok=True)
    if (sc["dir"] / "files").is_dir():
        shutil.copytree(sc["dir"] / "files", work, dirs_exist_ok=True)


def check_copy(sc, folder, work):
    """Grade check/<folder> on a copy of the project, so hidden tests never land in the model's
    folder. Returns (passed, total, the run's stderr with the copy's path taken out)."""
    with tempfile.TemporaryDirectory(prefix="purr-chat-check-") as tmp:
        copy = Path(tmp) / work.name
        shutil.copytree(work, copy, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
        passed, total, _, out = bench.run_check(sc["dir"] / "check" / folder, copy, sc["turn_checks"][folder])
        return passed, total, out.replace(str(copy) + "/", "")


def user_output(stderr):
    """What the user saw failing, for {output}: the check's failure message (a user check puts what
    the user would see there), else the run's end, without the hidden check's own lines."""
    ends = list(re.finditer(r"\n-{40,}\nRan \d+ tests? in", stderr))  # the last: a quoted run has its own
    text = stderr[:ends[-1].start()] if ends else stderr
    said = text.split("AssertionError: ", 1)
    if len(said) == 2:
        text = re.sub(r"^.*? : \n", "", said[1], count=1)  # "1 != 0 : <message>": the message
    lines, hidden = [], False
    for line in text.strip().splitlines():
        if "_bench_check" in line:
            hidden = line.lstrip().startswith("File ")  # the check's source line and ^^^ follow it
            continue
        if hidden and line.startswith("    "):
            continue
        hidden = False
        lines.append(line)
    return "\n".join(lines[-OUTPUT_LINES:])


def snapshot(work):
    """The project's files as bytes (no caches or dot folders), to check what /undo put back."""
    out = {}
    for p in work.rglob("*"):
        rel = p.relative_to(work)
        if p.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            out[str(rel)] = p.read_bytes()
    return out


def apply(action, work):
    """One thing the user does between turns (/undo aside). Returns (a short note of it, an error or None)."""
    if "run" in action:
        try:
            # the user's own terminal: the machine's python3 (child_env), as the model's commands get
            done = subprocess.run(action["run"], shell=True, cwd=work, capture_output=True, text=True, timeout=60,
                                  env=child_env(work))
            return f"ran {action['run'][:60]} (exit {done.returncode})", None
        except subprocess.TimeoutExpired:
            return f"ran {action['run'][:60]} (timed out)", None
    kind = next(k for k in ("edit", "append", "write", "delete") if k in action)
    path = action[kind]
    p = work / path
    if kind == "delete":
        p.unlink(missing_ok=True)
    elif kind == "write":
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(action["content"])
    elif kind == "append":
        p.write_text((p.read_text() if p.exists() else "") + action["text"])
    else:
        text = p.read_text() if p.exists() else ""
        if text.count(action["old"]) != 1:  # the model changed the text this edit is aimed at
            return None, f"hand edit couldn't apply: {path}"
        p.write_text(text.replace(action["old"], action["new"]))
    return f"{kind} {path}", None


# ---- running one ----

class ChatView(bench.BenchView):
    """BenchView for a chat: "N steps so far. keep going?" is answered yes, and counted."""

    def ask(self, question, allow_always=True):
        self.r["asked_to_continue"] = self.r.get("asked_to_continue", 0) + 1
        return "y", ""


def _final_check(users):
    """Which final check the turn ended with (green, red, other or none), by purr's words."""
    kind = "none"
    for u in users:
        kind = next((name for words, name in FINAL_CHECKS if words in u), kind)
    return kind


def _what_purr_did(t, agent, msgs, m0, rep0, streamed):
    """A turn's chat events into its record: purr's notes (by its repairs, which survive a compaction)
    and, from the turn's messages, checkpoints, the final check and a "Decided:" line. Returns the
    model's last words this turn."""
    repairs = agent.tools.repairs[rep0:]
    t["outside_told"] = sum(x.startswith("files changed outside the model's edits") for x in repairs)
    t["regression_note"] = sum(x.startswith("tests passed earlier and fail now") for x in repairs)
    t["compacted"] = agent.messages is not msgs  # a compaction makes a new list: the turn's messages are gone
    said = []
    if t["compacted"]:
        t.update(checkpoint_asks=None, final_check=None, decided=None)
    else:
        new = agent.messages[m0:]
        users = [str(m.get("content") or "") for m in new if m.get("role") == "user"]
        said = [str(m.get("content") or "") for m in new if m.get("role") == "assistant"]
        t["checkpoint_asks"] = sum(u.startswith("(purr: checkpoint") and "The user asked earlier:" in u for u in users)
        t["final_check"] = _final_check(users)
        found = next((m for m in map(DECIDED.search, said) if m), None)
        t["decided"] = found.group(0).strip()[:200] if found else None
    last = next((s for s in reversed(said) if s.strip()), "") or streamed or ""
    t["final_text"] = last[-300:]
    return last


def _check_markers(r, t, work, markers, lost):
    """The user's hand edits, still there? A marker gone counts once (r["clobbered"]); one that comes
    back is noted. lost: {marker: True while it's gone, False once it's back}."""
    for path, marker in markers:
        p = work / path
        there = p.is_file() and marker in p.read_text(errors="replace")
        if not there and not lost.get((path, marker)):
            if (path, marker) not in lost:
                r["clobbered"] += 1
            lost[(path, marker)] = True
            t.setdefault("clobbered", []).append(path)
        elif there and lost.get((path, marker)):
            lost[(path, marker)] = False
            t.setdefault("restored", []).append(path)


def run_purr_chat(config, model, sc, work, log_dir, timeout, turn_timeout=bench.CHAT_TURN, nochat=False,
                  make_agent=Agent, after_turn=None):
    """One scenario, turn by turn, in one chat. Returns the row (bench.finish grades it)."""
    r = bench.blank("purr-nochat" if nochat else "purr", model, sc)
    r.update(turns=[], clobbered=0, turn_passed=0, turn_total=0, followup_needed=False, undo_incomplete=False,
             asked_to_continue=0)
    if nochat:  # purr without what it does for chats: noticing your edits, the regression note
        config = {**config, "outside_changes": False, "regression_note": False}
    view = ChatView(r, work)
    try:
        agent = make_agent(config, work, model, view)
    except KeyError as e:
        r["error"] = e.args[0]
        return r
    agent.time_limit = None  # a chat: no time notes; the bench's timer stops a turn, its deadline cuts commands
    stopped = bench.purr_setup(agent, log_dir)

    # a steer goes in like the window's: at the start of a main-loop step (helpers are other Agents)
    st = {"step": 0, "steer": None, "landed": None, "deadline": None}
    take_steers, begin_turn = agent._take_steers, agent._begin_turn

    def take():
        st["step"] += 1
        if st["steer"] and st["step"] == st["steer"]["at_step"]:
            agent.steers.append(st["steer"]["text"])
            st["landed"], st["steer"] = st["step"], None
        take_steers()

    def begin(text):
        begin_turn(text)
        # the timer only sets stop_flag, which a command doesn't hear: run cuts its timeout to this
        agent.tools.deadline = st["deadline"]

    agent._take_steers, agent._begin_turn = take, begin
    stats = []        # every agent turn's numbers
    last_before = {}  # the files before the latest message: what /undo should bring back

    def send(t, text, budget):
        """One message, stopped (like Esc) after budget seconds."""
        last_before.clear()
        last_before.update(snapshot(work))
        st["deadline"] = time.monotonic() + budget
        timer = threading.Timer(budget, lambda: (t.__setitem__("timeout", True), setattr(agent, "stop_flag", True)))
        timer.start()
        try:
            agent.turn(text)
        finally:
            timer.cancel()
            stats.append(dict(getattr(agent, "turn_stats", None) or {}))
        t["calls"] = t.get("calls", 0) + stats[-1].get("calls", 0)
        t["out_tokens"] = t.get("out_tokens", 0) + stats[-1].get("out", 0)

    turns = sc["turns"]

    def budget_for(k, turn, spent):
        """(the scenario's seconds turn k may use, its own): what's left less what the turns still to
        come keep (MIN_LEFT each, so the last turn isn't lost to slow early ones), and of that the
        turn's own timeout (a scenario's may be longer than --turn-timeout)."""
        room = timeout - spent - MIN_LEFT * (len(turns) - k)
        return room, min(turn.get("timeout", turn_timeout), room)

    used, ran_text, markers, lost = 0.0, "", [], {}
    try:
        for k, turn in enumerate(turns, 1):
            t = {"turn": k}
            r["turns"].append(t)
            if turn.get("if_fails"):
                t["if_fails"] = True
            room, budget = budget_for(k, turn, used)  # turns' own seconds; grading and the user's actions don't count
            if bench.STOP.is_set() or room < MIN_LEFT:
                t["skipped"] = "stopped" if bench.STOP.is_set() else "out of time"
                r["turns"] += [{"turn": n, "skipped": t["skipped"]} for n in range(k + 1, len(turns) + 1)]
                r["timeout"] = r["timeout"] or t["skipped"] == "out of time"
                # their checks count as failed: skipping one mustn't raise the turn-check score
                r["turn_total"] += sum(sc["turn_checks"][x["check"]] for x in turns[k - 1:] if x.get("check"))
                break

            # the user's own doings first
            error, done, touched = None, [], set()
            for action in turn.get("before", []):
                if "undo" in action:  # what /undo calls
                    t["undone"] = agent.undo()
                    note = "undo: " + (", ".join(t["undone"]) or "nothing")
                    if stats:
                        # the user expects the files as they were before the last message; a shell edit
                        # isn't in purr's undo frame, so what still differs wasn't put back
                        now, was = snapshot(work), last_before
                        left_over = sorted(p for p in set(was) | set(now)
                                           if was.get(p) != now.get(p) and p not in touched)
                        if left_over:
                            r["undo_incomplete"] = True
                            t["undo_left"] = left_over
                else:
                    note, error = apply(action, work)
                    path = next((action[key] for key in ("edit", "append", "write", "delete") if key in action), None)
                    if path:
                        touched.add(path)
                    if not error and action.get("keep"):
                        markers.append((action.get("in", path), action["keep"]))
                if error:
                    break
                done.append(note)
            if done:
                t["actions"] = done
            if error:
                # what the model did earlier took away the text the user edits: not this scenario any
                # more, so the row isn't counted (the chat table says how many, per harness)
                t["error"] = r["error"] = error
                r["not_counted"] = True
                break

            text = turn["user"]
            if turn.get("if_fails"):  # the user tries it first: if it works, there's nothing to say
                passed, total, out = check_copy(sc, turn["if_fails"], work)
                if passed == total:
                    t["skipped"] = "already passing"
                    continue
                text = text.replace("{output}", user_output(out))
                r["followup_needed"] = True

            msgs, m0, rep0 = agent.messages, len(agent.messages), len(agent.tools.repairs)
            counts0 = {key: r[key] for key in COUNTERS}
            st.update(step=0, steer=turn.get("steer"), landed=None)
            r["final_text"] = ""
            bench.say_live("start", f"turn {k}: {text[:80]}")
            start = time.monotonic()
            try:
                send(t, text, budget)
                late, st["steer"] = st["steer"], None
                if late and not bench.STOP.is_set():
                    # the turn ended (or ran out of time) before the steer's step: the window sends it
                    # as the next message, if the scenario has the time
                    room, more = budget_for(k, turn, used + time.monotonic() - start)
                    t["steer"] = {"at_step": late["at_step"], "landed": None, "late": room >= MIN_LEFT,
                                  "lost": room < MIN_LEFT}
                    if room >= MIN_LEFT:
                        send(t, late["text"], more)
            except Exception as e:  # noqa: BLE001 - a crash is a result too
                t["error"] = f"{type(e).__name__}: {e}"
                r["error"] = r["error"] or t["error"]
            t["seconds"] = round(time.monotonic() - start, 1)
            used += t["seconds"]
            if turn.get("steer") and "steer" not in t:
                t["steer"] = {"at_step": turn["steer"]["at_step"], "landed": st["landed"], "late": False,
                              "lost": st["landed"] is None}
            if t.get("timeout"):
                r["timeout"] = True
            for key in COUNTERS:
                t[key] = r[key] - counts0[key]
            ran_text = _what_purr_did(t, agent, msgs, m0, rep0, r["final_text"]) or ran_text

            if turn.get("check"):  # where the chat stands now; the end state alone decides "solved"
                passed, total, _ = check_copy(sc, turn["check"], work)
                t["check"] = [passed, total]
                r["turn_passed"] += passed
                r["turn_total"] += total
            _check_markers(r, t, work, markers, lost)
            shutil.copytree(work, log_dir / "turns" / str(k), symlinks=True, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
            if after_turn:
                after_turn(r)
    finally:
        stopped.set()

    r["final_text"] = ran_text
    r["seconds"] = round(used, 1)
    bench.purr_record(r, agent, log_dir, stats)
    return r


HARNESSES = {"purr": run_purr_chat, "purr-nochat": functools.partial(run_purr_chat, nochat=True)}


# ---- running them all ----

def run_all_chat(config, models, scenarios, harnesses, runs, turn_timeout, timeout, out_dir, events):
    """bench.run_all with chats: model by model, harnesses taking turns going first."""
    table = {h: functools.partial(HARNESSES[h], turn_timeout=turn_timeout) for h in harnesses}
    return bench.run_all(config, models, scenarios, harnesses, runs, timeout, out_dir, events, table=table,
                         fill=fill, report=markdown_chat, after_turn=True,
                         meta={"chat": True, "turn_timeout": turn_timeout, "opencode_config": None})


# ---- the report ----

CHAT_NOTE = ("Chats: each scenario is several turns in one chat, with the user's hand edits, /undo and "
             "follow-ups like \"it still fails\" between turns. Hidden tests on the end state decide \"solved\"; "
             "the turn checks (hidden tests on a copy after a turn) show where a chat slipped. purr-nochat is "
             "purr without its notes for files changed behind the model's back and for tests that passed "
             "earlier and fail now.")
CHAT_COLUMNS = ["model", "harness", "turn checks", "user edits lost", "follow-ups needed", "steers late",
                "steers lost", "outside-change notes", "regression notes", "Decided lines", "undo incomplete",
                "not counted"]


def chat_rows(results):
    rows = {}
    for r in results:
        if (r["error"] and not r["calls"] and not r.get("not_counted")) or r.get("left_folder"):
            continue
        s = rows.setdefault((r["model"], r["harness"]), {"model": r["model"], "harness": r["harness"], "passed": 0,
                                                         "total": 0, "clobbered": 0, "followups": 0, "could": 0,
                                                         "late": 0, "lost": 0, "steers": 0, "outside": 0,
                                                         "regression": 0, "decided": 0, "green": 0, "undo": 0,
                                                         "not_counted": 0})
        if r.get("not_counted"):  # left out of every number, but said here: harnesses may differ in it
            s["not_counted"] += 1
            continue
        turns = r.get("turns", [])
        s["passed"] += r.get("turn_passed", 0)
        s["total"] += r.get("turn_total", 0)
        s["clobbered"] += r.get("clobbered", 0)
        s["followups"] += bool(r.get("followup_needed"))
        s["could"] += any(t.get("if_fails") for t in turns)
        s["undo"] += bool(r.get("undo_incomplete"))
        for t in turns:
            if t.get("steer"):
                s["steers"] += 1
                s["late"] += bool(t["steer"].get("late"))
                s["lost"] += bool(t["steer"].get("lost"))
            s["outside"] += t.get("outside_told") or 0
            s["regression"] += t.get("regression_note") or 0
            green = t.get("final_check") == "green"  # only these ask for a "Decided:" line
            s["decided"] += bool(t.get("decided")) and green
            s["green"] += green
    return [[s["model"], s["harness"], f"{100 * s['passed'] // s['total']}%" if s["total"] else "-",
             str(s["clobbered"]), f"{s['followups']}/{s['could']}", f"{s['late']}/{s['steers']}",
             f"{s['lost']}/{s['steers']}", str(s["outside"]), str(s["regression"]), f"{s['decided']}/{s['green']}",
             str(s["undo"]), str(s["not_counted"])]
            for s in rows.values()]


def events_of(t):
    """A turn's events, for the turn-by-turn table."""
    out = []
    if t.get("actions"):
        out.append("user: " + "; ".join(t["actions"]))
    if t.get("outside_told"):
        out.append("told of outside change")
    if t.get("regression_note"):
        out.append("regression note")
    if t.get("steer"):
        s = t["steer"]
        out.append("steer late" if s.get("late") else "steer lost" if s.get("lost") else f"steer @{s['landed']}")
    if t.get("checkpoint_asks"):
        out.append("checkpoint quoted earlier asks")
    if t.get("asked_to_continue"):
        out.append(f"asked to go on ×{t['asked_to_continue']}")
    if t.get("final_check") in ("green", "red"):
        out.append(f"final check {t['final_check']}")
    if t.get("decided"):
        out.append(t["decided"])
    out += [f"lost user edit: {p}" for p in t.get("clobbered", [])]
    out += [f"user edit back: {p}" for p in t.get("restored", [])]
    if t.get("undo_left"):
        out.append("undo left " + ", ".join(t["undo_left"]))
    if t.get("compacted"):
        out.append("compacted")
    if t.get("error"):
        out.append(t["error"])
    return ", ".join(out).replace("|", "\\|").replace("\n", " ")


def turn_result(t):
    if t.get("skipped"):
        return "skipped: " + t["skipped"]
    res = f"{t['check'][0]}/{t['check'][1]}" if t.get("check") else "done"
    return "timed out" + (f" ({res})" if t.get("check") else "") if t.get("timeout") else res


def markdown_chat(results, summary, scenarios):
    lines = [bench.markdown(results, summary, scenarios, title="purr chat bench", note=CHAT_NOTE)]
    lost = [r for r in results if r.get("not_counted")]
    if lost:
        lines += ["Not counted: " + ", ".join(f"{r['model']} · {r['harness']} · {r['task']} ({r['error']})"
                                              for r in lost), ""]
    lines += ["## chat", "", "turn checks: hidden tests passed after the turns that have them (a skipped turn's "
              "count as failed) · user edits lost: hand edits the model wrote over · follow-ups needed: \"it still "
              "fails\" turns that had to be sent · steers late: sent as the next message, the turn having ended "
              "first · steers lost: no time left to send them · Decided lines: turns with one / turns whose final "
              "check saw green tests · not counted: a hand edit couldn't apply (the model had changed its text)", "",
              "| " + " | ".join(CHAT_COLUMNS) + " |", "|" + "---|" * len(CHAT_COLUMNS)]
    lines += ["| " + " | ".join(row) + " |" for row in chat_rows(results)]
    lines += ["", "## turn by turn", "", "| model | harness | scenario | turn | result | time | calls | events |",
              "|---|---|---|---|---|---|---|---|"]
    for r in results:
        for t in r.get("turns", []):
            lines.append(f"| {r['model']} | {r['harness']} | {r['task']} | {t['turn']} | {turn_result(t)} | "
                         f"{t.get('seconds', 0):.0f}s | {t.get('calls', 0)} | {events_of(t)} |")
    return "\n".join(lines) + "\n"


def line(r):
    """One finished scenario, for the progress list."""
    if r.get("not_counted"):
        return f"{ui.ROSE}⚠ {r['error']}: not counted{ui.RESET}"
    turns = r.get("turns", [])
    ran = sum(1 for t in turns if "seconds" in t)
    return bench.line(r) + f"  {ui.DIM}{ran}/{len(turns)} turns sent{ui.RESET}"


def main(args, config):
    try:
        scenarios = load_scenarios(set(args.tasks.split(",")) if args.tasks else None, args.level)
    except ValueError as e:
        ui.say(ui.ROSE, f"  {e}")
        return 1
    wanted = args.harness.split(",")
    harnesses = [h for h in wanted if h in HARNESSES]
    if len(harnesses) < len(wanted):
        ui.say(ui.DIM, f"  not in the chat bench yet: {', '.join(h for h in wanted if h not in HARNESSES)}")
    models = args.models.split(",") if args.models else bench.pick_models(config)
    unknown = [m for m in models if m not in config["models"]]
    if unknown or not models or not scenarios or not harnesses:
        ui.say(ui.ROSE, f"  nothing to run{': no model called ' + ', '.join(unknown) if unknown else ''}")
        return 1
    out_dir = bench.new_out_dir("_chat")
    total = len(models) * len(scenarios) * len(harnesses) * args.runs
    ui.say(ui.DIM, f"\n  {len(models)} model(s) × {len(scenarios)} chats × {'+'.join(harnesses)}"
                   f"{f' × {args.runs} runs' if args.runs > 1 else ''} = {total} runs, at most "
                   f"{args.timeout // 60} min each ({args.turn_timeout // 60} a turn)   → {out_dir}\n")
    try:
        run_all_chat(config, models, scenarios, harnesses, args.runs, args.turn_timeout, args.timeout, out_dir,
                     bench.printer(args.watch, line=line, working="chatting…", width=18))
    except KeyboardInterrupt:
        bench.STOP.set()
        ui.say(ui.ROSE, f"\n  stopped. finished runs are in {out_dir}")
    return 0
