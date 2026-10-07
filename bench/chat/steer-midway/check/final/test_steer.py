"""What the steer asked for: never Pixel. The base task's own checks (checks_from_task) grade the rest,
so this scenario measures the steer on an easy task instead of on a hard one most small models fail anyway."""
import datetime
import unittest

from cafe.cats import cat_of_the_day


class Steer(unittest.TestCase):
    def test_never_pixel(self):
        for n in range(90):
            day = datetime.date(2026, 1, 1) + datetime.timedelta(days=n)
            self.assertNotEqual(cat_of_the_day(day)["name"], "Pixel", day)

    def test_still_more_than_one_cat(self):
        days = [datetime.date(2026, 5, 1) + datetime.timedelta(days=n) for n in range(40)]
        self.assertGreater(len({cat_of_the_day(d)["name"] for d in days}), 1)
