"""Tests for the cat (tui/cat.py) and the end-of-task stats line.

Run: python3 -m unittest discover tests
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import ui  # noqa: E402
from harness.tools import SCHEMAS  # noqa: E402
from tests.test_limits import agent  # noqa: E402

try:
    from tui import cat
except ImportError:  # rich comes with textual; plain installs may not have it
    cat = None


@unittest.skipIf(cat is None, "needs rich (uv sync)")
class CatTest(unittest.TestCase):
    def test_every_frame_is_three_lines_that_fit(self):
        for mood, (_, speed, frames) in cat.MOODS.items():
            self.assertGreater(speed, 0, mood)
            for frame in frames:
                self.assertEqual(len(frame), 3, mood)
                for body, extra in frame:
                    self.assertLessEqual(len(body + extra), cat.WIDTH, f"{mood}: {body + extra!r}")

    def test_every_tool_has_a_mood(self):
        for s in SCHEMAS:
            name = s["function"]["name"]
            self.assertIn(name, cat.ACTIVITY, f"{name} has no mood in cat.ACTIVITY")
            self.assertIn(cat.ACTIVITY[name], list(cat.MOODS) + [None])

    def test_every_mode_has_a_scene_and_a_colour(self):
        from harness.agent import MODES
        css = (Path(__file__).resolve().parent.parent / "tui/purr.tcss").read_text()
        for mode in MODES:
            self.assertIn(f"mode_{mode}", cat.MOODS, mode)
            self.assertIn(mode, cat.MODE_COLOUR, mode)
            self.assertIn(mode, cat.MODES_WHAT, mode)
            self.assertIn(f".mode-{mode} #box:focus-within", css, mode)
            self.assertIn(f"outer $mode-{mode};", css, mode)  # the colour comes from themes.MODE_COLOUR

    def test_the_stylesheets_use_only_colours_the_theme_has(self):
        import re

        from tui import bench_app, realbench_app, themes
        known = set(themes.make("plum").variables)
        from textual.app import App
        known |= set(App().get_css_variables())
        tui = Path(__file__).resolve().parent.parent / "tui"
        sheets = {"purr.tcss": (tui / "purr.tcss").read_text(), "bench.tcss": (tui / "bench.tcss").read_text(),
                  "bench_app": bench_app.CSS, "realbench_app": realbench_app.CSS}
        for name, css in sheets.items():
            for var in re.findall(r"\$([a-z][a-z0-9-]*)", css):
                self.assertIn(var, known, f"{name}: ${var}")
            if name != "purr.tcss":  # the bench windows: theme variables only, no hex colours
                self.assertNotRegex(css, r"#[0-9a-fA-F]{6}\b", name)

    def test_labels_use_her_name(self):
        self.assertIn("Luna", cat.label_for("exploring", "Luna"))
        self.assertTrue(all("{name}" not in v.format(name="x") for vs in cat.VERBS.values() for v in vs))

    def test_night_follows_the_desktop_switch(self):
        from tui import themes
        f = Path(tempfile.mkdtemp()) / "theme-switch.json"
        old, themes.SWITCH_STATE = themes.SWITCH_STATE, f
        try:
            f.write_text('{"mode": "night"}')
            self.assertTrue(themes.is_night())
            self.assertEqual(themes.resolve("auto"), "bubblegum-night")
            f.write_text('{"mode": "day"}')
            self.assertEqual(themes.resolve("auto"), "cotton-candy")
            self.assertEqual(themes.resolve("plum"), "plum")
        finally:
            themes.SWITCH_STATE = old

    def test_night_swaps_her_bow_for_a_moon(self):
        self.assertIn("☾", cat.render("sleeping", 0, night=True).plain)
        self.assertIn("♡", cat.render("sleeping", 0, night=False).plain.splitlines()[0])

    def test_render_shows_label_and_detail(self):
        text = cat.render("exploring", 0, "harness/agent.py", ["12s", "45 tok/s"]).plain
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("✧ exploring ✧", lines[0])
        self.assertIn("12s  ⋆  45 tok/s", lines[1])
        self.assertIn("harness/agent.py", lines[2])


@unittest.skipIf(cat is None, "needs textual (uv sync)")
class ThemeTest(unittest.TestCase):
    def test_every_theme_has_every_colour_the_stylesheet_uses(self):
        import re
        from tui import themes
        css = (Path(__file__).resolve().parent.parent / "tui/purr.tcss").read_text()
        used = set(re.findall(r"\$([a-z][a-z-]*)", css))
        for name in themes.PALETTES:
            known = set(themes.make(name).variables)
            self.assertFalse(used - known - {"text", "background"}, name)


class DiffRowsTest(unittest.TestCase):
    def test_a_tweaked_line_marks_the_changed_characters(self):
        d = ui.diff_rows("a\nif pets < 2:\nb\n", "a\nif pets <= 2:\nb\n")
        self.assertEqual((d["added"], d["removed"]), (1, 1))
        add = [r for r in d["rows"] if r[0] == "add"][0]
        self.assertEqual(add[2], 2)  # new line number
        self.assertEqual(add[4], [(9, 10)])  # just the "="

    def test_far_apart_changes_get_a_gap(self):
        before = "\n".join(f"line {n}" for n in range(30))
        after = before.replace("line 2\n", "line two\n").replace("line 27", "line twenty-seven")
        self.assertEqual([r[0] for r in ui.diff_rows(before, after)["rows"]].count("gap"), 1)

    def test_new_file(self):
        d = ui.diff_rows("", "x = 1\ny = 2\n")
        self.assertTrue(d["new"])
        self.assertEqual([r[0] for r in d["rows"]], ["add", "add"])

    @unittest.skipIf(cat is None, "needs rich (uv sync)")
    def test_card_keeps_every_line(self):
        from rich.console import Console
        from tui.diffview import DiffCard
        before, after = "def f():\n    return 1\n", "def f():\n    return 2\n"
        console = Console(width=60, record=True)
        console.print(DiffCard("f.py", before, after, ui.diff_rows(before, after)))
        out = console.export_text()
        self.assertIn("return 1", out)
        self.assertIn("return 2", out)


class StatsTest(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(ui.duration(8.7), "8s")
        self.assertEqual(ui.duration(72), "1m 12s")
        self.assertEqual(ui.duration(3780), "1h 3m")

    def test_summary_counts_only_writing_time(self):
        a = agent()
        a.turn_stats = {"start": time.monotonic() - 72, "out": 900, "gen": 20.0}
        self.assertEqual(a.tok_per_s(), 45)
        self.assertEqual(a._turn_summary(), "✓ 1m 12s · 45 tok/s · 900 tokens")

    def test_speed_shows_when_the_reply_came_in_one_piece(self):
        # what Ollama does with a tool call: everything arrives in one chunk at the end
        from tests.test_limits import reply, scripted
        a = agent()
        r = reply("")
        r.update(usage={"completion_tokens": 60}, gen_seconds=0.0, call_seconds=2.0)
        scripted(a, [r])
        a.turn("hi")
        self.assertEqual(a.tok_per_s(), 30)

    def test_no_speed_without_tokens(self):
        a = agent()
        a.turn_stats = {"start": time.monotonic(), "out": 0, "gen": 0.0}
        self.assertIsNone(a.tok_per_s())
        self.assertEqual(a._turn_summary(), "✓ 0s")


if __name__ == "__main__":
    unittest.main()
