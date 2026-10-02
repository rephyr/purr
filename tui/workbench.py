"""The workbench (ctrl+t or /files): what changed this session, the project's files, a small editor.

Left: the files changed this session (with +/−) above the project tree, where changed files
glow. Right: a summary of the session's changes, or the picked file as code (changed lines
marked) or as a diff against how it was before this session touched it. e edits it right here
(ctrl+s saves); n hands the terminal to your $EDITOR (nvim) until you quit it.
"""

import difflib
import os
import shlex
import subprocess
from pathlib import Path

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DirectoryTree, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from textual.widgets.text_area import TextAreaTheme

from harness import ui
from tui import diffview

PINK, LILAC, MINT, PEACH, ROSE = "#f5a9d0", "#c8a2f0", "#96dcaf", "#ffb8c8", "#f0829b"
TEXT, DIM, FAINT = "#e9dff2", "#82788f", "#5e5469"

# folders nobody wants to browse
HIDDEN = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache",
          ".ruff_cache", ".purr", ".godot", ".import", "dist", "build", ".idea", ".vscode"}
LANGUAGES = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "javascript",
             ".json": "json", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown",
             ".html": "html", ".css": "css", ".sh": "bash", ".zsh": "bash", ".rs": "rust", ".go": "go",
             ".sql": "sql", ".java": "java", ".xml": "xml", ".tcss": "css"}
MAX_LINES = 5000  # longer files are cut in the viewer (the editor and nvim get all of it)

SYNTAX = {  # tree-sitter names -> purr's code colours (the same as the diff cards)
    "keyword": PINK, "keyword.function": PINK, "keyword.return": PINK, "keyword.operator": PINK,
    "conditional": PINK, "repeat": PINK, "include": PINK, "exception": PINK,
    "string": MINT, "string.documentation": "#7fae90", "comment": "#6f6580",
    "number": "#ffbfa8", "float": "#ffbfa8", "boolean": "#ffbfa8", "constant.builtin": "#ffbfa8",
    "function": LILAC, "function.call": LILAC, "method": LILAC, "method.call": LILAC,
    "class": LILAC, "type": LILAC, "type.class": LILAC, "type.builtin": "#8fd8e8",
    "variable.builtin": "#8fd8e8", "operator": "#e7a6c9", "tag": PINK, "heading": f"bold {PINK}",
    "punctuation.bracket": "#a99bb8", "punctuation.delimiter": "#a99bb8", "json.label": LILAC,
    "toml.type": LILAC, "yaml.field": LILAC, "css.property": LILAC, "inline_code": MINT,
}


def read(path):
    """A file's text; "" if it's gone, None if it isn't text."""
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""
    except (UnicodeDecodeError, OSError):
        return None


def changed_lines(before, after):
    """Line numbers (from 1) in `after` that are new or different from `before`."""
    sm = difflib.SequenceMatcher(None, (before or "").splitlines(), after.splitlines(), autojunk=False)
    return {j + 1 for tag, _, _, j1, j2 in sm.get_opcodes() if tag in ("replace", "insert") for j in range(j1, j2)}


def counts(before, after):
    d = ui.diff_rows(before or "", after or "", max_rows=0)
    return d["added"], d["removed"]


class ProjectTree(DirectoryTree):
    """The project's folders; changed files glow mint, folders holding them get a dot."""

    ICON_NODE, ICON_NODE_EXPANDED, ICON_FILE = "▸ ", "▾ ", "  "

    def __init__(self, path, changed, **kwargs):
        self.changed = changed
        self.dirs = {parent for p in changed for parent in p.parents}
        super().__init__(path, **kwargs)

    def filter_paths(self, paths):
        return [p for p in paths if p.name not in HIDDEN and not p.name.endswith((".pyc", ".swp"))]

    def render_label(self, node, base_style, style):
        text = super().render_label(node, base_style, style)
        path = node.data.path.resolve() if node.data else None
        if path in self.changed:
            text.stylize(MINT)
            text.append("  +" if self.changed[path] is None else "  ●", style=MINT)
        elif path in self.dirs and node.allow_expand:
            text.append("  ·", style=MINT)
        return text


