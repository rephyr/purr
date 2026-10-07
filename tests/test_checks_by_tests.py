"""The final check picks its words by purr's own test run, the model learns the project's test command,
pasted line numbers are taken out of edits, and checkpoints quote the chat's real request.
No model is called.

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

from harness import agent as agent_module  # noqa: E402
from tests.test_bench_fixes import one_shot, users  # noqa: E402
from tests.test_limits import agent, reply, scripted  # noqa: E402

TEST = "import unittest\nimport a\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(a.x, 1)\n"


def chat_with_tests(x):
    a = agent()
    a.tools.trust_all = True
    Path(a.root, "test_a.py").write_text(TEST)
    scripted(a, [reply("", tool=("write_file", {"path": "a.py", "content": f"x = {x}\n"})),
                 reply("Set x."), reply("Decided: nothing open")])
    a.turn("set x to 1")
    return next(u for u in users(a) if "before you finish" in u)


class FinalCheckByTestsTest(unittest.TestCase):
    def test_green_tests_get_no_probe_but_a_decided_line(self):
        check = chat_with_tests(1)
        self.assertIn("they pass", check)
        self.assertIn('"Decided:"', check)
        self.assertNotIn("python3 -c", check)  # no invented inputs to "check"
        self.assertNotIn("short summary of what you changed", check)  # the summary isn't asked for twice

    def test_red_tests_get_the_failures_to_fix(self):
        check = chat_with_tests(2)
        self.assertIn("THEY FAIL", check)
        self.assertIn("the tests above fail", check)
        self.assertNotIn("python3 -c", check)

    def test_no_tests_keep_the_edge_case_probe(self):
        a = agent()
        a.tools.trust_all = True
        scripted(a, [reply("", tool=("write_file", {"path": "a.py", "content": "x = 1\n"})), reply("Set x."), reply("ok")])
        a.turn("set x to 1")
        check = next(u for u in users(a) if "before you finish" in u)
        self.assertIn("python3 -c", check)

    def test_a_small_model_with_green_tests_gets_no_extra_look(self):
        a = one_shot(time_limit=5400)
        Path(a.root, "test_a.py").write_text(TEST)
        scripted(a, [reply("", tool=("write_file", {"path": "a.py", "content": "x = 1\n"})), reply("done"), reply("ok")])
        a.turn("make a.py with x = 1")
        self.assertFalse(any("one line per requirement" in u for u in users(a)))


class TestCommandTest(unittest.TestCase):
    def test_the_prompt_names_the_test_command(self):
        root = tempfile.mkdtemp()
        Path(root, "test_a.py").write_text(TEST)
        prompt = agent_module.system_prompt(root, "m", "ollama")
        self.assertIn("- Tests: `python3 -m", prompt)
        self.assertNotIn("- Tests:", agent_module.system_prompt(tempfile.mkdtemp(), "m", "ollama"))
        self.assertNotIn("- Tests:", agent_module.system_prompt(root, "m", "ollama", mode="ask"))

    def test_a_test_run_that_found_nothing_names_the_right_command(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "a.py").write_text("x = 1\n")
        Path(a.root, "test_a.py").write_text(TEST)
        out = a.tools.call("run", json.dumps({"command": "python3 -m unittest discover -s tests"}))
        self.assertIn("This project's tests run with `python3 -m", out)
        out = a.tools.call("run", json.dumps({"command": "python3 -m unittest test_a"}))
        self.assertNotIn("purr: no tests ran", out)


class NumberedEditTest(unittest.TestCase):
    def test_line_numbers_pasted_from_read_file_are_taken_out(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "c.py").write_text("a = 1\nb = 2\nc = 3\n")
        a.tools.seen.add(Path(a.root, "c.py").resolve())
        out = a.tools.call("edit_file", json.dumps({"path": "c.py", "old_text": "    2\tb = 2\n    3\tc = 3",
                                                     "new_text": "    2\tb = 20\n    3\tc = 30"}))
        self.assertTrue(out.startswith("edited c.py"), out)
        self.assertEqual(Path(a.root, "c.py").read_text(), "a = 1\nb = 20\nc = 30\n")

    def test_a_file_that_really_has_tabs_after_numbers_matches_as_is(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "t.tsv").write_text("1\tone\n2\ttwo\n")
        a.tools.seen.add(Path(a.root, "t.tsv").resolve())
        out = a.tools.call("edit_file", json.dumps({"path": "t.tsv", "old_text": "2\ttwo", "new_text": "2\tTWO"}))
        self.assertTrue(out.startswith("edited"), out)
        self.assertEqual(Path(a.root, "t.tsv").read_text(), "1\tone\n2\tTWO\n")


class CheckpointQuoteTest(unittest.TestCase):
    def test_a_follow_up_doesnt_hide_the_task(self):
        a = agent()
        a._begin_turn("Make the inventory stack items, with max_stack per item and InventoryFull when it can't fit.")
        a._begin_turn("it still fails")
        quote = a._request_quote()
        self.assertIn("InventoryFull", quote)
        self.assertIn("and now: it still fails", quote)

    def test_a_long_request_keeps_its_middle(self):
        a = agent()
        middle = "the exact rule in the middle"
        a._begin_turn("x" * 1200 + middle + "y" * 600)
        self.assertIn(middle, a._request_quote())


class MissingFilesTest(unittest.TestCase):
    def test_a_named_file_that_was_never_written_is_pointed_out(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "cafe").mkdir()
        Path(a.root, "cafe/cats.py").write_text("CATS = []\n")
        a.tools.seen.add(Path(a.root, "cafe/cats.py").resolve())
        scripted(a, [reply("", tool=("write_file", {"path": "cafe/cats.py", "content": "CATS = [1]\n"})),
                     reply("Added cat_of_the_day and tests/test_cat_of_the_day.py."), reply("ok")])
        a.turn("Add cat_of_the_day() to cafe/cats.py. Write tests in tests/test_cat_of_the_day.py.")
        check = next(u for u in users(a) if "before you finish" in u)
        self.assertIn("`tests/test_cat_of_the_day.py`, which does not exist", check)
        self.assertNotIn("cafe/cats.py`", check)


class RemovedLinesTest(unittest.TestCase):
    def test_lines_deleted_outright_are_quoted_at_the_check(self):
        # Qwen3.6 took the sword and potion recipes out; the task's tests needed them
        a = agent()
        a.tools.trust_all = True
        recipes = 'RECIPES = {\n    "plank": {"wood": 2},\n    "sword": {"plank": 2},\n}\n\n\ndef craft():\n    return 1\n'
        Path(a.root, "crafting.py").write_text(recipes)
        a.tools.seen.add(Path(a.root, "crafting.py").resolve())
        new = 'RECIPES = {\n    "plank": {"wood": 2},\n}\n\n\ndef craft():\n    return 2\n'
        scripted(a, [reply("", tool=("write_file", {"path": "crafting.py", "content": new})), reply("done"), reply("ok")])
        a.turn("make craft return 2")
        check = next(u for u in users(a) if "before you finish" in u)
        self.assertIn('deleted these lines that were there before: `"sword": {"plank": 2},`', check)
        self.assertNotIn("return 1", check)  # rewritten, not deleted


class GptOssEditSlipsTest(unittest.TestCase):
    def setUp(self):
        self.a = agent()
        self.a.tools.trust_all = True

    def test_a_bare_line_number_for_an_empty_line_is_taken_out(self):
        Path(self.a.root, "cats.py").write_text("CATS = []\n\n\ndef awake():\n    return CATS\n")
        self.a.tools.seen.add(Path(self.a.root, "cats.py").resolve())
        out = self.a.tools.call("edit_file", json.dumps({"path": "cats.py", "old_text": "    3\ndef awake():\n    return CATS",
                                                          "new_text": "    3\ndef awake():\n    return list(CATS)"}))
        self.assertTrue(out.startswith("edited"), out)
        self.assertEqual(Path(self.a.root, "cats.py").read_text(), "CATS = []\n\n\ndef awake():\n    return list(CATS)\n")

    def test_a_stray_quote_at_the_end_of_a_written_file_goes(self):
        out = self.a.tools.call("write_file", json.dumps({"path": "d.py", "content": "def f():\n    return 1\n\""}))
        self.assertTrue(out.startswith("wrote d.py"), out)
        self.assertNotIn("syntax error", out)
        self.assertEqual(Path(self.a.root, "d.py").read_text(), "def f():\n    return 1\n")
        out = self.a.tools.call("write_file", json.dumps({"path": "e.py", "content": 'x = "a"\n'}))  # a real quote stays
        self.assertEqual(Path(self.a.root, "e.py").read_text(), 'x = "a"\n')


class RemovedDefinitionTest(unittest.TestCase):
    def test_an_edit_that_drops_a_method_says_so_at_once(self):
        a = agent()
        a.tools.trust_all = True
        code = "class C:\n    def __init__(self, n):\n        self.n = n\n\n    def get(self):\n        return self.n\n"
        Path(a.root, "c.py").write_text(code)
        a.tools.seen.add(Path(a.root, "c.py").resolve())
        out = a.tools.call("edit_file", json.dumps({"path": "c.py", "old_text": "    def __init__(self, n):\n        self.n = n",
                                                     "new_text": "    def get(self):\n        return 1"}))
        self.assertIn("this change removed def __init__", out)
        Path(a.root, "r.py").write_text("def calc_total(x):\n    return x\n")
        a.tools.seen.add(Path(a.root, "r.py").resolve())
        out = a.tools.call("edit_file", json.dumps({"path": "r.py", "old_text": "def calc_total(x):", "new_text": "def order_total(x):"}))
        self.assertNotIn("removed", out)  # a rename
        out = a.tools.call("edit_file", json.dumps({"path": "c.py", "old_text": "        return self.n", "new_text": "        return self.n + 1"}))
        self.assertNotIn("removed", out)
