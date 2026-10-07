import datetime
import unittest

from cafe.cats import cat_of_the_day
from cafe.receipt import receipt


class CatOfTheDayTest(unittest.TestCase):
    def test_awake_same_all_day_and_never_pixel(self):
        for n in range(30):
            day = datetime.date(2026, 1, 1) + datetime.timedelta(days=n)
            cat = cat_of_the_day(day)
            self.assertNotEqual(cat["mood"], "sleepy")
            self.assertNotEqual(cat["name"], "Pixel")
            self.assertEqual(cat_of_the_day(day), cat)

    def test_receipt_line(self):
        self.assertEqual(receipt(["latte"]).splitlines()[-1], f"cat of the day: {cat_of_the_day()['name']} ♡")


if __name__ == "__main__":
    unittest.main()
