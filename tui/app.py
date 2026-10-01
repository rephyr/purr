"""purr's full-screen mode, built with Textual.

The agent runs in a background thread so the screen stays responsive. It talks to the
screen through TuiView, which has the same methods as ui.PlainView.
"""

import json
import re
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
from harness.agent import STATE_DIR, Agent, list_sessions

HISTORY = STATE_DIR / "history"

PINK = "#f5a9d0"
LILAC = "#c8a2f0"
DIM = "#82788f"
MINT = "#96dcaf"
ROSE = "#f0829b"
YELLOW = "#f0d28c"
TEXT = "#e9dff2"
DIFF_STYLES = {"add": MINT, "del": ROSE, "hunk": LILAC, "same": DIM}
SPINNER = "✦✧✶✷✸✹✺✹✸✷✶✧"
VERSION = "0.2.0"
ATTACHED = re.compile(r'\n\n<file path="[^"]*">\n.*?\n</file>', re.S)
MENTION_AT_CURSOR = re.compile(r"(?:^|\s)@(\S*)$")
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


class TuiView:
    """Called from the agent's thread. Hands everything over to the app's thread,
    and batches streamed text so the screen isn't redrawn for every single token."""

    FLUSH_EVERY = 0.05

    def __init__(self, app):
        self.app = app
        self.pending = []  # [(kind, text)] not shown yet
        self.last_flush = 0.0

    def _send(self, fn, *args):
        self.app.call_from_thread(fn, *args)

    def _stream(self, kind, s):
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
        self._stream("think", s)

    def text(self, s):
        self._stream("text", s)

    def end_reply(self):
        self.flush()
        self._send(self.app.end_reply)

    def tool(self, line):
        self.flush()
        self._send(self.app.add_line, f"◆ {line}", "tool")

    def diff(self, path, before, after):
        self.flush()
        self._send(self.app.add_diff, ui.diff_lines(path, before, after))

    def note(self, s, kind="dim"):
        self.flush()
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

        self._send(self.app.push_screen, AskScreen(question, allow_always), answered)
        while not done.wait(0.1):
            if self.app.closing:
                return "n", ""
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
        Binding("pageup", "scroll_chat(-1)", show=False),
        Binding("pagedown", "scroll_chat(1)", show=False),
    ]

    def __init__(self, config, folder, model_name, trust=False, resume=False):
        super().__init__()
        self.config, self.folder, self.model_name = config, folder, model_name
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
        self.spin = 0
        self.status_text = ""

    # ---- layout ----

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            yield Static(logo(), id="logo")
            yield VerticalScroll(id="chat")
            with Center(id="dockrow"), Vertical(id="dock"):
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
        self.theme = "rose-pine"
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
        if self.agent.tools.trust_all:
            line.append("  trusting everything", style=YELLOW)
        self.query_one("#model", Static).update(line)

    def set_status(self, s):
        # the agent's status starts with the model name, which the box already shows
        name = self.agent.model_name if self.agent else ""
        self.status_text = s[len(name):].strip() if name and s.startswith(name) else s
        self.tick()

    def tick(self):
        status = self.query_one("#status", Static)
        if self.busy:
            self.spin = (self.spin + 1) % len(SPINNER)
            status.update(Text(f"{SPINNER[self.spin]} working", style=PINK))
        else:
            status.update(Text(self.status_text, style=DIM))

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

    def add_user(self, s):
        self._close_block()
        self._mount(Static(Text(s), classes="user"))

    def add_diff(self, lines):
        self._close_block()
        t = Text()
        for i, (kind, line) in enumerate(lines):
            t.append(line + ("\n" if i < len(lines) - 1 else ""), style=DIFF_STYLES[kind])
        self._mount(Static(t, classes="diff"))

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
                    box = Collapsible(body, title="thinking", collapsed=True, classes="thinking")
                    self._mount(box)
                    self.cur_widget = body
                else:
                    self.cur_widget = Markdown(classes="assistant")
                    self._mount(self.cur_widget)
                    self.md_stream = Markdown.get_stream(self.cur_widget)
            self.cur_text += s
            if kind == "think":
                self.cur_widget.update(Text(self.cur_text))
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
                    self.add_line("◆ " + self.agent.tools.summary(c["function"]["name"], args), "tool")
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
        else:
            self.add_user(text)
            self.busy = True
            self.run_turn(text)

    @work(thread=True, exclusive=True)
    def run_turn(self, text):
        try:
            self.agent.turn(text)
        except Exception as e:  # keep the app alive whatever happens in the agent
            self.view.note(f"purr broke: {type(e).__name__}: {e}", "error")
        finally:
            self.call_from_thread(self._turn_done)

    @work(thread=True, exclusive=True)
    def run_shell(self, command):
        try:
            output, code = self.agent.shell(command)
            self.call_from_thread(self.add_output, output, code)
        finally:
            self.call_from_thread(self._turn_done)

    @work(thread=True, exclusive=True)
    def run_compact(self):
        try:
            self.agent.compact()
        except Exception as e:
            self.view.note(f"compacting failed: {e}", "error")
        finally:
            self.call_from_thread(self._turn_done)

    def _turn_done(self):
        self.busy = False
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
        if name == "compact":
            self.busy = True
            self.run_compact()
            return
        result = commands.run(self.agent, text)
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
            label.append("paid" if spec.get("price") else "local", style=YELLOW if spec.get("price") else MINT)
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

    def action_stop_or_clear(self):
        if self.busy:
            self.agent.stop_flag = True
        else:
            self.prompt.clear()

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
