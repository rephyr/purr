"""The safety net: a one-shot run that ends with its tests failing where they passed before gets the
files back as they were when they passed. No model is called.

Run: python3 -m unittest discover -s tests -t .
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import TESTS_PASSED  # noqa: E402
from tests.test_bench_fixes import one_shot  # noqa: E402
from tests.test_limits import reply, scripted  # noqa: E402

TEST = "import unittest\nimport a\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(a.x, 1)\n"
RUN = ("run", {"command": "python3 -m unittest test_a"})


def write(path, text):
    return reply("", tool=("write_file", {"path": path, "content": text}))


class SafetyNetTest(unittest.TestCase):
    def run_turn(self, steps, **config):
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False, "review": False, **config}
        scripted(a, steps + [reply("done")])
        a.turn("make a.x 1 and test it")
        return a

    def test_a_late_break_is_put_back(self):
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                           write("a.py", "x = 2\nimport nope\n")])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertIn("tests failed at the end -> files put back to when they last passed", a.tools.repairs)

    def test_files_made_after_it_go_too(self):
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                           write("helper.py", "y = 2\n"), write("a.py", "from helper import y as x\n")])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertFalse(Path(a.root, "helper.py").exists())

    def test_work_that_still_passes_is_kept(self):
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                           write("a.py", "x = 1  # the answer\n")])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1  # the answer\n")
        self.assertFalse(a.tools.repairs)

    def test_never_passed_means_nothing_to_go_back_to(self):
        a = self.run_turn([write("a.py", "x = 2\n"), write("test_a.py", TEST), reply("", tool=RUN)])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 2\n")

    def test_it_can_be_turned_off(self):
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                           write("a.py", "x = 2\n")], safety_net=False)
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 2\n")

    def test_the_run_stops_at_its_time_limit(self):
        a = one_shot(time_limit=600)
        a._begin_turn("x")
        self.assertAlmostEqual(a._stop_at, time.monotonic() + 600, delta=5)

    def test_what_counts_as_passing(self):
        self.assertTrue(TESTS_PASSED("....\n------\nRan 4 tests in 0.01s\n\nOK\n[exit code 0]"))
        self.assertTrue(TESTS_PASSED("===== 12 passed in 0.3s =====\n[exit code 0]"))
        self.assertFalse(TESTS_PASSED("Ran 0 tests in 0.000s\n\nNO TESTS RAN\n[exit code 5]"))
        self.assertFalse(TESTS_PASSED("Ran 0 tests in 0.000s\n\nOK\n[exit code 0]"))
        self.assertFalse(TESTS_PASSED("== 3 passed, 1 failed ==\n[exit code 0]"))  # a pipe hid the code
        self.assertFalse(TESTS_PASSED("Ran 4 tests in 0.01s\n\nFAILED (failures=1)\n[exit code 1]"))
        self.assertFalse(TESTS_PASSED("hello\n[exit code 0]"))


if __name__ == "__main__":
    unittest.main()


class SyntaxNetTest(unittest.TestCase):
    def test_a_file_left_broken_gets_its_last_version_that_compiled(self):
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False}
        scripted(a, [write("a.py", "def f():\n    return 1\n"), write("a.py", "def f():\nreturn 2\n"),
                     write("b.py", "def g(:\n"), reply("I'm out of time.")])
        a.turn("write f")
        self.assertEqual(Path(a.root, "a.py").read_text(), "def f():\n    return 1\n")
        self.assertEqual(Path(a.root, "b.py").read_text(), "def g(:\n")  # never compiled: nothing to go back to
        self.assertIn("a file left with a syntax error -> its last version that compiled", a.tools.repairs)

    def test_chats_keep_what_the_model_wrote(self):
        from tests.test_limits import agent
        a = agent()
        a.tools.trust_all = True
        scripted(a, [write("a.py", "x = 1\n"), write("a.py", "x = (\n"), reply("oops")])
        a.turn("write a.py")
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = (\n")
