import unittest

from cafe.menu import total


class TotalTest(unittest.TestCase):
    def test_normal_price(self):
        self.assertEqual(total(["latte", "cat cookie"]), 6.70)

    def test_happy_hour_is_20_percent_off(self):
        self.assertEqual(total(["latte", "cat cookie"], hour=15), 5.36)
