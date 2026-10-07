import unittest

from shop.basket import Basket
from shop.invoice import invoice
from shop.prices import order_total


class ShopTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(order_total([("merino", 2), ("cotton", 1)]), 19.5)

    def test_ten_balls_get_10_percent_off(self):
        self.assertEqual(order_total([("merino", 5), ("cotton", 5)]), 54.0)
        self.assertEqual(order_total([("merino", 9)]), 67.5)

    def test_basket(self):
        b = Basket()
        b.add("mohair", 2)
        self.assertEqual(b.total(), 22.0)

    def test_invoice(self):
        self.assertIn("total: 7.50", invoice([("merino", 1)], "Emilia"))


class AlpacaTest(unittest.TestCase):
    def test_alpaca(self):
        self.assertEqual(order_total([("alpaca", 2)]), 18.0)
