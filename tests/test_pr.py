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
        self.assertEqual(pr.draft(self.a), ("Make x two", "- x is 2 now"))

    def test_create_branches_commits_with_co_author_and_pushes(self):
        with self.assertRaises(RuntimeError) as err:  # no GitHub here, so gh pr create fails
            pr.create(self.a, "Make x two", "- x is 2 now")
        self.assertIn("pushed purr/make-x-two", str(err.exception))
        root = self.a.root
        msg = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=root, capture_output=True, text=True).stdout
        self.assertIn(f"Co-Authored-By: purr-{self.a.model_name} <", msg)
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root,
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "purr/make-x-two")  # never straight onto main

    def test_slug(self):
        self.assertEqual(pr.slug("Fix: happy-hour discount (20%)!"), "purr/fix-happy-hour-discount-20")


if __name__ == "__main__":
    unittest.main()
