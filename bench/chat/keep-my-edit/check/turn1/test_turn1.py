import unittest

from shop.prices import calc_total


class Turn1(unittest.TestCase):
    def test_ten_balls_or_more_get_10_percent_off(self):
        self.assertAlmostEqual(calc_total([("mohair", 12)]), 118.8, places=2)
        self.assertAlmostEqual(calc_total([("merino", 5), ("cotton", 5)]), 51.75, places=2)  # all yarns together
        self.assertAlmostEqual(calc_total([("merino", 9)]), 67.5, places=2)  # 9 balls: full price
