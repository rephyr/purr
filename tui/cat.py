"""The little cat above the message box. It shows what purr is doing right now.

Every mood is a list of frames, and a frame is three lines of (cat, extra) pairs: the cat
part is drawn pink, the extra bits next to it (z's, bubbles, blocks) lilac. app.py only
picks which frame to show, so to change how the cat looks, edit the frames below and
restart purr.
"""

import random

from rich.text import Text

EARS = " /\\_/\\♡"  # the ♡ is a little bow on the ear
SIT = " > ^ < "
WIDTH = 19  # the cat and its extras; the label starts after this

PINK = "#f5a9d0"
LILAC = "#c8a2f0"
HOT = "#ff8fcf"     # the bow and hearts
SPARKLE = "#ffd6f0"
MOON = "#d9c8ff"
SUN = "#ffc9a8"
DIM = "#82788f"


def _frames(lines_per_frame):
    """[[(cat, extra), (cat, extra), (cat, extra)], ...] with a bare string meaning no extra."""
    return [[line if isinstance(line, tuple) else (line, "") for line in frame] for frame in lines_per_frame]


def _sleeping():
    zs = [("", "  z"), ("   z", "  Z"), ("    Z", "   z"), ("     z", ""), ("", "")]
    return _frames([[(EARS, z0), ("( -ω- )", z1), ' (")(")~'] for z0, z1 in zs])


def _thinking():
    bubbles = [" .", " . o", " . o O", " . o O", " . o O"]
    faces = ["( •ω• )", "( •ω• )", "( •ω• )", "( -ω- )", "( •ω• )"]
    return _frames([[(EARS, b), f, SIT] for b, f in zip(bubbles, faces)])


def _exploring():
    # walks to the right and back, looking where it's going
    out = []
    for pos in [0, 1, 2, 3, 4, 5, 6, 6, 5, 4, 3, 2, 1, 0, 0]:
        right = len(out) < 7
        pad = " " * pos
        face = "(  •ω•)" if right else "(•ω•  )"
        legs = " /   \\ " if pos % 2 else "  | |  "
        out.append([pad + EARS, pad + face, pad + legs])
    return _frames(out)


def _building():
    stacks = ["▂", "▂▄", "▂▄▆", "▂▄▆█", "▂▄▆█", ""]
    out = []
    for i, stack in enumerate(stacks):
        up = i % 2 == 0
        spark = "  ✦" if up else "   ✧"
        out.append([(EARS, spark), ("( •ω• )" + ("/" if up else " "), " " + stack),
                    SIT + ("" if up else "_")])
    return _frames(out)


def _running():
    typed = ["", "▪", "▪▪", "▪▪▪", "▪▪▪", ""]
    out = []
    for i, t in enumerate(typed):
        cursor = "_" if i % 2 == 0 else " "
        out.append([(EARS, "  ╭──────╮"), ("( •ω• )", f"  │$ {(t + cursor):<4}│"),
                    (SIT, "  ╰──────╯")])
    return _frames(out)


def _talking():
    waves = ["  ~", "  ~ ~", "  ~ ~ ~", "  ~ ~ ~"]
    faces = ["( •o• )", "( •ω• )", "( •o• )", "( •ω• )"]
    return _frames([[EARS, (f, w), SIT] for f, w in zip(faces, waves)])


def _waiting():
    return _frames([
        [(EARS, "  ?"), "( °ω° )", (SIT, " ~")],
        [(EARS, "  ?"), "( °ω° )", (SIT, "~")],
        [(EARS, ""), "( °ω° )", (SIT, " ~")],
        [(EARS, ""), "( °ω° )", (SIT, "~")],
    ])


def _happy():
    hearts = [("", "  ♡"), ("  ♡", "   ♡"), ("   ♡", "  ♡"), ("    ♡", ""), ("", "")]
    return _frames([[(EARS, h0), ("( ^ω^ )", h1), SIT + "~"] for h0, h1 in hearts])


