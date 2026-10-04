"""Big (API) models: two plans and a judge, the fresh-eyes tester, effort per phase, a fresh
context, and extras=false for benchmark runs that compare with the published ones.

Run: python3 -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import prompts  # noqa: E402
from harness.agent import Agent  # noqa: E402
from tests.test_bench_fixes import one_shot, users  # noqa: E402
from tests.test_limits import reply, scripted  # noqa: E402


def big(time_limit=None, **config):
    a = one_shot("api", time_limit=time_limit)
    a.config = dict(a.config, **config)
    return a


class GateTest(unittest.TestCase):
    def test_on_for_api_models_off_for_local_ones(self):
        def fresh(model):  # without the shared test config's two_plans/fresh_eyes = false
            a = one_shot(model)
            a.config = {k: v for k, v in a.config.items() if k not in ("two_plans", "fresh_eyes")}
            return a
        self.assertTrue(fresh("api")._big_on("fresh_eyes"))
        self.assertTrue(fresh("api")._big_on("two_plans"))
        self.assertFalse(fresh("small")._big_on("fresh_eyes"))
        self.assertFalse(fresh("big")._big_on("two_plans"))  # a local model, however big its context
        self.assertFalse(big(fresh_context=False)._big_on("fresh_context"))
        self.assertTrue(one_shot("api")._on("job_watch"))  # the cheap checks: every model


class TwoPlansTest(unittest.TestCase):
    def test_the_judged_plan_goes_into_the_first_message(self):
        a = big(two_plans=True)
        calls = scripted(a, [reply("PLAN A: use sed"), reply("PLAN B: use awk, watch the header"),
                             reply("Thinking...\nPLAN:\n- use awk\n- skip the header line"),
                             reply("Done.")])
        a.turn("drop the first column of data.csv")
        first = users(a)[0]
        self.assertIn("two independent plans were drafted", first)
        self.assertIn("- skip the header line", first)
        self.assertNotIn("Thinking", first)
        self.assertEqual(calls["n"], 4)

    def test_not_in_a_chat_or_for_small_models(self):
        a = Agent(dict(one_shot("api").config, two_plans=True), tempfile.mkdtemp(), "api", one_shot().view)
        self.assertEqual(a._two_plans("x"), "")  # not one-shot: you're there to steer
        self.assertEqual(one_shot("small")._two_plans("x"), "")


class FreshEyesTest(unittest.TestCase):
    def run_with_tester(self, tester_says):
        a = big(fresh_eyes=True, review=False)
        scripted(a, [reply("", tool=("write_file", {"path": "out.txt", "content": "41\n"})),
                     reply("Done."), reply("Checked: out.txt has the number."), reply("Fixed it."), reply("ok")])
        seen = {}

        def tester_call(self, messages=None, tools=True, quiet=False, hard=False):
            seen.setdefault("tester", self)
            seen["n"] = seen.get("n", 0) + 1
            return reply("", tool=("run", {"command": "cat out.txt"})) if seen["n"] == 1 else reply(tester_says)
        with mock.patch.object(Agent, "_call", tester_call):  # only the tester: the run's own calls are scripted
            a.turn("write 42 to out.txt")
        return a, seen["tester"]

    def test_a_fail_goes_back_to_the_model(self):
        a, tester = self.run_with_tester("FAIL out.txt holds 42: `cat out.txt` shows 41\nPASS out.txt exists: ls")
        notes = [u for u in users(a) if "a tester with a fresh context" in u]
        self.assertEqual(len(notes), 1)
        self.assertIn("FAIL out.txt holds 42", notes[0])
        self.assertNotIn("PASS out.txt exists", notes[0])
        # it saw only the request and the file names, ran a command, and had no way to edit
        self.assertIn("write 42 to out.txt", tester.messages[1]["content"])
        self.assertIn("out.txt", tester.messages[1]["content"])
        self.assertIn("41", tester.messages[3]["content"])  # cat's output
        names = {t["function"]["name"] for t in tester._body(None, True)["tools"]}
        self.assertIn("run", names)
        self.assertFalse(names & {"write_file", "edit_file", "task"})
        self.assertIn("You are a tester", tester.messages[0]["content"])
        import json
        self.assertEqual(json.loads(Path(a.log_path).read_text())["tester"][1]["role"], "user")  # kept in the log

    def test_all_pass_just_finishes(self):
        a, _ = self.run_with_tester("PASS out.txt holds 42: cat\nALL PASS")
        self.assertFalse([u for u in users(a) if "a tester with a fresh context" in u])

    def test_no_time_for_it(self):
        a = big(time_limit=120, fresh_eyes=True)
        a._begin_turn("x")
        a._acted = True
        self.assertFalse(a._fresh_eyes())  # 15% of 2 minutes: no time for a test and a fix


class EffortTest(unittest.TestCase):
    def test_hard_calls_ask_for_more_thinking_where_the_provider_takes_it(self):
        a = big()
        self.assertEqual(a._body(None, True, hard=True)["reasoning"]["effort"], "high")
        self.assertNotIn("reasoning", a._body(None, True))
        self.assertNotIn("reasoning_effort", one_shot("small")._body(None, True, hard=True))

    def test_the_first_step_and_a_send_back_are_hard(self):
        a = big()
        flags = []
        replies = [reply("", tool=("write_file", {"path": "a.txt", "content": "1\n"})), reply("", tool=(
            "read_file", {"path": "a.txt"})), reply("Done."), reply("Checked."), reply("ok")]

        def fake(*args, **kwargs):
            flags.append(a._think_hard)
            return replies[min(len(flags) - 1, len(replies) - 1)]
        a._call = fake
        a.turn("write 1 to a.txt")
        self.assertEqual(flags[:4], [True, False, False, True])  # first step; routine; wants to stop; after the check


class FreshContextTest(unittest.TestCase):
    def test_a_long_chat_goes_on_from_a_summary(self):
        a = big()
        a._compact_again_at = 0
        a.messages += [{"role": "user", "content": "x"}] * 6
        compacted = []
        a.compact = lambda auto=False: compacted.append(auto) or (200_000, 5_000)
        with mock.patch.object(Agent, "context_used", lambda self: 200_000):
            a._make_room()
        self.assertEqual(compacted, [True])
        small = one_shot("big")  # a local model: its own limits decide
        small._compact_again_at = 0
        small.messages += [{"role": "user", "content": "x"}] * 6
        small.compact = lambda auto=False: compacted.append("small")
        with mock.patch.object(Agent, "context_used", lambda self: 200_000):
            small._make_room()
        self.assertEqual(compacted, [True])


try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tbench"))
    import purr_agent
except ImportError:
    purr_agent = None


@unittest.skipIf(purr_agent is None, "needs harbor (uv tool install harbor)")
class BenchSwitchTest(unittest.TestCase):
    def test_extras_false_is_the_published_setup(self):
        agent = purr_agent.PurrAgent(logs_dir=Path(tempfile.mkdtemp()), model_name="openrouter/deepseek/deepseek-v4.1-flash",
                                     extras="false")
        config = agent.purr_config()
        self.assertTrue(all(config[key] is False for key in purr_agent.EXTRAS))
        import publish
        self.assertIn("without the newer checks", publish.label({"purr": "0.5.0", "settings": {"extras": "false"}}))
        self.assertNotIn("fresh_eyes", purr_agent.PurrAgent(logs_dir=Path(tempfile.mkdtemp()),
                         model_name="openrouter/deepseek/deepseek-v4.1-flash").purr_config())


class PromptTest(unittest.TestCase):
    def test_the_tester_reports_in_lines_purr_reads(self):
        for words in ("PASS <requirement>", "FAIL <requirement>", "ALL PASS", "Don't fix or change anything"):
            self.assertIn(words, prompts.TESTER)


if __name__ == "__main__":
    unittest.main()
