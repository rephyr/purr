"""Small fixes from the code review: purr bench's failure count, a missing file's suggestion,
an MCP server that crashed, one job per leaderboard submission.
No model is called.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import bench  # noqa: E402
from harness.mcp import Mcp  # noqa: E402
from tests.test_limits import agent, known  # noqa: E402


class GradeTest(unittest.TestCase):
    def test_skipped_tests_are_not_failures(self):
        task = Path(tempfile.mkdtemp())
        (task / "check").mkdir()
        (task / "check" / "test_x.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        pass\n\n"
            "    def test_bad(self):\n        self.fail('no')\n\n"
            "    @unittest.skip('later')\n    def test_skip(self):\n        pass\n")
        work = Path(tempfile.mkdtemp())
        passed, total, _ = bench.grade({"dir": task, "expected": 3}, work)
        self.assertEqual((passed, total), (2, 3))  # only the failure counts against it


class MissingFileTest(unittest.TestCase):
    def test_editing_a_guessed_path_names_the_real_ones(self):
        a = agent()
        a.tools.trust_all = True
        (Path(a.root) / "inventory.py").write_text("x = 1\n")
        known(a)
        out = a.tools.call("edit_file", json.dumps({"path": "src/inventory.py", "old_text": "x", "new_text": "y"}))
        self.assertIn("no file src/inventory.py", out)
        self.assertIn("inventory.py", out.split("Did you mean")[-1])
        self.assertNotIn("FileNotFoundError", out)


class McpRestartTest(unittest.TestCase):
    def test_a_crashed_server_is_started_again(self):
        m = Mcp({}, tempfile.mkdtemp())
        m.started = True
        server = mock.Mock(name="srv", proc=mock.Mock(poll=lambda: 1))
        server.name = "codebase"
        server.call.return_value = "outline ok"
        m.by_tool = {"outline": (server, {})}
        self.assertEqual(m.call("outline", {}), "outline ok")
        server.start.assert_called_once()

    def test_one_that_wont_start_says_so(self):
        m = Mcp({}, tempfile.mkdtemp())
        m.started = True
        server = mock.Mock(proc=mock.Mock(poll=lambda: 1))
        server.name = "codebase"
        server.start.side_effect = OSError("gone")
        m.by_tool = {"outline": (server, {})}
        self.assertIn("wouldn't start again", m.call("outline", {}))


class SubmissionFolderTest(unittest.TestCase):
    def test_an_earlier_jobs_copy_is_replaced(self):
        spec = importlib.util.spec_from_file_location("submit", ROOT / "tbench" / "submit.py")
        submit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(submit)
        from tests.test_submit import make_job
        out = Path(tempfile.mkdtemp())
        with mock.patch.object(submit, "OUT", out), mock.patch.object(submit, "known_keys", lambda: []):
            first, second = make_job(), make_job()
            (second.parent / "later").mkdir()
            later = second.rename(second.parent / "later" / "2026-10-04__20-00-00")
            self.assertEqual(submit.main([str(first)]), 0)
            self.assertEqual(submit.main([str(later)]), 0)
        folder = out / "purr__deepseek-v4.1-flash"
        self.assertEqual(sorted(p.name for p in folder.iterdir() if p.is_dir()), [later.name])
