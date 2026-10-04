"""Logic a silent regression in would cost whole benchmark runs, tested directly: tool calls written
as text, the indentation-forgiving edit match, @file attachments, the free router's switch.
No model is called.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import calls_from_text  # noqa: E402
from harness.api import ApiError  # noqa: E402
from harness.tools import _loose_match  # noqa: E402
from tests.test_limits import agent  # noqa: E402


class TextCallTest(unittest.TestCase):
    def test_qwen_style_calls_become_real_ones(self):
        text = ("Let me look.\n<tool_call>\n<function=read_file>\n<parameter=path>\napp.py\n</parameter>\n"
                "<parameter=offset>\n40\n</parameter>\n</function>\n</tool_call>\n"
                "<function=todo><parameter=items>[{\"text\": \"a\", \"status\": \"doing\"}]</parameter></function>")
        calls, cleaned = calls_from_text(text)
        self.assertEqual([c["name"] for c in calls], ["read_file", "todo"])
        self.assertEqual(json.loads(calls[0]["args"]), {"path": "app.py", "offset": 40})  # typed as the schema says
        self.assertEqual(json.loads(calls[1]["args"])["items"][0]["status"], "doing")
        self.assertEqual(cleaned, "Let me look.")

    def test_plain_text_has_no_calls(self):
        self.assertEqual(calls_from_text("just an answer"), ([], "just an answer"))


class LooseMatchTest(unittest.TestCase):
    def test_wrong_indentation_still_finds_the_lines_and_keeps_the_files(self):
        text = "def f():\n    if x:\n        return 1\n    return 2\n"
        old = "if x:\n  return 1"            # the model's 2-space version
        new = "if x:\n  return 10\n  # done"
        real, fixed = _loose_match(text, old, new)
        self.assertEqual(real, "    if x:\n        return 1\n")
        self.assertEqual(fixed, "    if x:\n        return 10\n        # done\n")

    def test_ambiguous_or_missing_is_none(self):
        twice = "a:\n    b\nc:\n    b\n"
        self.assertIsNone(_loose_match(twice, "  b", "  c"))
        self.assertIsNone(_loose_match("x = 1\n", "y = 2", "y = 3"))


class AttachTest(unittest.TestCase):
    def test_at_file_attaches_it_once_and_cuts_long_ones(self):
        a = agent()
        root = Path(a.root)
        (root / "shop.py").write_text("PRICE = 3\n")
        (root / "big.txt").write_text("x" * (a.limits.attach_max + 50))
        out = a.expand("fix @shop.py, then @shop.py again and @missing.py and @big.txt.")
        self.assertEqual(out.count('<file path="shop.py">'), 1)
        self.assertIn("PRICE = 3", out)
        self.assertNotIn("missing.py\">", out)
        self.assertIn("[file cut short; use read_file for the rest]", out)
        self.assertEqual(a.expand("no files here"), "no files here")


class FreeRouterTest(unittest.TestCase):
    def router_agent(self, pick="next-free"):
        a = agent()
        a.router = mock.Mock(pick=mock.Mock(return_value=pick), next_free_at=lambda mode: "21:00")
        a._use_model = mock.Mock()
        a._refused = 0
        return a

    def test_a_maxed_out_model_rests_and_the_next_one_takes_over(self):
        a = self.router_agent()
        err = ApiError("429: rate limit exceeded: free-models-per-min", status=429)
        with mock.patch("harness.agent.rest_for", return_value=(60, "busy", "model")):
            self.assertTrue(a._next_free(err))
        a.router.rest.assert_called_once_with(a.model_name, 60, "busy", "model")
        a._use_model.assert_called_once_with("next-free")

    def test_every_model_resting_gives_up(self):
        a = self.router_agent(pick=None)
        with mock.patch("harness.agent.rest_for", return_value=(60, "busy", "model")):
            self.assertFalse(a._next_free(ApiError("429", status=429)))

    def test_a_refused_request_moves_on_twice_at_most(self):
        a = self.router_agent()
        with mock.patch("harness.agent.rest_for", return_value=None):
            results = [a._next_free(ApiError("400: bad param", status=400)) for _ in range(3)]
        self.assertEqual(results, [True, True, False])  # a request every model refuses mustn't empty the list

    def test_no_router_no_switch(self):
        a = agent()
        a.router = None
        self.assertFalse(a._next_free(ApiError("429", status=429)))
