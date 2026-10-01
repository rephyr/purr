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
YELLOW = rgb(240, 210, 140)


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


def diff_lines(path, before, after, max_lines=60):
    """[(kind, line)] with kind "add", "del", "hunk" or "same"."""
    lines = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}", lineterm="", n=2))[2:]
    kinds = {"+": "add", "-": "del", "@": "hunk"}
    result = [(kinds.get(line[:1], "same"), line) for line in lines[:max_lines]]
    if len(lines) > max_lines:
        result.append(("same", f"... {len(lines) - max_lines} more lines"))
    return result


def show_diff(path, before, after):
    colours = {"add": MINT, "del": ROSE, "hunk": LILAC, "same": DIM}
    for kind, line in diff_lines(path, before, after):
        say(colours[kind], "    " + line)


def short(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


class PlainView:
    """How the agent talks to you in the plain terminal mode. The TUI has its own version
    with the same methods (tui/app.py), so the agent doesn't care which one it's using."""

    def __init__(self):
        self.mode = None  # printing "think" or "text" right now

    def thinking(self, s):
        if self.mode != "think":
            out(DIM, end="")
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
        colour = {"dim": DIM, "error": ROSE, "warn": YELLOW, "info": LILAC}[kind]
        say(colour, "    " + s if kind == "dim" else "  " + s)

    def ask(self, question, allow_always=True):
        return ask(question, allow_always)

    def status(self, s):
        say(DIM, "  " + s)

    def todos(self, items):
        marks = {"done": (MINT, "✓"), "doing": (PINK, "▸"), "pending": (DIM, "○")}
        for item in items:
            colour, mark = marks.get(item["status"], (DIM, "○"))
            say(colour, f"    {mark} {item['text']}")
