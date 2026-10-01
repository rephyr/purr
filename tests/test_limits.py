"""Tests for per-model limits, the loop guard, pruning old tool output and the compact transcript.

Run: python3 -m unittest discover tests
No model is called: the agent only talks to a fake view.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()  # keep test chats away from the real ones
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import Agent  # noqa: E402
from harness.limits import Limits  # noqa: E402
from harness.tools import schemas  # noqa: E402


class FakeView:
    def __init__(self):
        self.notes = []

    def note(self, s, kind="dim"):
        self.notes.append(s)

    def __getattr__(self, name):  # tool, text, status, ... do nothing
        return lambda *a, **k: None


CONFIG = {
    "providers": {"ollama": {"base_url": "http://127.0.0.1:1/v1"}},
    "models": {
        "small": {"provider": "ollama", "id": "small", "context": 32768},
        "big": {"provider": "ollama", "id": "big", "context": 1_000_000},
        "tuned": {"provider": "ollama", "id": "tuned", "context": 32768,
                  "limits": {"tool_output": 9000, "nonsense": 1}},
    },
}


def agent(model="small"):
    return Agent(CONFIG, tempfile.mkdtemp(), model, FakeView())


def guard(a, calls):
    """Feed the loop guard one step of (name, args, result) calls."""
    a._executed = calls
    return a._check_repeats()


class LimitsTest(unittest.TestCase):
    def test_small_is_tighter_than_big(self):
        small, big = Limits.for_model({"context": 32768}), Limits.for_model({"context": 1_000_000})
        self.assertLess(small.tool_output, big.tool_output)
        self.assertLess(small.compact_at, big.compact_at)
        self.assertLess(small.prune_at, small.compact_at)
        self.assertLessEqual(small.tool_output, 32768 * 4 * 0.05)  # under 5% of the context

    def test_overrides_from_config(self):
        lim = Limits.for_model(CONFIG["models"]["tuned"])
        self.assertEqual(lim.tool_output, 9000)  # unknown keys are ignored

    def test_switching_model_updates_tools(self):
        a = agent("small")
        a.set_model("big")
        self.assertEqual(a.tools.limits, a.limits)
        self.assertEqual(a.limits.context, 1_000_000)

    def test_read_file_description_shows_the_number(self):
        desc = json.dumps(schemas(read_lines=262))
        self.assertIn("Default 262.", desc)
        self.assertNotIn("{read_lines}", desc)


class LoopGuardTest(unittest.TestCase):
    def setUp(self):
        self.a = agent("small")
        self.a._repeats = {}

    def test_same_failure_nudges_then_stops(self):
        fail = ("run", '{"command": "pytest"}', "boom\n[exit code 1]")
        self.assertIsNone(guard(self.a, [fail]))
        self.assertIn("keeps failing", guard(self.a, [fail]))
        self.assertEqual(guard(self.a, [fail]), "stop failed")

    def test_rereading_a_file_gets_more_room(self):
        read = ("read_file", '{"path": "x.py"}', "    1\tprint(1)")
        results = [guard(self.a, [read]) for _ in range(5)]
        self.assertIsNone(results[0])
        self.assertIn("returns the same thing", results[1])
        self.assertEqual(results[2:4], [None, None])  # not stopped at 3 like a failure
        self.assertEqual(results[4], "stop same")

    def test_different_errors_are_not_a_loop(self):
        for n in range(6):
            self.assertIsNone(guard(self.a, [("run", '{"command": "pytest"}', f"error {n}\n[exit code 1]")]))

    def test_argument_order_does_not_matter(self):
        guard(self.a, [("grep", '{"pattern": "x", "path": "."}', "error: nope")])
        self.assertIsNotNone(guard(self.a, [("grep", '{"path": ".", "pattern": "x"}', "error: nope")]))

    def test_todo_is_exempt(self):
        for _ in range(6):
            self.assertIsNone(guard(self.a, [("todo", '{"items": []}', "ok")]))


class PruneTest(unittest.TestCase):
    def chat(self, a, n):
        for i in range(n):
            a.messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": "read_file", "arguments": json.dumps({"path": f"f{i}.py"})}}]})
            a.messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"file {i}\n" + "x" * 2000})

    def test_old_output_becomes_a_stub_naming_the_call(self):
        a = agent("small")
        self.chat(a, 6)
        self.assertGreater(a._prune_old_tools(), 0)
        tools = [m["content"] for m in a.messages if m["role"] == "tool"]
        keep = a.limits.keep_recent_tools
        self.assertIn('read_file({"path": "f0.py"})', tools[0])
        self.assertTrue(all(t.startswith("[old output of") for t in tools[:-keep]))
        self.assertTrue(all(t.startswith("file") for t in tools[-keep:]))

    def test_pruning_twice_changes_nothing(self):
        a = agent("small")
        self.chat(a, 6)
        a._prune_old_tools()
        before = json.dumps(a.messages)
        self.assertEqual(a._prune_old_tools(), 0)
        self.assertEqual(json.dumps(a.messages), before)  # no change = the cache stays valid


class TranscriptTest(unittest.TestCase):
    def test_small_model_cuts_tool_results_harder(self):
        msgs = [{"role": "tool", "content": "y" * 10_000}]
        small, big = agent("small").transcript(msgs), agent("big").transcript(msgs)
        self.assertLess(len(small), 1500)  # stricter than the old fixed 1500
        self.assertGreater(len(big), len(small))

    def test_transcript_fits_and_keeps_the_start(self):
        a = agent("small")
        msgs = [{"role": "user", "content": "GOAL: make the cat purr"}]
        msgs += [{"role": "assistant", "content": "z" * 5000} for _ in range(40)]
        text = a.transcript(msgs)
        self.assertLessEqual(len(text), a.limits.context * 2 + 200)
        self.assertTrue(text.startswith("USER: GOAL: make the cat purr"))
        self.assertIn("left out", text)


if __name__ == "__main__":
    unittest.main()
