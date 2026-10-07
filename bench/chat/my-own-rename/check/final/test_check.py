"""price_list, with the user's own rename (sed: YARN -> PRICES, between the turns) kept. A model that
writes from what it read in turn 1 uses YARN, or puts it back."""
import subprocess
import sys
import unittest

from shop import prices
from shop.prices import calc_total, price_list


class Check(unittest.TestCase):
    def test_wool(self):
        self.assertAlmostEqual(calc_total([("wool", 2), ("merino", 1)]), 19.5, places=2)

    def test_price_list_cheapest_first(self):
        self.assertEqual(price_list(), ["cotton 4.00", "wool 6.00", "merino 7.50", "mohair 11.00"])

    def test_the_users_rename_is_kept(self):
        self.assertIsInstance(getattr(prices, "PRICES", None), dict)
        self.assertIn("wool", prices.PRICES)

    def test_the_visible_tests_pass(self):
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
