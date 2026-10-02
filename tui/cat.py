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
    # z's drift up, then a tail twitch before she settles back into the loop
    zs = [("", "  z"), ("   z", "  Z"), ("    Z", "   z"), ("     z", ""), ("", ""),
          ("   z", ""), ("", " z"), ("  z", "")]
    tails = [' (")(")~', ' (")(")~', ' (")(")~', ' (")(")~', ' (")(")~',
             ' (")(") ', ' (")(")~', ' (")(")~']
    return _frames([[(EARS, z0), ("( -ω- )", z1), tail] for (z0, z1), tail in zip(zs, tails)])


def _thinking():
    bubbles = [" .", " . o", " . o O", " . o O", " . o O", " . o O", " . o", " ."]
    faces = ["( •ω• )", "( •ω• )", "( •ω• )", "( -ω- )", "( •ω• )", "( •ω•)", "( •ω• )", "( •ω• )"]
    tails = [SIT, SIT, SIT, SIT, SIT + "~", SIT, SIT, SIT]
    return _frames([[(EARS, b), f, t] for b, f, t in zip(bubbles, faces, tails)])


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


def _grooming():
    """Tidying up: a paw raised and licked clean, then an ear smoothed down."""
    return _frames([
        [(EARS, "  ."), ("( -ω- )/", ""), SIT],
        [(EARS, "   ."), ("( -ω- )/", " ~"), (SIT, "")],
        [(EARS, ""), ("( •ω• )", ""), SIT],
        [(EARS, "  ."), ("( -ω- )", " ~"), SIT],
    ])


def _listening():
    """You're typing: ears up, head tilted, waiting for the rest of the sentence."""
    return _frames([
        [(EARS, ""), "( •ω• )", SIT],
        [(EARS, "  ♪"), ("(•ω• )", ""), SIT],
        [(EARS, ""), "( •ω• )", (SIT, " .")],
        [(EARS, "  ♪"), ("( •ω•)", ""), SIT],
    ])


def _greeting():
    """Hello when purr opens: she waves a paw."""
    return _frames([
        [(EARS, "  ♡"), ("\\( •ω• )/", ""), SIT],
        [(EARS, "   ♡"), ("( •ω• )/", " ~"), (SIT, "")],
        [(EARS, "  ♡"), ("\\( •ω• )/", ""), SIT],
        [(EARS, " ♡"), ("( •ω• )", " ~"), SIT],
    ])


def _yawning():
    """A big yawn now and then while she's idle."""
    return _frames([
        [(EARS, "  o"), ("( •ω• )", ""), SIT],
        [(EARS, "   o"), ("( •o• )", ""), SIT],
        [(EARS, "    o"), ("( •O• )", " ~yawn"), SIT],
        [(EARS, ""), ("( -ω- )", " ~yawn"), SIT],
    ])


def _watching():
    """Idle but alert: watching the room with a slow tail flick."""
    return _frames([
        [(EARS, ""), "( •ω• )", (SIT, " ~")],
        [(EARS, ""), ("(•ω• )", ""), SIT],
        [(EARS, ""), "( •ω• )", (SIT, "  ~")],
        [(EARS, ""), ("( •ω•)", ""), SIT],
    ])


def _purring():
    """Deep purrs when she's really happy: eyes shut, kneading the air."""
    return _frames([
        [(EARS, " ♡ ♡"), ("( -ω- )", " prrr"), (SIT, "~")],
        [(EARS, "  ♡  ♡"), ("( -ω- )", " prrr~"), (SIT, "~ ~")],
        [(EARS, " ♡ ♡"), ("( -ω- )", " prrrr"), (SIT, "~")],
    ])


def _celebrating():
    """A milestone: a bigger confetti burst than 'proud'."""
    return _frames([
        [(EARS, " ✧ ⋆"), ("( ^ω^ )", " ♡ ✧"), (SIT, "  ⋆ ✧")],
        [(EARS, "  ✧ ⋆ ✧"), ("( ^ω^ )", "  ♡  ✧ ⋆"), (SIT, " ⋆  ✧ ⋆")],
        [(EARS, " ✧  ⋆  ✧"), ("( >ω< )", " ♡   ✧  ⋆"), (SIT, "  ✧   ⋆")],
        [(EARS, "  ⋆ ✧"), ("( ^ω^ )", "   ♡ ✧"), (SIT, " ✧ ⋆")],
    ])


def _startled():
    """Stopped or surprised: ears back, eyes wide."""
    return _frames([
        [(EARS, "  !"), ("( OωO )", ""), SIT],
        [(EARS, " !!"), ("( OωO )", " ~"), (SIT, "")],
        [(EARS, "  !"), ("( •ω• )", ""), SIT],
    ])


# mood -> (label, ticks per frame, frames). A tick is 0.12 s.
MOODS = {
    "sleeping": ("napping", 5, _sleeping()),
    "thinking": ("thinking", 3, _thinking()),
    "tidying": ("tidying up", 3, _grooming()),
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
    "listening": ("listening…", 3, _listening()),
    "greeting": ("hello!", 3, _greeting()),
    "yawning": ("yawning", 4, _yawning()),
    "watching": ("watching", 4, _watching()),
    "purring": ("purrr~", 3, _purring()),
    "celebrating": ("milestone!", 2, _celebrating()),
    "startled": ("!?", 3, _startled()),
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
    "listening": ["{name} is listening", "{name} is all ears", "{name} is waiting for the rest"],
    "greeting": ["hello! {name} is here", "{name} says hi", "purr is awake"],
    "yawning": ["{name} is yawning", "{name} is a sleepy loaf", "{name} could nap"],
    "watching": ["{name} is watching", "{name} is keeping an eye out", "{name} is on lookout"],
    "purring": ["purrr~ {name} is blissful", "{name} is kneading the air", "purrr~"],
    "celebrating": ["{name} is celebrating", "milestone! ♡", "look how far we've come"],
    "startled": ["!?", "{name} got a fright", "oh!"],
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
FIREFLIES = ["  ·   ✧", " ·   ✧ ", "  ✧  · ", " ✧   · "]  # drifting in the dark


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
            if night:
                sky = FIREFLIES if (tick // 24) % 2 else STARS  # stars, then fireflies
                extra = sky[(tick // 6) % len(sky)]
            else:
                extra = "   ☀"
        for ch in cat:
            t.append(ch, style=(MOON if ch == "☾" else HOT) if ch in "♡☾" else PINK)
        t.append(extra, style=HOT if "♡" in extra else SUN if "☀" in extra else LILAC)
        t.append(" " * max(1, WIDTH - len(cat) - len(extra)))
        t.append_text(right[row])
        if row < 2:
            t.append("\n")
    return t
