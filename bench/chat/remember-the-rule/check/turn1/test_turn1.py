import unittest

from duration import parse_duration


class Turn1(unittest.TestCase):
    def test_parse(self):
        for text, seconds in {"1h 30m": 5400, "45s": 45, "2h": 7200, "1h5m10s": 3910}.items():
            self.assertEqual(parse_duration(text), seconds, text)

    def test_errors_start_with_duration(self):
        with self.assertRaises(ValueError) as caught:
            parse_duration("5x")
        self.assertTrue(str(caught.exception).startswith("duration: "), str(caught.exception))
