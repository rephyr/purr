"""Pair mode: the model takes one small step, then hands the keyboard back; the user's own edits
between turns are shown to the model.

Run: python3 -m unittest discover tests
No model is called.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import PAIR_HAND_BACK, system_prompt  # noqa: E402
from tests.test_limits import agent, known, reply, scripted  # noqa: E402


def pair(files):
    a = agent()
    a.tools.trust_all = True
    for name, text in files.items():
        (Path(a.root) / name).write_text(text)
    a.set_mode("pair")
    return a


class PairTest(unittest.TestCase):
    def test_prompt(self):
        text = system_prompt("/tmp/x", "m", "ollama", mode="pair")
        self.assertIn("take turns", text)
        self.assertIn("edit_file", text)

    def test_one_edit_then_hand_back(self):
        a = pair({"calc.py": "def add(a, b):\n    return a - b\n"})
        known(a)
        calls = scripted(a, [
            reply("", tool=("edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"})),
            # it wants to keep going: purr holds that back
            reply("Fixed add.", tool=("edit_file", {"path": "calc.py", "old_text": "a + b", "new_text": "b + a"})),
            reply("never reached")])
        a.turn("fix add")
        self.assertEqual(calls["n"], 2)
        self.assertIn(PAIR_HAND_BACK, str(a.messages))
        self.assertEqual((Path(a.root) / "calc.py").read_text(), "def add(a, b):\n    return a + b\n")
        self.assertNotIn("tool_calls", a.messages[-1])  # history stays valid: no unanswered calls
        self.assertTrue(any("your move" in n for n in a.view.notes))

    def test_reading_is_free(self):
        a = pair({"calc.py": "x = 1\n"})
        calls = scripted(a, [reply("", tool=("read_file", {"path": "calc.py"})),
                             reply("", tool=("grep", {"pattern": "x"})),
                             reply("Plan: 1) ... sound right?")])
        a.turn("let's add a feature")
        self.assertEqual(calls["n"], 3)
        self.assertNotIn(PAIR_HAND_BACK, str(a.messages))

    def test_your_edits_reach_the_model(self):
        a = pair({"calc.py": "def add(a, b):\n    return a - b\n"})
        scripted(a, [reply("ok")])
        a.turn("hi")
        (Path(a.root) / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (Path(a.root) / "new.py").write_text("print('hi')\n")
        a.turn("I fixed add, have a look")
        msg = a.messages[-2]["content"]
        self.assertIn("the user changed these files", msg)
        self.assertIn("+    return a + b", msg)
        self.assertIn("new file new.py", msg)

    def test_its_own_edits_are_not_yours(self):
        a = pair({"calc.py": "def add(a, b):\n    return a - b\n"})
        known(a)
        scripted(a, [reply("", tool=("edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"})),
                     reply("Fixed add.")])
        a.turn("fix add")
        scripted(a, [reply("ok")])
        a.turn("go")
        self.assertNotIn("the user changed", str(a.messages[-2]["content"]))


if __name__ == "__main__":
    unittest.main()
