"""The task reaches purr through a file, not its command line (rstan-to-pystan: `pkill -9 -f
pystan_analysis` matched the task text on purr's own command line and killed purr), names inside
commands count as names, and a published run can carry a note.

Run: python3 -m unittest discover tests
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import prompts  # noqa: E402

try:
    from tbench import purr_agent
except ImportError:  # Harbor is a uv tool, not in purr's own environment
    purr_agent = None


class PromptFileTest(unittest.TestCase):
    def test_purr_reads_the_task_from_a_file(self):
        import purr
        task = Path(tempfile.mkdtemp()) / "task.txt"
        task.write_text("Convert /app/gp_rstan.R to /app/pystan_analysis.py")
        with mock.patch.object(sys, "argv", ["purr", tempfile.mkdtemp(), "--plain", "--prompt-file", str(task)]), \
                mock.patch.object(purr, "Agent") as agent:
            agent.return_value.failed = None
            purr.main()
        agent.return_value.turn.assert_called_once_with("Convert /app/gp_rstan.R to /app/pystan_analysis.py")

    def test_names_inside_commands_are_names(self):
        check = prompts.ONE_SHOT_CHECK.format(time="", services="", nobody="", scratch="")
        self.assertIn("the user in user@host", check)


@unittest.skipIf(purr_agent is None, "needs harbor (uv tool install harbor)")
class ContainerCommandTest(unittest.TestCase):
    def test_the_task_is_uploaded_not_on_the_command_line(self):
        agent = purr_agent.PurrAgent(logs_dir=Path(tempfile.mkdtemp()), model_name="openrouter/deepseek/deepseek-v4.1-flash")
        uploaded, commands = {}, []

        class Env:
            async def upload_file(self, source, target):
                uploaded[target] = Path(source).read_text()

        async def exec_as_agent(environment, command, env=None, **kw):
            commands.append(command)
        task = "Convert the R script to /app/pystan_analysis.py"
        with mock.patch.object(agent, "exec_as_agent", exec_as_agent), \
                mock.patch.object(type(agent), "model_connection", mock.PropertyMock(return_value=mock.Mock(env={}, api_key="k"))), \
                mock.patch.object(agent, "task_time_limit", return_value=None):
            asyncio.run(purr_agent.PurrAgent.run.__wrapped__(agent, task, Env(), mock.Mock()))
        self.assertEqual(uploaded, {f"{purr_agent.REMOTE}/task.txt": task})
        self.assertIn("--prompt-file", commands[-1])
        self.assertNotIn("pystan_analysis", commands[-1])  # pkill -f can't find purr by the task's words


class NoteTest(unittest.TestCase):
    def test_a_published_run_carries_a_footnote(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tbench"))
        import publish
        base = {"date": "2026-10-03", "pass@1": 80.9, "stderr": 4.2, "pass@k": 80.9, "timeouts": 1, "errors": 1,
                "trials": 89, "tasks": 89, "cost_usd": 4.52, "tokens_in": 1, "tokens_cached": 1, "tokens_out": 1,
                "median_agent_minutes": 5.7, "purr": "0.5.0+abc", "dataset": "terminal-bench/terminal-bench-2-1",
                "model": "openrouter/deepseek/deepseek-v4.1-flash", "profile": "one",
                "note": "qemu-alpine-ssh and qemu-startup can't pass right now"}
        page = publish.readme([base], "terminal-bench")
        self.assertIn("| 0.5.0+abc † | 2026-10-03 | **80.9%**", page)
        self.assertIn("† 0.5.0+abc: qemu-alpine-ssh and qemu-startup can't pass right now", page)
        self.assertEqual(json.loads(json.dumps(base))["note"], base["note"])


if __name__ == "__main__":
    unittest.main()
