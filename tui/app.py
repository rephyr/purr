"""purr's full-screen mode, built with Textual.

The agent runs in a background thread so the screen stays responsive. It talks to the
screen through TuiView, which has the same methods as ui.PlainView.
"""

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from harness import commands, ui
from tui import cat, diffview, themes
from harness.agent import MODES as MODE_NAMES
from harness.agent import STATE_DIR, Agent, list_sessions

HISTORY = STATE_DIR / "history"

PINK = "#f5a9d0"
LILAC = "#c8a2f0"
DIM = "#82788f"
MINT = "#96dcaf"
ROSE = "#f0829b"
PEACH = "#ffb8c8"  # warnings and "paid": soft peach-pink, not yellow
TEXT = "#e9dff2"
VERSION = "0.2.0"
ATTACHED = re.compile(r'\n\n<file path="[^"]*">\n.*?\n</file>', re.S)
MENTION_AT_CURSOR = re.compile(r"(?:^|\s)@(\S*)$")
CYAN = "#8fd8e8"
TEST_COMMAND = re.compile(r"\b(pytest|unittest|tests?|jest|vitest|cargo test|go test|npm test|gut)\b")
# tool name -> (icon, verb, colour) for the lines in the chat
TOOL_LOOK = {
    "read_file": ("◈", "read", LILAC), "list_files": ("◈", "list", LILAC),
    "grep": ("◈", "search", LILAC), "fetch_url": ("◈", "fetch", LILAC),
    "edit_file": ("✎", "edit", PINK), "write_file": ("✎", "write", PINK),
    "run": ("❯", "run", CYAN), "task": ("✦", "helper", PEACH),
}
JUNK = (".pyc", ".o", ".so", ".class", ".import", ".uid")  # never worth attaching

# The start screen logo. ░ = the darker inside of a letter, T = top edge with the inside below it.
# Each part: (rows, colour, inside colour).
LOGO = [
    (["█TT█ █  █ ", "█░░█ █░░█ ", "█▀▀▀ ▀▀▀▀ ", "▀         "], LILAC, "#4f3f66"),
    (["█▀▀▀ █▀▀▀   ", "█    █      ", "▀    ▀      ", "            "], PINK, "#6b4360"),
    (["▄██▄██▄", "▀█████▀", "  ▀█▀  ", "       "], PINK, PINK),
]


def logo():
    t = Text(no_wrap=True)
    for row in range(4):
        for rows, colour, inside in LOGO:
            for ch in rows[row]:
                if ch == "░":
                    t.append("█", style=inside)
                elif ch == "T":
                    t.append("▀", style=f"{colour} on {inside}")
                else:
                    t.append(ch, style=colour)
        if row < 3:
            t.append("\n")
    return t


def keys_text():
    t = Text(no_wrap=True)
    for key, what in (("/", "commands"), ("@", "files"), ("!", "shell"), ("esc", "stop"), ("ctrl+q", "quit")):
        t.append(key, style=f"bold {TEXT}")
        t.append(f" {what}   ", style=DIM)
    t.rstrip()
    return t


