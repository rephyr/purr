"""Long one-shot runs (benchmarks, purr -p): time limits, commands that need longer, and the
thinking that goes back to providers that want it.
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

from harness import agent as agent_module  # noqa: E402
from harness.api import ApiError  # noqa: E402
from tests.test_limits import agent, reply, scripted  # noqa: E402


class TimeLimitTest(unittest.TestCase):
    def test_told_up_front_and_reminded_at_half_and_four_fifths(self):
        a = agent()
        a.time_limit = 600
        clock = iter([0, 0, 100, 320, 400, 500, 560, 590, 595, 599])  # monotonic() as the turn goes on
        steps = [reply("", tool=("list_files", {"path": "."}))] * 7 + [reply("done")]
        scripted(a, steps)
        with mock.patch.object(agent_module.time, "monotonic", lambda: next(clock, 599)):
            a.turn("make the thing")
        notes = [m["content"] for m in a.messages if m["role"] == "user"]
        self.assertIn("about 10 minutes for this", notes[0])
        halves = [n for n in notes if "minutes left" in n]
        self.assertEqual(len(halves), 3)  # once at half time, at four fifths, and near the end
        self.assertIn("make a working version now", halves[0])
        self.assertIn("Stop exploring", halves[1])
        self.assertIn("no new experiments", halves[2])
        self.assertNotIn("every file the request names", halves[2])  # not one-shot: no spec check

    def test_no_limit_no_reminders(self):
        a = agent()
        scripted(a, [reply("done")])
        a.turn("hi")
        self.assertNotIn("minutes", a.messages[1]["content"])


class LongCommandTest(unittest.TestCase):
    def test_a_command_that_runs_out_of_time_says_how_to_go_on(self):
        a = agent()
        a.tools.trust_all = True
        out = a.tools.call("run", json.dumps({"command": "sleep 3", "timeout": 1}))
        self.assertIn("stopped after 1s", out)
        self.assertIn("bigger timeout", out)
        self.assertIn("nohup", out)


class ThinkingTest(unittest.TestCase):
    def chat(self, a, n):
        for i in range(n):
            a.messages.append({"role": "assistant", "content": f"step {i}", "reasoning": f"thought {i} " * 50})
            a.messages.append({"role": "user", "content": "go on"})

    def test_only_the_last_replies_keep_their_thinking(self):
        a = agent()
        a.provider = {**a.provider, "echo_reasoning": "reasoning"}
        self.chat(a, 6)
        sent = a._body(None, True)["messages"]
        kept = [m["content"] for m in sent if m.get("reasoning")]
        self.assertEqual(kept, ["step 4", "step 5"])
        self.assertEqual(len([m for m in a.messages if m.get("reasoning")]), 6)  # the chat itself keeps all

    def test_a_provider_that_wants_all_of_it_gets_all_of_it(self):
        a = agent()
        a.provider = {**a.provider, "echo_reasoning": "reasoning"}
        self.chat(a, 6)
        sent = []

        def fake(base, key, body, *rest):
            sent.append(sum(1 for m in body["messages"] if m.get("reasoning")))
            if len(sent) == 1:
                raise ApiError("400: the reasoning_content of earlier messages is missing", status=400)
            return {"text": "ok", "reasoning": "", "tool_calls": [], "usage": {}, "finish": "stop"}
        with mock.patch.object(agent_module, "stream_chat", fake):
            a._call()
        self.assertEqual(sent, [2, 6])  # trimmed, refused, then everything

    def test_context_counts_what_is_sent(self):
        a = agent()
        a.provider = {**a.provider, "echo_reasoning": "reasoning"}
        self.chat(a, 20)
        full = len(json.dumps(a.messages)) // 4
        self.assertLess(a.context_used(), full)



class OneShotTest(unittest.TestCase):
    def test_nobody_to_ask_in_one_shot_runs(self):
        a = agent()
        self.assertIn("ask one short question", a.messages[0]["content"])
        a.set_one_shot()
        prompt = a.messages[0]["content"]
        self.assertIn("Nobody answers during this run", prompt)
        self.assertNotIn("ask one short question", prompt)

    def test_purr_bench_runs_are_one_shot(self):
        import shutil

        from harness import bench
        from harness.agent import Agent
        from tests.test_limits import CONFIG
        seen = []
        task = bench.load_tasks({"rename"})[0]
        work = Path(tempfile.mkdtemp()) / "rename"
        shutil.copytree(task["dir"] / "files", work)
        with mock.patch.object(Agent, "turn", lambda agent, text: seen.append(agent.one_shot)):
            bench.run_purr(CONFIG, "small", task, work, Path(tempfile.mkdtemp()), 600)
        self.assertEqual(seen, [True])


class LoopBreakerTest(unittest.TestCase):
    """Gemma-4-12B thought "Wait, I'll just write it." for 30k tokens (eight minutes) before the
    context ran out. purr cuts a reply that keeps repeating itself and nudges the model on."""

    def test_what_counts_as_repeating(self):
        from harness.agent import repeating
        self.assertIn("Wait, I'll just write it.", repeating("thinking...\n" + "   Wait, I'll just write it.\n\n" * 200))
        self.assertIsNotNone(repeating('- "sentence": "The boy. He is a. Student." ' * 120))
        prose = " ".join(f"step {i}: check the cache, then the file number {i * 7}." for i in range(300))
        self.assertIsNone(repeating(prose))
        self.assertIsNone(repeating("short " * 5))

    def test_a_looping_reply_is_cut_and_the_turn_goes_on(self):
        from tests.test_limits import agent, reply
        from harness.api import Stopped
        a = agent()
        calls = {"n": 0}

        def fake(url, key, body, on_text, on_think, should_stop):
            calls["n"] += 1
            if calls["n"] == 1:
                for _ in range(2000):
                    on_think("Wait, I'll just write it.\n")
                    if should_stop():
                        raise Stopped(None)
                self.fail("never cut off")
            return reply("Done: nothing to change.")
        with mock.patch.object(agent_module, "stream_chat", fake):
            a.turn("fix it")
        self.assertEqual(calls["n"], 2)
        self.assertIn("reply stuck repeating itself -> cut off", a.tools.repairs)
        nudge = [m["content"] for m in a.messages if m["role"] == "user" and "stuck repeating" in m["content"]]
        self.assertTrue(nudge and "Wait, I'll just write it." in nudge[0])


class BrokenTemplateTest(unittest.TestCase):
    def test_control_tokens_on_repeat_point_at_the_template(self):
        from tests.test_limits import agent, reply
        from harness.api import Stopped
        a = agent()
        calls = {"n": 0}

        def fake(url, key, body, on_text, on_think, should_stop):
            calls["n"] += 1
            if calls["n"] == 1:
                for _ in range(2000):
                    on_text("<|channel>thought ")
                    if should_stop():
                        raise Stopped(None)
            return reply("ok")
        with mock.patch.object(agent_module, "stream_chat", fake):
            a.turn("hi")
        self.assertTrue(any("chat template looks broken" in n for n in a.view.notes))
