import unittest

from shop.basket import Basket
from shop.invoice import invoice
from shop.prices import calc_total


class ShopTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(calc_total([("merino", 2), ("cotton", 1)]), 19.0)

    def test_basket(self):
        b = Basket()
        b.add("mohair", 2)
        self.assertEqual(b.total(), 22.0)

    def test_invoice(self):
        self.assertIn("total: 7.50", invoice([("merino", 1)], "Emilia"))
