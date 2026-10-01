"""The cats who live at the café."""

CATS = [
    {"name": "Mochi", "mood": "sleepy"},
    {"name": "Pixel", "mood": "playful"},
    {"name": "Sakura", "mood": "curious"},
    {"name": "Biscuit", "mood": "sleepy"},
    {"name": "Neon", "mood": "playful"},
]


def awake_cats():
    """Cats you can actually play with right now."""
    return [c for c in CATS if c["mood"] != "sleepy"]
