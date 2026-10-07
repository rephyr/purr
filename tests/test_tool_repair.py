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

    def test_a_json_answer_stays_an_answer(self):
        """{"items": [...]} asked for as JSON once became a todo call: the task list emptied and
        the answer was gone. Likewise from an API model, in chat mode, or as a tool the mode lacks."""
        from tests.test_limits import reply, scripted
        answer = '{"items": ["milk", "eggs"]}'
        for model, mode, ask in [("small", "code", "Reply with just the JSON"),
                                 ("api", "code", "a shopping list please"),
                                 ("small", "chat", "a shopping list please"),
                                 ("small", "code", "give me a JSON-formatted shopping list")]:
            with self.subTest(model=model, mode=mode):
                a = agent(model)
                a.config = {**a.config, "final_check": False}
                a.set_mode(mode)
                a.tools.todo_list = [{"text": "fix the bug", "status": "doing"}]
                calls = scripted(a, [reply(answer), reply("again?")])
                a.turn(ask)
                self.assertEqual(calls["n"], 1)
                self.assertEqual(a.tools.todo_list, [{"text": "fix the bug", "status": "doing"}])
                self.assertEqual(a.messages[-1]["content"], answer)
                self.assertNotIn("tool call written as text -> real call", a.tools.repairs)
        a = agent()
        a.set_mode("ask")  # look only: write_file isn't offered, so it's no call
        self.assertEqual(a._json_call('{"path": "config.json", "content": "{}"}'), [])

    def test_a_json_answer_is_an_answer(self):
        # {"scripts": ...} fits no tool: it's the answer, not a tool call that failed to run
        from tests.test_limits import reply, scripted
        for mode in ("code", "chat"):
            a = agent()
            a.set_mode(mode)
            calls = scripted(a, [reply('{"scripts": {"test": "pytest"}}'), reply("unused")])
            a.turn("what is in package.json's scripts block? reply with just the JSON")
            self.assertEqual(calls["n"], 1, mode)
            self.assertFalse(any("came out as plain text" in (m.get("content") or "") for m in a.messages), mode)

    def test_a_json_file_name_is_not_asking_for_json(self):
        # "json" in package.json turned the rescue off in every JS/TS project
        from tests.test_limits import reply, scripted
        a = agent()
        Path(a.root, "package.json").write_text('{"version": "1.0.0"}\n')
        calls = scripted(a, [reply('{"path": "package.json", "offset": 1}'), reply("it is 1.0.0")])
        a.turn("bump the version in package.json")
        self.assertEqual(calls["n"], 2)
        self.assertIn("tool call written as text -> real call", a.tools.repairs)
        from harness.agent import asks_for_json
        self.assertFalse(asks_for_json("fix tsconfig.json and json/schema.ts"))
        self.assertTrue(asks_for_json("what's in package.json? Reply as JSON."))

    def test_a_one_shot_run_asked_for_json_still_gets_the_rescue(self):
        # purr bench: "Write a JSON file called /app/re.json ..." turned the rescue off, so a call
        # written as text ended the run with no file. In a one-shot run the files are the answer.
        from tests.test_limits import reply, scripted
        a = agent()
        a.tools.trust_all = True
        a.set_one_shot()
        calls = scripted(a, [reply('{"path": "re.out", "content": "e4"}'), reply("done")])
        a.turn("Write a JSON file called re.json, and the first move to re.out")
        self.assertGreaterEqual(calls["n"], 2)
        self.assertIn("tool call written as text -> real call", a.tools.repairs)
        self.assertEqual(Path(a.root, "re.out").read_text(), "e4")

    def test_a_json_answer_nobody_asked_for_is_not_called_again(self):
        # no "json" in the request, so only its shape says it's no call: {"scripts": ...} fits no tool
        from tests.test_limits import reply, scripted
        a = agent()
        a.set_mode("code")
        calls = scripted(a, [reply('{"scripts": {"test": "pytest"}}'), reply("unused")])
        a.turn("what scripts does the project define?")
        self.assertEqual(calls["n"], 1)
        self.assertFalse(any("came out as plain text" in (m.get("content") or "") for m in a.messages))

    def test_a_json_call_by_name_gets_asked_again(self):
        from tests.test_limits import reply, scripted
        a = agent()
        calls = scripted(a, [reply('{"name": "read_file", "arguments": {"path": "a.py"}}'), reply("ok")])
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

    def test_an_unknown_argument_with_a_value_is_refused_not_ignored(self):
        # dropping append: true turned an append into an overwrite, and cwd ran the command in the root
        a = agent()
        a.tools.trust_all = True
        notes = Path(a.root, "notes.md")
        notes.write_text("# notes\n- one\n- two\n- three\n")
        a.tools.seen.add(notes.resolve())
        out = a.tools.call("write_file", json.dumps({"path": "notes.md", "content": "- four\n", "append": True}))
        self.assertTrue(out.startswith("error:"), out)
        self.assertIn("append", out)
        self.assertEqual("# notes\n- one\n- two\n- three\n", notes.read_text())
        Path(a.root, "frontend").mkdir()
        out = a.tools.call("run", json.dumps({"command": "touch made_here", "workdir": "frontend"}))
        self.assertTrue(out.startswith("error:"), out)
        self.assertIn("workdir", out)
        self.assertFalse(Path(a.root, "made_here").exists())

    def test_opencode_style_arguments_still_run(self):
        # OpenCode's bash requires a description, and its grep filters files with include
        a = agent()
        a.tools.trust_all = True
        out = a.tools.call("bash", json.dumps({"command": "echo hi", "description": "say hi"}))
        self.assertIn("hi", out)
        self.assertFalse(out.startswith("error:"), out)
        self.assertIn("unknown argument description for run -> dropped", a.tools.repairs)
        Path(a.root, "a.py").write_text("needle = 1\n")
        Path(a.root, "a.txt").write_text("needle\n")
        out = a.tools.call("grep", json.dumps({"pattern": "needle", "include": "*.py"}))
        self.assertIn("a.py", out)
        self.assertNotIn("a.txt", out)


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
        self.assertIn("error: c.py has 3 lines, so lines 2-9 can't be edited", out)
        self.assertEqual(Path(self.a.root, "c.py").read_text(), "a = 1\nb = 2\nc = 3\n")

    def test_a_blank_line_is_edited_by_its_number(self):
        # its text "" matched everywhere: "old_text matches 14 times", and with replace_all new_text
        # went between every character of the file
        for replace_all in (False, True):
            Path(self.a.root, "c.py").write_text("a = 1\n\nb = 2\n")
            out = self.a.tools.call("edit_file", json.dumps({"path": "c.py", "line_start": 2, "new_text": "c = 3",
                                                              "replace_all": replace_all}))
            self.assertTrue(out.startswith("edited c.py (1 change)"), out)
            self.assertEqual(Path(self.a.root, "c.py").read_text(), "a = 1\nc = 3\nb = 2\n")

    def test_a_repeated_line_is_edited_by_its_number(self):
        Path(self.a.root, "c.py").write_text("def f():\n    return 1\ndef g():\n    return 1\n")
        out = self.a.tools.call("edit_file", json.dumps({"path": "c.py", "line_start": 4, "new_text": "    return 2"}))
        self.assertTrue(out.startswith("edited c.py"), out)
        self.assertEqual(Path(self.a.root, "c.py").read_text(), "def f():\n    return 1\ndef g():\n    return 2\n")

    def test_an_unread_file_says_read_it_first(self):
        Path(self.a.root, "d.py").write_text("a = 1\n")
        out = self.a.tools.call("edit_file", json.dumps({"path": "d.py", "line_start": 1, "new_text": "a = 2"}))
        self.assertIn("error: read d.py first", out)

    def test_numbers_from_before_an_edit_that_moved_lines_are_refused(self):
        # two numbered edits in one reply, both numbered from the same read: the first adds lines,
        # so the second's line 8 is now the old line 6. It must not silently replace that line.
        p = Path(self.a.root, "m.py")
        p.write_text("".join(f"x{i} = {i}\n" for i in range(1, 11)))
        self.a.tools.call("read_file", json.dumps({"path": "m.py"}))
        out = self.a.tools.call("edit_file", json.dumps({"path": "m.py", "line_start": 2, "line_end": 2,
                                                          "new_text": "x2 = 2\nextra_a = 0\nextra_b = 0"}))
        self.assertTrue(out.startswith("edited m.py"), out)
        after_first = p.read_text()
        out = self.a.tools.call("edit_file", json.dumps({"path": "m.py", "line_start": 8, "line_end": 8,
                                                          "new_text": "x8 = 800"}))
        self.assertIn("read_file m.py again", out)
        self.assertEqual(p.read_text(), after_first)
        # lines above the first edit kept their numbers
        out = self.a.tools.call("edit_file", json.dumps({"path": "m.py", "line_start": 1, "new_text": "x1 = 100"}))
        self.assertTrue(out.startswith("edited m.py"), out)
        # after a fresh read the new numbers work
        self.a.tools.call("read_file", json.dumps({"path": "m.py"}))
        out = self.a.tools.call("edit_file", json.dumps({"path": "m.py", "line_start": 10, "new_text": "x8 = 800"}))
        self.assertTrue(out.startswith("edited m.py"), out)
        self.assertIn("x6 = 6\nx7 = 7\nx8 = 800\nx9 = 9\n", p.read_text())

    def test_numbers_from_the_edits_own_result_are_fresh(self):
        # small models see the numbered lines around their edit: the next reply edits by those numbers
        p = Path(self.a.root, "m.py")
        p.write_text("".join(f"x{i} = {i}\n" for i in range(1, 11)))

        def step(name, **args):
            self.a._run_tools([{"id": "c", "name": name, "args": json.dumps(args)}])
            return self.a.messages[-1]["content"]

        step("read_file", path="m.py")
        out = step("edit_file", path="m.py", line_start=2, new_text="x2 = 2\nextra_a = 0\nextra_b = 0")
        self.assertIn("   10\tx8 = 8", out)  # the result numbers the lines as they are now
        out = step("edit_file", path="m.py", line_start=10, new_text="x8 = 800")
        self.assertTrue(out.startswith("edited m.py"), out)
        self.assertIn("x7 = 7\nx8 = 800\nx9 = 9\n", p.read_text())
        # numbers outside what it was shown are still the old read's
        import dataclasses
        self.a.tools.limits = dataclasses.replace(self.a.tools.limits, edit_window=3)
        out = step("edit_file", path="m.py", line_start=2, new_text="x2 = 2\nextra_c = 0")
        self.assertTrue(out.startswith("edited m.py"), out)
        out = step("edit_file", path="m.py", line_start=12, new_text="x9 = 900")
        self.assertIn("read_file m.py again", out)

    def test_a_whole_file_write_makes_line_numbers_fresh(self):
        # write_file shows the model the whole new file: an earlier edit's moved lines are moot
        p = Path(self.a.root, "m.py")
        p.write_text("".join(f"x{i} = {i}\n" for i in range(1, 11)))
        self.a.tools.call("read_file", json.dumps({"path": "m.py"}))
        self.a.tools.call("edit_file", json.dumps({"path": "m.py", "line_start": 2, "new_text": "x2 = 2\nextra = 0"}))
        new = "".join(f"y{i} = {i}\n" for i in range(1, 11))
        self.a.tools.call("write_file", json.dumps({"path": "m.py", "content": new}))
        out = self.a.tools.call("edit_file", json.dumps({"path": "m.py", "line_start": 8, "new_text": "y8 = 800"}))
        self.assertTrue(out.startswith("edited m.py"), out)
        self.assertIn("y7 = 7\ny8 = 800\ny9 = 9\n", p.read_text())

    def test_a_new_chat_forgets_moved_lines(self):
        # /clear: the old chat's edits say nothing about numbers the new chat read
        p = Path(self.a.root, "m.py")
        p.write_text("".join(f"x{i} = {i}\n" for i in range(1, 6)))
        self.a.tools.read_before_edit = False
        self.a.tools.call("edit_file", json.dumps({"path": "m.py", "line_start": 1, "new_text": "x0 = 0\nx1 = 1"}))
        self.a._forget_chat()
        p.write_text("".join(f"x{i} = {i}\n" for i in range(1, 6)))  # as the new chat sees it, unread
        out = self.a.tools.call("edit_file", json.dumps({"path": "m.py", "line_start": 4, "new_text": "x4 = 40"}))
        self.assertTrue(out.startswith("edited m.py"), out)


