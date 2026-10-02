"""What the first real Terminal-Bench run (0.3.1, 20 tasks) taught: one-shot runs check the spec
before finishing, never hand a question to nobody, keep the request through a compaction, and
don't trip over pipes, progress bars or the clock.

Run: python3 -m unittest discover tests
No model is called.
"""

import http.client
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
from harness import api  # noqa: E402
from harness.api import ApiError  # noqa: E402
from harness.tools import clip, run_shell  # noqa: E402
from tests.test_limits import CONFIG, FakeView, agent, reply, scripted  # noqa: E402


def one_shot(model="small", time_limit=None):
    a = agent(model)
    a.tools.trust_all = True
    a.set_one_shot()
    a.time_limit = time_limit
    return a


def users(a):
    return [m["content"] for m in a.messages if m["role"] == "user"]


class ContextTest(unittest.TestCase):
    def test_thinking_that_isnt_sent_back_doesnt_count(self):
        a = agent("api")
        a.last_usage = {"prompt_tokens": 20_000, "completion_tokens": 30_000}
        self.assertLess(a.context_used(), 25_000)
        a.provider = {**a.provider, "echo_reasoning": "reasoning"}
        self.assertEqual(a.context_used(), 50_000)


class ShellTest(unittest.TestCase):
    def test_a_failure_hidden_by_a_pipe_is_pointed_out(self):
        out, code = run_shell("false | tail -1", ".")
        self.assertEqual(code, 0)
        self.assertIn("an earlier command in the pipe exited 1", out)
        self.assertNotIn("earlier command", run_shell("yes | head -1", ".")[0])  # 141 is fine
        self.assertNotIn("earlier command", run_shell("true | cat", ".")[0])

    def test_the_trailer_keeps_exit_codes_and_heredocs_as_they_were(self):
        self.assertEqual(run_shell("exit 3", "."), ("", 3))
        self.assertEqual(run_shell("set -e; false; echo no", "."), ("", 1))
        self.assertEqual(run_shell("cat <<EOF\nhi\nEOF", "."), ("hi", 0))
        self.assertEqual(run_shell("set -o pipefail; false | cat", ".")[1], 1)
        self.assertEqual(run_shell("echo x # a comment", "."), ("x", 0))
        self.assertEqual(run_shell("sleep 0.1 & echo bg", "."), ("bg", 0))

    def test_progress_bars_and_bad_bytes(self):
        self.assertEqual(run_shell(r"printf '10%%\r50%%\r100%%\ndone\n'", ".")[0], "100%\ndone")
        out, code = run_shell(r"printf '\xff\xfeok'", ".")
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith("ok"))


