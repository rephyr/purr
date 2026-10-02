"""purr bench in a full-screen window: pick what to run, then watch it go.

The runs themselves are harness/bench.py's run_all, in a background thread; this only shows
what it reports. q stops (the run going on is cancelled and not counted), and q again quits.
"""

import time
from pathlib import Path

from rich import box
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, ProgressBar, RichLog, SelectionList, Static, Switch
from textual.widgets.selection_list import Selection

from harness import bench, ui
from tui import cat, themes
from tui.window import BenchWindow

from tui.themes import DIM, FAINT, LILAC, MINT, PEACH, PINK, ROSE, TEXT  # noqa: E402
HARNESS_COLOUR = {"purr": PINK, "purr+refine": PEACH, "purr-nocheck": "#e7a6c9", "purr-bare": "#d99fc0",
                  "opencode": LILAC}

CSS = """
#setup { align: center top; padding: 1 2; }
#title { width: 100%; text-align: center; margin-bottom: 0; }
#subtitle { width: 100%; text-align: center; color: #82788f; margin-bottom: 1; }
#lists { height: auto; max-height: 30; }
.column { width: 1fr; height: auto; margin: 0 1; }
.column > Static { color: #c8a2f0; text-style: bold; margin-bottom: 1; }
SelectionList { height: auto; max-height: 24; border: round $line; background: $panel; }
SelectionList:focus { border: round $pink; }
SelectionList > .option-list--option-highlighted { background: $hover; }
SelectionList:focus > .option-list--option-highlighted { background: $hover; }
SelectionList > .selection-list--button { color: $line-hi; background: transparent; }
SelectionList > .selection-list--button-selected { color: $pink; background: transparent; text-style: bold; }
SelectionList > .selection-list--button-highlighted { color: $line-hi; background: transparent; }
SelectionList > .selection-list--button-selected-highlighted { color: $pink; background: transparent; text-style: bold; }
#options { height: auto; margin-top: 1; }
#options Static { width: auto; color: #82788f; padding: 1 1 0 0; }
#runs { width: 8; border: round $line; background: $panel; }
#start { margin-top: 1; background: $button; color: #e9dff2; border: none; min-width: 20; }
#start:focus { background: $pink; color: #15101c; text-style: bold; }
#setuphint { width: 100%; text-align: center; color: #6f6580; margin-top: 1; }

#run { display: none; padding: 0 2; }
#run.show { display: block; }
#setup.hide { display: none; }
#runhead { height: auto; margin: 1 0; }
#bar { width: 1fr; }
#bar Bar > .bar--bar { color: $pink; background: $line; }
#bar Bar > .bar--complete { color: #96dcaf; }
#count { width: auto; margin-left: 2; }
#middle { height: 1fr; }
#runs_table { width: 1fr; height: 1fr; border: round $line; background: $panel; }
#board { width: 46; height: 1fr; margin-left: 1; padding: 0 1; border: round $line; }
#benchcat { height: 3; margin-top: 1; }
#results { display: none; height: 1fr; padding: 0 1; }
#watch { display: none; width: 2fr; height: 1fr; margin-left: 1; padding: 0 1; border: round $line;
         background: $bg; scrollbar-size-vertical: 1; }
#run.watching #watch { display: block; }
#run.watching #board { display: none; }
#run.results #results { display: block; }
#run.results #middle { display: none; }
#runhint { color: #6f6580; height: 1; }
"""