def _oops():
    return _frames([
        [(EARS, "  !"), "( >ω< )", SIT],
        [(EARS, ""), "( >ω< )", SIT],
    ])


def _proud():
    """Tests passed: a little confetti burst."""
    return _frames([
        [(EARS, " ✧"), ("( >ω< )", " ♡"), (SIT, " ✧")],
        [(EARS, "  ✧ ⋆"), ("( ^ω^ )", "  ♡ ✧"), (SIT, "  ⋆ ✧")],
        [(EARS, "   ⋆  ✧"), ("( >ω< )", "    ♡  ⋆"), (SIT, "   ✧   ⋆")],
        [(EARS, "    ✧   ⋆"), ("( ^ω^ )", "  ⋆    ♡"), (SIT, "     ⋆")],
    ])


def _sad():
    """Tests failed: one tear rolls down."""
    return _frames([
        [EARS, "( ;ω; )", SIT],
        [EARS, ("( ;ω; )", ""), (SIT, " ,")],
        [EARS, ("( TωT )", ""), (SIT, "  .")],
        [EARS, ("( ;ω; )", ""), SIT],
    ])


def _waking():
    """Stretch and yawn when a task wakes her up."""
    return _frames([
        [EARS, ("( -ω- )", " ~yawn"), SIT],
        [EARS, ("\\( -o- )/", " ~yawn"), SIT],
        [EARS, ("\\( •ω• )/", ""), SIT],
        [(EARS, " ✧"), "( •ω• )", SIT],
    ])


def _petted():
    """Hearts float up and she purrs."""
    return _frames([
        [(EARS, "  ♡"), ("( ^ω^ )", " prr"), SIT + "~"],
        [(EARS, "   ♡ ♡"), ("( -ω- )", " prrr~"), SIT + " ~"],
        [(EARS, " ♡   ♡"), ("( ^ω^ )", " prrrr~"), SIT + "~"],
        [(EARS, "  ♡ ♡ ♡"), ("( -ω- )", " prrr~"), SIT + " ~"],
    ])


# ---- a little scene for each mode, played when you switch to it (in the mode's colour) ----

MODE_COLOUR = {"code": PINK, "ask": LILAC, "learn": "#f0829b", "pair": "#a8b8ff", "plan": "#8fd8e8",
               "chat": "#ffb8c8", "create": "#96dcaf"}

MODE_ART = {
    "code": [  # at her laptop
        [(EARS, " ┌─────┐"), ("( •ω• )", " │ </>_│"), (SIT, " ╘═════╛")],
        [(EARS, " ┌─────┐"), ("( •ω• )", " │ </> │"), (SIT, " ╘═════╛")],
    ],
    "ask": [  # looking closely
        [(EARS, "     ╭─╮"), ("( •ω• )", "━━━━━│?│"), (SIT, "     ╰─╯")],
        [(EARS, "     ╭─╮"), ("( °ω° )", "━━━━━│·│"), (SIT, "     ╰─╯")],
    ],
    "learn": [  # reading a book with you
        [(EARS, "  ╭──┬──╮"), ("( •ω• )", "  │≡≡│≡ │"), (SIT, "  ╰──┴──╯")],
        [(EARS, "  ╭──┬──╮"), ("( -ω- )", "  │≡≡│≡≡│"), (SIT, "  ╰──┴──╯")],
    ],
    "pair": [  # two cats at one desk: the other one is you
        [(EARS, "    /\\_/\\"), ("( •ω• )", " ⇄ ( •ω• )"), (SIT, "    > ^ <")],
        [(EARS, "    /\\_/\\"), ("( ^ω^ )", " ⇄ ( ^ω^ )"), (SIT, "    > ^ <")],
    ],
    "plan": [  # making a list
        [(EARS, "  ┌─┴─┴─┐"), ("( •ω• )", "  │☑ ── │"), (SIT, "  │☐ ── │")],
        [(EARS, "  ┌─┴─┴─┐"), ("( •ω• )", "  │☑ ── │"), (SIT, "  │☑ ── │")],
    ],
    "chat": [  # wants to talk
        [(EARS, "  ╭───────╮"), ("( •o• )", " < meow! │"), (SIT, "  ╰───────╯")],
        [(EARS, "  ╭───────╮"), ("( •ω• )", " < hi! ♡ │"), (SIT, "  ╰───────╯")],
    ],
    "create": [  # painting sparkles
        [(EARS, "   ✧   ⋆"), ("( ^ω^ )", "/  ✦"), (SIT, "  ⋆   ✧")],
        [(EARS, "    ⋆  ✧"), ("( ^ω^ )", " \\ ✧  ✦"), (SIT, "   ✧  ⋆")],
    ],
}

