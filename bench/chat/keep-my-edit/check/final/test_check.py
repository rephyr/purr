"""The rename, with the user's own edit (cotton 4.5, alpaca 9.0, an alpaca test) still there."""
import pathlib
import subprocess
import sys
import unittest

from shop.prices import order_total


class Check(unittest.TestCase):
    def test_the_users_new_cotton_price_with_the_discount(self):
        self.assertAlmostEqual(order_total([("cotton", 10)]), 40.5, places=2)

    def test_the_users_alpaca(self):
        self.assertAlmostEqual(order_total([("alpaca", 1)]), 9.0, places=2)

    def test_discount_across_yarns(self):
        self.assertAlmostEqual(order_total([("merino", 5), ("cotton", 5)]), 54.0, places=2)

    def test_old_name_is_gone(self):
        for p in pathlib.Path(".").rglob("*.py"):
            if "_bench_check" not in p.parts:
                self.assertNotIn("calc_total", p.read_text(), str(p))

    def test_the_users_alpaca_test_is_kept(self):
        self.assertIn("alpaca", pathlib.Path("tests/test_shop.py").read_text())

    def test_the_visible_tests_pass(self):
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
