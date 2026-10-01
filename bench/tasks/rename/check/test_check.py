import pathlib
import unittest

from shop.basket import Basket
from shop.invoice import invoice
from shop.prices import order_total


class Check(unittest.TestCase):
    def test_new_name_works(self):
        self.assertEqual(order_total([("merino", 2), ("cotton", 1)]), 19.0)
        b = Basket()
        b.add("mohair", 2)
        self.assertEqual(b.total(), 22.0)
        self.assertIn("total: 7.50", invoice([("merino", 1)], "Emilia"))

    def test_old_name_is_gone(self):
        for p in list(pathlib.Path("shop").rglob("*.py")) + list(pathlib.Path("tests").rglob("*.py")):
            self.assertNotIn("calc_total", p.read_text(), str(p))
