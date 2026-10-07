"""Days in both directions, and the rule from the first turn: every ValueError says "duration: " first,
the one turn 3 brings in (units out of order) too."""
import unittest

from duration import format_duration, parse_duration


class Check(unittest.TestCase):
    def test_parse_with_days(self):
        self.assertEqual(parse_duration("1d 2h"), 93600)
        self.assertEqual(parse_duration("1h5m10s"), 3910)

    def test_format_with_days(self):
        cases = {93600: "1d 2h", 90061: "1d 1h 1m 1s", 5400: "1h 30m", 0: "0s", 86400: "1d"}
        for seconds, text in cases.items():
            self.assertEqual(format_duration(seconds), text, seconds)

    def test_every_error_starts_with_duration(self):
        for call, arg in [(format_duration, -1), (format_duration, 1.5), (parse_duration, ""),
                          (parse_duration, "1d 1d")]:
            with self.assertRaises(ValueError, msg=f"{call.__name__}({arg!r})") as caught:
                call(arg)
            self.assertTrue(str(caught.exception).startswith("duration: "),
                            f"{call.__name__}({arg!r}): {caught.exception}")

    def test_units_out_of_order_follow_the_rule(self):
        for text in ["2h 1d", "30s 1m"]:
            with self.assertRaises(ValueError, msg=text) as caught:
                parse_duration(text)
            self.assertTrue(str(caught.exception).startswith("duration: "), f"{text!r}: {caught.exception}")

    def test_round_trip(self):
        for seconds in [1, 59, 60, 3599, 3600, 86399, 86400, 93600, 200000]:
            self.assertEqual(parse_duration(format_duration(seconds)), seconds, seconds)
