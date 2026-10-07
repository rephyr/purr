"""The user's own run of the project's tests. The failure message (their output) is what the user
pastes back."""
import subprocess
import sys
import unittest


class UserRun(unittest.TestCase):
    def test_the_tests(self):
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, "\n$ python3 -m unittest discover -s tests -t .\n" + r.stderr[-3000:])
