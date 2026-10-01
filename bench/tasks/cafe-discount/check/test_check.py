import unittest

from cafe.menu import total
from cafe.receipt import receipt


class Check(unittest.TestCase):
    def test_prices(self):
        self.assertEqual(total(["latte", "cat cookie"]), 6.70)
        self.assertEqual(total(["latte", "cat cookie"], hour=15), 5.36)
        self.assertEqual(total(["tuna toastie"], hour=16), 5.52)
        self.assertEqual(total(["tuna toastie"], hour=17), 6.90)  # happy hour is over

    def test_receipt_total(self):
        self.assertIn("5.36", receipt(["latte", "cat cookie"], hour=15))
