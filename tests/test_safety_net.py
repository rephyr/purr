"""The safety net: a one-shot run that ends with its tests failing where they passed before gets the
files back as they were when they passed. No model is called.

Run: python3 -m unittest discover -s tests -t .
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import agent as agent_mod  # noqa: E402
from harness.agent import ONLY_TESTS, TESTS_PASSED  # noqa: E402
from tests.test_bench_fixes import one_shot  # noqa: E402
from tests.test_limits import reply, scripted  # noqa: E402

TEST = "import unittest\nimport a\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(a.x, 1)\n"
RUN = ("run", {"command": "python3 -m unittest test_a"})
WRITE_AND_RUN = ("run", {"command": "cat > a.py <<'EOF'\nx = 1\nEOF\npython3 -m unittest test_a"})
READ_A = ("read_file", {"path": "a.py"})
RUN_ALL = ("run", {"command": "python3 -m unittest discover -p 'test_*.py'"})


def write(path, text):
    return reply("", tool=("write_file", {"path": path, "content": text}))


class SafetyNetTest(unittest.TestCase):
    def run_turn(self, steps, before=None, **config):
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False, "review": False, **config}
        for name, text in (before or {}).items():
            Path(a.root, name).write_text(text)
        scripted(a, steps + [reply("done")])
        a.turn("make a.x 1 and test it")
        return a

    def test_a_late_break_is_put_back(self):
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                           write("a.py", "x = 2\nimport nope\n")])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertIn("tests failed at the end -> files put back to when they last passed", a.tools.repairs)

    def test_files_made_after_it_go_too(self):
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                           write("helper.py", "y = 2\n"), write("a.py", "from helper import y as x\n")])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertFalse(Path(a.root, "helper.py").exists())

    def test_files_from_before_the_run_are_not_deleted(self):
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False, "review": False}
        Path(a.root, "README.md").write_text("my readme\n")
        Path(a.root, "notes.txt").write_text("notes\n")
        a._disk_before = a._disk_snapshot()
        scripted(a, [write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                     reply("", tool=("read_file", {"path": "README.md"})), write("README.md", "my readme\nmore\n"),
                     reply("", tool=("run", {"command": "echo more >> notes.txt"})),
                     write("a.py", "x = 2\n"), reply("done")])
        a.turn("make a.x 1 and test it")
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertEqual(Path(a.root, "README.md").read_text(), "my readme\n")  # its text from before the run
        self.assertEqual(Path(a.root, "notes.txt").read_text(), "notes\n")  # as it was when they passed
        self.assertIn("tests failed at the end -> files put back to when they last passed", a.tools.repairs)

    def test_a_file_from_before_too_big_to_keep_stays(self):
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False, "review": False}
        Path(a.root, "notes.txt").write_text("n" * 2000 + "\n")
        a._disk_before = a._disk_snapshot()
        scripted(a, [write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                     reply("", tool=("run", {"command": "echo more >> notes.txt"})),
                     write("a.py", "x = 2\n"), reply("done")])
        with mock.patch.object(agent_mod, "SAFETY_BYTES", 1000):
            a.turn("make a.x 1 and test it")
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertEqual(Path(a.root, "notes.txt").read_text(), "n" * 2000 + "\nmore\n")  # no old bytes: it stays
        self.assertIn("tests failed at the end -> files put back to when they last passed", a.tools.repairs)

    def test_a_failing_test_added_after_it_goes(self):
        more = TEST.replace("a.x, 1", "a.x, 5").replace("test_x", "test_y")
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN_ALL),
                           write("test_more.py", more)])
        self.assertFalse(Path(a.root, "test_more.py").exists())
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertIn("tests failed at the end -> files put back to when they last passed", a.tools.repairs)

    def test_a_file_from_before_deleted_after_it_comes_back(self):
        b_test = "import unittest\nimport b\n\nclass B(unittest.TestCase):\n    def test_y(self):\n        self.assertEqual(b.y, 2)\n"
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN_ALL),
                           reply("", tool=("run", {"command": "mv b.py c.py"}))],
                          before={"b.py": "y = 2\n", "test_b.py": b_test})
        self.assertEqual(Path(a.root, "b.py").read_text(), "y = 2\n")
        self.assertFalse(Path(a.root, "c.py").exists())
        self.assertIn("tests failed at the end -> files put back to when they last passed", a.tools.repairs)

    def test_a_file_from_before_edited_after_it_is_put_back_not_deleted(self):
        b_test = "import unittest\nimport b\n\nclass B(unittest.TestCase):\n    def test_y(self):\n        self.assertEqual(b.y, 2)\n"
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN_ALL),
                           reply("", tool=("read_file", {"path": "b.py"})), write("b.py", "y = 3\n"),
                           write("a.py", "x = 2\n")],
                          before={"b.py": "y = 2\n", "test_b.py": b_test})
        self.assertEqual(Path(a.root, "b.py").read_text(), "y = 2\n")
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")

    def test_work_that_still_passes_is_kept(self):
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                           write("a.py", "x = 1  # the answer\n")])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1  # the answer\n")
        self.assertFalse(a.tools.repairs)

    def test_a_pass_too_big_to_keep_drops_the_older_one(self):
        # green, then green again with a file over the size cap, then a break: going back to the first
        # pass would delete the big file and the work after it, so there's no net instead
        with mock.patch("harness.agent.SAFETY_BYTES", 1000):
            a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                               write("data.txt", "d" * 2000), write("a.py", "x = 1\ny = 5\n"), reply("", tool=RUN),
                               write("a.py", "x = 1\ny = 5\nimport nope\n")])
        self.assertTrue(Path(a.root, "data.txt").exists())
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\ny = 5\nimport nope\n")

    def test_a_pass_with_no_command_to_rerun_drops_the_older_one(self):
        # the second pass also writes b.py and purr can't tell the project's test command (tests in
        # check/): going back to the first pass would delete b.py, so there's no net instead
        run = ("run", {"command": "cd check && python3 -m unittest test_a"})
        write_and_run = ("run", {"command": "cat > check/b.py <<'EOF'\ny = 2\nEOF\ncd check && python3 -m unittest test_a"})
        a = self.run_turn([write("check/a.py", "x = 1\n"), write("check/test_a.py", TEST), reply("", tool=run),
                           reply("", tool=write_and_run), reply("", tool=("read_file", {"path": "check/a.py"})),
                           write("check/a.py", "x = 2\n")])
        self.assertEqual(Path(a.root, "check/b.py").read_text(), "y = 2\n")
        self.assertEqual(Path(a.root, "check/a.py").read_text(), "x = 2\n")
        self.assertFalse(a.tools.repairs)

    def test_a_file_a_partial_walk_missed_is_not_deleted(self):
        # a project bigger than the snapshot limit: a file from before the run that the first walk
        # didn't reach isn't "made after they passed", even when a later walk sees it
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False, "review": False}
        for i in range(6):
            Path(a.root, f"f{i}.txt").write_text(f"{i}\n")
        Path(a.root, "z").mkdir()
        Path(a.root, "z", "x.txt").write_text("x\n")
        with mock.patch.object(agent_mod, "SNAPSHOT_LIMIT", 6):
            a._disk_before = a._disk_snapshot()  # the root's six files, not z/x.txt
            self.assertNotIn(Path(a.root, "z", "x.txt"), a._disk_before)
            scripted(a, [write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                         reply("", tool=("run", {"command": "rm f*.txt; echo more >> z/x.txt"})),
                         write("a.py", "x = 2\n"), reply("done")])
            a.turn("make a.x 1 and test it")
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertEqual(Path(a.root, "f0.txt").read_text(), "0\n")
        self.assertEqual(Path(a.root, "z", "x.txt").read_text(), "x\nmore\n")
        self.assertIn("tests failed at the end -> files put back to when they last passed", a.tools.repairs)

    def test_never_passed_means_nothing_to_go_back_to(self):
        a = self.run_turn([write("a.py", "x = 2\n"), write("test_a.py", TEST), reply("", tool=RUN)])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 2\n")

    def test_it_can_be_turned_off(self):
        a = self.run_turn([write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                           write("a.py", "x = 2\n")], safety_net=False)
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 2\n")

    def test_a_command_that_also_writes_isnt_run_again(self):
        # rerunning it would write the old a.py over the later work, then pass with no note
        a = self.run_turn([write("test_a.py", TEST), reply("", tool=WRITE_AND_RUN), reply("", tool=READ_A),
                           write("a.py", "x = 1\ny = 2  # more work\n")])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\ny = 2  # more work\n")
        self.assertFalse(a.tools.repairs)

    def test_a_late_break_after_one_is_still_put_back(self):
        a = self.run_turn([write("test_a.py", TEST), reply("", tool=WRITE_AND_RUN), reply("", tool=READ_A),
                           write("a.py", "x = 2\n")])
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertIn("tests failed at the end -> files put back to when they last passed", a.tools.repairs)

    def test_what_only_runs_tests(self):
        for cmd in ("python3 -m unittest test_a", "cd sub && python -m pytest -q 2>&1 | tail -5",
                    "pytest -x tests/test_a.py", "python3 test_a.py", "npm test", "cargo test 2>/dev/null"):
            self.assertTrue(ONLY_TESTS(cmd), cmd)
        for cmd in ("cat > a.py <<'EOF'\nx = 1\nEOF\npython3 -m unittest test_a", "sed -i 's/2/1/' a.py && pytest",
                    "git checkout . && pytest", "python3 gen.py; pytest", "pytest > log.txt", "pytest $(echo x)", ""):
            self.assertFalse(ONLY_TESTS(cmd), cmd)

    def test_the_run_stops_at_its_time_limit(self):
        a = one_shot(time_limit=600)
        a._begin_turn("x")
        self.assertAlmostEqual(a._stop_at, time.monotonic() + 600, delta=5)

    def test_what_counts_as_passing(self):
        self.assertTrue(TESTS_PASSED("....\n------\nRan 4 tests in 0.01s\n\nOK\n[exit code 0]"))
        self.assertTrue(TESTS_PASSED("===== 12 passed in 0.3s =====\n[exit code 0]"))
        self.assertFalse(TESTS_PASSED("Ran 0 tests in 0.000s\n\nNO TESTS RAN\n[exit code 5]"))
        self.assertFalse(TESTS_PASSED("Ran 0 tests in 0.000s\n\nOK\n[exit code 0]"))
        self.assertFalse(TESTS_PASSED("== 3 passed, 1 failed ==\n[exit code 0]"))  # a pipe hid the code
        self.assertFalse(TESTS_PASSED("Ran 4 tests in 0.01s\n\nFAILED (failures=1)\n[exit code 1]"))
        self.assertFalse(TESTS_PASSED("hello\n[exit code 0]"))


class OddFilesTest(unittest.TestCase):
    """What a command leaves behind is looked at before it's read: a FIFO hung the run in open(), and a
    multi-GB file was read whole before the size check (a MemoryError in a small container)."""

    def started(self):
        a = one_shot(time_limit=600)
        Path(a.root, "a.py").write_text("x = 1\n")
        a._disk_before = a._disk_snapshot()
        return a

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_a_fifo_a_command_made_doesnt_hang_the_run(self):
        a = self.started()
        fifo = Path(a.root, "events.pipe")
        os.mkfifo(fifo)
        Path(a.root, "a.py").write_text("x = 2\n")
        done = threading.Event()

        def go():
            a._keep_good("python3 -m unittest")
            a._change_diff(10000)
            done.set()
        threading.Thread(target=go, daemon=True).start()
        finished = done.wait(5)
        if not finished:  # free the blocked reader so the test run can end
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        self.assertTrue(finished, "blocked reading a FIFO")
        self.assertEqual(set(a._good[1]), {Path(a.root, "a.py")})

    def test_a_big_file_is_not_read_to_find_out_its_too_big(self):
        a = self.started()
        Path(a.root, "a.py").write_text("x = 2\n")
        big = Path(a.root, "data.bin")
        big.write_bytes(b"0" * 5000)
        read = []
        real = Path.read_bytes

        def spy(p):
            read.append(p)
            return real(p)
        with mock.patch.object(agent_mod, "SAFETY_BYTES", 1000), mock.patch.object(Path, "read_bytes", spy):
            a._keep_good("python3 -m unittest")
            self.assertIsNone(getattr(a, "_good", None))  # too big to keep: no net
            a._tests_net("python3 -m unittest", {Path(a.root, "a.py"): b"x = 1\n"})
        self.assertNotIn(big, read)
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 2\n")  # no net: the work is left alone
        self.assertTrue(big.exists())


class BaselineTest(unittest.TestCase):
    """extras=false (Terminal-Bench) and purr-bare compare with runs that had no safety net: no stop at
    the time limit, no files put back."""

    def test_extras_false_leaves_it_out(self):
        import ast
        source = (Path(__file__).resolve().parent.parent / "tbench" / "purr_agent.py").read_text()
        extras = next(ast.literal_eval(node.value) for node in ast.parse(source).body if isinstance(node, ast.Assign)
                      and getattr(node.targets[0], "id", None) == "EXTRAS")  # without importing harbor
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False, "review": False, **{key: False for key in extras}}
        scripted(a, [write("a.py", "x = 1\n"), write("test_a.py", TEST), reply("", tool=RUN),
                     write("a.py", "x = 2\n"), reply("done")])
        a.turn("make a.x 1 and test it")
        self.assertIsNone(a._stop_at)
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 2\n")

    def test_purr_bare_leaves_it_out(self):
        import shutil
        from unittest import mock

        from harness import bench
        from harness.agent import Agent
        from tests.test_limits import CONFIG
        heard = []
        task = bench.load_tasks({"rename"})[0]
        work = Path(tempfile.mkdtemp()) / "rename"
        shutil.copytree(task["dir"] / "files", work)
        with mock.patch.object(Agent, "turn", lambda agent, text: heard.append(agent._on("safety_net"))):
            bench.run_purr(CONFIG, "small", task, work, Path(tempfile.mkdtemp()), 600, extras=False)
        self.assertEqual(heard, [False])


if __name__ == "__main__":
    unittest.main()


class SyntaxNetTest(unittest.TestCase):
    def test_a_file_left_broken_gets_its_last_version_that_compiled(self):
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False}
        scripted(a, [write("a.py", "def f():\n    return 1\n"), write("a.py", "def f():\nreturn 2\n"),
                     write("b.py", "def g(:\n"), reply("I'm out of time.")])
        a.turn("write f")
        self.assertEqual(Path(a.root, "a.py").read_text(), "def f():\n    return 1\n")
        self.assertEqual(Path(a.root, "b.py").read_text(), "def g(:\n")  # never compiled: nothing to go back to
        self.assertIn("a file left with a syntax error -> its last version that compiled", a.tools.repairs)

    def test_code_the_projects_python_takes_stays(self):
        # purr on uv's 3.12, the model's python3 a 3.14 that takes `except A, B:`: not a broken file
        fake = Path(tempfile.mkdtemp(), "python3")
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        newer = "def f(s):\n    try:\n        return int(s)\n    except ValueError, TypeError:\n        return 0\n"
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{fake.parent}{os.pathsep}{old_path}"
        try:
            a = one_shot(time_limit=600)
            a.config = {**a.config, "final_check": False}
            scripted(a, [write("a.py", "def f(s):\n    return int(s)\n"), write("a.py", newer), reply("done")])
            a.turn("make f return 0 on bad input")
        finally:
            os.environ["PATH"] = old_path
        self.assertEqual(Path(a.root, "a.py").read_text(), newer)
        self.assertFalse(a.tools.repairs)

    def test_tests_that_never_import_a_file_dont_vouch_for_it(self):
        # the tests passed with a.py broken, but they don't import it: that proves nothing about a.py
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False, "review": False}
        passes = "import unittest\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n"
        scripted(a, [write("a.py", "x = 1\n"), write("a.py", "x = (\n"), write("test_a.py", passes),
                     reply("", tool=RUN), reply("done")])
        a.turn("make a.x 1")
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = 1\n")
        self.assertIn("a file left with a syntax error -> its last version that compiled", a.tools.repairs)

    def test_code_the_projects_venv_takes_stays(self):
        # the tests run on the project's own venv (uv run, .venv/bin/python), newer than python3 on PATH
        a = one_shot(time_limit=600)
        a.config = {**a.config, "final_check": False, "review": False}
        venv_python = Path(a.root, ".venv", "bin", "python")
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("#!/bin/sh\nexit 0\n")
        venv_python.chmod(0o755)
        scripted(a, [write("a.py", "x = 1\n"), write("a.py", "x = (\n"), reply("done")])
        a.turn("make a.x 1")
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = (\n")
        self.assertFalse(a.tools.repairs)

    def test_chats_keep_what_the_model_wrote(self):
        from tests.test_limits import agent
        a = agent()
        a.tools.trust_all = True
        scripted(a, [write("a.py", "x = 1\n"), write("a.py", "x = (\n"), reply("oops")])
        a.turn("write a.py")
        self.assertEqual(Path(a.root, "a.py").read_text(), "x = (\n")
