"""purr --key keeps any key readable; an MCP tool named like one of purr's aliases is reachable.
No model is called and no real key is touched (the keys file is a temporary one).
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import agent as agent_module  # noqa: E402
from harness.tools import Tools  # noqa: E402
from tests.test_limits import FakeView  # noqa: E402

spec = importlib.util.spec_from_file_location("purr_main", ROOT / "purr.py")
purr_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(purr_main)


class SavedKeyTest(unittest.TestCase):
    def test_a_key_with_quotes_and_backslashes_survives(self):
        keys = Path(tempfile.mkdtemp()) / "keys.toml"
        odd = 'sk-abc"def\\ghi'
        config = {"providers": {"groq": {"api_key_env": "GROQ_API_KEY"}, "x": {"api_key_env": "X_KEY"}}}
        with mock.patch.object(agent_module, "KEYS_FILE", keys), mock.patch("getpass.getpass", return_value=odd):
            self.assertEqual(purr_main.save_key("groq", config), 0)
            self.assertEqual(agent_module.saved_key("GROQ_API_KEY"), odd)
            with mock.patch("getpass.getpass", return_value="plain-key"):
                purr_main.save_key("x", config)
            self.assertEqual(agent_module.saved_key("GROQ_API_KEY"), odd)  # still there next to another
            self.assertEqual(agent_module.saved_key("X_KEY"), "plain-key")


class McpAliasTest(unittest.TestCase):
    def tools_with_mcp(self, names):
        t = Tools(tempfile.mkdtemp(), FakeView())
        t.trust_all = True
        t.mcp = mock.Mock(has=lambda n: n in names, read_only=lambda n: True,
                          call=lambda n, a: f"mcp {n} {a}")
        return t

    def test_an_mcp_tool_named_like_an_alias_is_called(self):
        t = self.tools_with_mcp({"search"})
        self.assertEqual(t.call("search", '{"query": "x"}'), "mcp search {'query': 'x'}")

    def test_without_one_the_alias_still_works(self):
        t = self.tools_with_mcp(set())
        out = t.call("search", '{"pattern": "nothing-here-at-all"}')
        self.assertIn("no matches", out)  # went to grep
        self.assertIn("tool search -> grep", t.repairs)
