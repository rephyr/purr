"""Switching mode also switches to that mode's model (mode_models in config.toml).

Run: python3 -m unittest discover tests
No model is called.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import commands  # noqa: E402
from harness.agent import Agent  # noqa: E402
from tests.test_limits import FakeView  # noqa: E402

CONFIG = {
    "providers": {"ollama": {"base_url": "http://127.0.0.1:1/v1"},
                  "paid": {"base_url": "http://127.0.0.1:1/v1", "api_key_env": "PURR_TEST_NO_SUCH_KEY"}},
    "models": {
        "local": {"provider": "ollama", "id": "local", "context": 32768},
        "big": {"provider": "ollama", "id": "big", "context": 1_000_000},
        "writer": {"provider": "ollama", "id": "writer", "context": 131072},
        "nokey": {"provider": "paid", "id": "nokey", "context": 131072},
    },
    "mode_models": {"code": "local", "ask": "big", "chat": "writer", "create": "nokey"},
}


def agent(config=CONFIG):
    return Agent(config, tempfile.mkdtemp(), "local", FakeView())


class SwitchTest(unittest.TestCase):
    def test_each_mode_gets_its_model(self):
        a = agent()
        self.assertEqual(a.switch_mode("ask"), "model: big")
        self.assertEqual((a.mode, a.model_name), ("ask", "big"))
        a.switch_mode("chat")
        self.assertEqual(a.model_name, "writer")
        self.assertEqual(a.switch_mode("code"), "model: local")
        self.assertEqual(a.model_name, "local")

    def test_a_hand_picked_model_sticks_to_its_mode(self):
        a = agent()
        a.switch_mode("chat")
        a.set_model("big")  # /model big while chatting
        a.switch_mode("code")
        self.assertEqual(a.model_name, "local")
        a.switch_mode("chat")
        self.assertEqual(a.model_name, "big")  # the pick, not the config's writer

    def test_missing_key_keeps_the_current_model(self):
        os.environ.pop("PURR_TEST_NO_SUCH_KEY", None)
        a = agent()
        moved = a.switch_mode("create")
        self.assertEqual(a.mode, "create")
        self.assertEqual(a.model_name, "local")
        self.assertTrue(moved.startswith("kept local"))

    def test_a_mode_not_listed_keeps_the_model(self):
        a = agent()
        self.assertIsNone(a.switch_mode("plan"))
        self.assertEqual(a.model_name, "local")

    def test_no_mode_models_changes_nothing(self):
        a = agent({k: v for k, v in CONFIG.items() if k != "mode_models"})
        for mode in ("ask", "chat", "create", "code"):
            self.assertIsNone(a.switch_mode(mode))
        self.assertEqual(a.model_name, "local")

    def test_set_mode_alone_never_moves_the_model(self):
        # helpers and the planner use set_mode: they must stay on the model they were given
        a = agent()
        a.set_mode("ask")
        self.assertEqual(a.model_name, "local")


class CommandTest(unittest.TestCase):
    def test_mode_command_says_the_model(self):
        a = agent()
        out = commands.run(a, "/mode ask")
        self.assertIn(("info", "model: big"), out)
        self.assertEqual(a.model_name, "big")


if __name__ == "__main__":
    unittest.main()
