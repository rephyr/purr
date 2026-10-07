"""The cats who live at the café."""

import datetime

CATS = [
    {"name": "Mochi", "mood": "sleepy"},
    {"name": "Pixel", "mood": "playful"},
    {"name": "Sakura", "mood": "curious"},
    {"name": "Biscuit", "mood": "sleepy"},
    {"name": "Neon", "mood": "playful"},
]
LEAVING = {"Pixel"}  # moving to a new home: never the cat of the day


def awake_cats():
    """Cats you can actually play with right now."""
    return [c for c in CATS if c["mood"] != "sleepy"]


def cat_of_the_day(day=None):
    """One awake cat, the same all day (day: a datetime.date, today by default)."""
    day = day or datetime.date.today()
    choices = [c for c in awake_cats() if c["name"] not in LEAVING]
    return choices[day.toordinal() % len(choices)]
