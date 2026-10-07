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
from tests.test_limits import agent, known, reply, scripted  # noqa: E402

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

    def test_an_undone_turn_is_not_blamed(self):
        a = self.chat()
        Path(a.root, "a.py").write_text("def x():\n    return 1\n")
        known(a)
        scripted(a, [write("a.py", "def x():\n    return 1\n\ndef extra():\n    return 5\n"), reply("", tool=RUN),
                     reply("done"),
                     reply("", tool=RUN), reply("", tool=("read_file", {"path": "a.py"})),
                     write("a.py", "def x():\n    return 2\n"), reply("", tool=RUN), reply("hm"), reply("ok")])
        a.turn("add extra()")
        self.assertEqual(a.undo(), ["a.py"])  # the user takes it back; the tests pass again
        a.turn("now change x()")
        failing = tools(a)[-1]
        self.assertIn("FAIL", failing)
        self.assertNotIn("def extra", failing)  # the user's undo isn't shown as what broke them

    def test_another_test_command_failing_is_not_blamed_on_the_edits(self):
        a = self.chat()
        Path(a.root, "test_b.py").write_text("import unittest\n\nclass T(unittest.TestCase):\n"
                                             "    def test_old(self):\n        self.assertEqual(1, 2)\n")
        scripted(a, [write("a.py", "def x():\n    return 1\n"), reply("", tool=RUN),
                     write("a.py", "# tidy\ndef x():\n    return 1\n"),
                     reply("", tool=("run", {"command": "python3 -m unittest test_b"})), reply("hm")])
        a.turn("write x()")
        failing = tools(a)[-1]
        self.assertIn("FAIL", failing)
        self.assertNotIn("passed earlier in this chat", failing)  # test_a passing says nothing about test_b

    def test_a_narrower_pass_does_not_hide_the_whole_suite_failing(self):
        a = self.chat()
        Path(a.root, "test_b.py").write_text("import unittest\nimport b\n\nclass T(unittest.TestCase):\n"
                                             "    def test_y(self):\n        self.assertEqual(b.y(), 2)\n")
        full = ("run", {"command": "python3 -m unittest"})
        scripted(a, [write("a.py", "def x():\n    return 1\n"), write("b.py", "def y():\n    return 2\n"),
                     reply("", tool=full),  # the whole suite passes
                     write("a.py", "# tidy\ndef x():\n    return 1\n"), reply("", tool=RUN),  # then only test_a
                     write("b.py", "def y():\n    return 3\n"), reply("", tool=full), reply("hm")])
        a.turn("write x() and y()")
        failing = tools(a)[-1]
        self.assertIn("FAIL", failing)
        self.assertIn("passed earlier in this chat, with `python3 -m unittest`", failing)
        self.assertIn("+    return 3", failing)

    def test_the_suite_and_a_narrower_run_failing_in_turns_are_told_once(self):
        a = self.chat()
        full = ("run", {"command": "python3 -m unittest"})
        scripted(a, [write("a.py", "def x():\n    return 1\n"), reply("", tool=full), reply("", tool=RUN),  # both pass
                     write("a.py", "def x():\n    return 2\n"),
                     reply("", tool=full), reply("", tool=RUN), reply("", tool=full), reply("", tool=RUN),
                     reply("hm")])
        a.turn("write x()")
        self.assertEqual(sum("FAIL" in t for t in tools(a)), 4)
        notes = [t for t in tools(a) if "passed earlier in this chat" in t]
        self.assertEqual(len(notes), 1)

    def test_only_the_newest_test_commands_are_remembered(self):
        from harness.agent import GREEN_COMMANDS
        a = self.chat()
        runs = [reply("", tool=("run", {"command": f"python3 -m unittest test_a.T.test_x -k x{i}"}))
                for i in range(GREEN_COMMANDS + 3)]
        scripted(a, [write("a.py", "def x():\n    return 1\n"), *runs, reply("done")])
        a.turn("write x()")
        self.assertEqual(len(a._green), GREEN_COMMANDS)
        self.assertIn(f"python3 -m unittest test_a.T.test_x -k x{GREEN_COMMANDS + 2}", a._green)
        self.assertNotIn("python3 -m unittest test_a.T.test_x -k x0", a._green)

    def test_not_said_after_leaving_code_mode(self):
        a = self.chat()
        scripted(a, [write("a.py", "def x():\n    return 1\n"), reply("", tool=RUN), reply("done"),
                     write("a.py", "def x():\n    # TODO(you)\n    raise NotImplementedError\n"),
                     reply("", tool=RUN), reply("your turn")])
        a.turn("write x()")
        a.set_mode("learn")
        a.turn("let me write x() myself")
        failing = tools(a)[-1]
        self.assertIn("NotImplementedError", failing)
        self.assertNotIn("passed earlier in this chat", failing)  # the stub fails on purpose

    def test_what_counts_as_a_failed_test_run(self):
        self.assertTrue(TESTS_FAILED("python3 -m unittest", "F\nFAIL: test_x (t.T)\n\nFAILED (failures=1)\n[exit code 1]"))
        self.assertTrue(TESTS_FAILED("pytest -q", "1 failed, 3 passed in 0.1s\n[exit code 1]"))
        self.assertFalse(TESTS_FAILED("python3 build.py", "FAILED (failures=1)\n[exit code 1]"))  # not a test run
        self.assertFalse(TESTS_FAILED("pytest", "4 passed\n[exit code 0]"))
        self.assertFalse(TESTS_FAILED("pytest", "ModuleNotFoundError: no module named x\n[exit code 2]"))
