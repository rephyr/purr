"""Settings in layers (harness/settings.py) and the local servers purr finds (harness/discover.py).

Run: python3 -m unittest discover tests
No real server is asked anything: discover._get is replaced by fake answers.
"""

import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import discover, settings  # noqa: E402

OLLAMA = "http://127.0.0.1:11434/v1"


def fake_servers(answers):
    """discover._get answering from {url: reply}; anything else is a server that isn't running."""
    def get(url, data=None, timeout=None):
        key = url if data is None else f"{url} {data.get('model')}"
        if key not in answers:
            raise ConnectionRefusedError(url)
        return answers[key]
    return mock.patch.object(discover, "_get", get)


SHOW = {"capabilities": ["completion", "tools"], "parameters": "num_ctx 65536\ntemperature 0.7"}
ANSWERS = {
    "http://127.0.0.1:11434/api/tags": {"models": [
        {"name": "coder-64k:latest", "digest": "a"},
        {"name": "nomic-embed-text:latest", "digest": "b"},  # can't call tools: left out
        {"name": "tiny:3b", "digest": "c"}]},
    "http://127.0.0.1:11434/api/show coder-64k:latest": SHOW,
    "http://127.0.0.1:11434/api/show nomic-embed-text:latest": {"capabilities": ["embedding"]},
    "http://127.0.0.1:11434/api/show tiny:3b": {"capabilities": ["completion", "tools", "vision"],
                                                "model_info": {"llama.context_length": 131072}},
    "http://127.0.0.1:8080/v1/models": {"data": [{"id": "qwen-gguf"}]},
    "http://127.0.0.1:8080/props": {"default_generation_settings": {"n_ctx": 32768}},
}
PROVIDERS = {"ollama": {"base_url": OLLAMA, "discover": "ollama"},
             "llamacpp": {"base_url": "http://127.0.0.1:8080/v1", "discover": "openai"},
             "lmstudio": {"base_url": "http://127.0.0.1:1234/v1", "discover": "openai"},  # not running
             "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "X"}}


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp()) / "ollama.json"
        patcher = mock.patch.object(discover, "CACHE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_finds_what_runs_and_only_models_that_can_use_tools(self):
        with fake_servers(ANSWERS), mock.patch.dict(os.environ, {"OLLAMA_CONTEXT_LENGTH": "16384"}):
            found = discover.find(PROVIDERS)
        self.assertEqual(sorted(found), ["llamacpp", "ollama"])  # LM Studio isn't running
        ollama = dict(found["ollama"])
        self.assertEqual(sorted(ollama), ["coder-64k", "tiny:3b"])  # no embedding model, no ":latest"
        self.assertEqual(ollama["coder-64k"]["context"], 65536)    # its own num_ctx
        self.assertEqual(ollama["tiny:3b"]["context"], 16384)      # Ollama's default, under what it supports
        self.assertTrue(ollama["tiny:3b"]["vision"])
        self.assertEqual(found["llamacpp"], [("qwen-gguf", {"context": 32768})])  # the server's n_ctx

    def test_ollamas_answers_are_cached_by_digest(self):
        with fake_servers(ANSWERS):
            discover.find(PROVIDERS)
        only_tags = {k: v for k, v in ANSWERS.items() if "api/show" not in k}  # /api/show isn't asked again
        with fake_servers(only_tags):
            self.assertEqual(len(discover.find(PROVIDERS)["ollama"]), 2)

    def test_your_own_entry_for_a_model_wins(self):
        config = {"providers": PROVIDERS,
                  "models": {"mine": {"provider": "ollama", "id": "coder-64k", "context": 65536,
                                      "body": {"temperature": 0.2}}}}
        with fake_servers(ANSWERS):
            added = discover.add_found(config)
        self.assertEqual(sorted(added), ["qwen-gguf", "tiny:3b"])
        self.assertEqual(config["models"]["mine"]["body"], {"temperature": 0.2})
        self.assertTrue(config["models"]["tiny:3b"]["found"])


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for name, value in (("CONFIG_DIR", self.dir), ("USER_CONFIG", self.dir / "config.toml")):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_yours_wins_table_by_table(self):
        (self.dir / "config.toml").write_text('default_model = "flash"\nmax_steps = 99\n'
                                              '[providers.llamacpp]\nbase_url = "http://127.0.0.1:8081/v1"\n')
        with mock.patch.object(discover, "find", return_value={}):
            config = settings.load()
        self.assertEqual(config["max_steps"], 99)
        self.assertEqual(config["default_model"], "flash")
        self.assertEqual(config["providers"]["llamacpp"]["base_url"], "http://127.0.0.1:8081/v1")
        self.assertEqual(config["providers"]["llamacpp"]["discover"], "openai")  # the rest of purr's table stays
        self.assertIn("openrouter", config["providers"])

    def test_a_new_user_starts_on_the_roomiest_local_model(self):
        found = {"ollama": [("small", {"context": 8192}), ("roomy-128k", {"context": 131072})]}
        with mock.patch.object(discover, "find", return_value=found):
            config = settings.load()
        self.assertEqual(config["default_model"], "roomy-128k")
        self.assertFalse(settings.is_set_up())

    def test_purr_config_means_exactly_that_file(self):
        f = self.dir / "bench.toml"
        f.write_text('default_model = "bench"\n[models.bench]\nprovider = "x"\nid = "y"\n')
        with mock.patch.dict(os.environ, {"PURR_CONFIG": str(f)}):
            config = settings.load()
        self.assertEqual(list(config["models"]), ["bench"])
        self.assertNotIn("providers", config)

    def test_saving_merges_into_yours_and_keeps_a_backup(self):
        (self.dir / "config.toml").write_text('cat_name = "Luna"\n[models.mine]\nprovider = "ollama"\nid = "m"\n')
        settings.save_user({"default_model": "mine",
                            "providers": {"work": {"base_url": "https://llm.example.com/v1", "api_key_env": "WORK_KEY"}},
                            "models": {"work-model": {"provider": "work", "id": "big:latest", "context": 200000}}})
        saved = tomllib.loads((self.dir / "config.toml").read_text())
        self.assertEqual(saved["cat_name"], "Luna")
        self.assertEqual(saved["default_model"], "mine")
        self.assertEqual(saved["models"]["work-model"]["id"], "big:latest")
        self.assertEqual(saved["providers"]["work"]["api_key_env"], "WORK_KEY")
        self.assertIn("Luna", (self.dir / "config.toml.bak").read_text())

    def test_toml_writer_round_trips_odd_values(self):
        data = {"a": 'quote " and \\ slash', "n": 1.5, "on": True, "list": ["x", 2],
                "providers": {"openrouter": {"body": {"provider": {"only": ["deepseek"], "allow_fallbacks": False}}}},
                "models": {"gemma:12b": {"provider": "ollama", "id": "gemma:12b"}}}
        self.assertEqual(tomllib.loads(settings.to_toml(data)), json.loads(json.dumps(data)))

    def test_ollama_host_moves_ollama_unless_you_set_its_address(self):
        for value, url in (("0.0.0.0", "http://127.0.0.1:11434"), ("172.20.0.1", "http://172.20.0.1:11434"),
                           ("gpu-box:8000", "http://gpu-box:8000"), ("https://ollama.lan/", "https://ollama.lan:443"),
                           ("[::1]:11434", "http://[::1]:11434"), ("::1", "http://[::1]:11434"),
                           ("http://box/ollama", "http://box:80/ollama"),
                           ("", "")):
            self.assertEqual(settings.ollama_host(value), url, value)
        with mock.patch.object(discover, "find", return_value={}), \
                mock.patch.dict(os.environ, {"OLLAMA_HOST": "172.20.0.1"}):
            self.assertEqual(settings.load()["providers"]["ollama"]["base_url"], "http://172.20.0.1:11434/v1")
            (self.dir / "config.toml").write_text('[providers.ollama]\nbase_url = "http://mine:1/v1"\n')
            self.assertEqual(settings.load()["providers"]["ollama"]["base_url"], "http://mine:1/v1")

    def test_the_model_is_told_its_system(self):
        from harness.agent import system_prompt
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertIn("System: macOS", system_prompt("/tmp/x", "m", "ollama"))
        with mock.patch.object(sys, "platform", "linux"):
            self.assertIn("- System: Linux\n", system_prompt("/tmp/x", "m", "ollama"))

    def test_opencode_is_asked_once_and_only_if_installed(self):
        from harness import agent
        export = '[{"integrationID": "deepseek", "value": {"key": "sk-1"}}]'
        for installed, want, runs in ((None, None, 0), ("/usr/bin/opencode", "sk-1", 1)):
            with mock.patch.dict(agent._OPENCODE_KEYS, clear=True), \
                    mock.patch.object(agent.shutil, "which", return_value=installed), \
                    mock.patch.object(agent.subprocess, "run", return_value=mock.Mock(stdout=export)) as run:
                got = [agent.opencode_key(name) for name in ("deepseek", "groq", "cerebras", "deepseek")]
            self.assertEqual(got, [want, None, None, want])
            self.assertEqual(run.call_count, runs)

    def test_purrs_own_defaults_load(self):
        defaults = tomllib.loads(settings.DEFAULTS.read_text())
        self.assertEqual({p for p, v in defaults["providers"].items() if v.get("discover")},
                         {"ollama", "llamacpp", "lmstudio", "vllm"})


if __name__ == "__main__":
    unittest.main()
