"""Files that change behind the model's back between turns (your editor, /undo, a command) are noticed:
the old reads go, the files count as unread, and the model sees what changed. No model is called.

Run: python3 -m unittest discover -s tests -t .
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_bench_fixes import users  # noqa: E402
from tests.test_limits import agent, reply, scripted  # noqa: E402


def chat():
    a = agent()
    a.tools.trust_all = True
    Path(a.root, "app.py").write_text("def greet():\n    return 'hi'\n")
    scripted(a, [reply("", tool=("read_file", {"path": "app.py"})), reply("It says hi."),
                 reply("", tool=("edit_file", {"path": "app.py", "old_text": "'hi'", "new_text": "'hey'"})),
                 reply("ok"), reply("ok"), reply("ok")])
    a.config = {**a.config, "final_check": False}
    a.turn("what does app.py say?")
    return a


class OutsideChangesTest(unittest.TestCase):
    def test_your_edit_between_turns_is_shown_and_must_be_read_again(self):
        a = chat()
        Path(a.root, "app.py").write_text("def greet(name):\n    return 'hi ' + name\n")  # you, in your editor
        a.turn("make it say hey")
        ask = next(u for u in users(a) if "make it say hey" in u)
        self.assertIn("these files changed, not by your edits", ask)
        self.assertIn("+def greet(name):", ask)  # the diff
        tools = [m["content"] for m in a.messages if m["role"] == "tool"]
        self.assertTrue(tools[0].startswith("[an earlier read of"))  # the old read is gone
        self.assertIn("read app.py first", tools[1])  # its edit against the old text is refused
        self.assertEqual(Path(a.root, "app.py").read_text(), "def greet(name):\n    return 'hi ' + name\n")

    def test_undo_counts_too(self):
        a = agent()
        a.tools.trust_all = True
        a.config = {**a.config, "final_check": False}
        Path(a.root, "x.py").write_text("x = 1\n")
        scripted(a, [reply("", tool=("read_file", {"path": "x.py"})),
                     reply("", tool=("edit_file", {"path": "x.py", "old_text": "x = 1", "new_text": "x = 2"})),
                     reply("done"), reply("ok")])
        a.turn("make x 2")
        a.undo()
        a.turn("what is x now?")
        ask = next(u for u in users(a) if "what is x now?" in u)
        self.assertIn("x.py", ask)
        self.assertIn("-x = 2", ask)

    def test_its_own_edits_are_no_news(self):
        a = agent()
        a.tools.trust_all = True
        a.config = {**a.config, "final_check": False}
        Path(a.root, "x.py").write_text("x = 1\n")
        scripted(a, [reply("", tool=("read_file", {"path": "x.py"})),
                     reply("", tool=("edit_file", {"path": "x.py", "old_text": "x = 1", "new_text": "x = 2"})),
                     reply("done"), reply("ok")])
        a.turn("make x 2")
        a.turn("thanks")
        self.assertFalse(any("not by your edits" in u for u in users(a)))
