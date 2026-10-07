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
        self.assertIsNotNone(repeating('- "sentence": "The boy. He is a. Student." ' * 200))
        prose = " ".join(f"step {i}: check the cache, then the file number {i * 7}." for i in range(300))
        self.assertIsNone(repeating(prose))
        self.assertIsNone(repeating("short " * 5))

    def test_answers_that_repeat_on_purpose_are_not_a_loop(self):
        # these were cut 3 times over and the turn ended with an empty answer
        from harness.agent import repeating
        board = "```python\nboard = [\n" + "".join("    [" + ", ".join(["0"] * 20) + "],\n" for _ in range(20)) + "]\n```"
        table = "uint8_t buf[256] = {\n" + "    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,\n" * 32 + "};"
        tiles = "\n".join(["    [" + ", ".join(['"grass"'] * 20) + "],"] * 20)
        level = "\n".join(["#" * 40] + ["#" + "." * 38 + "#"] * 20 + ["#" * 40])
        for text in (board, table, tiles, level):
            for size in (1, 3, 10):  # checked as it streams, the way _call watches it
                pieces = [text[i:i + size] for i in range(0, len(text), size)]
                for n in range(50, len(pieces) + 1, 50):
                    self.assertIsNone(repeating("".join(pieces[:n])), text[:40])

    def test_a_long_board_in_a_chat_reply_gets_through(self):
        from tests.test_limits import agent, reply
        a = agent()
        board = "board = [\n" + "".join("    [" + ", ".join(["0"] * 20) + "],\n" for _ in range(20)) + "]"

        def fake(url, key, body, on_text, on_think, should_stop):
            for i in range(0, len(board), 2):
                on_text(board[i:i + 2])
                self.assertFalse(should_stop())
            return reply(board)
        with mock.patch.object(agent_module, "stream_chat", fake):
            a.turn("write an empty 20x20 board as a python list")
        self.assertNotIn("reply stuck repeating itself -> cut off", a.tools.repairs)
        self.assertEqual(a.messages[-1]["content"], board)

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

    def test_a_long_looping_paragraph_streamed_in_small_pieces_is_cut(self):
        # a 303-char paragraph sent 4 chars at a time: 400 pieces (1600 chars) never held 8 copies
        from tests.test_limits import agent, reply
        from harness.api import Stopped
        a = agent()
        calls = {"n": 0}
        para = " ".join(f"line {i} of the plan: open file {i * 13} and read it" for i in range(8))[:302] + "\n"
        text = para * 60

        def fake(url, key, body, on_text, on_think, should_stop):
            calls["n"] += 1
            if calls["n"] == 1:
                for i in range(0, len(text), 4):
                    on_text(text[i:i + 4])
                    if should_stop():
                        raise Stopped(None)
                self.fail("never cut off")
            return reply("Done: nothing to change.")
        with mock.patch.object(agent_module, "stream_chat", fake):
            a.turn("fix it")
        self.assertEqual(calls["n"], 2)
        self.assertIn("reply stuck repeating itself -> cut off", a.tools.repairs)

    def test_a_paragraph_too_long_for_eight_in_the_span_is_still_a_loop(self):
        # 8 copies of a 930-char paragraph don't fit in 6000 characters: it went on 40 times uncut
        from harness.agent import repeating
        para = " ".join(f"step {i}: open the config file, read key {i * 13} and check it again" for i in range(14))
        para = para[:929] + "\n"
        self.assertIn("check it again", repeating(para * 40))
        streamed = [(para * 40)[i:i + 4] for i in range(0, len(para) * 40, 4)]
        self.assertIsNotNone(repeating("".join(streamed[-24000:])))  # what _call hands it
        self.assertIsNone(repeating(para * 7))  # not 8 times yet
        self.assertIsNone(repeating("intro text. " * 200 + para * 7))
        other = "".join(p.replace("step", f"stage {n}") for n, p in enumerate([para] * 40))
        self.assertIsNone(repeating(other))  # each copy differs: not stuck

    def test_a_bit_that_says_its_own_end_twice_is_still_a_loop(self):
        # the closest earlier copy of the end is inside the bit: a code block with a line twice, a
        # table's separator lines. Taking that as the bit's length never matched: never cut
        from harness.agent import repeating
        line = "    assert compute(value) == expected_result_for_this_case_ok\n"
        code = "def test_thing():\n" + line + "    print('checking the result now carefully here')\n" + line
        self.assertIn("expected_result_for_this_case_ok", repeating("start " + code * 200))
        sep = "-" * 70 + "\n"
        table = sep + "| step | file | status |\n" + sep + "| 1 | a.py | todo |\n" + sep
        self.assertIsNotNone(repeating(table * 200))
        self.assertIsNone(repeating(code * 7))  # not 8 times yet
        varied = "".join(code.replace("thing", f"thing_{n}") for n in range(200))
        self.assertIsNone(repeating(varied))  # each copy differs: not stuck

    def test_a_long_looping_paragraph_is_cut_in_a_turn(self):
        from harness.api import Stopped
        a = agent()
        calls = {"n": 0}
        para = (" ".join(f"line {i}: open file {i * 13}, read it, then think again" for i in range(20)))[:929] + "\n"
        text = para * 40

        def fake(url, key, body, on_text, on_think, should_stop):
            calls["n"] += 1
            if calls["n"] == 1:
                for i in range(0, len(text), 4):
                    on_text(text[i:i + 4])
                    if should_stop():
                        raise Stopped(None)
                self.fail("never cut off")
            return reply("Done: nothing to change.")
        with mock.patch.object(agent_module, "stream_chat", fake):
            a.turn("fix it")
        self.assertEqual(calls["n"], 2)
        self.assertIn("reply stuck repeating itself -> cut off", a.tools.repairs)

    def test_a_loop_cuts_time_and_tokens_are_counted(self):
        # same as an over-long think: the cut reply's minutes and tokens used to be recorded as 0
        from harness.api import Stopped
        a = agent()
        now = [1000.0]
        calls = []

        def fake(url, key, body, on_text, on_think, should_stop):
            calls.append(body)
            if len(calls) == 1:
                now[0] += 5  # reading the prompt
                for _ in range(2000):
                    now[0] += 0.25
                    on_text("Wait, I'll just write it.\n")
                    if should_stop():
                        raise Stopped(None)
                self.fail("never cut off")
            return reply("Done.")
        with mock.patch.object(agent_module, "stream_chat", fake), \
                mock.patch.object(agent_module.time, "monotonic", lambda: now[0]):
            a.turn("fix the cache")
        self.assertEqual(len(calls), 2)
        self.assertIn("reply stuck repeating itself -> cut off", a.tools.repairs)
        spent = now[0] - 1000.0
        self.assertAlmostEqual(a.turn_stats["model_s"], spent)
        self.assertAlmostEqual(a.turn_stats["gen"], spent - 5.25)  # from the first piece on
        self.assertGreaterEqual(a.turn_stats["out"], 1500)  # the 6000+ characters it repeated

    def test_a_retry_starts_with_a_clean_loop_detector(self):
        # a reply that broke off mid-repeat mustn't make the retry look stuck
        for first_len in (180, 400):  # under the 6000-char span alone; past it (loop seen, then broke)
            with self.subTest(first_len=first_len):
                a = agent()
                calls = []

                def fake(url, key, body, on_text, on_think, should_stop):
                    calls.append(body)
                    if len(calls) == 1:
                        for _ in range(first_len):
                            on_text("Wait, I'll just write it.\n")  # 26 chars: 180 of them ~4700
                        raise ApiError("the reply broke off", retry=True)
                    for _ in range(100):  # 2600 more: with the old 4700, a full span of repeats
                        on_text("Wait, I'll just write it.\n")
                        self.assertFalse(should_stop())
                    return reply("Done.")
                with mock.patch.object(agent_module, "stream_chat", fake), \
                        mock.patch.object(agent_module.time, "sleep"):
                    a.turn("fix the cache")
                self.assertEqual(len(calls), 2)
                self.assertNotIn("reply stuck repeating itself -> cut off", a.tools.repairs)
                self.assertEqual(a.messages[-1]["content"], "Done.")


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


