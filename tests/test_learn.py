"""Learn mode: purr writes the boring parts and leaves TODO(you) pieces for the user.

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

from harness.agent import LEARN_NUDGE, LEARN_SHORTEN, system_prompt  # noqa: E402
from tests.test_limits import agent, known, reply, scripted  # noqa: E402


def learner():
    a = agent()
    a.tools.trust_all = True
    a.set_mode("learn")
    return a


class LearnTest(unittest.TestCase):
    def test_prompt_teaches_instead_of_doing(self):
        text = system_prompt("/tmp/x", "m", "ollama", mode="learn")
        self.assertIn("TODO(you)", text)
        self.assertIn("edit_file", text)  # still has every tool
        self.assertNotIn("Do what was asked, no more", text)

    def test_writing_everything_gets_one_nudge(self):
        a = learner()
        (Path(a.root) / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        known(a)
        calls = scripted(a, [reply("", tool=("edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"})),
                             reply("Fixed it!"), reply("It was all boilerplate.")])
        a.turn("teach me to fix add")
        self.assertEqual(sum(m.get("content") == LEARN_NUDGE for m in a.messages), 1)
        self.assertEqual(calls["n"], 3)
        self.assertNotIn("read the user's request again", str(a.messages))  # code mode's check stays out

    def test_leaving_a_todo_is_fine_and_shown(self):
        a = learner()
        (Path(a.root) / "inv.py").write_text("def add_item(items, item):\n    pass\n")
        known(a)
        scripted(a, [reply("", tool=("edit_file", {"path": "inv.py", "old_text": "    pass",
                                                    "new_text": "    # TODO(you): stack same items\n    pass"})),
                     reply("Your turn: inv.py line 2.")])
        a.turn("teach me inventories")
        self.assertNotIn(LEARN_NUDGE, str(a.messages))
        self.assertTrue(any("your turn: inv.py:2" in n for n in a.view.notes))

    def test_a_hint_that_gives_it_all_away_gets_shortened(self):
        a = learner()
        (Path(a.root) / "inv.py").write_text("def add(self, name, count=1):\n    self.slots.append([name, count])\n")
        known(a)
        long_todo = ("    # TODO(you): don't always make a new slot. First look for a slot that\n"
                     "    # already holds this item and add to it, so one item never sits in two\n"
                     "    # slots until the first is full (MAX_STACK = 99 items per slot). If a\n"
                     "    # slot can't hold all of `count`, fill it to 99 and put the rest in a\n"
                     "    # new slot, repeating until nothing is left to place.\n"
                     "    # hint: while count > 0 — find a slot of this name with room, top it\n"
                     "    # up, subtract what you placed; if none has room, append a fresh slot.\n    pass")
        short_todo = "    # TODO(you): add to a slot that already holds this item\n    # hint: look at slot[0]\n    pass"
        calls = scripted(a, [
            reply("", tool=("edit_file", {"path": "inv.py", "old_text": "    self.slots.append([name, count])",
                                          "new_text": long_todo})),
            reply("Your turn!"),
            reply("", tool=("edit_file", {"path": "inv.py", "old_text": long_todo, "new_text": short_todo})),
            reply("Shortened it. Your turn: inv.py line 2.")])
        a.turn("teach me stacking")
        self.assertEqual(calls["n"], 4)
        self.assertIn("is 7 lines long", str(a.messages))
        self.assertEqual(sum(LEARN_SHORTEN[:30] in (m.get("content") or "") for m in a.messages), 1)
        self.assertIn("# hint: look at slot[0]", (Path(a.root) / "inv.py").read_text())

    def test_prompt_has_a_small_hint_example(self):
        text = system_prompt("/tmp/x", "m", "ollama", mode="learn")
        self.assertIn("One idea per piece", text)
        self.assertIn("# hint: loop over self.slots", text)

    def test_open_todos_travel_with_your_message(self):
        a = learner()
        (Path(a.root) / "inv.py").write_text("def add_item(items, item):\n    # TODO(you): stack\n    pass\n")
        scripted(a, [reply("Nice work!")])
        a.turn("done")
        self.assertIn("TODO(you) still in the code: inv.py:2", a.messages[1]["content"])

    def test_code_mode_ignores_todos(self):
        a = agent()
        (Path(a.root) / "inv.py").write_text("# TODO(you): stack\n")
        scripted(a, [reply("hi")])
        a.turn("hello")
        self.assertNotIn("still in the code", a.messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
