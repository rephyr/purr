"""How a change looks in the full-screen mode: a card with line numbers, syntax colours,
soft rose/mint rows for removed/added lines, and a brighter glow on the exact characters
that changed. The rows themselves come from harness.ui.diff_rows.
"""

from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.token import Token
from pygments.util import ClassNotFound
from rich.text import Text

# code colours, matched to purr's pink/lilac theme (the most specific token type wins)
PALETTE = {
    Token.Comment: "italic #6f6580",
    Token.Keyword: "#f5a9d0",
    Token.Keyword.Constant: "#ffbfa8",
    Token.Name.Builtin: "#8fd8e8",
    Token.Name.Builtin.Pseudo: "#8fd8e8",
    Token.Name.Function: "#c8a2f0",
    Token.Name.Class: "bold #c8a2f0",
    Token.Name.Decorator: "#c8a2f0",
    Token.Name.Tag: "#f5a9d0",
    Token.Name.Attribute: "#c8a2f0",
    Token.Literal.String: "#96dcaf",
    Token.Literal.String.Doc: "italic #7fae90",
    Token.Literal.Number: "#ffbfa8",
    Token.Operator: "#e7a6c9",
    Token.Operator.Word: "#f5a9d0",
    Token.Punctuation: "#a99bb8",
    Token.Text: "#e9dff2",
    Token: "#e9dff2",
}

ROW = {  # kind -> (row background, glow for changed characters, sign, sign colour, number colour)
    "add": ("#18291f", "#2f7050", "+", "#96dcaf", "#5f8a70"),
    "del": ("#2c1822", "#7a2f4a", "−", "#f0829b", "#9a5a6e"),
    "same": ("", "", " ", "#5e5469", "#4f465c"),
}
GAP = "#5e5469"


def _style(ttype):
    while ttype not in PALETTE:
        ttype = ttype.parent
    return PALETTE[ttype]


def highlight(code, path):
    """{line number: Text} for every line of code, coloured as a whole file (so strings and
    comments that span lines come out right)."""
    try:
        lexer = get_lexer_for_filename(path, stripnl=False, ensurenl=False)
    except ClassNotFound:
        lexer = TextLexer(stripnl=False, ensurenl=False)
    lines, cur = {}, Text()
    n = 1
    for ttype, value in lexer.get_tokens(code):
        parts = value.split("\n")
        for k, part in enumerate(parts):
            if k:
                lines[n], cur, n = cur, Text(), n + 1
            if part:
                cur.append(part, style=_style(ttype))
    lines[n] = cur
    return lines


class DiffCard:
    """A rich renderable: draws the rows to the full width it's given."""

    def __init__(self, path, before, after, diff):
        self.diff = diff
        self.old = highlight(before, path) if before else {}
        self.new = highlight(after, path)
        numbers = [r[1] or r[2] or 0 for r in diff["rows"]]
        self.digits = len(str(max(numbers + [1])))

    def _row(self, kind, old, new, text, spans, width):
        if kind == "gap":
            return Text(" " * (self.digits + 1) + "⋯", style=GAP)
        bg, glow, sign, sign_colour, num_colour = ROW[kind]
        if self.diff["new"]:
            bg = ""  # a new file is all "added": a preview reads better without the tint
        code = (self.new.get(new) if new else self.old.get(old)) or Text(text)
        code = code.copy()
        if code.plain != text:  # lexing disagreed with the diff's lines: fall back to plain
            code = Text(text, style=PALETTE[Token])
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(str(new or old).rjust(self.digits + 1) + " ", style=num_colour)
        t.append(sign + " ", style=f"bold {sign_colour}")
        lead = len(t.plain)
        t.append_text(code)
        t.truncate(width, overflow="ellipsis")
        if bg:  # the row's tint first, then the glow on top of it
            t.pad_right(width - t.cell_len)
            t.stylize(f"on {bg}")
            for start, end in spans:
                t.stylize(f"on {glow}", lead + start, lead + end)
        return t

    def __rich_console__(self, console, options):
        width = options.max_width
        for row in self.diff["rows"]:
            yield self._row(*row, width)
        if self.diff["more"]:
            yield Text(" " * (self.digits + 1) + f"… {self.diff['more']} more lines", style=GAP)


def title(path, diff):
    """The card's top border: file name, and what changed."""
    folder, _, name = path.rpartition("/")
    t = Text()
    if folder:
        t.append(folder + "/", style="#82788f")
    t.append(name, style="bold #e9dff2")
    if diff["new"]:
        t.append("  new file ", style="#96dcaf")
    else:
        t.append(f"  +{diff['added']}", style="#96dcaf")
        t.append(f" −{diff['removed']} ", style="#f0829b")
    return t
