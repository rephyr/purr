"""Minimal mode (harness/minimal.py): DSH Minimal's setup inside purr, and keep_reasoning = "all".
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import minimal  # noqa: E402
from harness.agent import Agent  # noqa: E402
from tests.test_limits import CONFIG, FakeView, reply, scripted  # noqa: E402

try:
    from tbench import purr_agent
except ImportError:  # Harbor is a uv tool, not in purr's own environment
    purr_agent = None


def minimal_agent(**config):
    cfg = {**CONFIG, "minimal": True, **config}
    cfg["providers"] = {**CONFIG["providers"],
                        "openrouter": {**CONFIG["providers"]["openrouter"], "echo_reasoning": "reasoning"}}
    a = Agent(cfg, tempfile.mkdtemp(), "api", FakeView())
    a.set_one_shot()
    return a


class BashSessionTest(unittest.TestCase):
    def setUp(self):
        self.shell = minimal.BashSession(tempfile.mkdtemp())
        self.addCleanup(self.shell.close)

    def test_the_shell_stays_open_between_commands(self):
        self.assertEqual(self.shell.run("cd /tmp && export FOO=bar"), "[exit code: 0]")
        self.assertEqual(self.shell.run("pwd; echo $FOO"), "/tmp\nbar\n[exit code: 0]")

    def test_nothing_waits_for_input_or_a_closing_quote(self):
        self.assertEqual(self.shell.run("cat"), "[exit code: 0]")  # stdin is empty
        self.assertIn("unexpected EOF", self.shell.run("echo 'unclosed"))
        self.assertEqual(self.shell.run("printf 'no newline'"), "no newline\n[exit code: 0]")
        self.assertEqual(self.shell.run("cat <<EOF\nhere\nEOF"), "here\n[exit code: 0]")

    def test_a_timeout_or_exit_starts_a_fresh_shell(self):
        self.shell.run("export FOO=bar")
        out = self.shell.run("sleep 5", timeout=1)
        self.assertIn("timed out after 1 seconds", out)
        self.assertEqual(self.shell.run("echo ${FOO:-gone}"), "gone\n[exit code: 0]")
        self.assertIn("[exit code: 3]", self.shell.run("exit 3"))
        self.assertEqual(self.shell.run("echo back"), "back\n[exit code: 0]")

    def test_long_output_keeps_its_start(self):
        out = self.shell.run("seq 1 100000")
        self.assertTrue(out.startswith("1\n2\n3\n"))
        self.assertIn("<response clipped", out)
        self.assertLess(len(out), minimal.MAX_OUTPUT + 400)


class MinimalAgentTest(unittest.TestCase):
    def test_one_line_prompt_one_tool_effort_high(self):
        a = minimal_agent()
        self.assertEqual(a.messages[0]["content"], minimal.SYSTEM)
        body = a._body(None, True)
        self.assertEqual([t["function"]["name"] for t in body["tools"]], ["bash"])
        self.assertEqual(body["reasoning"]["effort"], "high")

    def test_the_loop_runs_bash_until_it_answers_and_checks_nothing(self):
        a = minimal_agent()
        Path(a.root, "f.txt").write_text("x")
        calls = scripted(a, [reply("", tool=("bash", {"command": "cd /tmp && echo one"})),
                             reply("", tool=("bash", {"command": "pwd"})),
                             reply("done")])
        a.turn("do the task")
        self.assertEqual(calls["n"], 3)
        results = [m["content"] for m in a.messages if m["role"] == "tool"]
        self.assertEqual(results, ["one\n[exit code: 0]", "/tmp\n[exit code: 0]"])  # the same shell
        users = [m["content"] for m in a.messages if m["role"] == "user"]
        self.assertEqual(users, ["do the task"])  # no time note, probe, reminder or final check
        a._bash.close()

    def test_every_steps_thinking_goes_back(self):
        for agent in (minimal_agent(), minimal_agent(minimal=False, keep_reasoning="all")):
            agent.messages += [{"role": "assistant", "content": "", "reasoning": f"thought {i}"} for i in range(5)]
            sent = agent._for_provider(agent.messages)
            self.assertEqual(sum(1 for m in sent if m.get("reasoning")), 5)
        usual = minimal_agent(minimal=False)
        usual.messages += [{"role": "assistant", "content": "", "reasoning": f"thought {i}"} for i in range(5)]
        self.assertEqual(sum(1 for m in usual._for_provider(usual.messages) if m.get("reasoning")), 2)

    def test_no_effort_setting_goes_to_ollama(self):
        a = Agent({**CONFIG, "minimal": True}, tempfile.mkdtemp(), "big", FakeView())  # an Ollama model
        body = a._body(None, True)
        self.assertNotIn("reasoning_effort", body)
        self.assertNotIn("reasoning", body)

    def test_a_call_without_a_command_gets_told(self):
        a = minimal_agent()
        self.assertIn("needs a command", a._minimal_bash("bash", json.dumps({"cmd": "ls"})))


class PublishLabelTest(unittest.TestCase):
    def test_a_minimal_run_is_named_in_its_row(self):
        from tbench import publish
        r = {"purr": "0.4.0+abc", "settings": {"minimal": "true", "hosts": "deepseek"}}
        self.assertEqual(publish.label(r), "0.4.0+abc (minimal)")
        self.assertEqual(publish.label({"purr": "0.4.0+abc", "settings": {"minimal": "false"}}), "0.4.0+abc")
        self.assertEqual(publish.label({"purr": "0.4.0+abc", "settings": {"keep_reasoning": "all"}}),
                         "0.4.0+abc (all thinking kept)")
        self.assertEqual(publish.label({"purr": "0.4.0+abc", "settings": {"minimal": "true", "agent_timeout_multiplier": 3.0}}),
                         "0.4.0+abc (minimal, ×3 time)")
        self.assertEqual(publish.label({"purr": "0.4.0+abc", "settings": {"agent_timeout_multiplier": 1.0}}), "0.4.0+abc")


@unittest.skipIf(purr_agent is None, "needs harbor (uv tool install harbor)")
class BenchOptionTest(unittest.TestCase):
    def test_minimal_reaches_purrs_config_without_mcp(self):
        agent = purr_agent.PurrAgent(logs_dir=Path(tempfile.mkdtemp()), model_name="openrouter/deepseek/deepseek-v4.1-flash",
                                     minimal="true", keep_reasoning="all")
        config = agent.purr_config()
        self.assertTrue(config["minimal"])
        self.assertEqual(config["keep_reasoning"], "all")
        self.assertNotIn("mcp", config)
