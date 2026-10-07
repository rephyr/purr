import pathlib
import unittest

from cafe.menu import total
from cafe.pricing import happy_hour_price


class Turn1(unittest.TestCase):
    def test_happy_hour_price(self):
        self.assertAlmostEqual(happy_hour_price(10, 15), 8.0, places=2)
        self.assertAlmostEqual(happy_hour_price(10, 12), 10, places=2)

    def test_totals_unchanged(self):
        self.assertEqual(total(["latte", "cat cookie"]), 6.70)
        self.assertEqual(total(["latte", "cat cookie"], hour=15), 5.36)
        self.assertEqual(total(["tuna toastie"], hour=16), 5.52)
        self.assertEqual(total(["tuna toastie"], hour=17), 6.90)

    def test_the_rules_left_menu(self):
        self.assertNotRegex(pathlib.Path("cafe/menu.py").read_text(), r"(?m)^\s*HAPPY_HOUR(_DISCOUNT)?\s*=")
