"""Long one-shot runs (benchmarks, purr -p): time limits, commands that need longer, and the
thinking that goes back to providers that want it.

Run: python3 -m unittest discover tests
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
        clock = iter([0, 0, 100, 320, 400, 500, 590, 595, 599])  # monotonic() as the turn goes on
        steps = [reply("", tool=("list_files", {"path": "."}))] * 6 + [reply("done")]
        scripted(a, steps)
        with mock.patch.object(agent_module.time, "monotonic", lambda: next(clock, 599)):
            a.turn("make the thing")
        notes = [m["content"] for m in a.messages if m["role"] == "user"]
        self.assertIn("about 10 minutes for this", notes[0])
        halves = [n for n in notes if "minutes left" in n]
        self.assertEqual(len(halves), 2)  # once at half time, once at four fifths
        self.assertIn("make a working version now", halves[0])
        self.assertIn("Stop exploring", halves[1])

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
        self.assertIn("Nobody will answer questions", prompt)
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


if __name__ == "__main__":
    unittest.main()
