import unittest

from shop.basket import Basket
from shop.report import packing_list


class Turn1(unittest.TestCase):
    def test_remove(self):
        b = Basket()
        b.add("merino", 3)
        b.add("cotton", 1)
        b.remove("merino", 2)
        self.assertAlmostEqual(b.total(), 11.5, places=2)
        b.remove("cotton")
        self.assertEqual(packing_list(b), ["1 x merino"])  # none left: dropped

    def test_removing_too_many_changes_nothing(self):
        b = Basket()
        b.add("merino", 1)
        with self.assertRaises(ValueError):
            b.remove("merino", 2)
        self.assertEqual(packing_list(b), ["1 x merino"])
