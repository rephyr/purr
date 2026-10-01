import unittest

from purr_meter import purr_level


class PurrTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(purr_level(0), "silent")
        self.assertEqual(purr_level(1), "soft")
        self.assertEqual(purr_level(2), "soft")
        self.assertEqual(purr_level(4), "loud")
        self.assertEqual(purr_level(9), "motorboat")


if __name__ == "__main__":
    unittest.main()
