import os
import unittest

from duration import parse_duration


class Check(unittest.TestCase):
    def test_good(self):
        cases = {"1h 30m": 5400, "45s": 45, "2h": 7200, "1h5m10s": 3910, "90m": 5400,
                 "1h 0m 1s": 3601, "10m  5s": 605}
        for text, seconds in cases.items():
            self.assertEqual(parse_duration(text), seconds, text)
            self.assertIsInstance(parse_duration(text), int)

    def test_bad(self):
        for text in ["", "abc", "5x", "1h 1h", "h", "1.5h", "-1h"]:
            with self.assertRaises(ValueError, msg=text):
                parse_duration(text)

    def test_they_wrote_tests(self):
        self.assertTrue(os.path.exists("test_duration.py"))
