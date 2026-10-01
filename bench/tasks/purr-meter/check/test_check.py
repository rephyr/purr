import unittest

from purr_meter import purr_level


class Check(unittest.TestCase):
    def test_every_level(self):
        expect = {0: "silent", 1: "soft", 2: "soft", 3: "loud", 5: "loud", 6: "motorboat", 40: "motorboat"}
        for pets, level in expect.items():
            self.assertEqual(purr_level(pets), level, pets)
