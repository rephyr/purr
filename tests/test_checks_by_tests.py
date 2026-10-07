"""The final check picks its words by purr's own test run, the model learns the project's test command,
pasted line numbers are taken out of edits, and checkpoints quote the chat's real request.
No model is called.

Run: python3 -m unittest discover -s tests -t .
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import agent as agent_module, tools as tools_module  # noqa: E402
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

    def test_a_shell_edit_after_green_tests_still_gets_the_extra_look(self):
        # edit_gen only counts edit tools: a change through run after the green check looked like
        # "nothing changed since" and the look was skipped
        a = one_shot(time_limit=5400)
        Path(a.root, "test_a.py").write_text(TEST)
        scripted(a, [reply("", tool=("write_file", {"path": "a.py", "content": "x = 1\n"})), reply("done"),
                     reply("", tool=("run", {"command": "echo 'x = 1  # one' > a.py"})), reply("done"), reply("ok")])
        a.turn("make a.py with x = 1")
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1  # one\n")
        self.assertTrue(any("one line per requirement" in u for u in users(a)))


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

    def test_tests_from_the_wrong_folder_name_the_right_command(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "a.py").write_text("x = 1\n")
        Path(a.root, "test_a.py").write_text(TEST)
        out = a.tools.call("run", json.dumps({"command": "python3 -m unittest tests.test_a"}))
        self.assertIn("No module named 'tests", out)
        self.assertIn("This project's tests run with `python3 -m", out)

    def test_a_module_the_tests_import_missing_is_not_a_wrong_command(self):
        # TDD: the test imports cache.py before it's written; every test command fails the same way, so
        # pointing at another one sent the model off changing commands instead of writing cache.py
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "tests").mkdir()
        Path(a.root, "tests", "test_cache.py").write_text(
            "import unittest\nfrom cache import LRU\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        LRU()\n")
        commands = ["python3 -m unittest tests.test_cache", "python3 tests/test_cache.py"]
        has_pytest = subprocess.run(["python3", "-c", "import pytest"], env=tools_module.child_env(a.root), capture_output=True).returncode == 0
        if has_pytest:  # without pytest on the python3 commands get, "No module named pytest" rightly gets the hint
            commands.append("python3 -m pytest tests/test_cache.py -q")
        for command in commands:
            out = a.tools.call("run", json.dumps({"command": command}))
            self.assertNotIn("purr: no tests ran", out, command)
        Path(a.root, "app.py").write_text("import numpyzzz\n")
        out = a.tools.call("run", json.dumps({"command": "python3 app.py --test"}))
        self.assertIn("No module named 'numpyzzz'", out)
        self.assertNotIn("purr: no tests ran", out)

    def test_a_missing_test_runner_names_the_right_command(self):
        # `python3 -m pytest` where pytest isn't installed: the command is wrong, not the code
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "a.py").write_text("x = 1\n")
        Path(a.root, "test_a.py").write_text(TEST)
        for missing in ("pytest", "nose2"):
            with mock.patch.object(tools_module, "run_shell",
                                   lambda *k, m=missing: (f"/usr/bin/python3: No module named {m}", 1)):
                out = a.tools.call("run", json.dumps({"command": f"python3 -m {missing}"}))
            self.assertIn("This project's tests run with `python3 -m", out, missing)


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

    def test_a_plain_new_text_keeps_a_last_line_that_is_a_number(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "expected.txt").write_text("total\nsum:\n41\n")
        a.tools.seen.add(Path(a.root, "expected.txt").resolve())
        out = a.tools.call("edit_file", json.dumps({"path": "expected.txt", "old_text": "    2\tsum:\n    3\t41",
                                                     "new_text": "sum:\n42"}))
        self.assertTrue(out.startswith("edited"), out)
        self.assertEqual(Path(a.root, "expected.txt").read_text(), "total\nsum:\n42\n")

    def test_numbered_empty_lines_in_the_middle_of_new_text_stay_empty(self):
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "a.py").write_text("def f():\n    return 0\n\n\ndef g():\n    pass\n")
        a.tools.seen.add(Path(a.root, "a.py").resolve())
        new = "    1\tdef f():\n    2\t    return 1\n    3\n    4\n    5\tdef h():\n    6\t    pass\n    7\t[\n42\n]"
        out = a.tools.call("edit_file", json.dumps({"path": "a.py", "old_text": "    1\tdef f():\n    2\t    return 0",
                                                     "new_text": new}))
        self.assertTrue(out.startswith("edited"), out)
        self.assertEqual(Path(a.root, "a.py").read_text(),
                         "def f():\n    return 1\n\n\ndef h():\n    pass\n[\n42\n]\n\n\ndef g():\n    pass\n")


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


class AnotherChatTest(unittest.TestCase):
    """/clear (new) and /resume (load) start another chat on the same agent: nothing from the old one
    may reach it as the user's request, a file it saw change, or tests that passed."""

    def old_chat(self):
        a = agent()
        Path(a.root, "app.py").write_text("x = 1\n")
        a._begin_turn("delete every test in tests/ that mentions the old API")
        a.tools.t_read_file("app.py")
        a._green = {"python3 -m unittest": ("python3 -m unittest", {}, 0)}
        return a

    def check_forgotten(self, a):
        Path(a.root, "app.py").write_text("x = 2\n")
        a._begin_turn("what's 2+2?")
        self.assertNotIn("since you last saw them", a._turn_content("what's 2+2?"))
        self.assertEqual(a._request_quote(), "The request: what's 2+2?")
        self.assertEqual(a._green, {})
        self.assertNotIn(Path(a.root, "app.py").resolve(), a.tools.seen)

    def test_clear(self):
        a = self.old_chat()
        a.new()
        self.check_forgotten(a)

    def test_resume(self):
        a = self.old_chat()
        log = Path(a.root, "other.json")
        log.write_text(json.dumps({"model": "small", "mode": "executor", "messages": [
            {"role": "system", "content": "x"}, {"role": "user", "content": "fix the bug"}]}))
        a.load(log)
        self.check_forgotten(a)


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

    def check_for(self, request, files, *tools):
        a = agent()
        a.tools.trust_all = True
        for name in files:
            Path(a.root, name).parent.mkdir(parents=True, exist_ok=True)
            Path(a.root, name).write_text("x = 1\n")
            a.tools.seen.add(Path(a.root, name).resolve())
        scripted(a, [reply("", tool=t) for t in tools] + [reply("Done."), reply("ok")])
        a.turn(request)
        return next(u for u in users(a) if "before you finish" in u)

    def test_files_that_are_there_or_were_are_not_called_missing(self):
        # a bare name in a subfolder, a dot folder, a tool's name, and the old name of a rename
        write = ("write_file", {"path": "src/app/parser.py", "content": "x = 2\n"})
        check = self.check_for("Fix the off-by-one in parser.py and .github/workflows/ci.yml, like Node.js does.",
                               ["src/app/parser.py", ".github/workflows/ci.yml"], write)
        self.assertNotIn("the request names", check)
        check = self.check_for("Rename utils.py to helpers.py.", ["utils.py"],
                               ("write_file", {"path": "helpers.py", "content": "x = 1\n"}),
                               ("run", {"command": "rm utils.py"}))
        self.assertNotIn("the request names", check)
        check = self.check_for("Rename utils.py to helpers.py.", ["utils.py"],
                               ("write_file", {"path": "util.py", "content": "x = 1\n"}),
                               ("run", {"command": "rm utils.py"}))
        self.assertIn("`helpers.py`, which does not exist", check)  # the new name is still checked

    def test_a_bare_name_in_a_gitignored_folder_is_there(self):
        # rg --files skips gitignored folders: gen/schema.py was called missing
        root = Path(tempfile.mkdtemp())
        Path(root, ".git").mkdir()
        Path(root, ".gitignore").write_text("gen/\n")
        Path(root, "gen").mkdir()
        Path(root, "gen", "schema.py").write_text("x = 1\n")
        a = agent()
        a.root, a._request = root.resolve(), "fix the types in schema.py and in models.py"
        self.assertEqual(a._missing_files(), "(purr: the request names `models.py`, which does not exist.)\n")

    def test_a_lookup_that_times_out_calls_nothing_missing(self):
        a = agent()
        a._request = "fix parser.py"
        with mock.patch.object(agent_module, "files_under",
                               mock.Mock(side_effect=agent_module.subprocess.TimeoutExpired("rg", 30))):
            self.assertEqual(a._missing_files(), "")

    def test_a_file_a_steer_says_to_delete_is_not_called_missing(self):
        # "also delete old.py" typed while it worked: after `rm old.py` the check told it to make old.py
        a = agent()
        a.tools.trust_all = True
        for name in ("utils.py", "old.py"):
            Path(a.root, name).write_text("x = 1\n")
            a.tools.seen.add(Path(a.root, name).resolve())
        steps = iter([reply("", tool=("write_file", {"path": "utils.py", "content": "x = 2\n"})),
                      reply("", tool=("run", {"command": "rm old.py"})), reply("Done."), reply("ok")])

        def fake(*k, **kw):
            if "old.py" not in a._request and not a.steers:  # typed during its first step
                a.steers.append("also delete old.py, it's unused")
            return next(steps)
        a._call = fake
        a.turn("tidy up utils.py")
        self.assertIn("also delete old.py", a._request)
        self.assertFalse(Path(a.root, "old.py").exists())
        check = next(u for u in users(a) if "before you finish" in u)
        self.assertNotIn("the request names", check)


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

    def test_a_deletion_from_an_earlier_turn_is_not_quoted_again(self):
        # turn 1 took the debug print out on request; turn 2's check mustn't say "put them back"
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "app.py").write_text('def run(x):\n    print("debug", x)\n    return x\n')
        a.tools.seen.add(Path(a.root, "app.py").resolve())
        scripted(a, [reply("", tool=("write_file", {"path": "app.py", "content": "def run(x):\n    return x\n"})),
                     reply("done"), reply("ok")])
        a.turn("remove the debug prints")
        scripted(a, [reply("", tool=("write_file", {"path": "cli.py", "content": "VERBOSE = True\n"})),
                     reply("done"), reply("ok")])
        a.turn("add a --verbose flag")
        checks = [u for u in users(a) if "before you finish" in u]
        self.assertEqual(len(checks), 2)
        self.assertIn("deleted these lines", checks[0])
        self.assertNotIn("deleted these lines", checks[1])

    def test_a_moved_block_is_not_called_deleted(self):
        # helper moved above main: the lines are still in the file, putting them back defines it twice
        a = agent()
        a.tools.trust_all = True
        code = "import sys\n\n\ndef main():\n    print(helper(3))\n    return 0\n\n\ndef helper(x):\n    return x * 2\n"
        Path(a.root, "m.py").write_text(code)
        a.tools.seen.add(Path(a.root, "m.py").resolve())
        new = "import sys\n\n\ndef helper(x):\n    return x * 2\n\n\ndef main():\n    print(helper(3))\n    return 0\n"
        scripted(a, [reply("", tool=("write_file", {"path": "m.py", "content": new})), reply("done"), reply("ok")])
        a.turn("move helper above main")
        check = next(u for u in users(a) if "before you finish" in u)
        self.assertNotIn("deleted these lines", check)


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

    def test_making_a_definition_public_is_not_removing_it(self):
        from harness.checks import removed_definitions
        for path, before, after in [
            ("a.js", "function render(x) {\n}\n", "export function render(x) {\n}\n"),
            ("a.ts", "function f() {}\nclass Foo {}\n", "export async function f() {}\nexport default class Foo {}\n"),
            ("a.rs", "fn main() {}\nfn helper() {}\n", "fn main() {}\npub fn helper() {}\n"),
            ("a.rs", "fn parse() {}\n", "pub(crate) async fn parse() {}\n"),
            ("a.gd", "func make():\n\tpass\n", "static func make():\n\tpass\n"),
            ("a.go", "func F() {}\n", "func (s *S) F() {}\n"),
        ]:
            self.assertEqual(removed_definitions(path, before, after), [], (path, after))
        # a real removal behind a prefix is still seen
        self.assertEqual(removed_definitions("a.rs", "pub fn a() {}\npub fn b() {}\n", "pub fn a() {}\n"), ["fn b"])
        a = agent()
        a.tools.trust_all = True
        Path(a.root, "m.js").write_text("function render(x) {\n  return x\n}\n")
        a.tools.seen.add(Path(a.root, "m.js").resolve())
        out = a.tools.call("edit_file", json.dumps({"path": "m.js", "old_text": "function render", "new_text": "export function render"}))
        self.assertNotIn("removed", out)
        self.assertEqual(a.tools.warnings, [])