class Workbench(Screen):
    BINDINGS = [
        Binding("escape", "back", "back", priority=True),
        Binding("ctrl+s", "save", "save", priority=True),
        Binding("d", "toggle_view", "code/diff"),
        Binding("e", "edit", "edit here"),
        Binding("n", "nvim", "open in nvim"),
        Binding("s", "summary", "summary"),
    ]

    DEFAULT_CSS = """
    Workbench { background: $bg; }
    #wb { height: 1fr; }
    #wb-side { width: 34; max-width: 35%; min-width: 20; padding: 0 1 0 0; }
    #wb-head { height: auto; padding: 0 1; margin: 1 0 0 0; }
    #wb-changed { height: auto; max-height: 12; background: transparent; border: none; padding: 0 0 1 0; }
    #wb-changed > .option-list--option-highlighted { background: $line-hi; }
    #wb-tree { height: 1fr; background: transparent; border-top: solid $line; padding-top: 1; }
    #wb-tree > .tree--cursor { background: $line-hi; }
    #wb-main { width: 1fr; border: round $scroll; border-title-align: left; border-title-color: $lilac;
               border-subtitle-align: right; border-subtitle-color: #82788f; margin: 0 1 0 0; }
    #wb-view, #wb-editor, #wb-tree, #wb-changed {
        scrollbar-size-vertical: 1; scrollbar-size-horizontal: 1;
        scrollbar-background: $bg; scrollbar-background-hover: $bg; scrollbar-background-active: $bg;
        scrollbar-color: $line-hi; scrollbar-color-hover: $scroll; scrollbar-color-active: $scroll;
        scrollbar-corner-color: $bg;
    }
    #wb-view { height: 1fr; }
    #wb-body { padding: 0 1; width: auto; min-width: 100%; }
    #wb-editor { height: 1fr; border: none; display: none; }
    #wb-keys { height: 1; padding: 0 2; color: #82788f; background: $panel; }
    """

    def __init__(self, root, turns, start=None):
        super().__init__()
        self.root = Path(root).resolve()
        self.turns = [{Path(p).resolve(): b for p, b in t.items()} for t in turns if t]
        self.changed = {}
        for turn in self.turns:  # each file's text before its first change this session
            for p, before in turn.items():
                self.changed.setdefault(p, before)
        self.start = Path(start).resolve() if start else None
        self.path, self.view, self.editing, self.saved, self.warned = None, "summary", False, "", False

    def compose(self) -> ComposeResult:
        with Horizontal(id="wb"):
            with Vertical(id="wb-side"):
                yield Static(id="wb-head")
                yield OptionList(id="wb-changed")
                yield ProjectTree(self.root, self.changed, id="wb-tree")
            with Vertical(id="wb-main"):
                with VerticalScroll(id="wb-view"):
                    yield Static(id="wb-body")
                yield TextArea.code_editor("", id="wb-editor", soft_wrap=False)
        yield Static(id="wb-keys")

    def on_mount(self):
        self._fill_changed()
        editor = self.query_one("#wb-editor", TextArea)
        bg = self.app.get_css_variables().get("bg", "#1b1722")
        editor.register_theme(TextAreaTheme(
            "purr", base_style=Style(color=TEXT, bgcolor=bg), gutter_style=Style(color=FAINT, bgcolor=bg),
            cursor_line_style=Style(bgcolor=self.app.get_css_variables().get("panel", "#262030")),
            cursor_line_gutter_style=Style(color=PINK, bgcolor=self.app.get_css_variables().get("panel", "#262030")),
            selection_style=Style(bgcolor="#4a3a5c"), cursor_style=Style(color=bg, bgcolor=PINK),
            syntax_styles={k: Style.parse(v) for k, v in SYNTAX.items()}))
        editor.theme = "purr"
        if self.start and self.start.is_file():
            self.show(self.start, "diff" if self.start in self.changed else "code")
        else:
            self.show_summary()
        (self.query_one("#wb-changed") if self.changed else self.query_one("#wb-tree")).focus()

    # ---- the left side ----

    def _fill_changed(self):
        added = removed = 0
        options = []
        for p, before in sorted(self.changed.items()):
            now = read(p) or ""
            a, r = counts(before, now)
            added, removed = added + a, removed + r
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append("+ " if before is None else "✎ ", style=MINT if before is None else PINK)
            label.append(p.name, style=TEXT)
            label.append(f"  +{a}", style=MINT)
            if before is not None:
                label.append(f" −{r}", style=ROSE)
            options.append(Option(label, id=str(p)))
        head = Text()
        if self.changed:
            head.append(f"✎ changed this session  {len(self.changed)}\n", style=f"bold {LILAC}")
            head.append(f"+{added}", style=MINT)
            head.append(f"  −{removed}", style=ROSE)
            head.append("  lines", style=DIM)
        else:
            head.append("nothing changed yet ♡", style=DIM)
        self.query_one("#wb-head", Static).update(head)
        changed = self.query_one("#wb-changed", OptionList)
        changed.clear_options()
        changed.add_options(options)
        changed.display = bool(options)
        if options and changed.highlighted is None:
            changed.highlighted = 0

    def on_option_list_option_selected(self, event):
        self.show(Path(event.option.id), "diff")

    def on_directory_tree_file_selected(self, event):
        self.show(event.path.resolve(), "code")

    # ---- the right side ----

    def rel(self, p):
        try:
            return str(p.relative_to(self.root))
        except ValueError:
            return str(p)

    def show_summary(self):
        """Everything this session changed, at a glance."""
        self.path, self.view = None, "summary"
        main = self.query_one("#wb-main")
        main.border_title = "₊˚✧ what changed this session ✧˚₊"
        main.border_subtitle = ""
        parts = []
        if not self.changed:
            parts.append(Text("\nNothing changed yet this session.\n\nPick a file on the left to look at it, "
                              "e to edit it here, n to open it in nvim.", style=DIM))
        else:
            rows = [(p, before, *counts(before, read(p) or "")) for p, before in sorted(self.changed.items())]
            total_a, total_r = sum(r[2] for r in rows), sum(r[3] for r in rows)
            head = Text("\n")
            head.append(f"{len(rows)} file{'s' * (len(rows) != 1)}", style=f"bold {TEXT}")
            head.append("  ·  ", style=FAINT)
            head.append(f"+{total_a}", style=f"bold {MINT}")
            head.append(f"  −{total_r}", style=f"bold {ROSE}")
            head.append(f"  ·  {len(self.turns)} turn{'s' * (len(self.turns) != 1)}\n", style=DIM)
            parts.append(head)
            most = max((a + r for *_, a, r in rows), default=1) or 1
            width = max(len(self.rel(p)) for p, *_ in rows)
            for p, before, a, r in rows:
                t = Text(no_wrap=True, overflow="ellipsis")
                t.append("  + " if before is None else "  ✎ ", style=MINT if before is None else PINK)
                t.append(self.rel(p).ljust(width + 2), style=TEXT)
                t.append(f"+{a}".rjust(6), style=MINT)
                t.append(f" −{r}".ljust(7) if before is not None else " new   ", style=ROSE if before is not None else MINT)
                bar = 24 * (a + r) / most
                green = round(bar * a / (a + r)) if a + r else 0
                t.append("█" * green, style=MINT)
                t.append("█" * max(0, round(bar) - green), style=ROSE)
                parts.append(t)
            parts.append(Text("\nturn by turn", style=f"bold {LILAC}"))
            for n, turn in enumerate(self.turns, 1):
                t = Text(f"  {n:>2}  ", style=FAINT)
                t.append(", ".join(self.rel(p) for p in turn), style=TEXT)
                parts.append(t)
        parts.append(Text("\nenter on a file shows its diff · d switches to the whole file", style=FAINT))
        self.query_one("#wb-body", Static).update(Group(*parts))
        self._keys()

    def show(self, path, view=None):
        self.path, self.view = path, view or self.view
        if self.view == "summary":
            self.view = "code"
        text = read(path)
        main = self.query_one("#wb-main")
        title = Text()
        rel = self.rel(path)
        folder, _, name = rel.rpartition("/")
        if folder:
            title.append(folder + "/", style=DIM)
        title.append(name, style=f"bold {TEXT}")
        body = self.query_one("#wb-body", Static)
        if text is None:
            main.border_title, main.border_subtitle = title, ""
            body.update(Text("\n(not a text file)", style=DIM))
            self._keys()
            return
        before = self.changed.get(path, False)
        if before is not False:
            a, r = counts(before, text)
            title.append("  new" if before is None else f"  +{a}", style=MINT)
            if before is not None:
                title.append(f" −{r}", style=ROSE)
        if self.view == "diff" and before is not False:
            diff = ui.diff_rows(before or "", text, max_rows=MAX_LINES)
            body.update(diffview.DiffCard(rel, before or "", text, diff) if diff["rows"]
                        else Text("\n(back the way it was)", style=DIM))
            main.border_subtitle = "diff since the session started · d whole file"
        else:
            self.view = "code"
            body.update(self._code(path, text, changed_lines(before, text) if before is not False else set()))
            main.border_subtitle = ("whole file · ▎ = changed · d diff" if before is not False
                                    else f"{len(text.splitlines())} lines")
        main.border_title = title
        self.query_one("#wb-view").scroll_home(animate=False)
        self._keys()

    def _code(self, path, text, marks):
        lines = text.splitlines()[:MAX_LINES] or [""]
        coloured = diffview.highlight("\n".join(lines), str(path))
        digits = len(str(len(lines)))
        t = Text(no_wrap=True)
        for n in range(1, len(lines) + 1):
            mark = n in marks
            t.append(f"{n:>{digits}} ", style=PINK if mark else FAINT)
            t.append("▎" if mark else " ", style=MINT)
            t.append_text(coloured.get(n, Text()))
            t.append("\n")
        if len(text.splitlines()) > MAX_LINES:
            t.append(f"… {len(text.splitlines()) - MAX_LINES} more lines (e or n to see them all)", style=DIM)
        return t

    def _keys(self, note=""):
        t = Text(no_wrap=True, overflow="ellipsis")
        if note:
            t.append(note + "   ", style=PEACH)
        if self.editing:
            keys = (("ctrl+s", "save"), ("esc", "stop editing"))
        else:
            keys = (("enter", "open"), ("d", "code/diff"), ("e", "edit here"), ("n", "nvim"),
                    ("s", "summary"), ("tab", "switch side"), ("esc", "back to chat"))
        for k, what in keys:
            t.append(k, style=f"bold {TEXT}")
            t.append(f" {what}   ", style=DIM)
        self.query_one("#wb-keys", Static).update(t)

    # ---- actions ----

    def action_toggle_view(self):
        if self.path and self.path in self.changed:
            self.show(self.path, "code" if self.view == "diff" else "diff")

    def action_summary(self):
        if not self.editing:
            self.show_summary()

    def action_edit(self):
        if not self.path or self.editing:
            return
        text = read(self.path)
        if text is None:
            return
        editor = self.query_one("#wb-editor", TextArea)
        editor.load_text(text)
        lang = LANGUAGES.get(self.path.suffix.lower())
        editor.language = lang if lang in editor.available_languages else None
        first = min(changed_lines(self.changed[self.path], text) or {1}) if self.path in self.changed else 1
        editor.move_cursor((first - 1, 0), center=True)
        self.editing, self.saved, self.warned = True, text, False
        self.query_one("#wb-view").display = False
        editor.display = True
        editor.focus()
        self.query_one("#wb-main").border_subtitle = "editing"
        self._keys()

    def action_save(self):
        if not self.editing:
            return
        text = self.query_one("#wb-editor", TextArea).text
        self.path.write_text(text)
        self.saved, self.warned = text, False
        self._fill_changed()
        self._keys(f"saved {self.path.name} ♡")

    def _stop_editing(self):
        self.editing = False
        self.query_one("#wb-editor").display = False
        self.query_one("#wb-view").display = True
        self.show(self.path)
        self.query_one("#wb-tree").focus()

    def action_back(self):
        if not self.editing:
            self.app.pop_screen()
            return
        if self.query_one("#wb-editor", TextArea).text != self.saved and not self.warned:
            self.warned = True
            self._keys("unsaved changes: ctrl+s saves, esc again throws them away")
            return
        self._stop_editing()

    def action_nvim(self):
        """Hand the terminal to your editor ($VISUAL, $EDITOR, else nvim) until you quit it."""
        if not self.path:
            return
        if self.editing:  # keep what was typed here
            self.action_save()
            self.editing = False
            self.query_one("#wb-editor").display = False
            self.query_one("#wb-view").display = True
        text = read(self.path) or ""
        first = min(changed_lines(self.changed[self.path], text) or {1}) if self.path in self.changed else 1
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nvim"
        try:
            with self.app.suspend():
                subprocess.run([*shlex.split(editor), f"+{first}", str(self.path)], cwd=self.root)
        except Exception as e:  # e.g. a terminal that can't hand over control
            self._keys(f"couldn't open {editor}: {e}")
            return
        self.app.refresh()
        self._fill_changed()
        self.query_one("#wb-tree", ProjectTree).reload()
        self.show(self.path)
