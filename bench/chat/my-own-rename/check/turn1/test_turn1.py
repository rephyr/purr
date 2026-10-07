import unittest

from shop.prices import calc_total


class Turn1(unittest.TestCase):
    def test_wool(self):
        self.assertAlmostEqual(calc_total([("wool", 2)]), 12.0, places=2)