class FinalCheckTest(unittest.TestCase):
    def test_a_one_shot_run_that_only_ran_commands_is_checked_too(self):
        a = one_shot()
        scripted(a, [reply("", tool=("run", {"command": "echo trained > model.txt"})), reply("done"), reply("ok")])
        a.turn("train the model")
        self.assertTrue(any("before you finish" in u for u in users(a)))
        b = agent()  # a chat: running the tests needs no second look
        b.tools.trust_all = True
        scripted(b, [reply("", tool=("run", {"command": "true"})), reply("they pass")])
        b.turn("run the tests")
        self.assertFalse(any("before you finish" in u for u in users(b)))

    def test_only_looking_isnt_checked(self):
        a = agent()
        scripted(a, [reply("", tool=("list_files", {"path": "."})), reply("there are no files")])
        a.turn("what's here?")
        self.assertFalse(any("before you finish" in u for u in users(a)))

    def test_one_shot_runs_check_the_spec(self):
        a = one_shot(time_limit=600)
        scripted(a, [reply("", tool=("write_file", {"path": "out.json", "content": "[]"})), reply("done"), reply("ok")])
        a.turn("write out.json")
        check = next(u for u in users(a) if "before you finish" in u)
        self.assertIn("a wrong reading would give a different answer", check)
        self.assertIn("minutes left", check)
        self.assertIn("You created these files: out.json", check)  # leftovers break real tests
        self.assertNotIn("keep running", check)  # nothing was started
        self.assertNotIn("Nobody will answer", check)

    def test_plenty_of_time_left_gets_one_more_look(self):
        a = one_shot(time_limit=5400)
        scripted(a, [reply("", tool=("write_file", {"path": "a.py", "content": "x = 1\n"})), reply("done")])
        a.turn("make a.py")
        looks = [u for u in users(a) if "one line per requirement" in u]
        self.assertEqual(len(looks), 1)  # once, after the check
        self.assertIn("of 90 minutes left", looks[0])

    def test_little_time_left_just_finishes(self):
        a = one_shot(time_limit=5400)
        a._started = agent_module.time.monotonic() - 3000  # 50 of 90 minutes gone
        a._checked, a._evidence = True, False
        self.assertFalse(a._evidence_pass())
        b = one_shot()  # no time limit: no second look either
        b._checked, b._evidence = True, False
        self.assertFalse(b._evidence_pass())

    def test_the_requests_notation_wins(self):
        prompt = agent_module.system_prompt("/app", "m", "x", one_shot=True)
        self.assertIn("exact wording and notation", prompt)
        self.assertIn("beat the code's habits", agent_module.ONE_SHOT_CHECK)

    def test_a_question_at_the_end_is_answered_with_nobody(self):
        a = one_shot()
        scripted(a, [reply("", tool=("write_file", {"path": "a.txt", "content": "x"})),
                     reply("Done. If you'd rather have it as CSV, say so."), reply("Done.")])
        a.turn("make a.txt")
        check = next(u for u in users(a) if "before you finish" in u)
        self.assertIn("Nobody will answer", check)

    def test_a_question_after_the_check_gets_told_once(self):
        a = one_shot()
        scripted(a, [reply("", tool=("write_file", {"path": "a.txt", "content": "x"})), reply("Done."),
                     reply("Shall I also add tests?"), reply("Shall I also add tests?")])
        a.turn("make a.txt")
        told = [u for u in users(a) if u.startswith("(purr: Nobody will answer")]
        self.assertEqual(len(told), 1)

    def test_a_chat_can_still_ask(self):
        a = agent()
        scripted(a, [reply("Shall I also add tests?")])
        a.turn("hi")
        self.assertFalse(any("Nobody" in u for u in users(a)))

    def test_tests_that_time_out_are_not_called_failing(self):
        a = agent()
        a.tools.trust_all = True
        with mock.patch.object(agent_module.checks, "test_command", lambda root: "pytest"), \
                mock.patch.object(agent_module, "run_shell", lambda *a, **k: ("timed out after 180s", -1)):
            report = a._test_report()
        self.assertIn("timed out after 180s", report)
        self.assertNotIn("FAIL", report)


class PromptTest(unittest.TestCase):
    def test_one_shot_prompt_drops_what_needs_a_person(self):
        normal = agent_module.system_prompt("/app", "m", "openrouter")
        short = agent_module.system_prompt("/app", "m", "openrouter", one_shot=True)
        self.assertIn("running inside purr", normal)
        self.assertIn("approves every edit", normal)
        self.assertNotIn("running inside purr", short)
        self.assertNotIn("approves every edit", short)
        self.assertIn("exact absolute path", short)
        self.assertIn("Hidden tests", short)
        self.assertTrue(short.startswith("You are m, an AI model"))
        self.assertLess(len(short), len(normal) + 400)

    def test_every_mode_reads_instead_of_guessing_a_library(self):
        self.assertIn("how a library behaves", agent_module.system_prompt("/app", "m", "x"))


class PruneRepeatTest(unittest.TestCase):
    def test_task_list_updates_dont_protect_old_output(self):
        a = agent()
        a.limits = agent_module.Limits.for_model({"context": 32768, "limits": {"keep_recent_tools": 1}})
        big = "x" * 2000
        for i, (name, content) in enumerate([("run", big), ("run", big), ("todo", "task list saved")]):
            a.messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}]})
            a.messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": content})
        a._repeats = {"something": 1}
        self.assertGreater(a._prune_old_tools(), 0)
        tools = [m["content"] for m in a.messages if m["role"] == "tool"]
        self.assertTrue(tools[0].startswith("[old output of"))
        self.assertEqual(tools[1], big)  # the newest real output stays
        self.assertEqual(a._repeats, {})

    def test_reading_again_after_an_edit_is_not_a_repeat(self):
        a = agent()
        a._repeats = {}
        call = [("read_file", '{"path": "a.py"}', "1\tx = 1")]
        a._executed = call
        a._check_repeats()
        a.tools.edit_gen += 1
        a._executed = call
        self.assertIsNone(a._check_repeats())
        self.assertEqual(max(r["n"] for r in a._repeats.values()), 1)

    def test_one_shot_nudge_doesnt_suggest_asking(self):
        a = one_shot()
        a._repeats = {}
        nudge = None
        for _ in range(agent_module.REPEAT_NUDGE):
            a._executed = [("run", '{"command": "x"}', "error: nope")]
            nudge = a._check_repeats() or nudge
        self.assertIn("nobody will answer", nudge)
        self.assertNotIn("tell the user", nudge)


