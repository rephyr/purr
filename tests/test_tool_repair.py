"""Small models call tools that don't exist: a program on the machine runs as a command, anything
else gets the real tool names (gpt-oss-20b: objdump, size, readdir?).

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

from tests.test_limits import agent  # noqa: E402


class ToolRepairTest(unittest.TestCase):
    def setUp(self):
        self.a = agent()
        self.a.tools.trust_all = True
        Path(self.a.root, "notes.txt").write_text("one\ntwo\nthree\n")

    def test_a_program_called_as_a_tool_runs(self):
        out = self.a.tools.call("wc", json.dumps({"args": "-l notes.txt"}))
        self.assertIn("3 notes.txt", out)
        self.assertIn("tool wc -> run wc -l notes.txt", self.a.tools.repairs)
        out = self.a.tools.call("head", json.dumps({"file": "notes.txt"}))  # arguments of any name
        self.assertIn("one", out)

    def test_anything_else_gets_the_real_names(self):
        out = self.a.tools.call("readdir?", json.dumps({}))
        self.assertIn("there is no tool called readdir?", out)
        self.assertIn("The tools:", out)
        self.assertIn("run", out)
        self.assertNotIn("fetch_url", out)  # a small model isn't offered it: not listed either

    def test_look_only_modes_never_run_anything(self):
        self.a.set_mode("ask")
        out = self.a.tools.call("wc", json.dumps({"args": "-l notes.txt"}))
        self.assertIn("there is no tool called wc", out)
        self.assertNotIn("3 notes.txt", out)


class GptOssSlipsTest(unittest.TestCase):
    """gpt-oss-20b in purr bench: a tool call's arguments written as the whole answer, and an edit
    whose first line alone got an extra indent."""

    def test_arguments_written_as_the_answer_still_run(self):
        from tests.test_limits import reply, scripted
        a = agent()
        Path(a.root, "a.py").write_text("x = 1\n")
        calls = scripted(a, [reply('{"limit": 200, "offset": 1, "path": "a.py"}'), reply("x is 1")])
        a.turn("what is x?")
        self.assertEqual(calls["n"], 2)
        self.assertIn("x = 1", [m for m in a.messages if m["role"] == "tool"][0]["content"])
        self.assertIn("tool call written as text -> real call", a.tools.repairs)

    def test_unclear_arguments_get_asked_again(self):
        from tests.test_limits import reply, scripted
        a = agent()
        calls = scripted(a, [reply('{"path": "a.py"}'), reply("ok")])
        a.turn("look at a.py")
        self.assertEqual(calls["n"], 2)
        self.assertTrue(any("came out as plain text" in (m.get("content") or "") for m in a.messages))

    def test_a_first_line_indented_on_its_own_still_edits(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "b.py").write_text('class E(Exception):\n    pass\n\n\ndef f(d):\n    """doc"""\n    raise NotImplementedError\n')
        a.tools.seen.add(Path(a.root, "b.py").resolve())
        out = a.tools.call("edit_file", json.dumps({
            "path": "b.py", "old_text": '    def f(d):\n    """doc"""\n    raise NotImplementedError',
            "new_text": '    def f(d):\n    """doc"""\n    for x in d:\n        print(x)\n    return d'}))
        self.assertTrue(out.startswith("edited b.py"), out)
        self.assertIn('\n\ndef f(d):\n    """doc"""\n    for x in d:\n        print(x)\n    return d\n',
                      Path(a.root, "b.py").read_text())


class ExtraArgumentTest(unittest.TestCase):
    def test_an_argument_the_tool_doesnt_have_is_dropped(self):
        # gpt-oss added replace_whole_file to every edit and each one failed whole
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "b.py").write_text("x = 1\n")
        a.tools.seen.add(Path(a.root, "b.py").resolve())
        out = a.tools.call("edit_file", json.dumps({"path": "b.py", "old_text": "x = 1", "new_text": "x = 2",
                                                     "replace_whole_file": False}))
        self.assertTrue(out.startswith("edited b.py"), out)
        self.assertIn("unknown argument replace_whole_file for edit_file -> dropped", a.tools.repairs)


class LineRangeEditTest(unittest.TestCase):
    def setUp(self):
        self.a = agent()
        self.a.tools.trust_all = True
        Path(self.a.root, "c.py").write_text("a = 1\nb = 2\nc = 3\n")
        self.a.tools.seen.add(Path(self.a.root, "c.py").resolve())

    def test_lines_by_number_are_replaced(self):
        # gpt-oss: {"path": ..., "line_start": 2, "line_end": 2, "new_text": ...} and no old_text
        out = self.a.tools.call("edit_file", json.dumps({"path": "c.py", "line_start": 2, "line_end": 2,
                                                          "new_text": "b = 20\nbb = 21"}))
        self.assertTrue(out.startswith("edited c.py"), out)
        self.assertEqual(Path(self.a.root, "c.py").read_text(), "a = 1\nb = 20\nbb = 21\nc = 3\n")

    def test_old_text_wins_over_line_numbers(self):
        out = self.a.tools.call("edit_file", json.dumps({"path": "c.py", "line_start": 1, "line_end": 200,
                                                          "old_text": "c = 3", "new_text": "c = 30"}))
        self.assertTrue(out.startswith("edited c.py"), out)
        self.assertEqual(Path(self.a.root, "c.py").read_text(), "a = 1\nb = 2\nc = 30\n")

    def test_lines_past_the_end_are_an_error(self):
        out = self.a.tools.call("edit_file", json.dumps({"path": "c.py", "line_start": 2, "line_end": 9, "new_text": "x"}))
        self.assertIn("error: edit_file needs old_text", out)
