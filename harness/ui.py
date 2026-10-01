"""Colours and printing. Soft pink / lilac on a dark terminal."""

import difflib
import sys


def rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


RESET = "\033[0m"
BOLD = "\033[1m"
PINK = rgb(245, 169, 208)
LILAC = rgb(200, 162, 240)
DIM = rgb(130, 120, 145)
MINT = rgb(150, 220, 175)
ROSE = rgb(240, 130, 155)
YELLOW = rgb(255, 184, 200)  # soft peach-pink (the name is old)


def out(s="", end="\n"):
    sys.stdout.write(s + end)
    sys.stdout.flush()


def say(colour, s):
    out(f"{colour}{s}{RESET}")


def prompt_text():
    # \001 \002 tell readline the colour codes take no space, so editing the line works
    return f"\001{PINK}{BOLD}\002❯ \001{RESET}\002"


def ask(question, allow_always=True):
    """y / n / a(lways). Returns ("y"|"n"|"a", reason)."""
    keys = "[y]es  [n]o" + ("  [a]lways" if allow_always else "")
    try:
        ans = input(f"{YELLOW}  {question}  {keys} {RESET}").strip()
    except EOFError:
        return "n", ""
    low = ans.lower()
    if low in ("y", "yes", ""):
        return "y", ""
    if allow_always and low in ("a", "always"):
        return "a", ""
    if low in ("n", "no"):
        return "n", ""
    # anything else = no, and the words go to the model as the reason
    return "n", ans


def diff_rows(before, after, context=2, max_rows=80):
    """The changes between two texts, ready to draw.

    Returns {"rows", "more", "added", "removed", "new"}. Each row is
    (kind, old line number, new line number, text, changed spans) with kind "same", "add",
    "del" or "gap" (a jump between two changed areas). For a line that was only tweaked,
    the spans [(start, end), ...] mark the characters that changed, so they can glow.
    """
    a, b = before.splitlines(), after.splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    rows, added, removed = [], 0, 0
    for g, group in enumerate(sm.get_grouped_opcodes(context)):
        if g:
            rows.append(("gap", None, None, "", []))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows += [("same", i + 1, j + 1, a[i], []) for i, j in zip(range(i1, i2), range(j1, j2))]
                continue
            dels, adds = list(range(i1, i2)), list(range(j1, j2))
            removed, added = removed + len(dels), added + len(adds)
            spans_a, spans_b = {}, {}
            for i, j in zip(dels, adds):  # a line swapped for a similar one: find what changed
                m = difflib.SequenceMatcher(None, a[i], b[j], autojunk=False)
                if m.ratio() < 0.5:
                    continue
                for op, x1, x2, y1, y2 in m.get_opcodes():
                    if op != "equal":
                        if x2 > x1:
                            spans_a.setdefault(i, []).append((x1, x2))
                        if y2 > y1:
                            spans_b.setdefault(j, []).append((y1, y2))
            rows += [("del", i + 1, None, a[i], spans_a.get(i, [])) for i in dels]
            rows += [("add", None, j + 1, b[j], spans_b.get(j, [])) for j in adds]
    return {"rows": rows[:max_rows], "more": max(0, len(rows) - max_rows),
            "added": added, "removed": removed, "new": not before}


def show_diff(path, before, after):
    d = diff_rows(before, after, max_rows=40 if not before else 80)
    head = "new file" if d["new"] else f"+{d['added']} -{d['removed']}"
    say(LILAC, f"    {path}  {head}")
    width = len(str(max([r[1] or r[2] or 0 for r in d["rows"]] + [1])))
    for kind, old, new, text, _ in d["rows"]:
        if kind == "gap":
            say(DIM, "    " + " " * width + "  ⋯")
            continue
        colour, sign = {"add": (MINT, "+"), "del": (ROSE, "-"), "same": (DIM, " ")}[kind]
        say(colour, f"    {str(new or old).rjust(width)} {sign} {text}")
    if d["more"]:
        say(DIM, f"    … {d['more']} more lines")


def short(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def duration(seconds):
    """8s, 1m 12s, 1h 3m"""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {s % 3600 // 60}m"


class PlainView:
    """How the agent talks to you in the plain terminal mode. The TUI has its own version
    with the same methods (tui/app.py), so the agent doesn't care which one it's using."""

    def __init__(self):
        self.mode = None  # printing "think" or "text" right now

    def thinking(self, s):
        if self.mode != "think":
            out(DIM + "\033[3m", end="")  # grey italics, so thinking never looks like the answer
            self.mode = "think"
        out(s, end="")

    def text(self, s):
        if self.mode != "text":
            out(RESET + ("\n\n" if self.mode == "think" else ""), end="")
            self.mode = "text"
        out(s, end="")

    def end_reply(self):
        if self.mode:
            out(RESET)
        self.mode = None

    def tool(self, line):
        say(LILAC, f"  ◆ {line}")

    def diff(self, path, before, after):
        show_diff(path, before, after)

    def note(self, s, kind="dim"):
        colour = {"dim": DIM, "stats": DIM, "error": ROSE, "warn": YELLOW, "info": LILAC}[kind]
        say(colour, "    " + s if kind == "dim" else "  " + s)

    def ask(self, question, allow_always=True):
        return ask(question, allow_always)

    def status(self, s):
        say(DIM, "  " + s)

    def activity(self, what, detail=""):
        pass  # what the agent is doing ("thinking", a tool name); the TUI's cat shows it

    def tool_result(self, name, args, result):
        pass  # the TUI lets you click a tool line to see this; the session JSON has it too

    def todos(self, items):
        marks = {"done": (MINT, "✓"), "doing": (PINK, "▸"), "pending": (DIM, "○")}
        for item in items:
            colour, mark = marks.get(item["status"], (DIM, "○"))
            say(colour, f"    {mark} {item['text']}")
