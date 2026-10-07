import subprocess
import sys
import unittest


class Visible(unittest.TestCase):
    def test_the_visible_tests_pass(self):
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