class BenchApp(BenchWindow):
    TITLE = "purr bench"
    CSS_PATH = "purr.tcss"
    CSS = CSS  # the bench's own layout, on top of purr.tcss
    BINDINGS = [
        Binding("q", "stop_or_quit", "stop / quit", priority=True),
        Binding("ctrl+q", "quit_now", "quit", priority=True),
        Binding("o", "open_report", "open report"),
        Binding("s", "show('results')", "scoreboard"),
        Binding("l", "show('log')", "live log"),
        Binding("w", "watch", "watch the run"),
        Binding("r", "real", "real benchmarks"),
    ]

    def __init__(self, config, args):
        super().__init__(config)
        self.args = args
        self.results, self.running, self.done = [], False, False
        self.started = self.run_started = None
        self.report = None
        self.live_kind, self.live_line = None, ""  # the watch panel's line being streamed into

    # ---- layout ----

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(cat.shimmer("₊˚✧ purr bench ✧˚₊", 0.1), id="title")
            yield Static("purr and OpenCode get the same tasks and the same model · hidden tests decide",
                         id="subtitle")
            with Horizontal(id="lists"):
                with Vertical(classes="column"):
                    yield Static("models")
                    yield SelectionList(*self._model_choices(), id="models")
                with Vertical(classes="column"):
                    yield Static("tasks")
                    yield SelectionList(*self._task_choices(), id="tasks")
                with Vertical(classes="column"):
                    yield Static("contestants")
                    wanted = set(self.args.harness.split(","))
                    yield SelectionList(*[Selection(h, h, h in wanted) for h in bench.HARNESSES], id="harnesses")
                    with Horizontal(id="options"):
                        yield Static("vague prompts")
                        yield Switch(self.args.vague, id="vague")
                        yield Static("runs")
                        yield Input(str(self.args.runs), type="integer", id="runs")
            with Center():
                yield Button("start ✧", id="start")
            yield Static("space ticks · tab moves between lists · enter on start · "
                         "r real benchmarks (Terminal-Bench, DeepSWE) · q quits", id="setuphint")
        with Vertical(id="run"):
            with Horizontal(id="runhead"):
                yield ProgressBar(total=1, show_eta=False, show_percentage=False, id="bar")
                yield Static("", id="count")
            with Horizontal(id="middle"):
                yield DataTable(id="runs_table", cursor_type="none", zebra_stripes=False)
                yield Static("", id="board")
                yield RichLog(id="watch", wrap=True, markup=False, highlight=False, max_lines=5000)
            with VerticalScroll(id="results"):
                yield Static("", id="results_body")
            yield Static("", id="benchcat")
            yield Static("w watches the run live · q stops after cancelling the run going on · ctrl+q quits",
                         id="runhint")

    def _model_choices(self):
        wanted = set(self.args.models.split(",")) if self.args.models else set()
        names = sorted(self.config["models"], key=lambda n: (bool(self.config["models"][n].get("price")), n))
        out = []
        for n in names:
            spec = self.config["models"][n]
            t = Text(n.ljust(18))
            t.append(" paid" if spec.get("price") else " local ♡", style=PEACH if spec.get("price") else MINT)
            out.append(Selection(t, n, n in wanted))
        return out

    def _task_choices(self):
        wanted = set(self.args.tasks.split(",")) if self.args.tasks else None
        out = []
        for t in sorted(bench.load_tasks(), key=lambda t: (t.get("level") == "hard", t["name"])):
            hard = t.get("level") == "hard"
            label = Text("★ " if hard else "  ", style=PEACH)
            label.append(t["name"].ljust(16))
            label.append(t["kind"], style=DIM)
            on = (wanted is None or t["name"] in wanted) and (not self.args.level or t.get("level") == self.args.level)
            out.append(Selection(label, t["name"], on))
        return out

    def on_mount(self):
        table = self.query_one("#runs_table", DataTable)
        self.cols = table.add_columns("", "model", "task", "harness", "result", "time", "tok/s",
                                      "calls", "mistakes")
        self.set_interval(0.12, self.tick)
        if self.args.watch:
            self.query_one("#run").add_class("watching")
        if self.args.models:  # everything given on the command line: go straight away
            self.call_after_refresh(self.start)
        else:
            self.query_one("#models", SelectionList).focus()

    # ---- starting ----

    def on_button_pressed(self, event):
        if event.button.id == "start":
            self.start()

    def start(self):
        models = list(self.query_one("#models", SelectionList).selected)
        names = list(self.query_one("#tasks", SelectionList).selected)
        harnesses = [h for h in bench.HARNESSES if h in self.query_one("#harnesses", SelectionList).selected]
        vague = self.query_one("#vague", Switch).value
        try:
            runs = max(1, int(self.query_one("#runs", Input).value or 1))
        except ValueError:
            runs = 1
        tasks = bench.pick_tasks(names, vague)
        if not models or not tasks or not harnesses:
            what = "a model" if not models else "a task with a vague prompt" if vague and names else \
                "a task" if not tasks else "a contestant"
            self.notify(f"tick at least {what} ♡", severity="warning")
            return
        self.plan = (models, tasks, harnesses, runs)
        self.query_one("#setup").add_class("hide")
        self.query_one("#run").add_class("show")
        self.total = len(models) * len(tasks) * len(harnesses) * runs
        self.query_one("#bar", ProgressBar).update(total=self.total, progress=0)
        self.running, self.started = True, time.monotonic()
        self.out_dir = bench.new_out_dir()
        self.go()

    @work(thread=True, exclusive=True)
    def go(self):
        models, tasks, harnesses, runs = self.plan

        def events(kind, info):
            try:
                self.call_from_thread(self.bench_event, kind, info)
            except RuntimeError:  # the window has closed (ctrl+q): let the run finish its cleanup
                pass
        try:
            bench.run_all(self.config, models, tasks, harnesses, runs, self.args.timeout, self.out_dir, events)
        except Exception as e:  # noqa: BLE001 - show it rather than vanish
            try:
                self.call_from_thread(self.notify, f"bench broke: {type(e).__name__}: {e}", severity="error")
                self.call_from_thread(self.bench_event, "finished", {"results": self.results, "summary":
                                      bench.summarise(self.results), "report": None})
            except RuntimeError:  # closed meanwhile
                pass

    # ---- what run_all tells us ----

    LIVE_LOOK = {"think": f"italic {DIM}", "text": TEXT, "tool": LILAC, "result": "#6f6580",
                 "note": DIM, "start": f"bold {PINK}"}

    def action_watch(self):
        self.query_one("#run").toggle_class("watching")

    def show_live(self, kind, text):
        """The run's live stream: thinking and text come in pieces, the rest as whole lines."""
        log = self.query_one("#watch", RichLog)
        if kind in ("think", "text"):
            if kind != self.live_kind:
                self._flush_live()
            self.live_kind = kind
            self.live_line += text
            *done, self.live_line = self.live_line.split("\n")
            for line in done:
                log.write(Text(("│ " if kind == "think" else "") + line, style=self.LIVE_LOOK[kind]))
            return
        self._flush_live()
        if kind == "start":
            log.write(Text(""))
            log.write(Text(f"── {text} ──", style=self.LIVE_LOOK["start"]))
        elif kind == "tool":
            name, _, rest = text.partition(" ")
            log.write(Text.assemble(("◈ ", LILAC), (name, f"bold {LILAC}"), (" " + rest[:150], TEXT)))
        elif kind == "result":
            for line in text.splitlines():
                log.write(Text("   ↳ " + line, style=self.LIVE_LOOK["result"]))
        else:
            log.write(Text(text, style=self.LIVE_LOOK.get(kind, DIM)))

    def _flush_live(self):
        if self.live_line.strip():
            log = self.query_one("#watch", RichLog)
            log.write(Text(("│ " if self.live_kind == "think" else "") + self.live_line,
                           style=self.LIVE_LOOK.get(self.live_kind, TEXT)))
        self.live_kind, self.live_line = None, ""

    def bench_event(self, kind, info):
        if kind == "live":
            self.show_live(info["kind"], info["text"])
            return
        table = self.query_one("#runs_table", DataTable)
        if kind == "warming":
            self.mood("waking", f"loading {info['model']} onto the GPU (not timed)")
        elif kind == "warm":
            s = info["seconds"]
            self.notify(f"{info['model']} loaded in {s:.0f}s, not counted ♡" if s is not None
                        else f"couldn't warm up {info['model']}", timeout=3)
        elif kind == "unreachable":
            self.notify(f"{info['model']} skipped: {info['why']}", severity="error", timeout=12)
            self.query_one("#bar", ProgressBar).advance(len(self.plan[1]) * len(self.plan[2]) * self.plan[3])
        elif kind == "disturbed":
            self.notify(f"⚠ {', '.join(info['others'])} is also loaded: something else is using the GPU, "
                        "so runs get slow (they're marked)", severity="warning", timeout=8)
        elif kind == "start":
            self.run_started = time.monotonic()
            self.now = info
            self.mood("running", f"{info['model']} · {info['task']} · {info['harness']}")
            colour = HARNESS_COLOUR.get(info["harness"], TEXT)
            table.add_row(Text("…", style=DIM), info["model"], info["task"], Text(info["harness"], style=colour),
                          Text("working…", style=DIM), "", "", "", "", key=str(info["n"]))
            table.scroll_end(animate=False)
        elif kind == "done":
            r = info["result"]
            self.results.append(r)
            self.run_started = None
            self.query_one("#bar", ProgressBar).update(progress=info["n"])
            self.fill_row(table, str(info["n"]), r)
            self.draw_board()
            if not (r["error"] and not r["calls"]):
                self.mood("proud" if r["solved"] else "sad", f"{r['task']} · {r['harness']}", hold=2.4)
        elif kind == "finished":
            self.running, self.done = False, True
            self.report = info["report"]
            self.draw_board()
            solved = sum(r["solved"] for r in self.results)
            self.mood("happy", f"{solved} solved · report saved", hold=10**9)
            self.query_one("#runhint", Static).update(
                Text(f"report: {str(self.report).replace(str(Path.home()), '~', 1)}   ·   s scoreboard   ·   "
                     "l live log   ·   o opens the report   ·   q quits" if self.report else "q quits", style=DIM))
            if self.results:
                self.query_one("#results_body", Static).update(self.results_page())
                self.action_show("results")

    def action_show(self, which):
        if self.done:
            self.query_one("#run").set_class(which == "results", "results")

    # ---- the scoreboard at the end ----

    def results_page(self):
        summary = bench.summarise(self.results)
        if not summary:
            return Text("nothing finished ♡", style=DIM)
        rank = sorted(summary, key=lambda s: (-s["solved"] / s["runs"], -s["passed"] / max(s["total"], 1),
                                              s["seconds"] / s["runs"]))
        spent = sum(r["seconds"] for r in self.results)
        head = Text(justify="center")
        head.append_text(cat.shimmer("₊˚✧ the results ✧˚₊", 0.2))
        head.append(f"\n{len(self.results)} runs · {ui.duration(spent)} of work · "
                    f"{len({r['task'] for r in self.results})} tasks", style=DIM)
        return Group(head, Text(""), self._ranking(rank), Text(""), self._stats(rank), Text(""),
                     self._grid(rank), Text(""), self._notes(rank))

    def _who(self, s):
        return Text.assemble((s["model"], f"bold {TEXT}"), ("  ", ""),
                             (s["harness"], HARNESS_COLOUR.get(s["harness"], TEXT)))

    def _ranking(self, rank):
        t = Table(box=box.ROUNDED, border_style=themes.colour(self, "line-hi"), show_header=True, header_style=f"bold {LILAC}",
                  title=Text("♛ ranking", style=f"bold {PINK}"), title_justify="left", expand=True, padding=(0, 1))
        for col, just in (("", "right"), ("who", "left"), ("solved", "left"), ("hidden tests", "left"),
                          ("avg time", "right"), ("tok/s", "right"), ("mistakes", "right")):
            t.add_column(col, justify=just)
        best_time = min(s["seconds"] / s["runs"] for s in rank)
        for n, s in enumerate(rank, 1):
            place = Text("♛ 1" if n == 1 else f"{n}", style=f"bold {PEACH}" if n == 1 else DIM)
            hearts = Text()
            colour = HARNESS_COLOUR.get(s["harness"], TEXT)
            per = 1 if s["runs"] <= 16 else s["runs"] / 16  # long benches: one heart per few runs
            hearts.append("♥" * round(s["solved"] / per), style=colour)
            hearts.append("♡" * (round(s["runs"] / per) - round(s["solved"] / per)), style=FAINT)
            hearts.append(f" {s['solved']}/{s['runs']}", style=colour)
            pct = s["passed"] / max(s["total"], 1)
            bar = Text("█" * round(pct * 12), style=MINT)
            bar.append("░" * (12 - round(pct * 12)), style=themes.colour(self, "line"))
            bar.append(f" {100 * pct:.0f}%", style=MINT)
            avg = s["seconds"] / s["runs"]
            speed = bench.speed(s) or "–"
            mistakes = s["tool_errors"] + s["hallucinations"] + s["syntax_errors"]
            t.add_row(place, self._who(s), hearts, bar,
                      Text(ui.duration(avg) + (" ✧" if avg == best_time else ""), style=PEACH if avg == best_time else TEXT),
                      speed, Text(str(mistakes), style=ROSE if mistakes else DIM))
        return t

    def _stats(self, rank):
        t = Table(box=box.SIMPLE_HEAD, border_style=themes.colour(self, "line-hi"), header_style=f"bold {LILAC}",
                  title=Text("✧ every number", style=f"bold {PINK}"), title_justify="left", expand=True,
                  padding=(0, 1), row_styles=["", f"on {themes.colour(self, 'panel')}"])
        cols = [("tokens", "right"), ("calls", "right"), ("tool\ncalls", "right"),
                ("tool\nerrors", "right"), ("failed\nedits", "right"), ("made-up\ntools", "right"),
                ("made-up\nfiles", "right"), ("calls\nas text", "right"), ("false\n'done'", "right"),
                ("syntax\nerrors", "right"), ("time-\nouts", "right"), ("repaired", "right"),
                ("checks\ncaught", "right"), ("cost", "right")]
        t.add_column("who", min_width=18, no_wrap=True)
        for col, just in cols:
            t.add_column(col, justify=just, min_width=max(len(x) for x in col.split("\n")))

        def n(v, bad=True):
            return Text(str(v), style=(ROSE if bad else MINT) if v else DIM)
        for s in rank:
            purr = s["harness"].startswith("purr")
            who = Text.assemble((s["model"] + "\n", f"bold {TEXT}"), (s["harness"], HARNESS_COLOUR.get(s["harness"], TEXT)))
            t.add_row(who, ui.short(s["out"]), str(s["calls"]), str(s["tool_calls"]),
                      n(s["tool_errors"]), n(s["failed_edits"]), n(s["unknown_tool"]), n(s["missing_file"]),
                      n(s["leaked_calls"]), n(s["false_claims"]), n(s["syntax_errors"]), n(s["timeouts"]),
                      n(s["repaired"], bad=False) if purr else Text("–", style=DIM),
                      n(s["caught"], bad=False) if purr else Text("–", style=DIM),
                      f"${s['cost']:.3f}" if s["cost"] else Text("free ♡", style=MINT))
        return t

    def _grid(self, rank):
        t = Table(box=box.ROUNDED, border_style=themes.colour(self, "line-hi"), header_style=f"bold {LILAC}",
                  title=Text("♡ task by task", style=f"bold {PINK}"), title_justify="left", expand=True,
                  padding=(0, 1))
        t.add_column("task")
        for s in rank:
            t.add_column(Text.assemble((s["model"] + "\n", TEXT), (s["harness"], HARNESS_COLOUR.get(s["harness"], TEXT))),
                         justify="center")
        levels = {t_["name"]: t_.get("level") for t_ in bench.load_tasks()}
        for task in sorted({r["task"] for r in self.results}, key=lambda x: (levels.get(x) == "hard", x)):
            row = [Text.assemble(("★ " if levels.get(task) == "hard" else "  ", PEACH), (task, TEXT))]
            for s in rank:
                runs = [r for r in self.results if r["task"] == task and r["model"] == s["model"]
                        and r["harness"] == s["harness"] and not (r["error"] and not r["calls"])]
                if not runs:
                    row.append(Text("–", style=DIM))
                    continue
                cell = Text()
                for r in runs:
                    if r["solved"]:
                        cell.append(f"✓ {ui.duration(r['seconds'])} ", style=MINT)
                    else:
                        cell.append(f"✗ {r['passed']}/{r['total']}{' ⏱' if r['timeout'] else ''} ", style=ROSE)
                row.append(cell)
            t.add_row(*row)
        return t

    def _notes(self, rank):
        """A few friendly sentences: who won, who was fastest, what purr caught."""
        best = rank[0]
        lines = [Text.assemble(("♛ ", PEACH), (f"{best['model']} with {best['harness']}", f"bold {TEXT}"),
                               (f" solved the most: {best['solved']}/{best['runs']}", TEXT))]
        fast = min(rank, key=lambda s: s["seconds"] / s["runs"])
        lines.append(Text.assemble(("✧ ", PEACH), ("fastest: ", DIM), (f"{fast['model']} {fast['harness']}", TEXT),
                                   (f", {ui.duration(fast['seconds'] / fast['runs'])} a task on average", DIM)))
        careful = min(rank, key=lambda s: (s["tool_errors"] + s["hallucinations"] + s["syntax_errors"]) / s["runs"])
        lines.append(Text.assemble(("♡ ", PEACH), ("fewest mistakes: ", DIM),
                                   (f"{careful['model']} {careful['harness']}", TEXT)))
        repaired = sum(s["repaired"] for s in rank)
        if repaired:
            lines.append(Text.assemble(("✿ ", MINT), (f"purr quietly fixed {repaired} model slip"
                                                      f"{'s' * (repaired != 1)} along the way", DIM)))
        return Group(*lines)

    def fill_row(self, table, key, r):
        skipped = r["error"] and not r["calls"]
        if skipped:
            mark, result = Text("–", style=DIM), Text(("skipped: " + r["error"])[:40], style=DIM)
        elif r.get("left_folder"):
            mark, result = Text("✗", style=ROSE), Text("left its folder", style=ROSE)
        elif r["solved"]:
            mark, result = Text("✓", style=f"bold {MINT}"), Text("solved", style=MINT)
            if r.get("disturbed"):
                result.append(" ⚠", style=ROSE)
        else:
            mark = Text("✗", style=f"bold {ROSE}")
            result = Text(f"{r['passed']}/{r['total']}" + (" · timed out" if r["timeout"] else ""), style=ROSE)

        # one column for mistakes (tool errors + hallucinations + syntax errors); the report splits them
        mistakes = r["tool_errors"] + r.get("hallucinations", 0) + r["syntax_errors"]
        values = [mark, r["model"], r["task"], Text(r["harness"], style=HARNESS_COLOUR.get(r["harness"], TEXT)),
                  result, "" if skipped else ui.duration(r["seconds"]),
                  "" if skipped or not r["tok_s"] else f"{r['tok_s']:.0f}",
                  "" if skipped else str(r["calls"]),
                  "" if skipped else Text(str(mistakes), style=ROSE if mistakes else DIM)]
        for col, value in zip(self.cols, values):
            table.update_cell(key, col, value, update_width=True)

    def draw_board(self):
        """Per model and harness: solved as little hearts, then time, speed and mistakes."""
        t = Text()
        t.append("scoreboard\n\n", style=f"bold {LILAC}")
        model = None
        for s in bench.summarise(self.results):
            if s["model"] != model:
                model = s["model"]
                t.append(f"{model}\n", style=f"bold {TEXT}")
            colour = HARNESS_COLOUR.get(s["harness"], TEXT)
            t.append(f"  {s['harness']:<12}", style=colour)
            t.append("♥" * s["solved"], style=colour)
            t.append("♡" * (s["runs"] - s["solved"]), style=DIM)
            t.append(f"  {s['solved']}/{s['runs']}\n", style=colour)
            speed = f"{bench.speed(s) or '–'} tok/s"
            mistakes = s["tool_errors"] + s["hallucinations"] + s["syntax_errors"]
            t.append(f"    {ui.duration(s['seconds'] / s['runs'])} avg · {speed} · ", style=DIM)
            t.append(f"{mistakes} mistake{'s' * (mistakes != 1)}", style=ROSE if mistakes else DIM)
            if s["repaired"]:
                t.append(f" · {s['repaired']} repaired", style=MINT)
            t.append("\n")
        if not self.results:
            t.append("the first results show up here ♡", style=DIM)
        self.query_one("#board", Static).update(t)

    # ---- the cat ----

    def cat_label_for(self, mood):
        return f"{self.name_} is benchmarking" if mood == "running" else super().cat_label_for(mood)

    def tick(self):
        if self.closing():
            return
        self.cat_tick += 1
        now = time.monotonic()
        if self.cat_until and now > self.cat_until and self.running:
            self.cat_until = 0.0
            if self.run_started:
                n = self.now
                self.mood("running", f"{n['model']} · {n['task']} · {n['harness']}")
        stats = []
        if self.started:
            stats.append(ui.duration(now - self.started))
            if self.run_started:
                stats.append(f"this run {ui.duration(now - self.run_started)}")
            done = len(self.results)
            stats.append(f"{done}/{getattr(self, 'total', 0)} runs")
            self.query_one("#count", Static).update(Text(f"{done}/{self.total}  ·  {ui.duration(now - self.started)}",
                                                         style=PINK))
            if self.run_started:
                table = self.query_one("#runs_table", DataTable)
                try:
                    table.update_cell(str(self.now["n"]), self.cols[5],
                                      Text(ui.duration(now - self.run_started), style=DIM), update_width=True)
                except Exception:  # noqa: BLE001 - the row may not be drawn yet
                    pass
        if self.query_one("#run").has_class("show"):
            self.query_one("#benchcat", Static).update(cat.render(
                self.cat_mood, self.cat_tick, self.cat_detail, stats, label=self.cat_label, night=self.night))
        else:
            self.query_one("#title", Static).update(cat.shimmer("₊˚✧ purr bench ✧˚₊", self.cat_tick * 0.03))

    # ---- keys ----

    def action_stop_or_quit(self):
        if self.focused and isinstance(self.focused, Input):
            return
        if self.running and not bench.STOP.is_set():
            bench.STOP.set()
            self.mood("oops", "stopping: the run going on is cancelled and not counted", hold=10**9)
            self.notify("stopping ♡ press q again to quit once it has", timeout=4)
        elif not self.running:
            self.exit()

    def action_quit_now(self):
        bench.STOP.set()
        self.exit()

    def action_real(self):
        """Over to Terminal-Bench / DeepSWE (tui/realbench_app.py), from the setup screen only."""
        if not self.started and not (self.focused and isinstance(self.focused, Input)):
            self.exit("real")

    def action_open_report(self):
        self.open_path(self.report)
