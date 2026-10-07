import unittest

from cafe.menu import total


class Turn1(unittest.TestCase):
    def test_the_failing_test_is_fixed(self):
        self.assertEqual(total(["latte", "cat cookie"], hour=15), 5.36)
