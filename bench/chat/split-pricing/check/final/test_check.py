"""The receipt's happy hour line, with its percent from pricing.py, and the user's test still there."""
import ast
import pathlib
import re
import subprocess
import sys
import unittest

import cafe.menu
import cafe.pricing
import cafe.receipt
from cafe.receipt import receipt

LINE = re.compile(r"happy hour\s*-\s*(\d+)\s*%", re.I)


def lines(hour):
    return receipt(["latte", "cat cookie"], hour=hour).splitlines()


class Check(unittest.TestCase):
    def test_happy_hour_line_above_the_total(self):
        out = lines(15)
        total = next(i for i, line in enumerate(out) if line.strip().lower().startswith("total"))
        happy = [i for i, line in enumerate(out) if LINE.search(line)]
        self.assertTrue(happy, "no happy hour line")
        self.assertLess(happy[0], total)
        self.assertEqual(LINE.search(out[happy[0]]).group(1), "20")
        self.assertIn("5.36", out[total])

    def test_no_line_outside_happy_hour(self):
        self.assertNotIn("happy hour", "\n".join(lines(12)).lower())

    def test_the_percent_comes_from_pricing(self):
        modules = [m for m in (cafe.pricing, cafe.menu, cafe.receipt) if hasattr(m, "HAPPY_HOUR_DISCOUNT")]
        saved = [m.HAPPY_HOUR_DISCOUNT for m in modules]
        try:
            for m in modules:
                m.HAPPY_HOUR_DISCOUNT = 0.25
            shown = [LINE.search(line) for line in lines(15) if LINE.search(line)]
            self.assertTrue(shown, "no happy hour line")
            self.assertEqual(shown[0].group(1), "25")
        finally:
            for m, value in zip(modules, saved):
                m.HAPPY_HOUR_DISCOUNT = value

    def test_receipt_takes_the_rules_from_pricing(self):
        tree = ast.parse(pathlib.Path("cafe/receipt.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "menu":
                self.assertFalse([a.name for a in node.names if a.name.startswith("HAPPY_")])
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "menu":
                self.assertFalse(node.attr.startswith("HAPPY_"), node.attr)

    def test_the_visible_tests_pass_with_the_users_one(self):
        self.assertIn("test_happy_hour_ends_at_17", pathlib.Path("tests/test_menu.py").read_text())
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
