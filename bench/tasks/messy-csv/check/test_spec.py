"""Only the clear prompt asks for test_sales.py."""
import os
import unittest

class Spec(unittest.TestCase):
    def test_they_wrote_tests(self):
        self.assertTrue(os.path.exists("test_sales.py"))