class GptOssArgumentsTest(unittest.TestCase):
    def setUp(self):
        self.a = agent()
        self.a.tools.trust_all = True
        Path(self.a.root, "n.py").write_text("".join(f"x{i} = {i}\n" for i in range(1, 51)))

    def test_a_line_range_reads_those_lines(self):
        out = self.a.tools.call("read_file", json.dumps({"path": "n.py", "line_range": [10, 12]}))
        self.assertTrue(out.startswith("   10\tx10 = 10"), out)
        self.assertIn("x12 = 12", out)
        self.assertNotIn("x13", out.split("[")[0])

    def test_a_key_with_whitespace_still_counts(self):
        self.a.tools.seen.add(Path(self.a.root, "n.py").resolve())
        out = self.a.tools.call("edit_file", json.dumps({"path": "n.py", "old_text": "x1 = 1\n", "\nnew_text": "x1 = 100\n"}))
        self.assertTrue(out.startswith("edited n.py"), out)


class TestsOverCodeTest(unittest.TestCase):
    def test_tests_written_over_the_code_are_refused_once(self):
        # Ornith-9B wrote its unittest module over cache.py at the end of a run
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "cache.py").write_text("class LRUCache:\n    pass\n")
        a.tools.seen.add(Path(a.root, "cache.py").resolve())
        tests = "import unittest\nfrom cache import LRUCache\n\nclass T(unittest.TestCase):\n    pass\n"
        out = a.tools.call("write_file", json.dumps({"path": "cache.py", "content": tests}))
        self.assertIn("would replace it with a test module", out)
        self.assertIn("class LRUCache", Path(a.root, "cache.py").read_text())
        out = a.tools.call("write_file", json.dumps({"path": "cache.py", "content": tests}))  # meant it
        self.assertTrue(out.startswith("wrote cache.py"), out)

    def test_test_files_and_new_files_are_fine(self):
        a = agent()
        a.tools.trust_all = True
        tests = "import unittest\n\nclass T(unittest.TestCase):\n    pass\n"
        self.assertTrue(a.tools.call("write_file", json.dumps({"path": "test_x.py", "content": tests})).startswith("wrote"))
        Path(a.root, "test_y.py").write_text("x = 1\n")
        a.tools.seen.add(Path(a.root, "test_y.py").resolve())
        self.assertTrue(a.tools.call("write_file", json.dumps({"path": "test_y.py", "content": tests})).startswith("wrote"))

    def test_rewrites_that_keep_the_code_are_fine(self):
        # the guard refused these once and suggested test_conftest.py, where pytest never loads fixtures
        a = agent()
        a.tools.trust_all = True
        cases = [("conftest.py", "X = 1\n", "import pytest\n\n@pytest.fixture\ndef x():\n    return 1\n"),
                 ("tests.py", "# tests here\n", "import unittest\n\nclass T(unittest.TestCase):\n    pass\n"),
                 ("README.md", "# hi\n", "# hi\nThe tests use unittest.TestCase\n"),
                 ("net.py", "def port():\n    return 1\n",
                  "def port():\n    return 1\n\ndef test_port():\n    assert port() == 1\n"),
                 # these drop a definition: only the file's name (or kind) says they're fine
                 ("conftest.py", "def make_db():\n    return {}\n",
                  "import pytest\n\n@pytest.fixture\ndef db():\n    return {}\n"),
                 ("test.py", "def old():\n    pass\n", "import unittest\n\nclass T(unittest.TestCase):\n    pass\n"),
                 ("NOTES.md", "```\ndef old():\n    pass\n```\n", "import unittest is all the tests need\n")]
        for name, before, after in cases:
            Path(a.root, name).write_text(before)
            a.tools.seen.add(Path(a.root, name).resolve())
            out = a.tools.call("write_file", json.dumps({"path": name, "content": after}))
            self.assertTrue(out.startswith("wrote " + name), out)