class SteerTest(unittest.TestCase):
    """What you type while the model works reaches it at its next step (the window refused it before,
    and Esc threw the step away)."""

    def test_a_steer_goes_in_before_the_next_step(self):
        a = agent()
        a.tools.trust_all = True
        a.config = {**a.config, "final_check": False}
        seen = []
        replies = iter([reply("", tool=("list_files", {"path": "."})), reply("ok, pathlib it is")])

        def fake(messages=None, **kw):
            seen.append([m.get("content") for m in a.messages])
            if len(seen) == 1:
                a.steers.append("use pathlib, not os.path")
            return next(replies)
        a._call = fake
        a.turn("tidy the paths")
        self.assertIn("(the user, while you were working:) use pathlib, not os.path", seen[1])
        self.assertEqual(a.steers, [])

    def test_a_steer_attaches_its_at_files(self):
        a = agent()
        (a.root / "schema.sql").write_text("CREATE TABLE users (id INT);\n")
        a.steers.append("use the table in @schema.sql")
        a._take_steers()
        self.assertIn('<file path="schema.sql">\nCREATE TABLE users', a.messages[-1]["content"])

    def test_a_steer_while_it_writes_its_answer_keeps_the_turn_going(self):
        a = agent()
        calls = {"n": 0}

        def fake(messages=None, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                a.steers.append("also mention the tests")
                return reply("Here's the answer.")
            return reply("And the tests: none.")
        a._call = fake
        a.turn("explain it")
        self.assertEqual(calls["n"], 2)

    def test_a_steer_reaches_checkpoints_and_a_compaction(self):
        # they quoted only the request the steer replaced, and a compaction dropped the steer itself
        from harness.prompts import CHECKPOINT
        a = agent()
        a._begin_turn("store the API token in config.py")
        a.messages += [{"role": "user", "content": "store the API token in config.py"},
                       {"role": "assistant", "content": "reading config.py"},
                       {"role": "user", "content": "x" * 100}, {"role": "assistant", "content": "ok"}]
        a.steers.append("don't touch config.py, read it from an env var")
        a._take_steers()
        check = CHECKPOINT.format(steps=12, request=a._request_quote())
        self.assertIn("store the API token in config.py", check)
        self.assertIn("read it from an env var", check)
        a._call = lambda *k, **kw: reply("- user wants the API token in config.py")
        a.compact(auto=True)
        self.assertEqual(len(a.messages), 2)
        head = a.messages[1]["content"]
        self.assertIn("read it from an env var", head)
        self.assertNotIn("And earlier in this chat", head)  # not quoted twice


class ThinkBudgetTest(unittest.TestCase):
    """A local model that thinks past its budget in one step is cut; it gets the end of its thinking
    back and takes the next step without thinking."""

    def run_it(self, mode="code", **config):
        from harness.api import Stopped
        a = agent()
        a.config = {**a.config, **config}
        a.set_mode(mode)
        bodies = []

        def fake(url, key, body, on_text, on_think, should_stop):
            bodies.append(body)
            if len(bodies) == 1:
                for i in range(20000):
                    on_think(f"step {i}: maybe the cache key should include {i * 7} or not; ")
                    if should_stop():
                        raise Stopped(None)
            return reply("Done.")
        with mock.patch.object(agent_module, "stream_chat", fake):
            a.turn("fix the cache")
        return a, bodies

    def test_a_long_think_is_cut_and_the_next_step_acts(self):
        a, bodies = self.run_it()
        self.assertEqual(len(bodies), 2)
        self.assertIn("thinking past its budget -> cut, next step acts", a.tools.repairs)
        nudge = next(m["content"] for m in a.messages if m["role"] == "user" and "cut your thinking off" in m["content"])
        self.assertIn("maybe the cache key", nudge)  # where it had got to
        self.assertEqual(bodies[1].get("reasoning_effort"), "none")
        self.assertNotIn("reasoning_effort", bodies[0])

    def test_a_long_turn_can_be_cut_many_times(self):
        # the cuts had a per-turn cap of 3 shared with the loop breaker: the 4th long think came
        # back as an "empty reply", got told so, thought long again and the turn ended with ''
        from harness.api import Stopped
        a = agent()
        a.tools.trust_all = True
        a.config = {**a.config, "final_check": False}
        bodies = []

        def fake(url, key, body, on_text, on_think, should_stop):
            bodies.append(body)
            if len(bodies) == 13:
                return reply("All fixed.")
            if body.get("reasoning_effort") != "none":  # it always thinks too long
                for i in range(20000):
                    on_think(f"step {i}: maybe the cache key should include {i * 7} or not; ")
                    if should_stop():
                        raise Stopped(None)
            return reply("", tool=("run", {"command": f"echo {len(bodies)}"}))  # a new step each time
        with mock.patch.object(agent_module, "stream_chat", fake):
            a.turn("fix the cache")
        self.assertEqual(len(bodies), 13)
        self.assertEqual(a.messages[-1]["content"], "All fixed.")
        self.assertNotIn("empty reply -> nudged to continue", a.tools.repairs)
        self.assertEqual(sum("cut your thinking off" in (m["content"] or "")
                             for m in a.messages if m["role"] == "user"), 6)

    def test_the_cut_steps_time_and_tokens_are_counted(self):
        # the cut reply used to say 0 seconds and 0 tokens: bench's model_seconds and tokens
        # left out the minutes the model spent thinking
        from harness.api import Stopped
        a = agent()
        now = [1000.0]
        calls = []

        def fake(url, key, body, on_text, on_think, should_stop):
            calls.append(body)
            if len(calls) == 1:
                now[0] += 5  # reading the prompt
                for i in range(20000):
                    now[0] += 0.25  # each piece takes a while to write
                    on_think(f"step {i}: maybe the cache key should include {i * 7} or not; ")
                    if should_stop():
                        raise Stopped(None)
            return reply("Done.")
        with mock.patch.object(agent_module, "stream_chat", fake), \
                mock.patch.object(agent_module.time, "monotonic", lambda: now[0]):
            a.turn("fix the cache")
        self.assertEqual(len(calls), 2)
        spent = now[0] - 1000.0
        self.assertAlmostEqual(a.turn_stats["model_s"], spent)
        self.assertAlmostEqual(a.turn_stats["gen"], spent - 5.25)  # from the first piece on
        self.assertGreaterEqual(a.turn_stats["out"], 8000)  # the 8000-token budget it went past

    def test_it_can_be_turned_off(self):
        a, bodies = self.run_it(think_budget=0)
        self.assertEqual(len(bodies), 1)

    def test_a_retry_after_a_broken_reply_gets_the_whole_budget(self):
        from harness.api import Stopped
        a = agent()
        a.config = {**a.config, "think_budget": 1000}  # cut past 4000 characters
        bodies = []

        def fake(url, key, body, on_text, on_think, should_stop):
            bodies.append(body)
            if len(bodies) == 1:
                on_think("x" * 3000)
                raise ApiError("the reply broke off", retry=True)
            for _ in range(20):  # 2000 characters: well under the budget on its own
                on_think("y" * 100)
                if should_stop():
                    raise Stopped(None)
            return reply("Done.")
        with mock.patch.object(agent_module, "stream_chat", fake), mock.patch.object(agent_module.time, "sleep"):
            a.turn("fix the cache")
        self.assertEqual(len(bodies), 2)
        self.assertNotIn("thinking past its budget -> cut, next step acts", a.tools.repairs)
        self.assertEqual(a.messages[-1]["content"], "Done.")

    def test_chat_and_create_are_not_cut(self):
        # no tools there: the nudge would ask for a tool call it can't make, then answer thinking off
        for mode in ("chat", "create"):
            a, bodies = self.run_it(mode=mode)
            self.assertEqual(len(bodies), 1, mode)
            self.assertNotIn("reasoning_effort", bodies[0])
            self.assertFalse(any("cut your thinking off" in str(m.get("content")) for m in a.messages))

    def test_api_models_have_no_budget(self):
        self.assertEqual(agent("api")._think_budget(), 0)
        self.assertEqual(agent("small")._think_budget(), 8000)
        self.assertEqual(agent("big")._think_budget(), 8000)
