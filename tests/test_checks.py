"""purr checking the model's work by itself: lint after edits, the placeholder and shrink guards,
running the tests at the final check, and reminders of the request. No model is called.

Run: python3 -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import checks  # noqa: E402
from tests.test_limits import agent, known, reply, scripted  # noqa: E402


class CodeCheckTest(unittest.TestCase):
    def setUp(self):
        self.a = agent()
        self.a.tools.trust_all = True
        self.root = Path(self.a.root)
        (self.root / "shop.py").write_text("def total(order):\n    return sum(order)\n")

    def call(self, name, **args):
        known(self.a)  # as if it had read the files first
        return self.a.tools.call(name, json.dumps(args))

    def test_an_undefined_name_is_pointed_out(self):
        out = self.call("edit_file", path="shop.py", old_text="sum(order)", new_text="sum(ordr)")
        self.assertIn("edited", out)
        self.assertIn("Undefined name `ordr`", out)
        self.assertEqual(len(self.a.tools.warnings), 1)

    def test_a_syntax_error_is_pointed_out(self):
        out = self.call("write_file", path="new.py", content="def f(:\n    pass\n")
        self.assertIn("syntax error", out)

    def test_clean_code_says_nothing(self):
        out = self.call("edit_file", path="shop.py", old_text="sum(order)", new_text="round(sum(order), 2)")
        self.assertNotIn("⚠", out)

    def test_a_placeholder_rewrite_is_refused_once(self):
        body = "def total(order):\n    # ... rest of the code unchanged\n"
        first = self.call("write_file", path="shop.py", content=body)
        self.assertIn("not written", first)
        self.assertIn("sum(order)", (self.root / "shop.py").read_text())
        second = self.call("write_file", path="shop.py", content=body)  # it insists
        self.assertIn("wrote", second)

    def test_a_big_file_shrinking_is_refused_once(self):
        (self.root / "big.py").write_text("".join(f"x{n} = {n}\n" for n in range(60)))
        self.assertIn("would shrink from 60 to 2", self.call("write_file", path="big.py", content="a = 1\nb = 2\n"))

    def test_ordinary_comments_are_not_placeholders(self):
        for line in ["# Try to stack onto an existing stack of the same item first",
                     "# Attempt to add to an existing stack, then use empty slots",
                     "# compute the rest of the total", "x = 1  # unchanged", "# keep the other items as they are"]:
            self.assertIsNone(checks.placeholder("", line), line)
        for line in ["# ... rest of the code unchanged", "    # existing code here", "// ... remaining methods stay the same",
                     "# rest of the file remains unchanged", "# ... (unchanged)", "# previous implementation unchanged"]:
            self.assertIsNotNone(checks.placeholder("", line), line)

    def test_placeholder_in_an_edit(self):
        out = self.call("edit_file", path="shop.py", old_text="return sum(order)",
                        new_text="# ... existing code here\n    return 1")
        self.assertIn("placeholder", out)

    def test_can_be_turned_off(self):
        self.a.tools.code_checks = False
        out = self.call("edit_file", path="shop.py", old_text="sum(order)", new_text="sum(ordr)")
        self.assertNotIn("⚠", out)


class FinalCheckTestsTest(unittest.TestCase):
    def test_purr_runs_the_tests_and_shows_the_result(self):
        a = agent()
        a.tools.trust_all = True
        root = Path(a.root)
        (root / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        (root / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 2), 4)\n")
        known(a)
        scripted(a, [reply("", tool=("edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a * b"})),
                     reply("Done!"), reply("ok")])
        a.turn("fix add")
        check = next(m["content"] for m in a.messages if "read the user's request again" in (m.get("content") or ""))
        self.assertIn("ran the tests itself", check)
        self.assertIn("exit code 0", check)  # 2 * 2 == 4: it passes, by luck

    def test_command_guess(self):
        root = Path(tempfile.mkdtemp())
        self.assertIsNone(checks.test_command(root))
        (root / "test_x.py").write_text("")
        self.assertIn("python3 -m", checks.test_command(root))
        (root / ".purr").mkdir()
        (root / ".purr/test_command").write_text("make check\n")
        self.assertEqual(checks.test_command(root), "make check")

    def test_pytest_only_looks_in_tests_folder(self):
        # at the root it also collected purr's bench tasks, whose hidden tests fail on purpose
        root = Path(tempfile.mkdtemp())
        (root / "tests").mkdir()
        (root / "tests/test_x.py").write_text("")
        with mock.patch.object(checks, "_has_pytest", return_value=True):
            self.assertTrue(checks.test_command(root).endswith(" tests"))
            (root / "test_y.py").write_text("")
            self.assertFalse(checks.test_command(root).endswith(" tests"))


class ReadFirstTest(unittest.TestCase):
    def test_editing_an_unread_file_is_refused_until_read(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "x.py").write_text("x = 1\n")
        edit = json.dumps({"path": "x.py", "old_text": "x = 1", "new_text": "x = 2"})
        self.assertIn("read x.py first", a.tools.call("edit_file", edit))
        a.tools.call("read_file", '{"path": "x.py"}')
        self.assertIn("edited", a.tools.call("edit_file", edit))

    def test_new_files_need_no_reading(self):
        a = agent()
        a.tools.trust_all = True
        self.assertIn("wrote", a.tools.call("write_file", '{"path": "new.py", "content": "y = 1\\n"}'))

    def test_line_start_line_end(self):
        a = agent()
        Path(a.root, "x.py").write_text("a\nb\nc\nd\n")
        out = a.tools.call("read_file", '{"path": "x.py", "line_start": 2, "line_end": 3}')
        self.assertIn("b", out)
        self.assertIn("c", out)
        self.assertNotIn("d", out.split("[")[0])


class OutsideTest(unittest.TestCase):
    def test_writing_outside_the_project_is_refused(self):
        a = agent()
        a.tools.trust_all = True
        elsewhere = Path(tempfile.mkdtemp()) / "typo-folder" / "x.py"
        out = a.tools.call("write_file", json.dumps({"path": str(elsewhere), "content": "x = 1\n"}))
        self.assertIn("outside the project folder", out)
        self.assertFalse(elsewhere.parent.exists())  # no folder was made either

    def test_absolute_paths_inside_are_fine(self):
        a = agent()
        a.tools.trust_all = True
        out = a.tools.call("write_file", json.dumps({"path": str(Path(a.root) / "x.py"), "content": "x = 1\n"}))
        self.assertIn("wrote", out)


class PathRepairTest(unittest.TestCase):
    def test_a_path_missing_its_first_slash_is_fixed(self):
        a = agent()
        Path(a.root, "x.py").write_text("x = 1\n")
        out = a.tools.call("read_file", json.dumps({"path": str(Path(a.root, "x.py"))[1:]}))
        self.assertIn("x = 1", out)
        self.assertIn("path missing its leading / -> fixed", a.tools.repairs)

    def test_a_guessed_path_gets_a_suggestion(self):
        a = agent()
        Path(a.root, "README.md").write_text("hi\n")
        out = a.tools.call("read_file", json.dumps({"path": "build_order/README.md"}))
        self.assertIn("Did you mean README.md?", out)

    def test_read_file_without_a_path(self):
        self.assertEqual(agent().tools.call("read_file", "{}"), "error: read_file needs path")


class TamperTest(unittest.TestCase):
    def test_changing_what_a_test_expects_is_pointed_out(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "test_shop.py").write_text("def test_total():\n    assert total([1, 2]) == 3\n")
        known(a)
        out = a.tools.call("edit_file", json.dumps({"path": "test_shop.py", "old_text": "== 3", "new_text": "== 4"}))
        self.assertIn("you changed or removed what a test checks", out)

    def test_adding_tests_is_fine(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "test_shop.py").write_text("def test_total():\n    assert total([1, 2]) == 3\n")
        known(a)
        out = a.tools.call("edit_file", json.dumps({"path": "test_shop.py", "old_text": "== 3\n",
                                                    "new_text": "== 3\n\n\ndef test_empty():\n    assert total([]) == 0\n"}))
        self.assertNotIn("⚠", out)


class ReminderTest(unittest.TestCase):
    def test_the_request_comes_back_every_8_calls(self):
        a = agent()
        for n in range(9):  # different files, or purr's loop guard would (rightly) step in
            Path(a.root, f"x{n}.py").write_text(f"x = {n}\n")
        steps = [reply("", tool=("read_file", {"path": f"x{n}.py"})) for n in range(9)] + [reply("done")]
        calls = scripted(a, steps)
        a.turn("look at x very carefully")
        self.assertEqual(calls["n"], 10)
        reminders = [m for m in a.messages if "a reminder of what the user asked" in (m.get("content") or "")]
        self.assertEqual(len(reminders), 1)
        self.assertIn("look at x very carefully", reminders[0]["content"])


if __name__ == "__main__":
    unittest.main()
