"""The /stats card: big pixel numbers, a model chart, a day-clock of when you code, and the rest."""

from rich import box
from rich.align import Align
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

PINK, LILAC, MINT, PEACH, CYAN = "#f5a9d0", "#c8a2f0", "#96dcaf", "#ffb8c8", "#8fd8e8"
TEXT, DIM = "#e9dff2", "#82788f"

# a 3-row pixel font for the big numbers
FONT = {
    "0": ("█▀█", "█ █", "▀▀▀"), "1": ("▀█ ", " █ ", "▀▀▀"), "2": ("▀▀█", "█▀▀", "▀▀▀"),
    "3": ("▀▀█", " ▀█", "▀▀▀"), "4": ("█ █", "▀▀█", "  ▀"), "5": ("█▀▀", "▀▀█", "▀▀▀"),
    "6": ("█▀▀", "█▀█", "▀▀▀"), "7": ("▀▀█", "  █", "  ▀"), "8": ("█▀█", "█▀█", "▀▀▀"),
    "9": ("█▀█", "▀▀█", "▀▀▀"), ".": (" ", " ", "▀"), "k": ("█  ", "█▄▀", "▀ ▀"),
    "M": ("█▄ ▄█", "█ ▀ █", "▀   ▀"),
}
BARS = " ▏▎▍▌▋▊▉█"


def short(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        return f"{n // 1000}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def big(s, colour):
    """A number in the pixel font, 3 rows tall."""
    rows = [" ".join(FONT[ch][r] for ch in s if ch in FONT) for r in range(3)]
    return Text("\n".join(rows), style=f"bold {colour}")


def tile(value, label, colour):
    return Panel(Group(Align.center(big(value, colour)), Align.center(Text(label, style=DIM))),
                 box=box.ROUNDED, border_style=colour, padding=(0, 1), width=18)


def bar(n, most, width=18):
    """A smooth bar: whole blocks plus a part-block at the end."""
    eighths = round(n / most * width * 8) if most else 0
    return "█" * (eighths // 8) + (BARS[eighths % 8] if eighths % 8 else "")


def hour_colour(h):
    return CYAN if h < 6 or h >= 22 else PEACH if h < 10 else PINK if h < 18 else LILAC


def clock(hours, rows=3):
    """A column per hour, `rows` tall, coloured night / morning / day / evening."""
    most = max(hours.values(), default=0)
    heights = [round(hours.get(h, 0) / most * rows * 8) if most else 0 for h in range(24)]
    heights = [max(1, x) if hours.get(h) else 0 for h, x in enumerate(heights)]  # 1 chat still shows
    t = Text()
    for row in range(rows - 1, -1, -1):
        for h, x in enumerate(heights):
            fill = min(8, max(0, x - row * 8))
            if fill:
                t.append(" ▁▂▃▄▅▆▇█"[fill], style=hour_colour(h))
            else:
                t.append("·" if row == 0 else " ", style=DIM)
        t.append("\n")
    t.append("0     6     12    18   23", style=DIM)
    return t


def owl(hours):
    if not hours:
        return ""
    h = hours.most_common(1)[0][0]
    return ("night owl 🦉" if h < 5 or h >= 22 else "early bird 🐤" if h < 9
            else "daytime coder ☀" if h < 18 else "evening coder 🌙") + f"  peak {h:02d}:00"


def fact(icon, colour, *parts):
    """A line of facts: a coloured icon, then (text, style) pieces."""
    t = Text(f"{icon} ", style=colour)
    for text, style in parts:
        t.append(text, style=style)
    return t


def render(s, cat=None):
    if not s["chats"]:
        return Text("no chats yet: say something and come back ♡", style=LILAC)
    t = s["tools"]
    tokens = short(s["out"])
    tiles = Table.grid(padding=(0, 1))
    row = [tile(str(s["chats"]), "chats", PINK), tile(tokens, ("≈ " if s["guessed"] else "") + "tokens made", LILAC),
           tile(short(s["added"]), "lines written", MINT), tile(str(s["streak"]), "day streak 🔥", PEACH)]
    face = "^ω^" if (cat or {}).get("pets", 0) >= 5 else "˘ω˘"
    cat_art = Text(f"\n /\\_/\\\n( {face} )\n (\")(\")~", style=PINK)
    tiles.add_row(*row, cat_art)

    models = Table.grid(padding=(0, 1))
    most = s["models"].most_common(1)[0][1]
    colours = (PINK, LILAC, MINT, PEACH, CYAN)
    for n, (name, count) in enumerate(s["models"].most_common(5)):
        models.add_row(Text(name, style=TEXT, no_wrap=True, overflow="ellipsis"),
                       Text(bar(count, most), style=colours[n % len(colours)]), Text(str(count), style=DIM))

    middle = Table.grid(padding=(0, 4))
    middle.add_row(Group(Text("models", style=f"bold {LILAC}"), models),
                   Group(Text("when you code", style=f"bold {LILAC}"), clock(s["hours"]),
                         Text(owl(s["hours"]), style=PEACH)))

    lines = [
        fact("◆", MINT, ("models ", f"bold {LILAC}"), (f"read {t['read_file']} files · ", TEXT),
             (f"made {t['edit_file'] + t['write_file']} edits · ", TEXT), (f"ran {t['run']} commands · ", TEXT),
             (f"+{s['added']:,}", MINT), (" / ", DIM), (f"−{s['removed']:,}", "#f0829b"), (" lines", TEXT)),
        fact("✎", PINK, ("you    ", f"bold {LILAC}"), (f"{s['said']} messages · {s['words']:,} words", TEXT),
             *([(f" · please/thanks ×{s['nice']}", PEACH)] if s["nice"] else []),
             *([(f" · why/ugh ×{s['oops']}", DIM)] if s["oops"] else [])),
    ]
    project, n = s["projects"].most_common(1)[0]
    favs = [("⌂ ", MINT), (f"{project} ({n})", TEXT)]
    if s["files"]:
        f, n = s["files"].most_common(1)[0]
        favs += [("   ✂ ", PINK), (f"{f} ({n} edits)", TEXT)]
    lines.append(fact("★", PEACH, ("faves  ", f"bold {LILAC}"), *favs))
    extra = []
    if len(s["modes"]) > 1:
        extra.append(("modes " + " · ".join(f"{m} {c}" for m, c in s["modes"].most_common()), TEXT))
    extra.append((f"   {s['chats']} chats on {len(s['days'])} days", DIM))
    if s["cost"] >= 0.01:
        extra.append((f"   ${s['cost']:.2f} spent", PEACH))
    else:
        extra.append(("   all free ♡", MINT))
    lines.append(fact("◈", CYAN, ("also   ", f"bold {LILAC}"), *extra))
    if cat:
        lines.append(fact("₍^. .^₎", PINK, (f"{cat.get('name', 'your cat')}", f"bold {PINK}"),
                          (f" got {cat.get('pets', 0)} pets and helped with {cat.get('tasks', 0)} tasks", TEXT)))
    return Group(tiles, Text(""), middle, Text(""), *lines)
