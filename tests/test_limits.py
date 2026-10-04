"""Tests for per-model limits, the loop guard, pruning old tool output and the compact transcript.
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
import tests  # noqa: F401,E402 - one temporary folder for the whole run (tests/__init__.py)

from harness.agent import Agent  # noqa: E402
from harness.limits import Limits  # noqa: E402
from harness.tools import schemas  # noqa: E402


class FakeView:
    def __init__(self):
        self.notes = []

    def note(self, s, kind="dim"):
        self.notes.append(s)

    def plan_review(self, tickets, folder):
        return "run"

    def __getattr__(self, name):  # tool, text, status, ... do nothing
        return lambda *a, **k: None


CONFIG = {
    "blind_checks": False,  # its extra first call would shift every scripted reply (tests/test_blind_checks.py has it on)
    "two_plans": False, "fresh_eyes": False,  # likewise (tests/test_big_models.py)
    "providers": {"ollama": {"base_url": "http://127.0.0.1:1/v1"}, "openrouter": {"base_url": "http://127.0.0.1:1/v1"}},
    "models": {
        "small": {"provider": "ollama", "id": "small", "context": 32768},
        "big": {"provider": "ollama", "id": "big", "context": 1_000_000},
        "api": {"provider": "openrouter", "id": "big/api", "context": 1_000_000},
        "tuned": {"provider": "ollama", "id": "tuned", "context": 32768,
                  "limits": {"tool_output": 9000, "nonsense": 1}},
    },
}


def agent(model="small"):
    return Agent(CONFIG, tempfile.mkdtemp(), model, FakeView())


def known(a):
    """Mark every file in the agent's folder as read, like a model that looked first
    (purr refuses edits to files it hasn't read)."""
    a.tools.seen.update(p.resolve() for p in Path(a.root).rglob("*") if p.is_file())


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


def scripted(a, replies):
    """Make the agent's model calls return these replies in order. Returns the call counter."""
    calls = {"n": 0}

    def fake_call(*args, **kwargs):
        reply = replies[min(calls["n"], len(replies) - 1)]
        calls["n"] += 1
        return reply

    a._call = fake_call
    return calls


def reply(text="", *todo_statuses, tool=None):
    tool_calls = []
    if todo_statuses:
        items = [{"text": f"step {n}", "status": st} for n, st in enumerate(todo_statuses)]
        tool_calls.append({"id": "t", "name": "todo", "args": json.dumps({"items": items})})
    if tool:
        tool_calls.append({"id": "r", "name": tool[0], "args": json.dumps(tool[1])})
    return {"text": text, "reasoning": "", "tool_calls": tool_calls, "usage": None,
            "finish": "tool_calls" if tool_calls else "stop", "gen_seconds": 0.0}


class TextCallTest(unittest.TestCase):
    def test_a_tool_call_written_as_text_is_rescued_and_counted(self):
        a = agent()
        Path(a.root, "x.py").write_text("x = 1\n")
        calls = scripted(a, [reply("<function=read_file>\n<parameter=path>\nx.py\n</parameter>\n</function>"),
                             reply("x is 1")])
        a.turn("what is x?")
        self.assertEqual(calls["n"], 2)
        self.assertIn("tool call written as text -> real call", a.tools.repairs)


class FinalCheckTest(unittest.TestCase):
    def edit_then_done(self, a):
        Path(a.root, "x.py").write_text("x = 1\n")
        known(a)
        a.tools.trust_all = True
        return scripted(a, [reply("", tool=("edit_file", {"path": "x.py", "old_text": "x = 1", "new_text": "x = 2"})),
                            reply("Done!"), reply("All parts done: x is 2 now."), reply("again?")])

    def test_asks_once_after_changes(self):
        a = agent()
        calls = self.edit_then_done(a)
        a.turn("set x to 2")
        self.assertEqual(calls["n"], 3)  # edit, "Done!", then the answer to the check; not a 4th
        self.assertEqual(sum("read the user's request again" in (m.get("content") or "") for m in a.messages), 1)

    def test_can_be_turned_off(self):
        a = agent()
        a.config = {**a.config, "final_check": False}
        calls = self.edit_then_done(a)
        a.turn("set x to 2")
        self.assertEqual(calls["n"], 2)

    def test_no_check_when_nothing_changed(self):
        a = agent()
        calls = scripted(a, [reply("x is 1")])
        a.turn("what is x?")
        self.assertEqual(calls["n"], 1)


class EmptyReplyTest(unittest.TestCase):
    def test_an_empty_reply_gets_a_nudge_not_the_end(self):
        a = agent()
        calls = scripted(a, [reply(""), reply("All done: nothing needed changing.")])
        a.turn("check x")
        self.assertEqual(calls["n"], 2)
        self.assertIn("empty reply -> nudged to continue", a.tools.repairs)

    def test_empty_reply_is_saved_as_text_not_null(self):
        # Ollama answers 400 "invalid message content type: <nil>" to a null without tool calls
        a = agent()
        scripted(a, [reply(""), reply("All done.")])
        a.turn("check x")
        quiet = [m for m in a.messages if m["role"] == "assistant" and not m.get("tool_calls")]
        self.assertTrue(all(m["content"] is not None for m in quiet))

    def test_at_most_twice(self):
        a = agent()
        calls = scripted(a, [reply("")] * 5)
        a.turn("check x")
        self.assertEqual(calls["n"], 3)


class TodoLoopTest(unittest.TestCase):
    def test_chatty_todo_loop_ends(self):
        # what qwen3-coder did with "hello who are you": answer + todo, forever
        a = agent()
        calls = scripted(a, [reply("I am qwen.", "pending"), reply("I am qwen.", "done"),
                             reply("Hello!", "pending"), reply("Hi again", "done")])
        a.turn("hello who are you")
        self.assertEqual(calls["n"], 2)  # stops once it has answered and everything is done

    def test_todo_only_steps_stop_after_three(self):
        a = agent()
        calls = scripted(a, [reply("", "pending"), reply("", "doing"), reply("", "pending"),
                             reply("", "doing")])
        a.turn("do the thing")
        self.assertEqual(calls["n"], 3)

    def test_real_work_with_a_todo_list_carries_on(self):
        a = agent()
        Path(a.root, "x.py").write_text("print(1)\n")
        calls = scripted(a, [reply("plan", "doing", "pending", tool=("read_file", {"path": "x.py"})),
                             reply("", "done", "doing", tool=("grep", {"pattern": "print"})),
                             reply("", "done", "done"),  # todo only, no text: not the end yet
                             reply("All done: x.py prints 1.")])
        a.turn("check x.py")
        self.assertEqual(calls["n"], 4)


class HiddenToolsTest(unittest.TestCase):
    def test_small_models_are_not_offered_web_or_helpers(self):
        from harness.tools import schemas
        a = agent("small")
        names = [s["function"]["name"] for s in schemas(hidden=a.limits.hidden_tools)]
        self.assertNotIn("fetch_url", names)
        self.assertNotIn("task", names)
        prompt = a.messages[0]["content"]
        self.assertNotIn("fetch_url", prompt)  # or it would try to call them anyway
        self.assertNotIn("- task:", prompt)

    def test_big_models_get_everything(self):
        a = agent("big")
        self.assertEqual(a.limits.hidden_tools, ())
        self.assertIn("fetch_url", a.messages[0]["content"])


class RefineModeTest(unittest.TestCase):
    def test_auto_refines_only_a_short_first_message(self):
        a = agent()
        self.assertEqual(a.refine_mode, "auto")
        self.assertTrue(a.should_refine("tests broken pls fix"))
        self.assertFalse(a.should_refine("yes"))  # a reply, not a task
        self.assertFalse(a.should_refine(" ".join(["word"] * 30)))  # already detailed
        self.assertFalse(a.should_refine("/mode chat"))
        a.messages.append({"role": "user", "content": "fix the cache"})
        self.assertFalse(a.should_refine("also add some tests"))  # a follow-up needs the chat

    def test_on_off_and_other_modes(self):
        a = agent()
        a.refine_mode = "on"
        self.assertTrue(a.should_refine("also add some tests please"))
        a.refine_mode = "off"
        self.assertFalse(a.should_refine("tests broken pls fix"))
        a.refine_mode = "auto"
        a.set_mode("chat")
        self.assertFalse(a.should_refine("tests broken pls fix"))  # only code mode refines

    def test_the_command(self):
        from harness import commands
        a = agent()
        commands.run(a, "/refine off")
        self.assertEqual(a.refine_mode, "off")
        self.assertIn("refine off", commands.run(a, "/refine")[0][1])


class TrainingWheelsTest(unittest.TestCase):
    def test_local_models_get_them_api_models_dont(self):
        local, api = agent("small"), agent("api")
        self.assertEqual((local.limits.helpers, api.limits.helpers), ("full", "light"))
        self.assertEqual((local.refine_mode, api.refine_mode), ("auto", "off"))
        self.assertTrue(local._helper_on("reminders"))
        self.assertFalse(api._helper_on("reminders"))
        self.assertFalse(api._helper_on("edge_cases"))

    def test_switching_model_follows_unless_pinned(self):
        a = agent("small")
        a.set_model("api")
        self.assertEqual(a.refine_mode, "off")
        from harness import commands
        commands.run(a, "/refine on")
        a.set_model("small")
        self.assertEqual(a.refine_mode, "on")  # you pinned it

    def test_config_wins(self):
        a = agent("api")
        a.config = {**a.config, "reminders": True}
        self.assertTrue(a._helper_on("reminders"))

    def test_api_models_get_the_short_final_check(self):
        a = agent("api")
        Path(a.root, "x.py").write_text("x = 1\n")
        known(a)
        a.tools.trust_all = True
        scripted(a, [reply("", tool=("edit_file", {"path": "x.py", "old_text": "x = 1", "new_text": "x = 2"})),
                     reply("Done!"), reply("ok")])
        a.turn("set x to 2")
        check = next(m["content"] for m in a.messages if "read the user's request again" in (m.get("content") or ""))
        self.assertNotIn("edge cases", check)


class ModeTest(unittest.TestCase):
    def test_ask_mode_can_look_but_not_change(self):
        a = agent()
        Path(a.root, "x.py").write_text("x = 1\n")
        a.set_mode("ask")
        self.assertIn("ask mode", a.messages[0]["content"])
        self.assertIn("there is no tool", a.tools.call("edit_file", json.dumps(
            {"path": "x.py", "old_text": "x = 1", "new_text": "x = 2"})))
        self.assertIn("x = 1", a.tools.call("read_file", '{"path": "x.py"}'))
        self.assertEqual(Path(a.root, "x.py").read_text(), "x = 1\n")

    def test_chat_mode_has_no_tools_and_a_short_prompt(self):
        a = agent()
        a.set_mode("chat")
        self.assertNotIn("edit_file", a.messages[0]["content"])
        self.assertIn("error: no tools", a.tools.call("read_file", '{"path": "x.py"}'))

    def test_old_tool_calls_become_plain_text_without_tools(self):
        from harness.agent import plain_history
        msgs = [{"role": "user", "content": "fix it"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "1", "content": "x = 1"}]
        out = plain_history(msgs)
        self.assertFalse(any("tool_calls" in m or m["role"] == "tool" for m in out))
        self.assertIn("read_file", out[1]["content"])
        self.assertIn("x = 1", out[2]["content"])


class GrepTest(unittest.TestCase):
    def test_lower_case_search_ignores_case(self):
        a = agent()
        Path(a.root, "menu.py").write_text("HAPPY_HOUR_DISCOUNT = 0.20\n")
        self.assertIn("menu.py:1:", a.tools.t_grep("discount"))
        self.assertIn("no matches", a.tools.t_grep("Discount"))  # a capital means exact


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
