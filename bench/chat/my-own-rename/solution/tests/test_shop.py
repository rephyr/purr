import unittest

from shop.basket import Basket
from shop.invoice import invoice
from shop.prices import calc_total, price_list


class ShopTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(calc_total([("merino", 2), ("cotton", 1)]), 19.0)

    def test_wool(self):
        self.assertEqual(calc_total([("wool", 2)]), 12.0)

    def test_price_list(self):
        self.assertEqual(price_list(), ["cotton 4.00", "wool 6.00", "merino 7.50", "mohair 11.00"])

    def test_basket(self):
        b = Basket()
        b.add("mohair", 2)
        self.assertEqual(b.total(), 22.0)

    def test_invoice(self):
        self.assertIn("total: 7.50", invoice([("merino", 1)], "Emilia"))
