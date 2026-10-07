import unittest

from duration import format_duration, parse_duration


class DurationTest(unittest.TestCase):
    def test_parse(self):
        for text, seconds in {"1h 30m": 5400, "45s": 45, "2h": 7200, "1h5m10s": 3910, "1d 2h": 93600}.items():
            self.assertEqual(parse_duration(text), seconds, text)

    def test_format(self):
        for seconds, text in {5400: "1h 30m", 45: "45s", 3600: "1h", 0: "0s", 93600: "1d 2h"}.items():
            self.assertEqual(format_duration(seconds), text)

    def test_errors_start_with_duration(self):
        for call, arg in [(parse_duration, "5x"), (parse_duration, "1h 1h"), (parse_duration, "2h 1d"),
                          (format_duration, -1)]:
            with self.assertRaises(ValueError) as caught:
                call(arg)
            self.assertTrue(str(caught.exception).startswith("duration: "))


if __name__ == "__main__":
    unittest.main()
