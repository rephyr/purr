"""Tests for purr bench's grading (no model is called).

Run: python3 -m unittest discover tests
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import bench  # noqa: E402


def copy_of(task_name):
    task = next(t for t in bench.load_tasks() if t["name"] == task_name)
    work = Path(tempfile.mkdtemp()) / "work"
    shutil.copytree(task["dir"] / "files", work)
    return task, work


class GradeTest(unittest.TestCase):
    def test_every_task_starts_unsolved(self):
        for task in bench.load_tasks():
            _, work = copy_of(task["name"])
            passed, total, _ = bench.grade(task, work)
            self.assertLess(passed, total, task["name"])
            self.assertGreater(total, 0, task["name"])

    def test_a_fix_is_graded_as_solved(self):
        task, work = copy_of("purr-meter")
        f = work / "purr_meter.py"
        f.write_text(f.read_text().replace("pets < 2", "pets <= 2"))
        self.assertEqual(bench.grade(task, work)[:2], (1, 1))

    def test_syntax_errors_are_counted(self):
        task, work = copy_of("purr-meter")
        (work / "broken.py").write_text("def oops(:\n")
        self.assertEqual(bench.grade(task, work)[2], 1)

    def test_false_claim_is_a_hallucination(self):
        task, work = copy_of("purr-meter")
        r = bench.blank("purr", "m", task)
        r["final_text"] = "Fixed it, all tests pass now!"
        r = bench.finish(r, task, work)
        self.assertTrue(r["false_claim"])
        self.assertEqual(r["hallucinations"], 1)


class OutsideTest(unittest.TestCase):
    def test_leaving_the_task_folder_is_caught(self):
        work = Path("/tmp/bench-x/work")
        self.assertTrue(bench.outside({"command": "cd /home/someone/project && git status"}, work))
        self.assertTrue(bench.outside({"command": "ls ~/projects"}, work))
        self.assertFalse(bench.outside({"command": "python3 -m unittest discover tests"}, work))
        self.assertFalse(bench.outside({"path": "cafe/menu.py"}, work))
        self.assertFalse(bench.outside({"command": "/usr/bin/python3 x.py"}, work))


if __name__ == "__main__":
    unittest.main()
