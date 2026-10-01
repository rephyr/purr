import datetime
import unittest

from cafe.cats import CATS, cat_of_the_day
from cafe.receipt import receipt


class Check(unittest.TestCase):
    def test_awake_and_the_same_all_day(self):
        for n in range(40):
            day = datetime.date(2026, 1, 1) + datetime.timedelta(days=n)
            cat = cat_of_the_day(day)
            self.assertIn(cat, CATS)
            self.assertNotEqual(cat["mood"], "sleepy")
            self.assertEqual(cat_of_the_day(day), cat)

    def test_not_always_the_same_cat(self):
        days = [datetime.date(2026, 3, 1) + datetime.timedelta(days=n) for n in range(40)]
        self.assertGreater(len({cat_of_the_day(d)["name"] for d in days}), 1)

    def test_default_is_today(self):
        self.assertEqual(cat_of_the_day(), cat_of_the_day(datetime.date.today()))

    def test_receipt_line(self):
        last = receipt(["latte"]).splitlines()[-1]
        self.assertEqual(last, f"cat of the day: {cat_of_the_day()['name']} ♡")

    def test_they_wrote_tests(self):
        import os
        self.assertTrue(os.path.exists("tests/test_cat_of_the_day.py"))
