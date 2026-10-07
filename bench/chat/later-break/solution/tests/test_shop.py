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

    def test_same_yarn_twice_is_one_entry(self):
        b = Basket()
        b.add("merino")
        b.add("merino", 2)
        self.assertEqual(b.items, {"merino": 3})

    def test_remove(self):
        b = Basket()
        b.add("merino", 3)
        b.remove("merino", 2)
        self.assertEqual(b.total(), 7.5)
        b.remove("merino")
        self.assertEqual(b.items, {})

    def test_removing_too_many_changes_nothing(self):
        b = Basket()
        b.add("cotton")
        with self.assertRaises(ValueError):
            b.remove("cotton", 2)
        self.assertEqual(b.items, {"cotton": 1})

    def test_invoice(self):
        self.assertIn("total: 7.50", invoice([("merino", 1)], "Emilia"))