# shown under the label while a mode's scene plays
MODES_WHAT = {"code": "reads, edits and runs things", "ask": "looks around, never changes anything",
              "learn": "she sets it up, you write the key lines", "pair": "you take turns, one step each",
              "plan": "a big model makes tickets, a small one does them", "chat": "just talking, no tools",
              "create": "ideas, names and stories"}

# mood -> (label, ticks per frame, frames). A tick is 0.12 s.
MOODS = {
    "sleeping": ("napping", 5, _sleeping()),
    "thinking": ("thinking", 3, _thinking()),
    "tidying": ("tidying up", 3, _thinking()),
    "exploring": ("exploring", 2, _exploring()),
    "building": ("building", 3, _building()),
    "running": ("running", 3, _running()),
    "talking": ("talking", 3, _talking()),
    "waiting": ("waiting for you", 4, _waiting()),
    "happy": ("done!", 3, _happy()),
    "oops": ("oops", 4, _oops()),
    "proud": ("tests pass!", 2, _proud()),
    "sad": ("tests failed", 4, _sad()),
    "waking": ("waking up", 3, _waking()),
    "petted": ("prrr~", 3, _petted()),
    **{f"mode_{m}": (f"{m} mode", 5, _frames(art)) for m, art in MODE_ART.items()},
}

# what she says she's doing; one is picked at random each time the mood changes
VERBS = {
    "sleeping": ["{name} is napping", "{name} is dreaming of fish", "{name} is a loaf", "{name} is snoozing"],
    "thinking": ["{name} is pondering", "{name} is thinking very hard", "{name} is daydreaming",
                 "{name} is plotting"],
    "tidying": ["{name} is grooming the chat", "{name} is tidying up"],
    "exploring": ["{name} is sniffing around", "{name} is investigating", "{name} is pawing through files",
                  "{name} is on a treasure hunt"],
    "building": ["{name} is kneading code", "{name} is crafting", "{name} is knitting code",
                 "{name} is fixing things with her paws"],
    "running": ["{name} is chasing a command", "{name} is pouncing", "{name} is zooming"],
    "talking": ["{name} is meowing back", "{name} is chatting", "{name} is typing with her paws"],
    "waiting": ["{name} is waiting for you", "{name} is looking at you expectantly",
                "{name} is waiting for pets"],
    "happy": ["all done!", "nailed it!", "done ♡"],
    "oops": ["oopsie", "uh oh"],
    "proud": ["tests pass! so proud", "yay, all green!", "tests pass!"],
    "sad": ["tests failed…", "oh no, red tests", "a little sad"],
    "waking": ["{name} is waking up", "{name} is stretching"],
    "petted": ["prrr~ {name} loves you", "{name} is purring", "prrr~"],
    "mode_code": ["code mode: {name} is at her laptop", "code mode: paws on the keyboard"],
    "mode_ask": ["ask mode: {name} is curious", "ask mode: looking, not touching"],
    "mode_learn": ["learn mode: {name} is studying with you", "learn mode: you write, she helps"],
    "mode_pair": ["pair mode: you and {name}, side by side", "pair mode: one step each"],
    "mode_plan": ["plan mode: {name} is making a list", "plan mode: tickets first"],
    "mode_chat": ["chat mode: {name} wants to talk", "chat mode: just meowing"],
    "mode_create": ["create mode: {name} is dreaming up ideas", "create mode: sparkles everywhere"],
}


