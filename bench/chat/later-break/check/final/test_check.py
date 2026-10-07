"""One line per yarn, remove still right, and nothing the dict broke (packing list, invoice, tests)."""
import subprocess
import sys
import unittest

from shop.basket import Basket
from shop.invoice import invoice
from shop.report import packing_list


def basket():
    b = Basket()
    b.add("merino", 2)
    b.add("cotton")
    b.add("merino")
    return b


class Check(unittest.TestCase):
    def test_one_line_per_yarn(self):
        b = basket()
        self.assertEqual(packing_list(b), ["3 x merino", "1 x cotton"])
        self.assertAlmostEqual(b.total(), 26.5, places=2)

    def test_stored_as_a_dict(self):
        # what the user asked for, not only lines merged when the list is printed (items, or a name of its own)
        self.assertIn({"merino": 3, "cotton": 1}, vars(basket()).values())

    def test_remove(self):
        b = basket()
        b.remove("merino", 2)
        self.assertAlmostEqual(b.total(), 11.5, places=2)

    def test_removing_too_many_changes_nothing(self):
        b = basket()
        with self.assertRaises(ValueError):
            b.remove("cotton", 5)
        self.assertEqual(packing_list(b), ["3 x merino", "1 x cotton"])

    def test_invoice(self):
        self.assertIn("total: 7.50", invoice([("merino", 1)], "Emilia"))

    def test_the_visible_tests_pass(self):
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
