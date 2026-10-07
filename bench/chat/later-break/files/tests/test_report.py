import unittest

from shop.basket import Basket
from shop.report import packing_list


class ReportTest(unittest.TestCase):
    def test_packing_list(self):
        b = Basket()
        b.add("merino", 2)
        b.add("cotton")
        self.assertEqual(packing_list(b), ["2 x merino", "1 x cotton"])