class ReadClipTest(unittest.TestCase):
    def test_read_file_stops_on_a_whole_line_and_says_where_to_go_on(self):
        a = agent()
        (Path(a.root) / "big.txt").write_text("".join(f"line {i} " + "y" * 200 + "\n" for i in range(400)))
        out = a.tools.call("read_file", json.dumps({"path": "big.txt"}))
        self.assertNotIn("chars cut", out)
        self.assertRegex(out, r"\[lines 1-\d+ of 400; next: offset=\d+\]")
        self.assertLessEqual(len(out), a.limits.tool_output)

    def test_long_lines_say_how_much_is_missing(self):
        a = agent()
        (Path(a.root) / "min.js").write_text("z" * 1500 + "\n")
        self.assertIn("…[+1000 chars]", a.tools.call("read_file", json.dumps({"path": "min.js"})))

    def test_clip_says_how_to_see_the_rest(self):
        self.assertIn("save the output to a file", clip("a" * 100, 50))


class CompactTest(unittest.TestCase):
    def test_the_request_and_open_tasks_survive(self):
        a = agent()
        a._request = "Write /app/out.csv with columns a,b. " + "detail " * 600 + "Must finish under 2s."
        a.tools.todo_list = [{"text": "write it", "status": "done"}, {"text": "time it", "status": "pending"}]
        a.messages += [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}]
        scripted(a, [{"text": "we wrote a draft", "reasoning": "", "tool_calls": [], "usage": None, "finish": "stop"}])
        a.compact(auto=True)
        text = a.messages[1]["content"]
        self.assertIn("/app/out.csv", text)
        self.assertIn("under 2s", text)
        self.assertIn("time it", text)
        self.assertNotIn("write it;", text)

    def test_a_failed_compaction_trims_instead_of_ending_the_run(self):
        a = agent()
        a.model = {**a.model, "context": 1000}
        a.messages += [{"role": "user", "content": "x" * 5000}] * 5
        scripted(a, [reply("done")])
        with mock.patch.object(a, "compact", side_effect=ApiError("context too long", status=400)):
            a.turn("go on")
        self.assertIsNone(a.failed)

    def test_reminder_keeps_both_ends(self):
        text = "start " + "m" * 2000 + " the end"
        self.assertIn("the end", agent_module._ends(text, 500, 300))
        self.assertLess(len(agent_module._ends(text, 500, 300)), 820)


class PathTest(unittest.TestCase):
    def test_one_shot_may_write_where_the_task_says(self):
        a = one_shot()
        a._one_shot_setup("Save the result to /srv/out/result.txt and keep scratch work out of the way.")
        self.assertIsNone(a.tools._outside("/srv/out/result.txt"))
        self.assertIsNone(a.tools._outside("/tmp/scratch.py"))
        self.assertIn("use run", a.tools._outside("/srv/other.txt"))

    def test_naming_a_system_folder_doesnt_open_it(self):
        a = one_shot()
        a._one_shot_setup("Look in /etc for the config.")
        self.assertIsNotNone(a.tools._outside("/etc/passwd"))


class TimeTest(unittest.TestCase):
    def test_a_command_cant_outlast_the_run(self):
        a = agent()
        a.tools.trust_all = True
        a.tools.deadline = agent_module.time.monotonic() + 100
        out = a.tools.call("run", json.dumps({"command": "echo hi", "timeout": 3600}))
        self.assertRegex(out, r"timeout cut to (39|40)s: that is all the time left")
        a.tools.deadline = None
        self.assertNotIn("timeout cut", a.tools.call("run", json.dumps({"command": "echo hi"})))

    def test_last_note_carries_the_short_check_in_one_shot_runs(self):
        a = one_shot(time_limit=600)
        a._started, a._time_said, a._checked = 0, {0.5, 0.8}, False
        a.messages = [a.messages[0]]
        with mock.patch.object(agent_module.time, "monotonic", lambda: 560):
            a._time_note()
        self.assertIn("no new experiments", a.messages[-1]["content"])
        self.assertIn("every file the request names", a.messages[-1]["content"])


