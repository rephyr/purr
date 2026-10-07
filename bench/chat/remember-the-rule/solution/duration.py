"""Durations like "1d 2h 30m" to seconds and back. Every ValueError starts with "duration: "."""

import re

UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
PART = re.compile(r"\s*(\d+)\s*([dhms])\s*")


def parse_duration(text):
    """"1h 30m" -> 5400. Spaces are optional; units d, h, m and s, each at most once, biggest first."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("duration: empty")
    seconds, seen, pos = 0, set(), 0
    while pos < len(text):
        m = PART.match(text, pos)
        if not m:
            raise ValueError(f"duration: can't read {text!r}")
        number, unit = m.groups()
        if unit in seen:
            raise ValueError(f"duration: {unit} twice in {text!r}")
        if any(UNITS[u] < UNITS[unit] for u in seen):
            raise ValueError(f"duration: units go biggest first (d h m s), not {text!r}")
        seen.add(unit)
        seconds += int(number) * UNITS[unit]
        pos = m.end()
    return seconds


def format_duration(seconds):
    """5400 -> "1h 30m"; 0 -> "0s"."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
        raise ValueError(f"duration: need a whole number of seconds, 0 or more, not {seconds!r}")
    if seconds == 0:
        return "0s"
    parts = []
    for unit, size in UNITS.items():
        n, seconds = divmod(seconds, size)
        if n:
            parts.append(f"{n}{unit}")
    return " ".join(parts)
