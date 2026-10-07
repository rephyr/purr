"""Tests that passed earlier in a chat and fail now: the model is shown what changed since they passed.
No model is called.

Run: python3 -m unittest discover -s tests -t .
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import TESTS_FAILED  # noqa: E402
from tests.test_limits import agent, reply, scripted  # noqa: E402

TEST = "import unittest\nimport a\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(a.x(), 1)\n"
RUN = ("run", {"command": "python3 -m unittest test_a"})


def write(path, text):
    return reply("", tool=("write_file", {"path": path, "content": text}))


def tools(a):
    return [m["content"] for m in a.messages if m["role"] == "tool"]


class RegressionNoteTest(unittest.TestCase):
    def chat(self):
        a = agent()
        a.tools.trust_all = True
        a.config = {**a.config, "final_check": False}
        Path(a.root, "test_a.py").write_text(TEST)
        return a

    def test_a_later_turn_that_breaks_them_sees_the_diff(self):
        a = self.chat()
        scripted(a, [write("a.py", "def x():\n    return 1\n"), reply("", tool=RUN), reply("done"),
                     write("helper.py", "Y = 2\n"), write("a.py", "from helper import Y\n\ndef x():\n    return Y\n"),
                     reply("", tool=RUN), reply("hm"), reply("ok")])
        a.turn("write x() returning 1, with tests")
        a.turn("now read the value from a helper module")
        failing = tools(a)[-1]
        self.assertIn("FAIL", failing)
        self.assertIn("the tests passed earlier in this chat", failing)
        self.assertIn("+    return Y", failing)
        self.assertIn("+Y = 2", failing)  # a file made after they passed is in it too

    def test_said_once_until_they_pass_again(self):
        a = self.chat()
        scripted(a, [write("a.py", "def x():\n    return 1\n"), reply("", tool=RUN),
                     write("a.py", "def x():\n    return 2\n"), reply("", tool=RUN), reply("", tool=RUN), reply("hm")])
        a.turn("write x()")
        notes = [t for t in tools(a) if "passed earlier in this chat" in t]
        self.assertEqual(len(notes), 1)

    def test_what_counts_as_a_failed_test_run(self):
        self.assertTrue(TESTS_FAILED("python3 -m unittest", "F\nFAIL: test_x (t.T)\n\nFAILED (failures=1)\n[exit code 1]"))
        self.assertTrue(TESTS_FAILED("pytest -q", "1 failed, 3 passed in 0.1s\n[exit code 1]"))
        self.assertFalse(TESTS_FAILED("python3 build.py", "FAILED (failures=1)\n[exit code 1]"))  # not a test run
        self.assertFalse(TESTS_FAILED("pytest", "4 passed\n[exit code 0]"))
        self.assertFalse(TESTS_FAILED("pytest", "ModuleNotFoundError: no module named x\n[exit code 2]"))