def git_branch(folder):
    try:
        res = subprocess.run(["git", "-C", str(folder), "rev-parse", "--abbrev-ref", "HEAD"],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


class CatWidget(Static):
    """The cat above the message box. Click her to pet her."""

    def on_click(self, event):
        if not self.screen.get_selected_text():
            self.app.pet_cat()


class ToolLine(Vertical):
    """A tool line you can click: it opens to show the exact call and what came back."""

    def __init__(self, header):
        super().__init__(classes="toolline")
        self.header = header
        self.has_details = False

    def compose(self) -> ComposeResult:
        yield Static(self.header, classes="line tool")
        yield Static("", classes="tooldetails")

    def set_details(self, details):
        self.query_one(".tooldetails", Static).update(details)
        self.has_details = True
        self._draw_header()

    def _draw_header(self):
        t = self.header.copy()
        t.append("  ▾" if self.has_class("open") else "  ▸", style="#5e5469")
        self.query_one(".tool", Static).update(t)

    def on_click(self, event):
        if self.has_details and not self.screen.get_selected_text():
            self.toggle_class("open")
            self._draw_header()


def tool_details(name, args, result, max_lines=200):
    """The exact call (every argument) and the full result, for an opened tool line."""
    t = Text()
    t.append("call ", style=DIM)
    t.append(name, style=f"bold {LILAC}")
    for key, value in args.items():
        value = value if isinstance(value, str) else json.dumps(value)
        lines = value.splitlines() or [""]
        t.append(f"\n  {key}: ", style=DIM)
        if len(lines) == 1:
            t.append(lines[0], style=TEXT)
        else:
            shown = lines[:30]
            t.append("\n    " + "\n    ".join(shown), style=TEXT)
            if len(lines) > 30:
                t.append(f"\n    … {len(lines) - 30} more lines", style=DIM)
    lines = (result or "").splitlines()
    t.append(f"\n\nresult ", style=DIM)
    t.append(f"{len(lines)} line{'s' if len(lines) != 1 else ''}, what the model got back",
             style="#5e5469")
    t.append("\n  " + "\n  ".join(lines[:max_lines]), style="#b9aec7")
    if len(lines) > max_lines:
        t.append(f"\n  … {len(lines) - max_lines} more lines", style=DIM)
    return t


class TuiView:
    """Called from the agent's thread. Hands everything over to the app's thread,
    and batches streamed text so the screen isn't redrawn for every single token."""

    FLUSH_EVERY = 0.05

    def __init__(self, app):
        self.app = app
        self.pending = []  # [(kind, text)] not shown yet
        self.last_flush = 0.0
        self.mood = ("sleeping", "")
        self.live_first, self.live_chars = None, 0

    def _mood(self, mood, detail=""):
        if (mood, detail) != self.mood:
            self.mood = (mood, detail)
            self._send(self.app.set_cat, mood, detail)

    def activity(self, what, detail=""):
        mood = cat.ACTIVITY.get(what, "thinking")
        if mood:
            self._mood(mood, detail[:60])

    def _send(self, fn, *args):
        self.app.call_from_thread(fn, *args)

    def _stream(self, kind, s):
        self.live_chars += len(s)
        self.live_first = self.live_first or time.monotonic()
        if self.pending and self.pending[-1][0] == kind:
            self.pending[-1] = (kind, self.pending[-1][1] + s)
        else:
            self.pending.append((kind, s))
        if time.monotonic() - self.last_flush > self.FLUSH_EVERY:
            self.flush()

    def flush(self):
        if self.pending:
            batch, self.pending = self.pending, []
            self._send(self.app.stream_batch, batch)
        self.last_flush = time.monotonic()

    def thinking(self, s):
        self._mood("thinking")
        self._stream("think", s)

    def text(self, s):
        self._mood("talking")
        self._stream("text", s)

    def end_reply(self):
        self.flush()
        self.live_first, self.live_chars = None, 0
        self._send(self.app.end_reply)

    def live_rate(self):
        """Rough speed of the answer streaming in right now (about 4 characters a token)."""
        if not self.live_first or time.monotonic() - self.live_first < 0.5:
            return None
        return self.live_chars / 4 / (time.monotonic() - self.live_first)

    def tool(self, line):
        self.flush()
        self._send(self.app.add_tool, line)

    def tool_result(self, name, args, result):
        self._send(self.app.add_tool_result, name, args, result)
        if name == "run" and TEST_COMMAND.search(str(args.get("command", ""))):
            code = re.search(r"\[exit code (-?\d+)\]\s*$", result or "")
            if code:  # she's proud when tests pass, sad when they fail
                self._mood("proud" if code.group(1) == "0" else "sad")

    def diff(self, path, before, after):
        self.flush()
        self._send(self.app.add_diff, path, before, after)

    def note(self, s, kind="dim"):
        self.flush()
        if kind == "error":
            self._mood("oops", s[:60])
        if kind == "stats":
            self._send(self.app.add_stats, s)
            return
        self._send(self.app.add_line, s, kind)

    def todos(self, items):
        self.flush()
        self._send(self.app.show_todos, items)

    def status(self, s):
        self._send(self.app.set_status, s)

    def ask(self, question, allow_always=True):
        self.flush()
        done = threading.Event()
        box = {}

        def answered(result):
            box["result"] = result
            done.set()

        before = self.mood
        self._mood("waiting")
        self._send(self.app.push_screen, AskScreen(question, allow_always), answered)
        while not done.wait(0.1):
            if self.app.closing:
                return "n", ""
        self._mood(*before)
        return box["result"]


class AskScreen(ModalScreen):
    """y / a / n popup before edits and commands. Typing a reason = no, with the reason."""

    BINDINGS = [
        Binding("y", "answer('y')", "yes"),
        Binding("a", "answer('a')", "always"),
        Binding("n", "answer('n')", "no"),
        Binding("escape", "answer('n')", "no"),
    ]

    def __init__(self, question, allow_always):
        super().__init__()
        self.question = question
        self.allow_always = allow_always

    def compose(self) -> ComposeResult:
        with Vertical(id="askbox"):
            yield Static(self.question, id="question")
            with Horizontal(id="buttons"):
                yield Button("y  yes", id="y")
                if self.allow_always:
                    yield Button("a  always", id="a")
                yield Button("n  no", id="n")
            yield Input(placeholder="or tell it what to do instead, then enter", id="reason")

    def on_mount(self):
        self.query_one("#y", Button).focus()

    def action_answer(self, ans):
        if ans == "a" and not self.allow_always:
            return
        self.dismiss((ans, ""))

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss((event.button.id, ""))

    def on_input_submitted(self, event: Input.Submitted):
        if event.value.strip():
            self.dismiss(("n", event.value.strip()))


class RefineScreen(ModalScreen):
    """Your message, rewritten into a clear task. Edit it if you like, then send it or yours."""

    BINDINGS = [
        Binding("ctrl+s", "send('refined')", "send it", priority=True),
        Binding("ctrl+o", "send('original')", "send yours", priority=True),
        Binding("escape", "send('cancel')", "cancel"),
    ]

    def __init__(self, original, refined):
        super().__init__()
        self.original, self.refined = original, refined

    def compose(self) -> ComposeResult:
        with Vertical(id="refinebox"):
            yield Static(Text("✧ your message, made clearer", style=f"bold {PINK}"), id="refinetitle")
            yield Static(Text(f"you wrote: {self.original}", style=DIM), id="refineorig")
            yield TextArea(self.refined, id="refined", soft_wrap=True, compact=True)
            hint = Text()
            for key, what in (("ctrl+s", "send this"), ("ctrl+o", "send yours"), ("esc", "cancel")):
                hint.append(key, style=f"bold {TEXT}")
                hint.append(f" {what}   ", style=DIM)
            yield Static(hint, id="refinehint")

    def on_mount(self):
        self.query_one("#refined", TextArea).focus()

    def action_send(self, which):
        if which == "refined":
            self.dismiss(self.query_one("#refined", TextArea).text.strip() or self.original)
        elif which == "original":
            self.dismiss(self.original)
        else:
            self.dismiss(None)


class PRScreen(ModalScreen):
    """A pull request to check before anything is pushed: title and description are editable."""

    BINDINGS = [
        Binding("ctrl+s", "create", "create it", priority=True),
        Binding("escape", "cancel", "cancel"),
    ]

    def __init__(self, title, body, files, model):
        super().__init__()
        self.pr_title, self.body, self.files, self.model = title, body, files, model

    def compose(self) -> ComposeResult:
        with Vertical(id="prbox"):
            yield Static(Text("✧ pull request", style=f"bold {PINK}"), id="prtitle")
            yield Input(self.pr_title, id="prname")
            yield TextArea(self.body, id="prbody", soft_wrap=True, compact=True)
            info = Text()
            info.append("files  ", style=DIM)
            info.append(", ".join(f[3:] for f in self.files), style=TEXT)
            info.append("\nco-author  ", style=DIM)
            info.append(f"purr-{self.model}", style=LILAC)
            info.append("\nmakes a branch, commits, pushes and runs gh pr create", style=DIM)
            yield Static(info, id="prinfo")
            hint = Text()
            for key, what in (("ctrl+s", "create it"), ("esc", "cancel, nothing is pushed")):
                hint.append(key, style=f"bold {TEXT}")
                hint.append(f" {what}   ", style=DIM)
            yield Static(hint, id="prhint")

    def on_mount(self):
        self.query_one("#prname", Input).focus()

    def action_create(self):
        title = self.query_one("#prname", Input).value.strip() or self.pr_title
        self.dismiss((title, self.query_one("#prbody", TextArea).text.strip()))

    def action_cancel(self):
        self.dismiss(None)


class Picker(ModalScreen):
    """A list to choose from: type to filter, up/down to move, enter to pick, esc to close.
    items: [(id, label as rich Text)]. Returns the chosen id, or None."""

    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("up", "move(-1)", show=False, priority=True),
        Binding("down", "move(1)", show=False, priority=True),
    ]

    def __init__(self, title, items, current=None, empty="nothing here"):
        super().__init__()
        self.title_text, self.items, self.current, self.empty = title, items, current, empty

    def compose(self) -> ComposeResult:
        with Vertical(id="pickbox"):
            yield Static(self.title_text, id="picktitle")
            yield Input(placeholder="type to search", id="search")
            yield OptionList(id="picklist")

    def on_mount(self):
        self.query_one("#picklist", OptionList).can_focus = False
        self.fill("")
        self.query_one("#search").focus()

    def fill(self, query):
        query = query.lower().strip()
        picklist = self.query_one("#picklist", OptionList)
        picklist.clear_options()
        options = [Option(label, id=item_id) for item_id, label in self.items
                   if not query or query in label.plain.lower()]
        if not options:
            picklist.add_option(Option(Text(self.empty, style=DIM), disabled=True))
            return
        picklist.add_options(options)
        ids = [o.id for o in options]
        picklist.highlighted = ids.index(self.current) if self.current in ids else 0

    def on_input_changed(self, event: Input.Changed):
        self.fill(event.value)

    def action_move(self, step):
        picklist = self.query_one("#picklist", OptionList)
        picklist.action_cursor_down() if step > 0 else picklist.action_cursor_up()

    def action_close(self):
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        picklist = self.query_one("#picklist", OptionList)
        if picklist.highlighted is not None:
            option = picklist.get_option_at_index(picklist.highlighted)
            if not option.disabled:
                self.dismiss(option.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        event.stop()
        self.dismiss(event.option.id)


class PromptArea(TextArea):
    """The message box. Enter sends, ctrl+j (or shift+enter) starts a new line.
    Up/down/tab/escape go to the / and @ menus or the history first."""

    class Submitted(Message):
        def __init__(self, text):
            super().__init__()
            self.text = text

    async def _on_key(self, event):
        key = event.key
        if key in ("up", "down", "tab", "escape") and self.app.prompt_key(key):
            event.stop()
            event.prevent_default()
            return
        if key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if key in ("ctrl+j", "shift+enter", "alt+enter"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)

    def set_text(self, text):
        self.load_text(text)
        self.move_cursor(self.document.end)


class PurrApp(App):
    TITLE = "purr"
    CSS_PATH = "purr.tcss"
    BINDINGS = [
        Binding("ctrl+q", "quit", "quit", priority=True),
        Binding("ctrl+d", "quit", "quit", priority=True),
        Binding("ctrl+c", "stop_or_clear", "stop", priority=True),
        Binding("escape", "stop", "stop"),
        Binding("shift+tab", "next_mode", "mode", priority=True),
        Binding("pageup", "scroll_chat(-1)", show=False),
        Binding("pagedown", "scroll_chat(1)", show=False),
    ]

    def __init__(self, config, folder, model_name, trust=False, resume=False):
        super().__init__()
        self.config, self.folder, self.model_name = config, folder, model_name
        # the stylesheet's $bg, $panel, ... come from the theme, so it has to be set before it loads
        for name in themes.PALETTES:
            self.register_theme(themes.make(name))
        self.set_theme(self.saved_theme())
        self.trust, self.resume = trust, resume
        self.view = TuiView(self)
        self.agent = None
        self.busy = False
        self.closing = False
        self.cur_kind = None   # the block being streamed into: "think" or "text"
        self.cur_widget = None
        self.cur_text = ""
        self.md_stream = None
        self.menu_items = []   # the / or @ menu: [{"label", "value", "runs", "kind"}]
        self.files = None      # project files for @, loaded the first time you type @
        self.history = self._load_history()
        self.hist_pos = len(self.history)
        self.status_text = ""
        self.busy_since = None
        self.job = None         # what's running: "turn" (the model), "shell" or "compact"
        self.cat_mood, self.cat_detail = "sleeping", ""
        self.cat_tick = 0
        self.cat_shown = None   # (mood, frame, detail) on screen, so it's only redrawn when it changes
        self.cat_until = None   # "done!" and "oops" go back to napping after a moment
        self.last_stats = []    # the last task's [time, tok/s, tokens], shown while the cat naps
        self.cat_label = ""
        self.cat_hold = 0.0     # a short reaction (proud, petted, ...) shows until then
        self.cat_pending = None # (mood, detail) to show once the reaction is over
        self.night = themes.is_night()
        self.night_checked = time.monotonic()
        self.pet = self.load_pet()
        self.cat_label = cat.label_for("sleeping", self.pet["name"])

    # ---- layout ----

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            yield Static(logo(), id="logo")
            yield Static(id="pinned")  # your latest message, once it has scrolled out of sight
            yield VerticalScroll(id="chat")
            with Center(id="dockrow"), Vertical(id="dock"):
                yield CatWidget(id="cat")
                with Vertical(id="box"):
                    yield PromptArea(id="prompt", placeholder='ask anything…  "fix the failing test"',
                                     soft_wrap=True, highlight_cursor_line=False, compact=True)
                    with Horizontal(id="modelline"):
                        yield Static(id="model")
                        yield Static(id="status")
                with Horizontal(id="under"):
                    yield Static(id="where")
                    yield Static(keys_text(), id="keys")
        yield Static(f"purr {VERSION}", id="version")
        yield OptionList(id="menu")  # floats above the message box, on its own layer

    def on_mount(self):

        self.screen.add_class("home")
        try:
            self.agent = Agent(self.config, self.folder, self.model_name, self.view)
        except KeyError as e:
            self.exit(1, message=e.args[0])
            return
        self.agent.tools.trust_all = self.trust
        self.chat = self.query_one("#chat", VerticalScroll)
        self.chat.can_focus = False
        self.query_one("#menu", OptionList).can_focus = False
        where = str(self.agent.root).replace(str(Path.home()), "~", 1)
        branch = git_branch(self.agent.root)
        self.query_one("#where", Static).update(Text(f"{where}:{branch}" if branch else where, style=DIM,
                                                     no_wrap=True, overflow="ellipsis"))
        if self.resume:
            sessions = list_sessions(self.agent.root)
            if sessions:
                self._load_session(sessions[0]["path"])
        self.refresh_info()
        self.set_status("")
        self.prompt.focus()
        self.set_interval(0.12, self.tick)

    @property
    def prompt(self) -> PromptArea:
        return self.query_one("#prompt", PromptArea)

    def refresh_info(self):
        """The line inside the message box: model, where it runs, trust mode."""
        provider = self.agent.model["provider"]
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(self.agent.model_name, style=f"bold {LILAC}")
        line.append(f"  {provider} ({'paid' if self.agent.model.get('price') else 'local'})", style=DIM)
        icon, colour = self.MODE_LOOK[self.agent.mode]
        line.append(f"   {icon} {self.agent.mode}", style=f"bold {colour}")
        if self.agent.refine_mode != "off" and self.agent.mode == "code":
            line.append("  ✧ refine" if self.agent.refine_mode == "on" else "  ✧ auto-refine", style=PEACH)
        if self.agent.tools.trust_all:
            line.append("  trusting everything", style=PEACH)
        self.query_one("#model", Static).update(line)

    def set_status(self, s):
        # the agent's status starts with the model name, which the box already shows
        name = self.agent.model_name if self.agent else ""
        self.status_text = s[len(name):].strip() if name and s.startswith(name) else s
        self.tick()

    def tick(self):
        if self.busy:
            self.busy_since = self.busy_since or time.monotonic()
        else:
            self.busy_since = None
        self.query_one("#status", Static).update(Text(self.status_text, style=DIM))
        self.update_pinned()
        self.cat_tick += 1
        now = time.monotonic()
        if self.cat_hold and now > self.cat_hold:
            self.cat_hold = 0.0
            mood, detail = self.cat_pending or (("thinking" if self.busy else "sleeping"), "")
            self.cat_pending = None
            if not self.busy and mood in self.WORK:
                mood, detail = "sleeping", ""
            self.set_cat(mood, detail)
        if self.cat_until and not self.busy and now > self.cat_until:
            self.set_cat("sleeping")
        if now - self.night_checked > 60:  # follows your desktop's day/night switch
            self.night_checked, night = now, themes.is_night()
            if night != self.night:
                self.night = night
                self.cat_shown = None
                if self.theme_choice == "auto":
                    self.set_theme("auto")
        self.draw_cat()

    # short reactions play for this long, then she goes back to what she was doing
    HOLD = {"proud": 2.6, "sad": 2.6, "petted": 2.2, "waking": 1.4}
    WORK = {"thinking", "exploring", "building", "running", "talking", "tidying"}

    def set_cat(self, mood, detail=""):
        now = time.monotonic()
        if mood in self.WORK and self.cat_mood == "sleeping":
            self.cat_pending = (mood, detail)  # a task woke her: stretch first
            self._show_cat("waking", "")
            return
        if now < self.cat_hold and mood not in self.HOLD and mood not in ("waiting", "oops"):
            self.cat_pending = (mood, detail)  # let the reaction finish
            return
        self._show_cat(mood, detail)

    def _show_cat(self, mood, detail):
        before = (self.cat_mood, self.cat_detail)
        if mood != self.cat_mood:
            self.cat_tick = 0  # start the new mood's animation from its first frame
            self.cat_label = cat.label_for(mood, self.pet["name"])
        self.cat_mood, self.cat_detail = mood, detail
        self.cat_hold = time.monotonic() + self.HOLD[mood] if mood in self.HOLD else 0.0
        if mood in self.HOLD and self.cat_pending is None and before[0] not in self.HOLD:
            self.cat_pending = before
        self.cat_until = time.monotonic() + 3 if mood in ("happy", "oops") else None
        self.cat_shown = None
        self.draw_cat()

    def cat_command(self, arg):
        """/cat: her card. /cat name Luna: rename her."""
        word, _, rest = arg.partition(" ")
        if word == "name" and rest.strip():
            self.pet["name"] = rest.strip()[:24]
            self.save_pet()
            self.cat_label = cat.label_for(self.cat_mood, self.pet["name"])
            self.cat_shown = None
            self.draw_cat()
            self.add_line_text(Text(f"♡ say hi to {self.pet['name']}!", style=PINK), "info")
            return
        p = self.pet
        t = Text()
        t.append(f"₊˚✧ {p['name']} ✧˚₊\n", style=f"bold {PINK}")
        t.append("petted ", style=DIM)
        t.append(f"{p['pets']} time{'s' if p['pets'] != 1 else ''}", style=PINK)
        t.append("  ⋆  tasks together ", style=DIM)
        t.append(str(p["tasks"]), style=LILAC)
        t.append("  ⋆  friends since ", style=DIM)
        t.append(p["since"], style=MINT)
        t.append("\nclick her to pet her ♡   /cat name <new name> to rename her", style=DIM)
        self.add_line_text(t, "info")

    def pet_cat(self):
        self.pet["pets"] = self.pet.get("pets", 0) + 1
        self.save_pet()
        self._show_cat("petted", f"petted {self.pet['pets']} time{'s' if self.pet['pets'] != 1 else ''} ♡")

    # her name, how often she's been petted, tasks done together: ~/.local/state/purr/cat.json
    PET_FILE = STATE_DIR / "cat.json"

    def load_pet(self):
        try:
            pet = json.loads(self.PET_FILE.read_text())
        except (OSError, ValueError):
            pet = {}
        pet.setdefault("name", self.config.get("cat_name", "Mochi"))
        pet.setdefault("pets", 0)
        pet.setdefault("tasks", 0)
        pet.setdefault("since", time.strftime("%Y-%m-%d"))
        return pet

    def save_pet(self):
        try:
            self.PET_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.PET_FILE.write_text(json.dumps(self.pet, indent=1))
        except OSError:
            pass

    def cat_stats(self):
        """[time, tok/s, tokens] for next to the cat: live while working, else the last task's."""
        if not (self.busy and self.busy_since):
            return self.last_stats
        parts = [ui.duration(time.monotonic() - self.busy_since)]
        if self.job == "turn":
            live = self.view.live_rate() if self.agent.on_my_gpu() else None
            rate = live or self.agent.tok_per_s()
            if rate:
                parts.append(f"{rate:.0f} tok/s")
            out = (getattr(self.agent, "turn_stats", None) or {}).get("out", 0) + self.view.live_chars // 4
            if out:
                parts.append(f"{ui.short(out)} tokens")
        return parts

    def draw_cat(self):
        stats = tuple(self.cat_stats())
        still = self.cat_mood in ("sleeping", "oops")
        frame = cat.frame_index(self.cat_mood, self.cat_tick)
        shown = (self.cat_mood, frame, self.cat_detail, stats, None if still else self.cat_tick)
        if shown != self.cat_shown:  # still moods only redraw when something changes; the rest shimmer
            self.cat_shown = shown
            self.query_one("#cat", Static).update(cat.render(
                self.cat_mood, self.cat_tick, self.cat_detail, stats,
                sleepy_stats=not self.busy and self.cat_mood == "sleeping",
                label=self.cat_label, night=self.night))

    # ---- your prompt, pinned on top while you scroll ----

    def _pin_text(self, s):
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append("♡ ", style="#ff8fcf")
        t.append(" ".join(ATTACHED.sub("", s).split()), style="#f7d6ea")
        return t

    def update_pinned(self):
        if not getattr(self, "chat", None):
            return
        pinned = self.query_one("#pinned", Static)
        msg = getattr(self, "last_prompt", None)
        hidden = bool(msg and msg.is_mounted and msg.virtual_region.bottom <= self.chat.scroll_y)
        pinned.set_class(hidden, "show")

    def leave_home(self):
        self.screen.remove_class("home")

    # ---- chat blocks (all called on the app's thread) ----

    def _at_bottom(self):
        return self.chat.scroll_y >= self.chat.max_scroll_y - 1

    def _follow(self, was_at_bottom):
        """Keep the newest text in view, unless you scrolled up to read something."""
        if was_at_bottom:
            self.chat.call_after_refresh(self.chat.scroll_end, animate=False)

    def _mount(self, widget):
        self.leave_home()
        follow = self._at_bottom()
        self.chat.mount(widget)
        self._follow(follow)

    def add_line(self, s, kind="dim"):
        self._close_block()
        self._mount(Static(Text(s), classes=f"line {kind}"))

    def add_tool(self, line):
        """◈ read  harness/agent.py: an icon and verb per kind of tool, the path's file name bright."""
        name, _, arg = line.partition(" ")
        icon, verb, colour = TOOL_LOOK.get(name, ("◆", name, LILAC))
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(f"{icon} ", style=colour)
        t.append(f"{verb:<7}", style=f"bold {colour}")
        if name in ("read_file", "list_files", "edit_file", "write_file") and "/" in arg:
            folder, _, base = arg.rpartition("/")
            t.append(folder + "/", style=DIM)
            t.append(base, style=TEXT)
        else:
            t.append(arg, style=TEXT if name == "run" else "#b9aec7")
        self._close_block()
        self.last_tool = ToolLine(t)
        self._mount(self.last_tool)
        return self.last_tool

    def add_tool_result(self, name, args, result, line=None):
        line = line or getattr(self, "last_tool", None)
        if line is not None:
            line.set_details(tool_details(name, args, result))

    def add_stats(self, s):
        """₊˚✧ 5s ⋆ 89 tok/s ⋆ 356 tokens ✧˚₊ under a finished answer."""
        parts = s.lstrip("✓ ").split(" · ")
        t = Text("₊˚✧ ", style="#ffd6f0")
        for n, part in enumerate(parts):
            if n:
                t.append(" ⋆ ", style=DIM)
            t.append(part, style=(PINK, LILAC, MINT)[n % 3])
        t.append(" ✧˚₊", style="#ffd6f0")
        self.add_line_text(t, "stats")

    def add_line_text(self, t, kind):
        self._close_block()
        self._mount(Static(t, classes=f"line {kind}"))

    def add_user(self, s):
        self._close_block()
        self.last_prompt = Static(Text(s), classes="user")
        self._mount(self.last_prompt)
        self.query_one("#pinned", Static).update(self._pin_text(s))

    def add_diff(self, path, before, after):
        """A change as a card: file name in the border, syntax colours, changed bits glowing."""
        self._close_block()
        diff = ui.diff_rows(before, after, max_rows=24 if not before else 80)
        card = Static(diffview.DiffCard(path, before, after, diff), classes="diff")
        card.border_title = diffview.title(path, diff)
        self._mount(card)

    def add_output(self, output, code):
        """What a !command printed (the last 40 lines)."""
        lines = output.splitlines()
        t = Text()
        if len(lines) > 40:
            t.append(f"… {len(lines) - 40} lines above\n", style=DIM)
        t.append("\n".join(lines[-40:]) or "(no output)", style=DIM)
        if code:
            t.append(f"\nexit code {code}", style=ROSE)
        self._close_block()
        self._mount(Static(t, classes="output"))

    def show_todos(self, items):
        marks = {"done": (MINT, "✓"), "doing": (f"bold {PINK}", "▸"), "pending": (DIM, "○")}
        t = Text()
        for i, item in enumerate(items):
            style, mark = marks.get(item["status"], (DIM, "○"))
            t.append(f"{mark} {item['text']}" + ("\n" if i < len(items) - 1 else ""), style=style)
        self._close_block()
        last = self.chat.children[-1] if self.chat.children else None
        if last is not None and last.has_class("todos"):
            last.update(t)  # same list, new ticks: update it where it is
        else:
            self._mount(Static(t, classes="todos"))

    async def stream_batch(self, batch):
        follow = self._at_bottom()
        for kind, s in batch:
            if kind != self.cur_kind:
                await self._close_block_async()
                self.cur_kind, self.cur_text = kind, ""
                if kind == "think":
                    body = Static("", classes="thinking-text")
                    # shown as it streams, greyed out so it never looks like the answer; click to fold
                    box = Collapsible(body, title="thinking", collapsed=False, classes="thinking")
                    self._mount(box)
                    self.cur_widget = body
                else:
                    self.cur_widget = Markdown(classes="assistant")
                    self._mount(self.cur_widget)
                    self.md_stream = Markdown.get_stream(self.cur_widget)
            self.cur_text += s
            if kind == "think":
                self.cur_widget.update(Text(self.cur_text.strip("\n"), style="italic"))
            else:
                await self.md_stream.write(s)
        self._follow(follow)

    async def end_reply(self):
        await self._close_block_async()

    async def _close_block_async(self):
        if self.md_stream is not None:
            await self.md_stream.stop()
            self.md_stream = None
            # the stream can drop its last piece when stopped, so set the full text once
            await self.cur_widget.update(self.cur_text)
        self.cur_kind = self.cur_widget = None
        self.cur_text = ""

    def _close_block(self):
        if self.md_stream is not None:
            self.call_later(self._close_block_async)
        else:
            self.cur_kind = self.cur_widget = None
            self.cur_text = ""

    def replay(self):
        """Draw a loaded chat: your messages, the answers and the tool lines."""
        self.chat.remove_children()
        results = {m.get("tool_call_id"): m.get("content") for m in self.agent.messages if m.get("role") == "tool"}
        for m in self.agent.messages[1:]:
            role, content = m.get("role"), m.get("content") or ""
            if role == "user":
                if content.startswith("(purr:"):
                    self.add_line("(earlier part of the chat was compacted)", "info")
                elif content.startswith("(I ran this myself:"):
                    self.add_line(content.split("\n", 1)[0], "dim")
                else:
                    self.add_user(ATTACHED.sub("", content))
            elif role == "assistant":
                if content.strip():
                    self._mount(Markdown(content, classes="assistant"))
                for c in m.get("tool_calls") or []:
                    try:
                        args = json.loads(c["function"]["arguments"] or "{}")
                    except ValueError:
                        args = {}
                    name = c["function"]["name"]
                    if name == "todo":
                        continue
                    line = self.add_tool(self.agent.tools.summary(name, args))
                    if c.get("id") in results:
                        self.call_after_refresh(self.add_tool_result, name, args, results[c["id"]], line)
        self.chat.call_after_refresh(self.chat.scroll_end, animate=False)

    # ---- sending messages ----

    def on_prompt_area_submitted(self, event: PromptArea.Submitted):
        if self.menu_items and self._menu_pick(submit=True):
            return
        text = event.text.strip()
        if not text:
            return
        if self.busy and not text.startswith("/"):
            self.notify("still working on the last one (esc stops it)", severity="warning")
            return
        self.prompt.clear()
        self._remember(text)
        if text.startswith("/"):
            self.run_command(text)
        elif text.startswith("!"):
            self.add_user(text)
            self.busy = True
            self.run_shell(text[1:].strip())
        elif self.agent.should_refine(text):
            self.busy = True
            self.run_refine(text)
        else:
            self.add_user(text)
            self.busy = True
            self.run_turn(text)

    # ---- /pr: draft a pull request, you check it, then it's pushed ----

    def start_pr(self):
        from harness import pr
        files = pr.changed_files(self.agent.root)
        if not files:
            self.add_line("no changes to make a pull request from" if files is not None else "not a git repo", "info")
            return
        self.leave_home()
        self.busy = True
        self.draft_pr(files)

    @work(thread=True, exclusive=True)
    def draft_pr(self, files):
        from harness import pr
        self.job = "pr"
        try:
            title, body = pr.draft(self.agent)
        except Exception as e:  # noqa: BLE001
            self.view.note(f"couldn't write the pull request: {e}", "error")
            self.call_from_thread(self._turn_done)
            return
        self.call_from_thread(self._pr_drafted, title, body, files)

    def _pr_drafted(self, title, body, files):
        self.busy = False
        self.view.mood = ("sleeping", "")
        self.set_cat("waiting", "check your pull request")

        def chosen(result):
            self.prompt.focus()
            if not result:
                self.add_line("okay, nothing was pushed", "info")
                self.set_cat("sleeping")
                return
            self.busy = True
            self.push_pr(*result)
        self.push_screen(PRScreen(title, body, files, self.agent.model_name), chosen)

    @work(thread=True, exclusive=True)
    def push_pr(self, title, body):
        from harness import pr
        self.job = "pr"
        self.view.activity("run", "git push · gh pr create")
        try:
            url = pr.create(self.agent, title, body)
            self.call_from_thread(self.add_line_text, Text(f"♡ pull request: {url}", style=f"bold {MINT}"), "info")
        except Exception as e:  # noqa: BLE001
            self.view.note(str(e), "error")
        self.call_from_thread(self._turn_done)

    @work(thread=True, exclusive=True)
    def run_refine(self, text):
        self.job = "refine"
        try:
            better = self.agent.refine(text)
        except Exception as e:  # noqa: BLE001 - fall back to what you wrote
            self.view.note(f"couldn't refine it ({e}), sending yours", "warn")
            better = text
        self.call_from_thread(self._refined, text, better)

    def _refined(self, original, better):
        self.busy = False
        self.view.mood = ("sleeping", "")
        self.set_cat("waiting", "your refined message is ready")

        def chosen(text):
            self.prompt.focus()
            if not text:
                self.prompt.set_text(original)  # cancelled: your message is back in the box
                self.set_cat("sleeping")
                return
            self.add_user(text)
            self.busy = True
            self.run_turn(text)
        self.push_screen(RefineScreen(original, better), chosen)

    @work(thread=True, exclusive=True)
    def run_turn(self, text):
        self.job = "turn"
        try:
            self.agent.turn(text)
        except Exception as e:  # keep the app alive whatever happens in the agent
            self.view.note(f"purr broke: {type(e).__name__}: {e}", "error")
        finally:
            summary = (getattr(self.agent, "turn_stats", None) or {}).get("summary", "")
            self.call_from_thread(self._turn_done, summary.lstrip("✓ "))

    @work(thread=True, exclusive=True)
    def run_shell(self, command):
        self.job = "shell"
        try:
            self.view.activity("run", command)
            output, code = self.agent.shell(command)
            self.call_from_thread(self.add_output, output, code)
        finally:
            self.call_from_thread(self._turn_done)

    @work(thread=True, exclusive=True)
    def run_compact(self):
        self.job = "compact"
        try:
            self.agent.compact()
        except Exception as e:
            self.view.note(f"compacting failed: {e}", "error")
        finally:
            self.call_from_thread(self._turn_done)

    def _turn_done(self, summary=""):
        self.busy = False
        self.job = None
        self.view.mood = ("sleeping", "")
        if self.cat_mood == "oops":
            self.set_cat("oops", self.cat_detail)
        else:
            if summary:
                self.last_stats = summary.split(" · ")
                self.pet["tasks"] = self.pet.get("tasks", 0) + 1
                self.save_pet()
            self.set_cat("happy")
        self.tick()

    def run_command(self, text):
        name, arg = commands.parse(text)
        if self.busy and name not in ("help", "cost", "quit"):
            self.notify("wait until it's done (esc stops it)", severity="warning")
            return
        if name == "models" or (name == "model" and not arg):
            self.open_model_picker()
            return
        if name == "resume" and not arg:
            self.open_session_picker()
            return
        if name == "theme":
            self._picked_theme(arg) if arg else self.open_theme_picker()
            return
        if name == "cat":
            self.cat_command(arg)
            return
        if name == "compact":
            self.busy = True
            self.run_compact()
            return
        result = commands.run(self.agent, text)
        if isinstance(result, dict) and result.get("pr"):
            self.start_pr()
            return
        if name == "mode" and arg in MODE_NAMES:
            self.prompt.placeholder = self.MODE_HINT[arg]
        if result is None:
            self.exit()
            return
        if isinstance(result, dict):  # /init or one of your own commands
            self.add_user(text)
            self.busy = True
            self.run_turn(result["send"])
            return
        if name == "clear":
            self.chat.remove_children()
            self.screen.add_class("home")
            self.set_status("")
        else:
            for kind, line in result:
                self.add_line(line, kind if kind != "dim" else "info")
            if name == "help":
                self.add_line("enter send   ctrl+j new line   esc stop   pgup/pgdn scroll   ctrl+q quit", "dim")
        self.refresh_info()

    # ---- pickers ----

    def open_model_picker(self):
        items = []
        for name, spec in self.config["models"].items():
            label = Text(no_wrap=True, overflow="ellipsis")
            current = name == self.agent.model_name
            label.append("♡ " if current else "  ", style=PINK)
            label.append(name.ljust(18), style=f"bold {LILAC}" if current else TEXT)
            label.append((spec["id"] if spec["id"] != name else "").ljust(22), style=DIM)
            label.append("paid" if spec.get("price") else "local", style=PEACH if spec.get("price") else MINT)
            items.append((name, label))
        self.push_screen(Picker("pick a model", items, self.agent.model_name), self._picked_model)

    def _picked_model(self, name):
        self.prompt.focus()
        if not name or name == self.agent.model_name:
            return
        try:
            self.agent.set_model(name)
        except KeyError as e:
            self.notify(e.args[0], severity="error")
        self.refresh_info()

    # ---- colour themes (tui/themes.py) ----

    THEME_FILE = STATE_DIR / "theme"  # the one you picked last time

    def saved_theme(self):
        try:
            name = self.THEME_FILE.read_text().strip()
        except OSError:
            name = ""
        if name in themes.PALETTES or name == "auto":
            return name
        return self.config.get("theme", themes.DEFAULT)

    def set_theme(self, name):
        """name is a palette or "auto" (day/night)."""
        self.theme_choice = name if name in themes.PALETTES or name == "auto" else themes.DEFAULT
        self.theme = f"purr-{themes.resolve(self.theme_choice)}"
        return self.theme_choice

    def open_theme_picker(self):
        current = self.theme_choice
        items = []
        auto = Text(no_wrap=True)
        auto.append("♡ " if current == "auto" else "  ", style=PINK)
        for key in ("day", "night"):
            auto.append("██", style=themes.PALETTES[themes.AUTO[key]]["panel"])
        auto.append("      auto", style=f"bold {LILAC}" if current == "auto" else TEXT)
        auto.append("  ☀ cotton-candy by day, ☾ bubblegum-night at night", style=DIM)
        items.append(("auto", auto))
        for name, c in themes.PALETTES.items():
            label = Text(no_wrap=True)
            label.append("♡ " if name == current else "  ", style=PINK)
            for key in ("bg", "panel", "line-hi", "scroll"):  # a little swatch of its colours
                label.append("██", style=c[key])
            label.append("  " + name, style=f"bold {LILAC}" if name == current else TEXT)
            items.append((name, label))
        self.push_screen(Picker("pick a theme", items, current), self._picked_theme)

    def _picked_theme(self, name):
        self.prompt.focus()
        if not name:
            return
        if name not in themes.PALETTES and name != "auto":
            self.notify(f"no theme called {name}: auto, {', '.join(themes.PALETTES)}", severity="error")
            return
        self.set_theme(name)
        try:
            self.THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.THEME_FILE.write_text(name)
        except OSError:
            pass
        self.notify(f"theme: {name} ♡", timeout=2)

    def open_session_picker(self):
        items = []
        for s in list_sessions(self.agent.root)[:50]:
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append(f"{s['when']:%d.%m %H:%M}  ", style=DIM)
            label.append(s["title"].ljust(40)[:40], style=TEXT)
            label.append(f"  {s['model']}", style=DIM)
            items.append((str(s["path"]), label))
        self.push_screen(Picker("carry on a chat", items, empty="no earlier chats in this folder"),
                         self._picked_session)

    def _picked_session(self, path):
        self.prompt.focus()
        if path:
            self._load_session(path)

    def _load_session(self, path):
        self.agent.load(path)
        self.replay()
        self.add_line(f"carrying on: {self.agent.title}", "info")
        self.refresh_info()

    # ---- keys ----

    def action_stop(self):
        if self.menu_items:
            self._menu_hide()
        elif self.busy:
            self.agent.stop_flag = True

    # ---- copying: drag over any text to copy it; ctrl+c copies a selection too ----

    def copy_text(self, text):
        """To the clipboard, two ways: through the terminal (OSC 52, kitty allows it) and
        with wl-copy on Wayland, in case the terminal says no."""
        self.copy_to_clipboard(text)
        if shutil.which("wl-copy"):
            try:
                subprocess.run(["wl-copy"], input=text, text=True, timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass
        lines = text.count("\n") + 1
        self.notify(f"copied {lines} line{'s' if lines > 1 else ''}" if lines > 1 else "copied",
                    timeout=1.5)

    def selected_text(self):
        return self.screen.get_selected_text() or self.prompt.selected_text

    def on_text_selected(self, event):
        text = self.screen.get_selected_text()
        if text and text.strip():
            self.copy_text(text)

    def action_stop_or_clear(self):
        text = self.selected_text()
        if text:  # something is selected: ctrl+c copies, like everywhere else
            self.copy_text(text)
            self.screen.clear_selection()
            return
        if self.busy:
            self.agent.stop_flag = True
        else:
            self.prompt.clear()

    # ---- modes: code, ask, chat, create ----

    MODE_LOOK = {"code": ("✎", PINK), "ask": ("◈", LILAC), "chat": ("♡", PEACH), "create": ("✧", MINT)}
    MODE_HINT = {"code": 'ask anything…  "fix the failing test"', "ask": "ask about the code, nothing gets changed…",
                 "chat": "say hi ♡", "create": "let's dream something up ✧"}

    def action_next_mode(self):
        if self.busy:
            return
        modes = list(MODE_NAMES)
        self.set_mode(modes[(modes.index(self.agent.mode) + 1) % len(modes)])

    def set_mode(self, mode):
        self.agent.set_mode(mode)
        self.prompt.placeholder = self.MODE_HINT[mode]
        self.refresh_info()

    def action_scroll_chat(self, direction):
        self.chat.scroll_page_down() if direction > 0 else self.chat.scroll_page_up()

    async def action_quit(self):
        self.closing = True
        if self.agent:
            self.agent.stop_flag = True
        self.exit()

    def prompt_key(self, key):
        """Keys from the message box. Returns True when we used the key."""
        if self.menu_items:
            menu = self.query_one("#menu", OptionList)
            if key == "up":
                menu.action_cursor_up()
            elif key == "down":
                menu.action_cursor_down()
            elif key == "tab":
                self._menu_pick(submit=False)
            elif key == "escape":
                self._menu_hide()
            return True
        area = self.prompt
        row = area.cursor_location[0]
        last_row = area.document.line_count - 1
        # up/down move inside a long message; on its first/last line they go through history
        if key == "up" and row == 0 and self.history:
            self.hist_pos = max(0, self.hist_pos - 1)
        elif key == "down" and row == last_row and self.hist_pos < len(self.history):
            self.hist_pos += 1
        else:
            return False
        area.set_text(self.history[self.hist_pos] if self.hist_pos < len(self.history) else "")
        return True

    # ---- the / and @ menus ----

    def on_text_area_changed(self, event: TextArea.Changed):
        if event.text_area.id == "prompt":
            self._menu_update()

    def _menu_update(self):
        area = self.prompt
        value = area.text
        row, col = area.cursor_location
        before = area.document.get_line(row)[:col]
        items = []
        mention = MENTION_AT_CURSOR.search(before)
        if mention:
            items = self._file_items(mention.group(1))
        elif value.startswith("/") and "\n" not in value:
            name, space, arg = value[1:].partition(" ")
            if not space:
                options = [(c, h, d) for c, (h, d) in commands.COMMANDS.items()]
                options += [(c, "", d) for c, (d, _) in commands.custom(self.agent.root).items()]
                for cmd, hint, desc in options:
                    if cmd.startswith(name.lower()):
                        label = Text(f"/{cmd} {hint}".ljust(16), style=PINK)
                        label.append(desc, style=DIM)
                        items.append({"label": label, "value": f"/{cmd}" + (" " if hint else ""),
                                      "runs": not hint, "kind": "set"})
            elif commands.ALIASES.get(name, name) == "model":
                for m, spec in self.config["models"].items():
                    if m.startswith(arg) and m != arg:
                        label = Text(m.ljust(18), style=PINK)
                        label.append("paid" if spec.get("price") else "local", style=DIM)
                        items.append({"label": label, "value": f"/model {m}", "runs": True, "kind": "set"})
        menu = self.query_one("#menu", OptionList)
        menu.clear_options()
        self.menu_items = items
        if items:
            menu.add_options([Option(item["label"]) for item in items])
            menu.highlighted = 0
            self._place_menu()
            menu.add_class("show")
        else:
            menu.remove_class("show")

    def _file_items(self, query):
        if self.files is None:
            try:
                res = subprocess.run(["rg", "--files"], cwd=self.agent.root, capture_output=True,
                                     text=True, timeout=5)
                files = [f for f in res.stdout.splitlines()[:20000]
                         if "__pycache__" not in f and not f.endswith(JUNK)]
                self.files = sorted(files, key=lambda f: (f.count("/"), f))
            except (OSError, subprocess.TimeoutExpired):
                self.files = []
        if query in self.files:
            return []  # already a whole file name: enter should send, not pick it again
        q = query.lower()
        hits = [f for f in self.files if q in f.lower()]
        # names that start with what you typed first, then the rest
        hits.sort(key=lambda f: (not Path(f).name.lower().startswith(q), len(f)))
        items = []
        for f in hits[:30]:
            label = Text(no_wrap=True, overflow="ellipsis")
            folder, _, name = f.rpartition("/")
            if folder:
                label.append(folder + "/", style=DIM)
            label.append(name, style=PINK)
            items.append({"label": label, "value": f, "runs": False, "kind": "mention", "query": query})
        return items

    def _place_menu(self):
        """Put the menu right above the message box, on top of whatever is there."""
        menu = self.query_one("#menu", OptionList)
        box = self.query_one("#box").region
        rows = min(len(self.menu_items), 10)
        menu.styles.width = box.width
        menu.styles.height = rows
        menu.styles.offset = (box.x, box.y - rows)

    def on_resize(self, event):
        if self.menu_items:
            self.call_after_refresh(self._place_menu)

    def _menu_hide(self):
        self.menu_items = []
        self.query_one("#menu", OptionList).remove_class("show")

    def _menu_pick(self, submit):
        """Use the highlighted menu item. Returns True when the menu used the key."""
        menu = self.query_one("#menu", OptionList)
        area = self.prompt
        item = self.menu_items[menu.highlighted or 0]
        if item["kind"] == "mention":
            row, col = area.cursor_location
            start = col - len(item["query"]) - 1  # the @ and what you typed after it
            area.replace(f"@{item['value']} ", (row, start), (row, col))
            self._menu_hide()
            return True
        if submit and area.text.strip() == item["value"].strip() and not item["runs"]:
            return False
        if submit and item["runs"]:
            area.clear()
            self._menu_hide()
            self._remember(item["value"])
            self.run_command(item["value"])
            return True
        area.set_text(item["value"])
        return True

    def on_collapsible_expanded(self, event):
        # clicking "thinking" shouldn't take the cursor away from the message box
        self.prompt.focus()

    on_collapsible_collapsed = on_collapsible_expanded

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        if event.option_list.id != "menu":
            return
        self.query_one("#menu", OptionList).highlighted = event.option_index
        self._menu_pick(submit=True)
        self.prompt.focus()

    # ---- history (shared with the plain mode) ----

    def _load_history(self):
        try:
            return [line for line in HISTORY.read_text().splitlines() if line and not line.startswith("_HiStOrY")]
        except OSError:
            return []

    def _remember(self, text):
        if "\n" in text:
            return
        if not self.history or self.history[-1] != text:
            self.history.append(text)
            try:
                HISTORY.parent.mkdir(parents=True, exist_ok=True)
                with HISTORY.open("a") as f:
                    f.write(text + "\n")
            except OSError:
                pass
        self.hist_pos = len(self.history)
