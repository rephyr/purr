"""purr bench --real in a full-screen window: Terminal-Bench or DeepSWE, the way other harnesses
are scored, with everything that's happening on screen and Mochi's verdict at the end.

The run is tbench/fair.sh, or tbench/local.sh for a model on this machine (harness/realbench.py
starts it and reads Harbor's job folder); this only shows it. Several picks can wait in a queue and
run one after another (overnight). q stops (Harbor cancels the trials and cleans up), q again quits.
"""

import subprocess
import threading
import time
from pathlib import Path

from rich import box
from rich.align import Align
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.widgets import (Button, DataTable, Input, ProgressBar, RadioButton, RadioSet, RichLog, Static,
                             Switch)

from harness import realbench, ui
from tui import cat, themes
from tui.window import BenchWindow

from tui.themes import DIM, FAINT, LILAC, MINT, PEACH, PINK, ROSE, TEXT  # noqa: E402

CSS = """
#start { min-width: 22; }
#choices { height: auto; }
#picks, #picks2 { width: 44; height: auto; margin-right: 2; }
#picks > Static, #picks2 > Static { color: $lilac; text-style: bold; margin: 1 0 0 1; }
#buttons { height: auto; width: auto; }
#queue { margin-top: 1; margin-right: 2; background: $button; color: $fg; border: none; min-width: 16; }
#queue:focus { background: $lilac; color: $ink; text-style: bold; }
RadioSet { width: 100%; border: round $line; background: $panel; padding: 0 1; }
RadioSet:focus { border: round $pink; }
RadioSet > RadioButton.-selected { background: transparent; }
RadioSet RadioButton.-on > .toggle--label { color: $pink; text-style: bold; background: transparent; }
RadioSet RadioButton > .toggle--label { background: transparent; }
RadioSet:focus > RadioButton.-selected > .toggle--label { background: $hover; }
#knobs, #knobs2 { height: auto; margin-top: 1; }
#knobs Static, #knobs2 Static { width: auto; color: $dim; padding: 1 1 0 1; }
#knobs2 { margin-top: 0; }
#jobs { width: 7; border: round $line; background: $panel; }
#timemult { width: 7; border: round $line; background: $panel; }
#edge { border: round $line; background: $panel; width: 10; }
#edge:focus { border: round $pink; }
#edge > .switch--slider { color: $pink; background: $line; }
#preview { width: 1fr; height: auto; min-height: 20; border: round $line-hi; background: $panel;
           padding: 1 2; }
#setupcat { height: 3; margin-top: 2; padding-left: 2; }

#runhead { height: 1; margin-top: 1; }
#runtitle { width: auto; margin-right: 2; }
#tally { height: 1; margin: 1 0; }
#leftside { width: 66; height: 1fr; }
#bigscore { height: 9; border: round $line-hi; background: $panel; content-align: center middle; }
#trials { width: 66; height: 1fr; border: round $line; background: $panel; scrollbar-size-horizontal: 0; }
#trials:focus { border: round $pink; }
#liveside { width: 1fr; height: 1fr; margin-left: 1; }
#livehead { height: 1; padding: 0 1; }
#live { height: 1fr; border: round $line; background: $bg; padding: 0 1; scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0; }
#runner { height: 1; color: $hint; margin-top: 1; }
#run.results #tally { display: none; }
#run.results #runner { display: none; }
"""

ICON = {"setup": ("◌", DIM), "grading": ("⚖", LILAC), "passed": ("✓", MINT), "failed": ("✗", ROSE),
        "error": ("⚠", PEACH), "timeout": ("⏱", ROSE), "cancelled": ("⊘", FAINT)}
SPIN = "◐◓◑◒"
STATE_COLOUR = {"setup": DIM, "working": PINK, "grading": LILAC, "passed": MINT, "failed": ROSE,
                "error": PEACH, "timeout": ROSE, "cancelled": FAINT}
FINISHED = realbench.FINISHED

# a 5-line font for the score, 3 columns a glyph (. is 1): plain blocks read best in every terminal
BIG = {"0": ["███", "█ █", "█ █", "█ █", "███"], "1": [" █ ", "██ ", " █ ", " █ ", "███"],
       "2": ["███", "  █", "███", "█  ", "███"], "3": ["███", "  █", "███", "  █", "███"],
       "4": ["█ █", "█ █", "███", "  █", "  █"], "5": ["███", "█  ", "███", "  █", "███"],
       "6": ["███", "█  ", "███", "█ █", "███"], "7": ["███", "  █", "  █", "  █", "  █"],
       "8": ["███", "█ █", "███", "█ █", "███"], "9": ["███", "█ █", "███", "  █", "███"],
       ".": [" ", " ", " ", " ", "█"], "-": ["   ", "   ", "███", "   ", "   "],
       "%": ["██  █", "██ █ ", "  █  ", " █ ██", "█  ██"]}