class ToolTextTest(unittest.TestCase):
    def test_terminal_is_hidden_from_small_one_shot_runs_unless_the_task_is_interactive(self):
        a = one_shot()
        a._one_shot_setup("Train a classifier on data.csv")
        self.assertIn("terminal", a.hidden_tools)
        b = one_shot()
        b._one_shot_setup("Boot the VM with qemu and log in over ssh")
        self.assertNotIn("terminal", b.hidden_tools)
        self.assertNotIn("terminal", agent().hidden_tools)  # a chat keeps it

    def test_a_model_can_insist(self):
        config = {**CONFIG, "models": {**CONFIG["models"],
                                       "tty": {"provider": "ollama", "id": "tty", "context": 32768, "terminal": True}}}
        a = agent_module.Agent(config, tempfile.mkdtemp(), "tty", FakeView())
        a.set_one_shot()
        a._one_shot_setup("Train a classifier")
        self.assertNotIn("terminal", a.hidden_tools)

    def test_todo_says_what_is_next(self):
        a = agent()
        out = a.tools.call("todo", json.dumps({"items": [{"text": "read", "status": "done"},
                                                         {"text": "write the parser", "status": "pending"}]}))
        self.assertEqual(out, "task list saved; next: write the parser")


class EndTest(unittest.TestCase):
    def test_one_shot_never_asks_to_keep_going(self):
        a = one_shot()
        a.config = {**a.config, "max_steps": 3}
        a.time_limit = 600
        calls = scripted(a, [reply("", tool=("run", {"command": f"echo {i}"})) for i in range(40)])
        with mock.patch.object(agent_module, "ONE_SHOT_STEPS", 4), \
                mock.patch.object(a.view, "ask", side_effect=AssertionError("asked")):
            a.turn("loop forever")
        self.assertTrue(any("before you finish" in u for u in users(a)))  # checked once at the cap
        self.assertEqual(calls["n"], 4 + agent_module.CHECK_STEPS)

    def test_a_benchmarks_own_step_limit_wins(self):
        a = one_shot(time_limit=5400)
        a.config = {**a.config, "max_steps": 500}  # fair.sh: the same as DeepSeek's harnesses
        self.assertEqual(a._step_cap(), 500)
        a.config = {**a.config, "max_steps": 40}
        self.assertEqual(a._step_cap(), agent_module.ONE_SHOT_STEPS)

    def stream(self, *lines):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.__iter__.return_value = iter(line.encode() for line in lines)
        return resp

    def test_a_dropped_stream_can_be_retried(self):
        resp = self.stream('data: {"error": {"message": "upstream reset"}}')
        with mock.patch.object(api.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(ApiError) as e:
                api.stream_chat("http://x", "", {}, print, print)
        self.assertTrue(e.exception.retry)
        resp = self.stream('data: {"error": {"message": "bad request", "code": 400}}')
        with mock.patch.object(api.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(ApiError) as e:
                api.stream_chat("http://x", "", {}, print, print)
        self.assertFalse(e.exception.retry)

    def test_a_broken_connection_can_be_retried(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.__iter__.side_effect = http.client.IncompleteRead(b"")
        with mock.patch.object(api.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(ApiError) as e:
                api.stream_chat("http://x", "", {}, print, print)
        self.assertTrue(e.exception.retry)


class ProbeTest(unittest.TestCase):
    def test_a_bare_machine_gets_one_line_about_itself(self):
        a = one_shot(time_limit=600)
        scripted(a, [reply("done")])
        a.turn("compile hello.c")
        first = users(a)[0]
        self.assertIn("(purr: this machine: python 3.", first)
        self.assertLess(len(first.split("this machine:")[1]), 320)

    def test_a_project_or_a_chat_gets_none(self):
        a = one_shot(time_limit=600)
        (Path(a.root) / "pyproject.toml").write_text("[project]\nname='x'\n")
        scripted(a, [reply("done")])
        a.turn("fix the bug")
        self.assertNotIn("this machine", users(a)[0])
        b = agent()
        b.time_limit = 600
        scripted(b, [reply("done")])
        b.turn("hi")
        self.assertNotIn("this machine", users(b)[0])


class PublishTest(unittest.TestCase):
    """tbench/publish.py: Terminal-Bench 2.1 runs, and the older 2.0 ones in their own tables."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tbench"))
        import publish
        self.publish = publish

    def test_which_runs_are_published(self):
        quick = [{"task": t} for t in self.publish.QUICK]
        self.assertEqual(self.publish.profile("terminal-bench/terminal-bench-2-1", quick), "quick")
        self.assertEqual(self.publish.profile("terminal-bench@2.0", quick), "quick")
        self.assertIsNone(self.publish.profile("terminal-bench/terminal-bench", quick))  # 4.0
        self.assertIsNone(self.publish.profile("terminal-bench/terminal-bench-2-1", quick[:5]))
        every = [{"task": f"t{i}"} for i in range(89)]
        self.assertEqual(self.publish.profile("terminal-bench/terminal-bench-2-1", every), "one")
        self.assertEqual(self.publish.profile("terminal-bench/terminal-bench-2-1", every * 3), "full")

    def test_each_dataset_has_its_own_tables(self):
        base = {"date": "2026-10-02", "pass@1": 75.0, "stderr": 9.9, "pass@k": 75.0, "timeouts": 0, "errors": 0,
                "trials": 20, "cost_usd": 0.5, "tokens_in": 1, "tokens_cached": 1, "tokens_out": 1,
                "median_agent_minutes": 3.0, "profile": "quick"}
        page = self.publish.readme([{**base, "purr": "0.3.1", "dataset": "terminal-bench@2.0"},
                                    {**base, "purr": "0.4.0", "dataset": "terminal-bench/terminal-bench-2-1"}])
        new, old = page.split("### Terminal-Bench 2.0")
        self.assertIn("| 0.4.0 |", new)
        self.assertNotIn("| 0.3.1 |", new)
        self.assertIn("| 0.3.1 |", old)
        self.assertNotIn("2.0: full runs", page)  # no full 2.0 run: no empty table for it
        self.assertIn("2.1: one-try runs", page)
        self.assertNotIn("DeepSWE 1.1 (113", page)  # the other benchmark's references go on its own page

    def test_a_stopped_run_gets_its_own_table_with_its_note(self):
        base = {"date": "2026-10-02", "pass@1": 56.2, "stderr": 7.2, "pass@k": 56.2, "timeouts": 0, "errors": 0,
                "trials": 54, "tasks": 48, "cost_usd": 7.0, "tokens_in": 1, "tokens_cached": 1, "tokens_out": 1,
                "median_agent_minutes": 14.0, "purr": "0.4.0", "dataset": "datacurve/deep-swe-1-1"}
        page = self.publish.readme([{**base, "profile": "stopped", "note": "a step cap bug"}], "deepswe")
        self.assertIn("stopped runs (not comparable)", page)
        self.assertIn("| 0.4.0 | 2026-10-02 | 48 of 113 | 56.2% | $7.0 | a step cap bug |", page)
        one = page.split("one-try runs")[1].split("###")[0]
        self.assertIn("(none yet)", one)  # not in the real tables

    def test_deepswe_has_its_own_page(self):
        quick = [{"task": t} for t in self.publish.BENCHES["DeepSWE 1.1"]["quick"]]
        self.assertEqual(len(quick), 20)
        self.assertEqual(self.publish.profile("datacurve/deep-swe-1-1", quick), "quick")
        self.assertEqual(self.publish.profile("datacurve/deep-swe-1-1", [{"task": f"t{i}"} for i in range(113)]), "one")
        self.assertIsNone(self.publish.profile("datacurve/deep-swe-1-1", [{"task": f"t{i}"} for i in range(89)]))
        page = self.publish.readme([], "deepswe")
        self.assertIn("# purr on DeepSWE", page)
        self.assertIn("all 113 tasks", page)
        self.assertIn("| mini-SWE-agent | DeepSeek V4.1 Flash | DeepSWE 1.1 (113 tasks) | 74.2%", page)
        self.assertNotIn("Terminal-Bench 2.1 (89", page)


if __name__ == "__main__":
    unittest.main()
