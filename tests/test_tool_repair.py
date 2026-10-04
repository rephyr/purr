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