def big(text, phase=0.0):
    """The score in big letters, pink fading to lilac from top to bottom."""
    shades = [PINK, "#e9a6dc", "#dfa4e4", "#d3a3ea", LILAC]
    out = Text(no_wrap=True, overflow="crop")
    for r in range(5):
        # every row the same width, trailing spaces kept: rows centred one by one would shift apart
        out.append("  ".join(BIG.get(ch, ["   "] * 5)[r] for ch in text), style=f"bold {shades[r]}")
        if r < 4:
            out.append("\n")
    return Align.center(out)


def _mtime(path):
    """A log's last change, or 0 if Harbor just removed it (stat() would stop the update timer)."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def landmarks(entries):
    """A few leaderboard rows to place purr between: the top, the well-known tools, cheap models."""
    picks = {1, 4, 32, 63, 86, 116}
    rows = [e for e in entries if e["rank"] in picks]
    return rows + entries[-1:] if entries else rows


def bar(pct, width=34, colour=LILAC, lo=0.0, empty="#3d3349"):
    """A bar from lo% to 100%: scores bunched together (65-74%) still look different."""
    filled = max(1, round((pct - lo) / (100 - lo) * width)) if pct > lo else 0
    t = Text("━" * filled, style=f"bold {colour}")
    t.append("╌" * (width - filled), style=empty)
    return t


def floor_of(scores):
    """Where the bars start: 10 points under the lowest, in tens (0 when they're spread out)."""
    return max(0, (int(min(scores)) - 10) // 10 * 10) if scores else 0


QUEUE_PAUSE = 15  # seconds between queued runs: to read the result, or q to stop the rest


def short_pick(pick):
    """'DeepSeek Flash · minimal'."""
    variant = "" if pick["variant"] == "purr" else f" · {pick['variant']}"
    model = "DeepSeek Flash" if pick["model"] == realbench.DEEPSEEK else pick["model"]
    more = f" · ×{pick['time_mult']:g} time" if pick.get("time_mult", 1.0) != 1.0 else ""
    return f"{model}{variant}{more}"


def describe(pick):
    """'Terminal-Bench 2.1 one · DeepSeek Flash · minimal'."""
    return f"{realbench.SUITES[pick['suite']]['bench']} {pick['size']} · {short_pick(pick)}"


class RealBenchApp(BenchWindow):
    TITLE = "purr bench · real"
    CSS_PATH = ["purr.tcss", "bench.tcss"]
    CSS = CSS
    BINDINGS = [
        Binding("q", "stop_or_quit", "stop / quit", priority=True),
        Binding("ctrl+q", "quit_now", "quit", priority=True),
        Binding("f", "follow", "follow the newest"),
        Binding("r", "toggle_results", "results / live"),
        Binding("p", "publish", "publish"),
        Binding("o", "open_job", "open the job folder"),
    ]

    def __init__(self, config, suite=None, size=None, jobs=6, edge_cases=False):
        super().__init__(config)
        self.choice = {"suite": suite or "terminal-bench", "size": size or "quick",
                       "model": realbench.DEEPSEEK, "variant": "purr"}
        self.locals = realbench.local_models()  # Ollama models with room for a benchmark
        self.queue = []      # picks waiting their turn (dicts like self.choice)
        self.ran = []        # finished runs: what, score, job folder, published
        self.next_at = None  # when the next queued run starts (a pause to read the result, or stop)
        self.go_now = bool(suite and size)
        self.jobs, self.edge_cases = jobs, edge_cases
        self.job_run = None
        self.trials, self.rows = [], {}
        self.selected, self.follow = None, True
        self.live_pos, self.live_buf, self.live_lines = 0, "", []
        self.finished_names = set()
        self.final = None  # (mood, headline, detail) once it's over

    # ---- layout ----

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(cat.shimmer("₊˚✧ real benchmarks ✧˚₊", 0.1), id="title")
            yield Static("the tasks and settings other harnesses are scored on · hidden tests decide",
                         id="subtitle")
            with Horizontal(id="choices"):
                with Vertical(id="picks"):
                    yield Static("benchmark")
                    with RadioSet(id="suite"):
                        for key, s in realbench.SUITES.items():
                            yield RadioButton(s["bench"], value=key == self.choice["suite"], name=key)
                    yield Static("how much")
                    with RadioSet(id="size"):
                        for key, s in realbench.SIZES.items():
                            yield RadioButton(f"{key:<6} {s['what']}", value=key == self.choice["size"], name=key)
                    with Horizontal(id="knobs"):
                        yield Static("at once")
                        yield Input(str(self.jobs), type="integer", id="jobs")
                        yield Static("edge cases")
                        yield Switch(self.edge_cases, id="edge")
                    with Horizontal(id="knobs2"):
                        yield Static("time × (local)")
                        yield Input("1", type="number", id="timemult")
                with Vertical(id="picks2"):
                    yield Static("model")
                    with RadioSet(id="model"):
                        yield RadioButton("DeepSeek V4.1 Flash · OpenRouter", value=True, name=realbench.DEEPSEEK)
                        for name in self.locals:
                            yield RadioButton(f"{name} · your GPU", name=name)
                    yield Static("harness")
                    with RadioSet(id="variant"):
                        for key, v in realbench.VARIANTS.items():
                            yield RadioButton(v["what"], value=key == "purr", name=key)
                yield Static("", id="preview")
            with Center(), Horizontal(id="buttons"):
                yield Button("＋ queue", id="queue")
                yield Button("start ✧", id="start")
            yield Static("↑↓ picks · tab moves · ＋ queue adds the pick, start runs the queue (or just the pick) · "
                         "q quits", id="setuphint")
            yield Static("", id="setupcat")
        with Vertical(id="run"):
            with Horizontal(id="runhead"):
                yield Static("", id="runtitle")
                yield ProgressBar(total=1, show_eta=False, show_percentage=False, id="bar")
                yield Static("", id="count")
            yield Static("", id="tally")
            with Horizontal(id="middle"):
                with Vertical(id="leftside"):
                    yield Static("", id="bigscore")
                    yield DataTable(id="trials", cursor_type="row", zebra_stripes=False)
                with Vertical(id="liveside"):
                    yield Static("", id="livehead")
                    yield RichLog(id="live", wrap=True, markup=False, highlight=False, max_lines=4000, min_width=20)
            with VerticalScroll(id="results"):
                yield Static("", id="results_body")
            yield Static("", id="runner")
            yield Static("", id="benchcat")
            yield Static("", id="runhint")

    def on_mount(self):
        table = self.query_one("#trials", DataTable)
        self.cols = table.add_columns("", "task", "doing", "time", "cost")
        self.draw_preview()
        self.set_interval(0.12, self.tick)
        self.set_interval(1.0, self.poll)
        if self.go_now:
            self.call_after_refresh(self.start)
        else:
            self.query_one("#suite", RadioSet).focus()

    # ---- the setup screen ----

    def on_radio_set_changed(self, event):
        self.choice[event.radio_set.id] = event.pressed.name
        self.draw_preview()

    def draw_preview(self):
        suite, size, model = self.choice["suite"], self.choice["size"], self.choice["model"]
        s = realbench.SUITES[suite]
        tasks, trials = realbench.tasks_in(suite, size), realbench.trials_in(suite, size)
        when, cost = realbench.estimate(suite, size, model)
        where = "DeepSeek's own host" if model == realbench.DEEPSEEK else "Ollama, your GPU"
        t = Text()
        t.append_text(cat.shimmer(s["bench"], 0.15))
        t.append(f"\n{s['what']}\n\n", style=DIM)
        for label, value in (("tasks", f"{tasks} × {realbench.SIZES[size]['attempts']} = {trials} tries"),
                             ("time", when), ("cost", cost), ("model", f"{realbench.model_name(model)}, {where}"),
                             ("harness", realbench.VARIANTS[self.choice["variant"]]["what"])):
            t.append(f"  {label:<9}", style=LILAC)
            t.append(f"{value}\n", style=TEXT)
        why = realbench.problem(suite, size, model)
        if why:
            t.append(f"\n  ✗ {why}\n", style=f"bold {ROSE}")
        if model != realbench.DEEPSEEK:
            t.append("  time × gives each task more time (a GPU writes slower)\n", style=DIM)
        if self.queue:
            t.append(f"\nqueued, one after another ({len(self.queue)})\n", style=f"bold {PINK}")
            for n, q in enumerate(self.queue, 1):
                t.append(f"  {n}. {describe(q)}\n", style=TEXT)
            t.append("  start runs these (＋ adds the pick above)\n", style=DIM)
        t.append("\n")
        if size == "quick":
            before = realbench.previous(suite, size, model)
            t.append("earlier quick runs\n", style=f"bold {PINK}")
            if not before:
                t.append("  none yet: this one sets the baseline ♡\n", style=DIM)
            lo = floor_of([p for _, _, p in before])
            for version, date, pct in before[-6:]:
                t.append(f"  {version[:20]:<21}", style=TEXT)
                t.append_text(bar(pct, 26, PINK, lo, empty=themes.colour(self, "line")))
                t.append(f" {pct:.1f}%\n", style=PINK)
            t.append("\nquick runs compare purr versions; the other harnesses only have whole-set scores\n",
                     style=DIM)
        elif suite == "terminal-bench-2":
            entries, as_of = realbench.leaderboard()
            t.append("on the leaderboard ", style=f"bold {PINK}")
            t.append(f"{len(entries)} entries as of {as_of} · bars start at 0%\n", style=DIM)
            for e in landmarks(entries):
                t.append(f"  #{e['rank']:<4}", style=DIM)
                t.append(f"{(e['agent'] + ' · ' + e['model'])[:30]:<31}", style=TEXT)
                t.append_text(bar(e["score"], 20, LILAC, empty=themes.colour(self, "line")))
                t.append(f" {e['score']:.1f}%\n", style=LILAC)
            if size == "submit":
                t.append("\n5 tries a task and purr's web tool off, as the leaderboard asks; then\n", style=DIM)
                t.append("tbench/submit.py <job> checks every rule and readies the entry", style=DIM)
        else:
            refs = realbench.references(suite, model)
            lo = floor_of([p for _, p in refs])
            t.append("to beat ", style=f"bold {PINK}")
            t.append(f"same model, published · bars start at {lo}%\n", style=DIM)
            for harness, pct in refs:
                t.append(f"  {harness[:28]:<29}", style=TEXT)
                t.append_text(bar(pct, 26, LILAC, lo, empty=themes.colour(self, "line")))
                t.append(f" {pct:.1f}%\n", style=LILAC)
        self.query_one("#preview", Static).update(t)

    def on_button_pressed(self, event):
        if event.button.id == "start":
            self.start()
        elif event.button.id == "queue":
            self.add_to_queue()

    def pick(self):
        """The current pick with the knobs, or None (and a note) when it can't run."""
        why = realbench.problem(self.choice["suite"], self.choice["size"], self.choice["model"])
        if why:
            self.notify(f"{why} ♡", severity="warning")
            return None
        try:
            jobs = max(1, min(16, int(self.query_one("#jobs", Input).value or 6)))
        except ValueError:
            jobs = 6
        try:  # more time per task: local models only (a GPU writes slower than a hosted model)
            time_mult = max(1.0, min(5.0, float(self.query_one("#timemult", Input).value or 1)))
        except ValueError:
            time_mult = 1.0
        if self.choice["model"] == realbench.DEEPSEEK:
            time_mult = 1.0
        return {**self.choice, "jobs": jobs, "edge_cases": self.query_one("#edge", Switch).value,
                "time_mult": time_mult}

    def add_to_queue(self):
        pick = self.pick()
        if pick:
            self.queue.append(pick)
            self.notify(f"queued: {describe(pick)} ♡", timeout=3)
            self.draw_preview()

    def start(self):
        if not self.queue:
            pick = self.pick()
            if not pick:
                return
            self.queue.append(pick)
        self.query_one("#setup").add_class("hide")
        self.query_one("#run").add_class("show")
        self.next_run()

    def next_run(self):
        """The first queued pick: a clean run view, and its script started."""
        pick = self.queue.pop(0)
        suite, size = pick["suite"], pick["size"]
        self.trials, self.rows, self.finished_names = [], {}, set()
        self.selected, self.follow, self.final = None, True, None
        self.live_pos, self.live_buf, self.live_lines = 0, "", []
        self.query_one("#trials", DataTable).clear()
        self.query_one("#live", RichLog).clear()
        self.query_one("#run").remove_class("results")
        self.jobs, self.edge_cases = pick["jobs"], pick["edge_cases"]
        self.total = realbench.trials_in(suite, size)
        self.job_run = realbench.Run(suite, size, self.jobs, self.edge_cases, pick["model"], pick["variant"],
                                     pick.get("time_mult", 1.0))
        self.job_run.pick = pick
        try:
            self.job_run.start()
        except OSError as e:
            self.notify(f"couldn't start {Path(self.job_run.cmd[0]).name}: {e}", severity="error")
            return
        threading.Thread(target=self.job_run.read_output, daemon=True).start()
        bench = realbench.SUITES[suite]["bench"]
        left = f"  ·  {len(self.queue)} more queued" if self.queue else ""
        self.query_one("#runtitle", Static).update(Text.assemble(
            (f"{bench} ", f"bold {PINK}"), (size, LILAC), (f"  ·  {short_pick(pick)}{left}", DIM)))
        self.query_one("#bar", ProgressBar).update(total=self.total, progress=0)
        self.query_one("#runhint", Static).update(Text(
            "↑↓ picks a task to watch · f follows the newest · q stops (Harbor cleans up) · ctrl+q quits", style=DIM))
        self.mood("waking", "checking the model's host, then Harbor builds the first containers")
        self.query_one("#trials", DataTable).focus()

    # ---- while it runs ----

    def poll(self):
        if not self.job_run or not self.screen.query("#trials"):  # closing: the widgets are gone
            return
        if self.next_at:
            if time.monotonic() >= self.next_at:
                self.next_at = None
                self.next_run()
            else:
                self.hint()
                return
        self.job_run.find_job()
        self.trials = realbench.scan(self.job_run.job)
        self.draw_trials()
        self.draw_live()
        if self.job_run.lines:
            self.query_one("#runner", Static).update(Text("harbor ▸ " + self.job_run.lines[-1][-160:], style="#6f6580"))
        if self.job_run.done and self.final is None:
            self.finish()

    def draw_trials(self):
        table = self.query_one("#trials", DataTable)
        done = 0
        counts = {k: 0 for k in ("passed", "failed", "error", "timeout", "working", "setup", "grading")}
        for t in self.trials:
            counts[t["state"]] = counts.get(t["state"], 0) + 1
            done += t["state"] in FINISHED
            cells = self.cells(t)
            if t["name"] not in self.rows:
                self.rows[t["name"]] = table.add_row(*cells, key=t["name"])
            else:
                for col, value in zip(self.cols, cells):
                    table.update_cell(t["name"], col, value)
            if t["state"] in FINISHED and t["name"] not in self.finished_names:
                self.finished_names.add(t["name"])
                passed = t["state"] == "passed"
                self.mood("proud" if passed else "sad", f"{t['task']} {'passed ♡' if passed else STATES_SAY[t['state']]}",
                          hold=2.6)
        self.query_one("#bar", ProgressBar).update(progress=done)
        pct, se = realbench.score(self.trials)
        spent = sum(t["cost"] or 0 for t in self.trials)
        elapsed = ui.duration(time.time() - self.job_run.started)
        self.query_one("#count", Static).update(Text(f"{done}/{self.total}  ·  {elapsed}", style=PINK))
        tally = Text()
        for key, label in (("passed", "passed"), ("failed", "failed"), ("timeout", "out of time"), ("error", "broke")):
            icon, colour = ICON[key]
            tally.append(f"{icon} {counts[key]} {label}", style=colour if counts[key] else FAINT)
            tally.append("   ")
        tally.append("│  ", style=FAINT)
        for icon, n, label in (("◐", counts["working"], "working"), ("⚖", counts["grading"], "grading"),
                               ("◌", counts["setup"], "setting up")):
            tally.append(f"{icon} {n} {label}   ", style=DIM if n else FAINT)
        tally.append("│  ", style=FAINT)
        tally.append(f"${spent:.2f} so far", style=PEACH)
        self.query_one("#tally", Static).update(tally)
        self.draw_bigscore(pct, se, done)

    def draw_bigscore(self, pct, se, done):
        """The score so far, big, above the tasks."""
        if pct is None:
            body = Group(big("--"), Align.center(Text("waiting for the first task to be graded ♡", style=DIM)))
        else:
            body = Group(big(f"{pct:.1f}%"), Align.center(Text(f"so far  ·  ± {se}  ·  {done} of {self.total} graded",
                                                               style=DIM)))
        self.query_one("#bigscore", Static).update(body)

    def cells(self, t):
        state = t["state"]
        if state == "working":
            icon = Text(SPIN[(self.cat_tick // 3) % 4], style=f"bold {PINK}")
        else:
            glyph, colour = ICON[state]
            icon = Text(glyph, style=f"bold {colour}")
        doing = realbench.STATES[state] if state != "error" else ERRORS.get(t["error"], "broke")
        secs = t["seconds"]
        return (icon, Text(t["task"][:28], style=DIM if state == "setup" else TEXT),
                Text(doing, style=STATE_COLOUR[state]), Text(ui.duration(secs) if secs else "", style=DIM),
                Text(f"${t['cost']:.3f}" if t["cost"] else "", style=DIM))

    def on_data_table_row_highlighted(self, event):
        name = event.row_key.value if event.row_key else None
        if name and name != self.selected and self.job_run:  # following moves the cursor after watch(): same name
            if self.selected is not None:
                self.follow = False  # you picked one: stay on it (f follows again)
            self.show_trial(name)

    def action_follow(self):
        self.follow = True
        self.notify("following the newest task ♡", timeout=2)

    def show_trial(self, name):  # not "watch": that's Textual's own method on every widget
        self.selected = name
        self.live_pos, self.live_buf = 0, ""
        log = self.query_one("#live", RichLog)
        log.clear()
        t = next((t for t in self.trials if t["name"] == name), None)
        if t:
            lines, self.live_pos = realbench.tail(t["log"])
            self.live_lines = lines[-400:]
            for line in self.live_lines:
                log.write(Text.from_ansi(line))
            self.mirror(t, self.live_lines)

    def draw_live(self):
        working = [t for t in self.trials if t["state"] == "working" and t["log"].exists()]
        if self.follow and working:
            newest = max(working, key=lambda t: _mtime(t["log"]))
            if newest["name"] != self.selected:
                self.show_trial(newest["name"])
                table = self.query_one("#trials", DataTable)
                table.move_cursor(row=table.get_row_index(newest["name"]))
        t = next((t for t in self.trials if t["name"] == self.selected), None)
        head = Text()
        if not t:
            head.append("purr's own log shows up here once a task gets going ♡", style=DIM)
            self.query_one("#livehead", Static).update(head)
            return
        head.append("watching ", style=DIM)
        head.append(t["task"], style=f"bold {PINK}")
        head.append(f"  ·  {realbench.STATES[t['state']]}", style=STATE_COLOUR[t["state"]])
        head.append("  ·  following the newest" if self.follow else "  ·  f follows the newest", style=DIM)
        self.query_one("#livehead", Static).update(head)
        try:
            with open(t["log"], "rb") as f:
                f.seek(self.live_pos)
                data = f.read()
                self.live_pos = f.tell()
        except OSError:
            return
        if not data:
            return
        self.live_buf += data.decode("utf-8", "replace")
        *lines, self.live_buf = self.live_buf.split("\n")
        log = self.query_one("#live", RichLog)
        for line in lines:
            log.write(Text.from_ansi(line))
        self.live_lines = (self.live_lines + lines)[-400:]
        self.mirror(t, lines)

    def mirror(self, t, lines):
        """Mochi does what the purr she's watching does (reads, edits, runs), unless she's still
        reacting to a task that just finished."""
        tool = realbench.last_tool(lines)
        if tool and time.monotonic() > self.cat_until:
            self.mood(cat.ACTIVITY.get(tool) or "running", f"{t['task']} · {tool}", label=f"{self.name_} is watching")

    # ---- the end ----

    def finish(self):
        self.trials = realbench.scan(self.job_run.job)
        self.draw_trials()
        done = sum(t["state"] in FINISHED for t in self.trials)
        pct, se = realbench.score(self.trials)
        self.final = realbench.verdict(self.job_run.suite, self.job_run.size, pct, done, self.total,
                                       stopped=self.job_run.stopping, name=self.name_, model=self.job_run.model)
        mood, headline, detail = self.final
        self.mood(mood, detail, hold=10**9, label=headline)
        ok = self.job_run.proc.returncode == 0 and done == self.total and not self.job_run.stopping
        self.ran.append({"pick": getattr(self.job_run, "pick", None) or {
            "suite": self.job_run.suite, "size": self.job_run.size, "model": self.job_run.model,
            "variant": self.job_run.variant}, "pct": pct, "done": done, "total": self.total, "ok": ok,
            "stopped": self.job_run.stopping, "job": self.job_run.job, "published": False})
        if self.job_run.stopping:
            self.queue.clear()  # q stops the whole queue, not just this run
        self.query_one("#results_body", Static).update(self.results_page(pct, se, done))
        self.query_one("#run").add_class("results")
        if self.queue:
            self.next_at = time.monotonic() + QUEUE_PAUSE
        self.hint()

    def hint(self):
        publishable = any(r["ok"] and not r["published"] for r in self.ran)
        hint = ("p publishes " + ("them" if sum(r["ok"] for r in self.ran) > 1 else "it") + " and opens a pull request · "
                if publishable else "")
        if self.next_at:
            wait = max(0, round(self.next_at - time.monotonic()))
            text = f"next: {describe(self.queue[0])} in {wait}s · q cancels the rest of the queue"
        else:
            text = f"{hint}r live view / results · o opens the job folder · q quits"
        self.query_one("#runhint", Static).update(Text(text, style=PINK if self.next_at else DIM))

    def results_page(self, pct, se, done):
        suite, size = self.job_run.suite, self.job_run.size
        bench = realbench.SUITES[suite]["bench"]
        parts = [Text(justify="center")]
        parts[0].append_text(cat.shimmer(f"₊˚✧ {bench} · {size} ✧˚₊", 0.2))
        if pct is None:
            why = Text("\nnothing got graded. the last lines from fair.sh / Harbor:\n\n", style=DIM)
            for line in self.job_run.lines[-14:]:
                why.append(f"  {line}\n", style=ROSE if "error" in line.lower() or "refus" in line.lower() else TEXT)
            return Group(parts[0], why)
        spent = sum(t["cost"] or 0 for t in self.trials)
        took = ui.duration(time.time() - self.job_run.started)
        counts = Text()
        for key, label in (("passed", "passed"), ("failed", "failed"), ("timeout", "out of time"), ("error", "broke")):
            n = sum(t["state"] == key for t in self.trials)
            icon, colour = ICON[key]
            counts.append(f"{icon} {n} {label}", style=colour if n else FAINT)
            counts.append("    ")
        mood, headline, detail = self.final
        parts += [Text(""), big(f"{pct:.1f}%"), Text(""),
                  Align.center(Text(f"± {se}  ·  {done} of {self.total} graded  ·  {took}  ·  ${spent:.2f}", style=DIM)),
                  Align.center(counts), Text(""),
                  Align.center(Text.assemble(("♡ ", PINK), (headline, f"bold {TEXT}"))),
                  Align.center(Text(detail, style=DIM)), Text(""),
                  Align.center(self.standings(pct)), Text(""), self.task_grid()]
        misses = self.misses()
        if misses:
            parts += [Text(""), misses]
        if len(self.ran) > 1 or self.queue:
            parts += [Text(""), self.queue_table()]
        return Group(*parts)

    def queue_table(self):
        """Every run this window did, and what's still queued."""
        t = Table(box=box.SIMPLE_HEAD, border_style=themes.colour(self, "line-hi"), header_style=f"bold {LILAC}",
                  expand=True, padding=(0, 1), title=Text("♡ the queue", style=f"bold {PINK}"), title_justify="left")
        for col, just in (("run", "left"), ("score", "right"), ("", "left")):
            t.add_column(col, justify=just)
        for r in self.ran:
            score = "--" if r["pct"] is None else f"{r['pct']:.1f}%"
            how = ("published ♡" if r["published"] else "whole run" if r["ok"]
                   else "stopped" if r["stopped"] else f"{r['done']} of {r['total']} graded")
            t.add_row(Text(describe(r["pick"]), style=TEXT), Text(score, style=f"bold {PINK}"),
                      Text(how, style=MINT if r["ok"] else DIM))
        for q in self.queue:
            t.add_row(Text(describe(q), style=DIM), Text("queued", style=DIM), Text(""))
        return t

    def misses(self):
        """What didn't pass, and how: for deciding what to fix (or which task was at fault)."""
        bad = sorted((t for t in self.trials if t["state"] in ("failed", "timeout", "error")),
                     key=lambda t: (t["state"], t["task"]))
        if not bad:
            return None
        t = Table(box=box.SIMPLE_HEAD, border_style=themes.colour(self, "line-hi"), header_style=f"bold {LILAC}", expand=True,
                  padding=(0, 1), title=Text("✗ didn't pass", style=f"bold {PINK}"), title_justify="left")
        for col, just in (("task", "left"), ("how", "left"), ("time", "right"), ("cost", "right")):
            t.add_column(col, justify=just)
        for tr in bad:
            how = {"failed": "the hidden tests failed", "timeout": "ran out of time"}.get(
                tr["state"], ERRORS.get(tr["error"], tr["error"] or "broke") + " (not purr's answer)")
            t.add_row(Text(tr["task"], style=TEXT), Text(how, style=STATE_COLOUR[tr["state"]]),
                      Text(ui.duration(tr["seconds"]) if tr["seconds"] else "", style=DIM),
                      Text(f"${tr['cost']:.3f}" if tr["cost"] else "", style=DIM))
        return t

    def standings(self, pct):
        suite, size, model = self.job_run.suite, self.job_run.size, self.job_run.model
        t = Table(box=box.ROUNDED, border_style=themes.colour(self, "line-hi"), show_header=False, padding=(0, 1),
                  title=Text("♛ where purr lands" if size != "quick" else "♛ quick runs so far", style=f"bold {PINK}"),
                  title_justify="left")
        t.add_column("who", no_wrap=True, min_width=30)
        t.add_column("bar", no_wrap=True)
        t.add_column("score", justify="right", min_width=7)
        if size == "quick":
            rows = [(f"purr {v[:28]}", s, False) for v, _, s in realbench.previous(suite, size, model)[-7:]]
        elif suite == "terminal-bench-2" and model == realbench.DEEPSEEK:
            rank, _ = realbench.place(pct)
            rows = [(f"#{e['rank']} {e['agent']} · {e['model']}"[:38], e["score"], False)
                    for e in landmarks(realbench.leaderboard()[0])]
            rows.append((f"#{rank} purr · DeepSeek V4.1 Flash ♡", pct, True))
        else:
            rows = [(h, s, False) for h, s in realbench.references(suite, model)]
        if not (size != "quick" and suite == "terminal-bench-2" and model == realbench.DEEPSEEK):
            rows.append(("purr (this run) ♡", pct, True))
        lo = (0 if suite == "terminal-bench-2" and size != "quick" and model == realbench.DEEPSEEK
              else floor_of([r[1] for r in rows]))
        for who, score, mine in sorted(rows, key=lambda r: -r[1]):
            t.add_row(Text(who, style=f"bold {PINK}" if mine else TEXT), bar(score, 44, PINK if mine else LILAC, lo, empty=themes.colour(self, "line")),
                      Text(f"{score:.1f}%", style=f"bold {PINK}" if mine else LILAC))
        note = f"bars start at {lo}%"
        if size != "quick" and suite == "terminal-bench-2" and model == realbench.DEEPSEEK:
            note += " · the leaderboard's entries ran 5 tries a task (a submit run does too)"
        elif size != "quick" and model == realbench.DEEPSEEK:
            note += " · theirs: DeepSeek's model card (3 or 8 tries a task); one try has a wider margin"
        elif size != "quick":
            note += " · theirs: the model makers' own run, full weights (yours runs in Ollama)"
        t.caption = Text(note, style=DIM)
        return t

    def task_grid(self):
        t = Table(box=box.ROUNDED, border_style=themes.colour(self, "line-hi"), show_header=False, expand=True, padding=(0, 1),
                  title=Text("♡ task by task", style=f"bold {PINK}"), title_justify="left")
        cols = 3
        for _ in range(cols):
            t.add_column(ratio=1, no_wrap=True)
        by_task = {}
        for tr in self.trials:
            by_task.setdefault(tr["task"], []).append(tr)
        cells = []
        for task in sorted(by_task, key=lambda k: (not all(x["state"] == "passed" for x in by_task[k]), k)):
            marks = Text()
            for tr in by_task[task]:
                glyph, colour = ICON.get(tr["state"], ("◐", PINK))
                marks.append(glyph, style=f"bold {colour}")
            marks.append("  " + task[:34], style=TEXT if all(x["state"] == "passed" for x in by_task[task]) else DIM)
            cells.append(marks)
        for i in range(0, len(cells), cols):
            row = cells[i:i + cols]
            t.add_row(*row, *[""] * (cols - len(row)))
        return t

    def action_toggle_results(self):
        if self.final is not None:
            self.query_one("#run").toggle_class("results")

    def action_publish(self):
        if self.final is None or self.next_at:
            return
        todo = [r for r in self.ran if r["ok"] and not r["published"]]
        if not todo:
            if not any(r["ok"] for r in self.ran):
                self.notify("only a whole run can be published ♡", severity="warning")
            return
        for r in todo:
            r["published"] = True  # not twice, while it runs
        self.notify("publishing ♡", timeout=3)
        self.publish_in_background(todo)

    @work(thread=True)
    def publish_in_background(self, runs):
        """publish.py writes every run, then wraps everything not committed into one pull request
        (co-authored as /pr does): seconds of git and gh, not on the window's own thread."""
        res = subprocess.run(["python3", str(realbench.ROOT / "tbench" / "publish.py"), "--pr",
                              *[str(r["job"]) for r in runs]], capture_output=True, text=True, cwd=realbench.ROOT)
        self.call_from_thread(self.published_it, runs, res.returncode, (res.stdout + res.stderr).strip().splitlines())

    def published_it(self, runs, code, out):
        url = next((line.split(": ", 1)[1] for line in out if line.startswith("pull request: ")), None)
        if code == 0:
            self.mood("celebrating", "published: the pull request is up ♡", hold=6)
            self.notify(f"pull request: {url}" if url else (out[-1] if out else "published ♡"), timeout=12)
        else:
            if not any(line.startswith("published, but") for line in out):
                for r in runs:
                    r["published"] = False  # it can be tried again
            self.notify(out[-1] if out else "publishing failed", severity="error", timeout=12)
        self.hint()

    def action_open_job(self):
        if self.job_run:
            self.open_path(self.job_run.job)

    # ---- Mochi ----

    def tick(self):
        if self.closing():
            return
        self.cat_tick += 1
        if not self.job_run:
            self.query_one("#title", Static).update(cat.shimmer("₊˚✧ real benchmarks ✧˚₊", self.cat_tick * 0.03))
            refs = realbench.references(self.choice["suite"], self.choice["model"])
            best = f"best published: {refs[0][1]:.1f}% ({refs[0][0]})" if refs else ""
            self.query_one("#setupcat", Static).update(cat.render(
                "watching", self.cat_tick, best, (), label=f"{self.name_} wants to see how purr does",
                night=self.night))
            return
        stats = [ui.duration(time.time() - self.job_run.started)]
        done = sum(t["state"] in FINISHED for t in self.trials)
        stats.append(f"{done}/{self.total} graded")
        pct, _ = realbench.score(self.trials)
        if pct is not None:
            stats.append(f"{pct:.1f}%")
        self.query_one("#benchcat", Static).update(cat.render(
            self.cat_mood, self.cat_tick, self.cat_detail, stats, label=self.cat_label, night=self.night))
        # the spinner in the table
        if self.cat_tick % 3 == 0:
            table = self.query_one("#trials", DataTable)
            for t in self.trials:
                if t["state"] == "working":
                    try:
                        table.update_cell(t["name"], self.cols[0],
                                          Text(SPIN[(self.cat_tick // 3) % 4], style=f"bold {PINK}"))
                    except Exception:  # noqa: BLE001 - not drawn yet
                        pass

    # ---- keys ----

    def action_stop_or_quit(self):
        if self.focused and isinstance(self.focused, Input):
            return
        if self.next_at:  # between two queued runs: stop here, keep what's done
            self.next_at = None
            self.notify(f"the rest of the queue is cancelled ({len(self.queue)}) ♡", timeout=4)
            self.queue.clear()
            self.hint()
            self.query_one("#results_body", Static).update(self.results_page(*realbench.score(self.trials),
                                                                                self.ran[-1]["done"]))
            return
        if self.job_run and not self.job_run.done and not self.job_run.stopping:
            self.job_run.stop()
            self.mood("startled", "stopping: Harbor is cancelling the trials and cleaning up", hold=10**9)
            self.notify("stopping ♡ press q again to quit once it's done", timeout=5)
        elif not self.job_run or self.job_run.done:
            self.exit()

    def action_quit_now(self):
        if self.job_run:
            self.job_run.stop()
        self.exit()


# Harbor's errors, the way you'd say them: none of them is purr's answer being wrong
ERRORS = {"NonZeroAgentExitCodeError": "install/exit failed", "AgentSetupTimeoutError": "setup too slow",
          "EnvironmentStartTimeoutError": "container too slow", "RuntimeError": "harness error",
          "VerifierTimeoutError": "grader too slow", "RewardFileNotFoundError": "grader broke"}

STATES_SAY = {"failed": "failed", "error": "broke (an error, not purr's answer)", "timeout": "ran out of time"}

