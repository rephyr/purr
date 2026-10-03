"""Tests for /pr (no model, no GitHub: a local folder plays "origin").

Run: python3 -m unittest discover tests
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import pr  # noqa: E402
from tests.test_limits import agent, reply, scripted  # noqa: E402


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class PRTest(unittest.TestCase):
    def setUp(self):
        self.a = agent()
        root, origin = self.a.root, Path(tempfile.mkdtemp()) / "origin.git"
        sh(root, "git", "init", "-q", "--bare", str(origin))
        sh(root, "git", "init", "-q", "-b", "main")
        sh(root, "git", "config", "user.name", "test")
        sh(root, "git", "config", "user.email", "test@test")
        (root / "a.py").write_text("x = 1\n")
        sh(root, "git", "add", "-A")
        sh(root, "git", "commit", "-qm", "start")
        sh(root, "git", "remote", "add", "origin", str(origin))
        (root / "a.py").write_text("x = 2\n")

    def test_draft_reads_title_and_body(self):
        scripted(self.a, [reply("TITLE: Make x two\nBODY:\n- x is 2 now")])
        self.assertEqual(pr.draft(self.a), (f"Make x two · {self.a.model_name}", "- x is 2 now"))

    def test_create_branches_commits_with_co_author_and_pushes(self):
        with self.assertRaises(RuntimeError) as err:  # no GitHub here, so gh pr create fails
            pr.create(self.a, "Make x two", "- x is 2 now")
        self.assertIn("pushed purr/make-x-two", str(err.exception))
        root = self.a.root
        title = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=root, capture_output=True, text=True).stdout
        self.assertEqual(title.strip(), f"Make x two · {self.a.model_name}")  # the model in the top bar
        root = self.a.root
        msg = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=root, capture_output=True, text=True).stdout
        self.assertIn(f"Co-Authored-By: purr-{self.a.model_name} <", msg)
        self.assertIn(f"Model: {self.a.model_name}", msg)
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root,
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "purr/make-x-two")  # never straight onto main

    def test_a_report_gets_its_own_branch_and_the_checkout_goes_back(self):
        root = self.a.root
        sh(root, "git", "checkout", "-q", "-b", "feature")  # even off a feature branch
        with self.assertRaises(RuntimeError) as err:  # no GitHub here, so gh pr create fails after the push
            pr.open_pr(root, {"co_author_email": "1+purr-harness@users.noreply.github.com"}, "claude-opus-5.5",
                       "Benchmark: Terminal-Bench 2.1 one 88.8%", "- the run", new_branch=True, back=True)
        self.assertIn("pushed purr/benchmark-terminal-bench-2-1-one-88-8", str(err.exception))
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root,
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "feature")  # back where it was
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                                        text=True).stdout.strip(), "")  # clean for the next run
        msg = subprocess.run(["git", "log", "-1", "--format=%B", "purr/benchmark-terminal-bench-2-1-one-88-8"],
                             cwd=root, capture_output=True, text=True).stdout
        self.assertIn("Co-Authored-By: purr-claude-opus-5.5 <1+purr-harness@users.noreply.github.com>", msg)
        self.assertIn("· claude-opus-5.5", msg.splitlines()[0])

    def test_model_is_added_once(self):
        self.assertEqual(pr.with_model("Fix it · m", "m"), "Fix it · m")
        self.assertEqual(pr.with_model("Fix it", "m"), "Fix it · m")

    def test_slug(self):
        self.assertEqual(pr.slug("Fix: happy-hour discount (20%)!"), "purr/fix-happy-hour-discount-20")


if __name__ == "__main__":
    unittest.main()
