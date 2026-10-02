"""/private: a fresh chat with local models only, no web, nothing saved.

Run: python3 -m unittest discover tests
No model is called.
"""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import commands  # noqa: E402
from harness.agent import LOG_DIR, Agent  # noqa: E402
from tests.test_limits import FakeView, reply, scripted  # noqa: E402

CONFIG = {
    "default_model": "cloud",
    "providers": {"ollama": {"base_url": "http://127.0.0.1:1/v1"}, "or": {"base_url": "http://127.0.0.1:1/v1"}},
    "models": {
        "cloud": {"provider": "or", "id": "cloud", "context": 262144, "price": {"hit": 1, "miss": 1, "out": 1}},
        "qwen": {"provider": "ollama", "id": "qwen", "context": 32768},
        "free": {"provider": "or", "router": "free", "id": "(auto)", "context": 262144},
    },
    "mode_models": {"chat": "cloud"},
    "plan": {"executor": "qwen", "planner": "cloud"},
}


def agent():
    return Agent(copy.deepcopy(CONFIG), tempfile.mkdtemp(), "cloud", FakeView())


class PrivateTest(unittest.TestCase):
    def test_on_switches_to_local_and_starts_fresh(self):
        a = agent()
        a.messages.append({"role": "user", "content": "secret plans"})
        self.assertEqual(a.set_private(True), "qwen")
        self.assertEqual((a.model_name, len(a.messages)), ("qwen", 1))
        self.assertIsNone(a.log_path)

    def test_nothing_is_saved(self):
        a = agent()
        a.set_private(True)
        before = set(LOG_DIR.glob("*.json")) if LOG_DIR.exists() else set()
        scripted(a, [reply("hi")])
        a.turn("my secret")
        after = set(LOG_DIR.glob("*.json")) if LOG_DIR.exists() else set()
        self.assertEqual(after, before)

    def test_api_models_are_refused_everywhere(self):
        a = agent()
        a.set_private(True)
        for name in ("cloud", "free"):
            with self.assertRaises(KeyError):
                a.set_model(name)
        self.assertIsNone(a.switch_mode("chat"))  # chat's API model stays out
        self.assertEqual(a.model_name, "qwen")
        with self.assertRaises(KeyError):  # helpers, the planner and ticket workers share the config
            Agent(a.config, a.root, "cloud", FakeView())

    def test_no_web(self):
        a = agent()
        a.set_private(True)
        with mock.patch("harness.agent.stream_chat") as fake:
            fake.return_value = {"text": "", "tool_calls": [], "usage": {}, "finish": "stop", "reasoning": ""}
            a._call()
            offered = [t["function"]["name"] for t in fake.call_args[0][2]["tools"]]
        self.assertNotIn("fetch_url", offered)
        a.tools.trust_all = True
        self.assertIn("private mode", a.tools.call("fetch_url", json.dumps({"url": "example.com"})))

    def test_commands(self):
        a = agent()
        on = commands.run(a, "/private")
        self.assertIn("🔒 private", on[0][1])
        self.assertEqual([line.split()[1] for _, line in commands.run(a, "/model")], ["qwen"])
        self.assertEqual(commands.run(a, "/pr")[0][0], "error")
        self.assertEqual(commands.run(a, "/free")[0][0], "error")
        off = commands.run(a, "/private")
        self.assertIn("left private mode", off[0][1])
        self.assertFalse(a.private)
        self.assertIsNotNone(a.log_path)  # saved again from here on

    def test_needs_a_local_model(self):
        config = copy.deepcopy(CONFIG)
        del config["models"]["qwen"]
        a = Agent(config, tempfile.mkdtemp(), "cloud", FakeView())
        self.assertEqual(commands.run(a, "/private")[0][0], "error")
        self.assertFalse(a.private)

    def test_godot_docs_stay_offline(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "servers"))
        import stack
        with mock.patch.dict(os.environ, {"PURR_OFFLINE": "1", "XDG_CACHE_HOME": tempfile.mkdtemp()}), \
                mock.patch.object(stack, "CACHE", Path(tempfile.mkdtemp())), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("went online")):
            self.assertIsNone(stack.full_doc("Node", "4.7.2-stable"))


if __name__ == "__main__":
    unittest.main()
