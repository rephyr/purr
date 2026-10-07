import unittest

from cafe.menu import total
from cafe.pricing import happy_hour_price
from cafe.receipt import receipt


class TotalTest(unittest.TestCase):
    def test_normal_price(self):
        self.assertEqual(total(["latte", "cat cookie"]), 6.70)

    def test_happy_hour_is_20_percent_off(self):
        self.assertEqual(total(["latte", "cat cookie"], hour=15), 5.36)

    def test_happy_hour_ends_at_17(self):
        self.assertEqual(total(["latte"], hour=17), 4.20)

    def test_happy_hour_price(self):
        self.assertAlmostEqual(happy_hour_price(10, 15), 8.0)
        self.assertEqual(happy_hour_price(10, 12), 10)

    def test_receipt_says_happy_hour(self):
        self.assertIn("happy hour -20%", receipt(["latte"], hour=15))
        self.assertNotIn("happy hour", receipt(["latte"], hour=12))
