import unittest

from cafe.cats import awake_cats


class CatsTest(unittest.TestCase):
    def test_sleepy_cats_are_left_alone(self):
        self.assertNotIn("Mochi", [c["name"] for c in awake_cats()])
