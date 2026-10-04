"""From two full Terminal-Bench 2.1 runs: no looking the benchmark up (harness/benchguard.py), an
admitted gap goes back to be closed, the grader's yardstick in the final check, evidence that
isn't the model's own, and a review that sees what commands made.

Run: python3 -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import benchguard, prompts  # noqa: E402
from tests.test_bench_fixes import one_shot, users  # noqa: E402
from tests.test_limits import reply, scripted  # noqa: E402


class GuardTest(unittest.TestCase):
    def test_what_counts_as_looking_the_benchmark_up(self):
        lookups = ["curl -sL https://raw.githubusercontent.com/harbor-framework/terminal-bench-1/main/x/solution.sh",
                   "git clone --depth 1 https://github.com/laude-institute/terminal-bench.git tb",
                   "python3 -c \"import urllib.request; urllib.request.urlopen('https://api.github.com/search/repositories?q=terminal-bench')\"",
                   "https://marginlab.ai/explorers/terminal-bench/mteb-retrieve/"]
        for text in lookups:
            self.assertIsNotNone(benchguard.lookup(text), text)
        for fine in ("pip install numpy scipy", "curl -sL https://raw.githubusercontent.com/primer3-org/primer3/main/src/oligotm.c",
                     "cat /app/tbench_notes.txt"):  # a name with no network, or the network for something else
            self.assertIsNone(benchguard.lookup(fine), fine)

    def test_the_tools_refuse_in_a_benchmark_run_only(self):
        a = one_shot()
        url = "https://raw.githubusercontent.com/harbor-framework/terminal-bench-1/main/raman-fitting/solution.sh"
        self.assertIsNone(a.tools._benchmark_lookup(url))  # off outside benchmark runs (purr_agent turns it on)
        a.tools.bench_guard = True
        out = a.tools.call("fetch_url", json.dumps({"url": url}))
        self.assertTrue(out.startswith(benchguard.REFUSED), out)
        out = a.tools.call("run", json.dumps({"command": "git clone https://github.com/laude-institute/terminal-bench.git"}))
        self.assertTrue(out.startswith(benchguard.REFUSED), out)
        self.assertIn("looked up the benchmark itself -> refused", a.tools.repairs)

    def test_a_trial_that_looked_it_up_is_found_in_its_log(self):
        trial = Path(tempfile.mkdtemp())
        sessions = trial / "agent" / "purr-state" / "sessions"
        sessions.mkdir(parents=True)

        def log(result):
            call = {"id": "c1", "type": "function", "function": {"name": "fetch_url", "arguments": json.dumps(
                {"url": "https://marginlab.ai/explorers/terminal-bench/mteb-retrieve/"})}}
            (sessions / "s.json").write_text(json.dumps({"messages": [
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": "c1", "content": result}]}))
        log("expected = 'MTEB: Massive Text Embedding Benchmark'")
        self.assertTrue(benchguard.looked_up(trial))
        log(benchguard.REFUSAL.format(name="terminal-bench"))  # refused: nothing was fetched
        self.assertFalse(benchguard.looked_up(trial))

    def test_publish_counts_a_looked_up_pass_as_failed(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tbench"))
        import publish
        job = Path(tempfile.mkdtemp())
        (job / "config.json").write_text(json.dumps({"agents": [{"name": "tbench.purr_agent:PurrAgent"}]}))
        for name, looked in (("mteb-retrieve__a", True), ("hello__b", False)):
            (job / name / "agent" / "purr-state" / "sessions").mkdir(parents=True)
            (job / name / "result.json").write_text(json.dumps({
                "task_name": "terminal-bench/" + name.split("__")[0], "trial_name": name,
                "verifier_result": {"rewards": {"reward": 1.0}}}))
            calls = [{"id": "c", "type": "function", "function": {"name": "run", "arguments": json.dumps(
                {"command": "curl https://github.com/laude-institute/terminal-bench" if looked else "ls"})}}]
            (job / name / "agent" / "purr-state" / "sessions" / "s.json").write_text(json.dumps({"messages": [
                {"role": "assistant", "tool_calls": calls}, {"role": "tool", "tool_call_id": "c", "content": "..."}]}))
        _, config, trials = publish.load_job(job)
        by_task = {t["task"]: t for t in trials}
        self.assertEqual(by_task["mteb-retrieve"]["reward"], 0.0)
        self.assertTrue(by_task["mteb-retrieve"]["looked_up"])
        self.assertEqual(by_task["hello"]["reward"], 1.0)


class GapTest(unittest.TestCase):
    def test_an_admitted_gap_goes_back_once(self):
        a = one_shot(time_limit=900)
        scripted(a, [reply("", tool=("write_file", {"path": "out.txt", "content": "flag{?}\n"})),
                     reply("Done."),  # the final check comes
                     reply("Wrote out.txt. The flag is my best guess from the ASCII render. Nothing else to do."),
                     reply("Now verified: the render reads flag{x}."), reply("ok")])
        a.turn("find the flag and write it to out.txt")
        gaps = [u for u in users(a) if "that part isn't done" in u]
        self.assertEqual(len(gaps), 1)
        self.assertIn('"The flag is my best guess from the ASCII render"', gaps[0])
        self.assertIn("minutes", gaps[0])

    def test_a_clean_reply_just_finishes(self):
        a = one_shot(time_limit=900)
        scripted(a, [reply("", tool=("write_file", {"path": "out.txt", "content": "1\n"})),
                     reply("Done."), reply("All requirements met and tested."), reply("ok")])
        a.turn("write 1 to out.txt")
        self.assertFalse([u for u in users(a) if "that part isn't done" in u])


class CheckWordsTest(unittest.TestCase):
    def test_the_final_check_uses_the_graders_yardstick(self):
        check = prompts.ONE_SHOT_CHECK.format(time="", services="", nobody="", scratch="")
        for words in ("scope words (per, each, across, total)", "element-wise to numerical precision",
                      "what the standard tool produces", "its conventions, not a better score",
                      "loaded fresh and fed raw inputs", "try two or three ways"):
            self.assertIn(words, check)
        self.assertIn("designed around your own solution doesn't count", prompts.EVIDENCE_PASS)
        self.assertIn("run your end-to-end check of the whole deliverable again", prompts.REVIEW_NOTE)


class ReviewSeesCommandsTest(unittest.TestCase):
    def test_a_file_a_command_made_is_in_the_review(self):
        a = one_shot(time_limit=900)
        Path(a.root, "old.txt").write_text("unchanged\n")
        a._disk_before = a._disk_snapshot()
        Path(a.root, "made.sh").write_text("#!/bin/sh\necho hello\n")  # as if `cp` or a script made it
        diff = a._change_diff(10000)
        self.assertIn("made.sh (made by a command; its start)", diff)
        self.assertIn("echo hello", diff)
        self.assertNotIn("old.txt", diff)


if __name__ == "__main__":
    unittest.main()
