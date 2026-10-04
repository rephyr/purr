"""/model free: the free router picks a free model per kind of work and moves on when one is maxed out.
No model is called (the API is faked).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import agent as agent_module  # noqa: E402
from harness import free  # noqa: E402
from harness.agent import Agent  # noqa: E402
from harness.api import ApiError  # noqa: E402
from tests.test_limits import FakeView  # noqa: E402

FREE = {"provider": "or", "price": {"hit": 0, "miss": 0, "out": 0}}
CONFIG = {
    "providers": {"or": {"base_url": "http://127.0.0.1:1/v1"}, "other": {"base_url": "http://127.0.0.1:1/v1"},
                  "keyless": {"base_url": "http://127.0.0.1:1/v1", "api_key_env": "PURR_TEST_NO_SUCH_KEY"},
                  "ollama": {"base_url": "http://127.0.0.1:1/v1"}},
    "models": {
        "local": {"provider": "ollama", "id": "local", "context": 32768},
        "coder": {**FREE, "id": "coder:free", "context": 262144},
        "big": {**FREE, "provider": "other", "id": "big:free", "context": 1_000_000},
        "or-big": {**FREE, "id": "or-big:free", "context": 1_000_000},
        "nokey": {**FREE, "provider": "keyless", "id": "nokey", "context": 1_000_000},
        "talker": {**FREE, "id": "talker:free", "context": 131072},
        "free": {**FREE, "router": "free", "id": "(auto)", "context": 262144},
    },
    "free": {"code": ["nokey", "coder", "or-big", "big"], "ask": ["big", "coder"], "talk": ["talker", "coder"]},
}

OK = {"text": "hi", "reasoning": "", "tool_calls": [], "usage": {}, "finish": "stop"}


def fresh():
    free.FREE_FILE.unlink(missing_ok=True)
    return Agent(CONFIG, tempfile.mkdtemp(), "free", FakeView())


class RestTest(unittest.TestCase):
    def test_which_errors_mean_maxed_out(self):
        day = free.rest_for(ApiError('429: {"message": "Rate limit exceeded: free-models-per-day"}', status=429))
        self.assertEqual(day[1], "out of free requests today")
        self.assertGreater(day[0], 0)
        self.assertLessEqual(day[0], 24 * 3600)
        self.assertEqual(day[2], "provider")  # OpenRouter's daily free cap is per account
        self.assertEqual(free.rest_for(ApiError("429: slow down", status=429)), (180, "rate-limited", "model"))
        self.assertEqual(free.rest_for(ApiError("404: No endpoints found", status=404))[1], "no host for it right now")
        self.assertEqual(free.rest_for(ApiError("400: The model `x` does not exist", status=400))[1],
                         "isn't available (renamed or gone?)")
        self.assertEqual(free.rest_for(ApiError("401: Invalid API Key", status=401))[2], "provider")
        self.assertIsNone(free.rest_for(ApiError("400: bad tool schema", status=400)))


class RouterTest(unittest.TestCase):
    def test_picks_per_kind_skipping_resting_and_small(self):
        free.FREE_FILE.unlink(missing_ok=True)
        r = free.FreeRouter(CONFIG)
        self.assertEqual(r.pick("code"), "coder")  # nokey's provider has no key: skipped
        self.assertEqual(r.pick("ask"), "big")
        self.assertEqual(r.pick("chat"), "talker")
        self.assertEqual(r.pick("chat", need=200_000), "coder")  # talker's 131k is too small
        r.rest("coder", 60, "rate-limited")
        self.assertEqual(r.pick("code"), "or-big")
        self.assertEqual(free.FreeRouter(CONFIG).pick("code"), "or-big")  # remembered on disk
        r.rest("coder", 60, "out of free requests today", scope="provider")  # all of "or" rests
        self.assertEqual(r.pick("code"), "big")
        self.assertIn("no key", dict(r.status("code"))["nokey"])
        r.reset()
        self.assertEqual(r.pick("code"), "coder")


class AgentTest(unittest.TestCase):
    def test_free_picks_and_switches_when_maxed_out(self):
        a = fresh()
        self.assertEqual((a.chosen_model, a.model_name), ("free", "coder"))
        calls = []

        def fake(base, key, body, *rest):
            calls.append(body["model"])
            if body["model"] == "coder:free":
                raise ApiError("429: Rate limit exceeded: free-models-per-day", retry=True, status=429)
            return OK
        with mock.patch.object(agent_module, "stream_chat", fake):
            a._call()
        # the daily cap is per account: or-big (same provider) is skipped, no waiting
        self.assertEqual(calls, ["coder:free", "big:free"])
        self.assertEqual(a.model_name, "big")
        self.assertTrue(any("coder is out of free requests today: switching to big" in n for n in a.view.notes))
        self.assertIn("provider:or", free.FreeRouter(CONFIG).resting)

    def test_all_resting_gives_up(self):
        a = fresh()

        def fake(base, key, body, *rest):
            raise ApiError("429: rate limit", retry=False, status=429)
        with mock.patch.object(agent_module, "stream_chat", fake):
            with self.assertRaises(ApiError):
                a._call()
        self.assertTrue(any("every other free model is resting" in n for n in a.view.notes))
        with self.assertRaises(KeyError):  # and /model free says so too
            a.set_model("free")

    def test_a_refused_request_switches_twice_at_most(self):
        a = fresh()
        calls = []

        def fake(base, key, body, *rest):
            calls.append(body["model"])
            raise ApiError("400: bad request", status=400)
        with mock.patch.object(agent_module, "stream_chat", fake):
            with self.assertRaises(ApiError):
                a._call()
        self.assertEqual(calls, ["coder:free", "or-big:free", "big:free"])  # then it gives up

    def test_modes_keep_the_router_on(self):
        a = fresh()
        self.assertEqual(a.switch_mode("chat"), "model: free → talker")
        self.assertEqual(a.switch_mode("code"), "model: free → coder")
        a.set_model("local")  # picking a model by hand turns it off
        self.assertIsNone(a.router)
        self.assertEqual(a.chosen_model, "local")

    def test_saved_chats_remember_free(self):
        a = fresh()
        a.messages.append({"role": "user", "content": "hi"})
        a.save_log()
        b = Agent(CONFIG, a.root, "local", FakeView())
        b.load(a.log_path)
        self.assertEqual((b.chosen_model, b.model_name), ("free", "coder"))



class HistoryTest(unittest.TestCase):
    def test_other_providers_thinking_is_left_out(self):
        a = fresh()
        a.messages += [{"role": "user", "content": "hi"},
                       {"role": "assistant", "content": "yo", "reasoning": "hmm", "reasoning_content": "hm"}]
        sent = a._body(None, True)["messages"]
        self.assertEqual(sent[-1], {"role": "assistant", "content": "yo"})
        a.provider = {**a.provider, "echo_reasoning": "reasoning"}  # a provider that wants it back gets it
        self.assertEqual(a._body(None, True)["messages"][-1]["reasoning"], "hmm")
