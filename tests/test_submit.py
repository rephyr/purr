"""tbench/submit.py: a run that meets the Terminal-Bench 2.0 leaderboard's rules is made ready to
submit, and one that doesn't (too few tries, an override, a leaked key, the web tool on) is not.
Nothing is uploaded: the job folders are made up.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("submit", HERE / "tbench" / "submit.py")
submit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(submit)


def make_job(tries=5, web="false", multiplier=1.0, extra=None):
    job = Path(tempfile.mkdtemp()) / "2026-10-03__20-00-00"
    job.mkdir(parents=True)
    agent = {"name": "tbench.purr_agent:PurrAgent", "model_name": "openrouter/deepseek/deepseek-v4.1-flash",
             "kwargs": {"hosts": "deepseek", "web": web, "max_steps": 500}}
    config = {"agents": [agent], "datasets": [{"name": "terminal-bench/terminal-bench-2"}],
              "agent_timeout_multiplier": multiplier, **(extra or {})}
    (job / "config.json").write_text(json.dumps(config))
    for t in range(submit.TASKS):
        for k in range(tries):
            d = job / f"task{t}__{k}"
            (d / "agent").mkdir(parents=True)
            (d / "agent" / "purr.txt").write_text("  ◆ run ls\n")
            (d / "result.json").write_text(json.dumps({"task_name": f"terminal-bench/task{t}",
                                                       "agent_info": {"version": "0.4.0+abc1234"}}))
    return job


class SubmitTest(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(submit, "OUT", self.out), mock.patch.object(submit, "known_keys", lambda: [])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_a_run_that_meets_the_rules_gets_a_submission_folder(self):
        job = make_job()
        self.assertEqual(submit.main([str(job)]), 0)
        folder = self.out / "purr__deepseek-v4.1-flash"
        self.assertIn("agent_display_name: \"purr\"", (folder / "metadata.yaml").read_text())
        self.assertTrue((folder / job.name / "config.json").exists())

    def test_too_few_tries_fail(self):
        rules, _, _ = submit.check(make_job(tries=3))
        self.assertFalse(dict((w.split(" (")[0], ok) for ok, w in rules)["at least 5 tries every task"])

    def test_overrides_and_multipliers_fail(self):
        job = make_job(multiplier=2.0, extra={"environment": {"override_cpus": 8}})
        failed = [w for ok, w in submit.check(job)[0] if not ok]
        self.assertTrue(any("multiplier" in w for w in failed))
        self.assertTrue(any("override_cpus" in w for w in failed))

    def test_the_web_tool_on_fails(self):
        failed = [w for ok, w in submit.check(make_job(web="true"))[0] if not ok]
        self.assertEqual(failed, ["purr's web tool was off (web=false)"])

    def test_a_leaked_key_is_caught(self):
        job = make_job()
        (job / "task3__0" / "agent" / "purr.txt").write_text("export OPENROUTER_API_KEY=sk-or-v1-" + "a1" * 32)
        failed = [w for ok, w in submit.check(job)[0] if not ok]
        self.assertTrue(any("task3__0/agent/purr.txt" in w for w in failed))
        with mock.patch.object(submit, "known_keys", lambda: ["my-real-key-0123456789"]):
            (job / "task3__0" / "agent" / "purr.txt").write_text("key my-real-key-0123456789 here")
            self.assertTrue(any("API keys" in w for ok, w in submit.check(job)[0] if not ok))
            self.assertEqual(submit.main([str(job)]), 1)
        self.assertFalse((self.out / "purr__deepseek-v4.1-flash").exists())
