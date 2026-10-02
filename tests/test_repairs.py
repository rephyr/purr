"""purr fixing small model slips by itself (seen in the first benchmark with gpt-oss-32k).

Run: python3 -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_limits import agent, known  # noqa: E402


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.a = agent()
        self.a.tools.trust_all = True
        self.root = Path(self.a.root)
        (self.root / "shop").mkdir()
        (self.root / "shop/prices.py").write_text("def calc_total(items):\n    return 1\n")
        (self.root / "shop/basket.py").write_text("from .prices import calc_total\n")

    def call(self, name, **args):
        known(self.a)  # as if it had read the files first
        return self.a.tools.call(name, json.dumps(args))

    def test_search_means_grep(self):
        self.assertIn("prices.py", self.call("search", pattern="calc_total"))
        self.assertIn("tool search -> grep", self.a.tools.repairs)

    def test_other_harnesses_argument_names(self):
        out = self.call("edit_file", file_path="shop/prices.py", old_string="return 1", new_string="return 2")
        self.assertIn("edited", out)
        self.assertIn("return 2", (self.root / "shop/prices.py").read_text())

    def test_edit_with_only_content_rewrites_the_file(self):
        self.assertIn("wrote", self.call("edit_file", path="shop/basket.py", content="x = 1\n").lower())
        self.assertEqual((self.root / "shop/basket.py").read_text(), "x = 1\n")

    def test_wrong_file_says_where_it_is(self):
        out = self.call("edit_file", path="shop/basket.py", old_text="def calc_total(items):", new_text="x")
        self.assertIn("shop/prices.py (line 1)", out)

    def test_closest_lines_when_slightly_off(self):
        out = self.call("edit_file", path="shop/prices.py", old_text="def calc_totals(items):", new_text="x")
        self.assertIn("closest lines are 1-1", out)
        self.assertIn("def calc_total(items):", out)

    def test_unicode_escapes_are_undone(self):
        (self.root / "re.py").write_text('p = r"(?P<hours>\\d+)"\n')
        out = self.call("edit_file", path="re.py", old_text='(?P\\u003chours\\u003e', new_text="(?P<h>")
        self.assertIn("edited", out)
        self.assertIn("(?P<h>", (self.root / "re.py").read_text())

    def test_unknown_tools_still_show_up(self):
        seen = []
        self.a.tools.view.tool_result = lambda name, args, result: seen.append((name, result))
        self.call("teleport", to="moon")
        self.assertEqual(seen[0][0], "teleport")
        self.assertIn("no tool called", seen[0][1])


if __name__ == "__main__":
    unittest.main()
