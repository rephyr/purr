import unittest

from duration import format_duration


class Turn2(unittest.TestCase):
    def test_format(self):
        for seconds, text in {5400: "1h 30m", 45: "45s", 3600: "1h", 0: "0s"}.items():
            self.assertEqual(format_duration(seconds), text, seconds)

    def test_errors_still_start_with_duration(self):
        with self.assertRaises(ValueError) as caught:
            format_duration(-1)
        self.assertTrue(str(caught.exception).startswith("duration: "), str(caught.exception))
