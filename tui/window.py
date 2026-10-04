"""What the two bench windows (tui/bench_app.py, tui/realbench_app.py) share: purr's themes, the
pet's name, Mochi's mood and opening a file or folder."""

import json
import subprocess
import sys
import time

from textual.app import App

from harness.agent import STATE_DIR
from tui import cat, themes


class BenchWindow(App):
    def __init__(self, config):
        super().__init__()
        self.config = config
        for name in themes.PALETTES:  # same colours as purr itself
            self.register_theme(themes.make(name))
        try:
            choice = (STATE_DIR / "theme").read_text().strip()
        except OSError:
            choice = config.get("theme", themes.DEFAULT)
        self.theme = f"purr-{themes.resolve(choice)}"
        try:
            self.pet = json.loads((STATE_DIR / "cat.json").read_text())
        except (OSError, ValueError):
            self.pet = {"name": config.get("cat_name", "Mochi")}
        self.name_ = self.pet.get("name", "Mochi")
        self.cat_mood, self.cat_label, self.cat_detail, self.cat_tick, self.cat_until = "sleeping", "", "", 0, 0.0
        self.night = themes.is_night()

    def cat_label_for(self, mood):
        return cat.label_for(mood, self.name_)

    def mood(self, mood, detail, hold=0.0, label=None):
        if mood != self.cat_mood or label:
            self.cat_tick = 0 if mood != self.cat_mood else self.cat_tick
            self.cat_label = label or self.cat_label_for(mood)
        self.cat_mood, self.cat_detail = mood, detail
        self.cat_until = time.monotonic() + hold if hold else 0.0

    def closing(self):
        """True once the widgets are gone (quitting): timers must not touch them then."""
        return not self.screen.query("#benchcat")

    @staticmethod
    def open_path(path):
        if path:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
