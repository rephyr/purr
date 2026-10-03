"""purr setup: the logic (harness/onboard.py) and the window (tui/setup.py), as a brand-new user.

Run: python3 -m unittest discover tests
No real server or provider is asked anything.
"""

import json
import os
import sys
import tempfile
import tomllib
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import agent, discover, onboard, settings  # noqa: E402

try:
    from tui import setup as setup_window
except ImportError:  # textual comes with uv sync
    setup_window = None


class NewUser(unittest.TestCase):
    """A fresh config folder, keys file and state folder for every test."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for target, name, value in ((settings, "CONFIG_DIR", self.dir), (settings, "USER_CONFIG", self.dir / "config.toml"),
                                    (agent, "KEYS_FILE", self.dir / "keys.toml"), (agent, "STATE_DIR", self.dir / "state"),
                                    (discover, "CACHE", self.dir / "cache.json")):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {k: "" for k in os.environ if k.endswith("_API_KEY")})
        env.start()
        self.addCleanup(env.stop)
        nocode = mock.patch.object(agent, "opencode_key", return_value=None)  # no OpenCode keys either
        nocode.start()
        self.addCleanup(nocode.stop)


class OnboardTest(NewUser):
    def test_a_key_is_checked_then_saved_only_for_you(self):
        provider = {"base_url": "https://api.example.com/v1", "api_key_env": "EX_API_KEY"}
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 401, "no", {}, None)):
            self.assertEqual(onboard.check_key(provider, "bad"), (False, "the key was refused (401)"))
        with mock.patch("urllib.request.urlopen") as ok:
            self.assertTrue(onboard.check_key(provider, "good")[0])
        self.assertTrue(ok.call_args.args[0].full_url.endswith("/models"))
        with mock.patch("urllib.request.urlopen") as ok:  # OpenRouter's model list needs no key: its /key does
            onboard.check_key({"base_url": "https://openrouter.ai/api/v1"}, "k")
        self.assertTrue(ok.call_args.args[0].full_url.endswith("/key"))
        onboard.store_key("EX_API_KEY", 'odd"key')
        self.assertEqual(agent.saved_key("EX_API_KEY"), 'odd"key')
        self.assertEqual(oct(agent.KEYS_FILE.stat().st_mode & 0o777), "0o600")
        self.assertEqual(onboard.key_source(provider), "saved")

    def test_your_own_provider_and_its_models(self):
        added = onboard.custom_provider("My Work", "https://llm.example.com/v1/", "secret",
                                        [("org/big-coder:latest", 200000), ("small", None)])
        self.assertEqual(added["providers"], {"my-work": {"base_url": "https://llm.example.com/v1",
                                                          "api_key_env": "MY_WORK_API_KEY"}})
        self.assertEqual(added["models"]["big-coder"], {"provider": "my-work", "id": "org/big-coder:latest",
                                                        "context": 200000})
        self.assertEqual(added["models"]["small"]["context"], 131072)  # the server didn't say
        self.assertEqual(agent.saved_key("MY_WORK_API_KEY"), "secret")

    def test_local_first_roomiest_on_top_and_the_pick(self):
        config = {"providers": {"ollama": {"base_url": "x"}, "openrouter": {"base_url": "y", "api_key_env": "OR_KEY"},
                                "groq": {"base_url": "z", "api_key_env": "GROQ_API_KEY"}},
                  "models": {"api": {"provider": "openrouter", "id": "a", "context": 1000000},
                             "tiny": {"provider": "ollama", "id": "t", "context": 4096, "found": True},
                             "mid": {"provider": "ollama", "id": "m", "context": 32768, "found": True},
                             "big": {"provider": "ollama", "id": "b", "context": 131072, "found": True},
                             "nokey": {"provider": "groq", "id": "g", "context": 128000}}}
        with mock.patch.dict(os.environ, {"OR_KEY": "k"}):
            self.assertEqual([n for n, _ in onboard.usable_models(config)], ["big", "mid", "api"])  # no tiny, no nokey
            self.assertEqual(onboard.recommend(config), "big")
            self.assertEqual(onboard.too_small(config), ["tiny"])
            for name in ("big", "mid"):
                del config["models"][name]
            self.assertEqual(onboard.recommend(config), "api")  # nothing roomy locally: the API one with a key

    def test_finish_saves_your_choices(self):
        onboard.finish("big", cat_name="Luna", theme="plum",
                       extra={"providers": {"my-work": {"base_url": "https://llm.example.com/v1"}}})
        saved = tomllib.loads(settings.USER_CONFIG.read_text())
        self.assertEqual((saved["default_model"], saved["theme"]), ("big", "plum"))
        self.assertEqual(saved["providers"]["my-work"]["base_url"], "https://llm.example.com/v1")
        self.assertEqual(json.loads((agent.STATE_DIR / "cat.json").read_text())["name"], "Luna")
        self.assertEqual((agent.STATE_DIR / "theme").read_text(), "plum")
        self.assertTrue(settings.is_set_up())

    def test_a_roomier_copy_is_only_settings(self):
        config = {"providers": {"ollama": {"base_url": "http://127.0.0.1:11434/v1"}},
                  "models": {"qwen3.6:35b": {"provider": "ollama", "id": "hf.co/unsloth/Qwen3.6:IQ3", "found": True}}}
        with mock.patch.object(discover, "_get") as get:
            self.assertEqual(onboard.make_roomy(config, "qwen3.6:35b"), "qwen3.6-iq3-32k")
        url, body = get.call_args.args
        self.assertEqual(url, "http://127.0.0.1:11434/api/create")
        self.assertEqual(body, {"model": "qwen3.6-iq3-32k", "from": "hf.co/unsloth/Qwen3.6:IQ3",
                                "parameters": {"num_ctx": 32768}, "stream": False})


class FirstStartTest(NewUser):
    def test_the_first_start_opens_setup_and_leaving_it_starts_nothing(self):
        import purr
        folder = tempfile.mkdtemp()
        with mock.patch.object(sys, "argv", ["purr", folder]), mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch.object(purr, "full_screen_python", return_value=True), \
                mock.patch.object(purr, "run_setup", return_value=None) as setup, \
                mock.patch("tui.app.PurrApp") as app:
            self.assertEqual(purr.main(), 0)
        setup.assert_called_once()
        app.assert_not_called()
        settings.USER_CONFIG.write_text('default_model = "x"\n')  # set up: no setup window this time
        with mock.patch.object(sys, "argv", ["purr", "--plain", "-p", "hi", folder]), \
                mock.patch.object(purr, "run_setup") as setup, mock.patch.object(purr, "Agent") as fake:
            fake.return_value.failed = None
            purr.main()
        setup.assert_not_called()


@unittest.skipIf(setup_window is None, "needs textual (uv sync)")
class SetupWindowTest(NewUser, unittest.IsolatedAsyncioTestCase):
    async def test_from_hello_to_all_set_as_a_new_user(self):
        found = {"ollama": [("coder-64k", {"context": 65536}), ("tiny", {"context": 4096})]}
        with mock.patch.object(discover, "find", return_value=found):
            app = setup_window.SetupApp(settings.load(discover=False))
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.click("#next")          # hello -> local models
                await pilot.pause(0.5)
                self.assertIn("2 models that can use tools", str(app.query_one("#local-text").render()))
                self.assertTrue(app.query_one("#roomy").display)  # tiny gets the offer of a roomier copy
                await pilot.click("#next")          # -> API providers
                await pilot.pause(0.2)
                self.assertGreater(app.query_one("#providers").option_count, 5)
                await pilot.click("#next")          # -> your model: the roomy local one is the pick
                await pilot.pause(0.2)
                self.assertEqual(app.picked_model, "coder-64k")
                await pilot.click("#next")          # -> your cat
                app.query_one("#cat-name").value = "Luna"
                await pilot.click("#next")          # -> all set
                await pilot.pause(0.3)
                self.assertEqual(app.step, len(setup_window.STEPS) - 1, app.step)
                self.assertIn("coder-64k", str(app.query_one("#done-text").render()))
                await pilot.click("#next")          # start purr: saved
                await pilot.pause(0.1)
        self.assertEqual(app.return_value, "coder-64k")
        self.assertEqual(tomllib.loads(settings.USER_CONFIG.read_text())["default_model"], "coder-64k")
        self.assertEqual(json.loads((agent.STATE_DIR / "cat.json").read_text())["name"], "Luna")

    async def test_nothing_is_saved_when_you_leave(self):
        with mock.patch.object(discover, "find", return_value={}):
            app = setup_window.SetupApp(settings.load(discover=False))
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.press("ctrl+q")
                await pilot.pause(0.1)
        self.assertIsNone(app.return_value)
        self.assertFalse(settings.USER_CONFIG.exists())


if __name__ == "__main__":
    unittest.main()