def label_for(mood, name):
    return random.choice(VERBS.get(mood) or [MOODS[mood][0]]).format(name=name)

# what the agent says it's doing (activity) -> mood. None keeps the current mood.
ACTIVITY = {
    "thinking": "thinking", "compacting": "tidying",
    "read_file": "exploring", "list_files": "exploring", "grep": "exploring",
    "fetch_url": "exploring", "task": "exploring",
    "edit_file": "building", "write_file": "building",
    "run": "running", "todo": None, "refining": "thinking",
    # tools from purr's MCP servers (servers/)
    "project_overview": "exploring", "code_map": "exploring", "outline": "exploring", "find_symbol": "exploring",
    "related_files": "exploring", "godot_class": "exploring", "python_api": "exploring",
}


def frame_index(mood, tick):
    _, speed, frames = MOODS[mood]
    return (tick // speed) % len(frames)


def _blend(a, b, f):
    a, b = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


def shimmer(text, phase, colours=(PINK, LILAC, "#ffc2e2", PINK)):
    """Text in a pink-lilac gradient; moving the phase makes it glimmer."""
    t = Text()
    for i, ch in enumerate(text):
        x = (i / max(len(text), 1) * 0.6 + phase) % 1.0 * (len(colours) - 1)
        k = int(x)
        t.append(ch, style=f"bold {_blend(colours[k], colours[k + 1], x - k)}")
    return t


STARS = ["  ⋆    ✧", "  ✧    ⋆", "   ⋆     ", "     ✧  ⋆"]  # twinkling at night


def render(mood, tick, detail="", stats=(), sleepy_stats=False, label=None, night=False):
    """Three lines: the cat on the left; on the right a shimmering label, the stats
    (time, tok/s, tokens) and what it's working on. At night her bow is a moon and
    stars twinkle while she sleeps; by day she naps in the sun."""
    default_label, _, frames = MOODS[mood]
    label = label or default_label
    still = mood in ("sleeping", "oops", "sad")
    right = []
    head = Text()
    head.append("✧ ", style=SPARKLE)
    head.append_text(Text(label, style=f"bold {LILAC}") if still else shimmer(label, tick * 0.035))
    head.append(" ✧", style=SPARKLE)
    right.append(head)
    line = Text()
    if stats:
        line.append("♡ ", style=DIM if sleepy_stats else HOT)
        if sleepy_stats:
            line.append("last task  ", style=DIM)
        colours = [PINK, LILAC, "#96dcaf"]
        for n, part in enumerate(stats):
            if n:
                line.append("  ⋆  ", style=DIM)
            line.append(part, style=DIM if sleepy_stats else colours[n % len(colours)])
    right.append(line)
    right.append(Text(detail, style=DIM) if detail else Text())

    t = Text(no_wrap=True, overflow="ellipsis")
    for row, (cat, extra) in enumerate(frames[frame_index(mood, tick)]):
        if night and row == 0:
            cat = cat.replace("♡", "☾")
        if mood == "sleeping" and row == 2:
            extra = STARS[(tick // 6) % len(STARS)] if night else "   ☀"
        for ch in cat:
            t.append(ch, style=(MOON if ch == "☾" else HOT) if ch in "♡☾" else PINK)
        extra_colour = MODE_COLOUR.get(mood[5:], LILAC) if mood.startswith("mode_") else LILAC
        t.append(extra, style=HOT if "♡" in extra else SUN if "☀" in extra else extra_colour)
        t.append(" " * max(1, WIDTH - len(cat) - len(extra)))
        t.append_text(right[row])
        if row < 2:
            t.append("\n")
    return t
