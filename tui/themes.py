"""purr's colour themes. Each one is a Textual theme: the background colours below fill the
$bg / $panel / ... variables in purr.tcss, and the standard Textual colours (primary,
warning, ...) style what Textual draws itself, like `inline code` and tables in answers.
Pick one with `theme = "..."` in config.toml or /theme inside purr. "auto" (the default)
follows your desktop's day/night switch: cotton-candy by day, bubblegum-night at night.
"""

import json
import time
from pathlib import Path

from textual.theme import Theme

# purr's colours, the same in every theme (only the backgrounds change): every window imports them from here
PINK, LILAC, PEACH, ROSE, MINT, TEXT = "#f5a9d0", "#c8a2f0", "#ffb8c8", "#f0829b", "#96dcaf", "#e9dff2"
DIM, FAINT, CYAN = "#82788f", "#5e5469", "#8fd8e8"

# name -> background colours, darkest to lightest
PALETTES = {
    "plum": {  # the original
        "bg": "#1b1722", "panel": "#262030", "panel-hi": "#2e2639", "deep": "#15101c",
        "popup": "#1d1626", "button": "#2b2038", "shade": "#0c0810", "line": "#3d3349",
        "line-hi": "#4a3a5c", "hover": "#3a2a4c", "scroll": "#6b4f8a",
    },
    "strawberry-milk": {
        "bg": "#22141d", "panel": "#311d2a", "panel-hi": "#3b2333", "deep": "#1a0f16",
        "popup": "#27161f", "button": "#3a2030", "shade": "#0d070b", "line": "#4c2c40",
        "line-hi": "#5e3752", "hover": "#4a293e", "scroll": "#8f5277",
    },
    "lilac-dream": {
        "bg": "#1a1530", "panel": "#261f42", "panel-hi": "#2f2750", "deep": "#130f24",
        "popup": "#1d1836", "button": "#2d2448", "shade": "#08060f", "line": "#3d335e",
        "line-hi": "#4d4175", "hover": "#3a2f5c", "scroll": "#6f5ca8",
    },
    "bubblegum-night": {  # deep like your kitty theme, with pink panels
        "bg": "#120c16", "panel": "#22152b", "panel-hi": "#2c1b36", "deep": "#0b070e",
        "popup": "#170f1c", "button": "#2b1a35", "shade": "#050306", "line": "#3c2346",
        "line-hi": "#4c2d59", "hover": "#3b2149", "scroll": "#7c4092",
    },
    "cotton-candy": {
        "bg": "#1f1626", "panel": "#2e2038", "panel-hi": "#382744", "deep": "#17101d",
        "popup": "#241a2c", "button": "#352640", "shade": "#0a070d", "line": "#4a3558",
        "line-hi": "#5c426d", "hover": "#46315a", "scroll": "#8a62a8",
    },
}
DEFAULT = "auto"
AUTO = {"day": "cotton-candy", "night": "bubblegum-night"}  # what "auto" uses by day / at night
SWITCH_STATE = Path.home() / ".local/state/theme-switch.json"  # your desktop's day/night switch


def is_night():
    """Night if your theme-switch says so; without it, 18:00-06:00."""
    try:
        return json.loads(SWITCH_STATE.read_text())["mode"] == "night"
    except (OSError, ValueError, KeyError, TypeError):
        return not 6 <= time.localtime().tm_hour < 18


def resolve(name):
    """A theme choice ("auto" included) -> the palette to use right now."""
    if name == "auto":
        return AUTO["night" if is_night() else "day"]
    return name if name in PALETTES else AUTO["night" if is_night() else "day"]


def make(name):
    c = PALETTES[name]
    return Theme(
        name=f"purr-{name}", dark=True,
        primary=PINK, secondary=LILAC, accent=PEACH, warning=PEACH, error=ROSE, success=MINT,
        foreground=TEXT, background=c["bg"], surface=c["panel"], panel=c["panel-hi"],
        variables={**c, "pink": PINK, "lilac": LILAC, "peach": PEACH,
                   "border": c["line-hi"], "block-cursor-background": PINK,
                   "input-selection-background": c["line-hi"], "footer-background": c["panel"]},
    )


def colour(app, name):
    """A theme colour (line, line-hi, panel, ...) for Rich tables and bars, in the app's current
    theme: hardcoded, they stayed plum in every other theme."""
    try:
        return app.get_css_variables().get(name) or PALETTES["plum"][name]
    except Exception:  # noqa: BLE001 - not mounted yet: plum's
        return PALETTES["plum"][name]
