"""The job watch (harness/jobs.py): a background job's log read for progress, and a word when it
won't finish in the time left or stopped moving; the pilot rule in the time note.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import jobs, prompts  # noqa: E402
from tests.test_bench_fixes import one_shot, users  # noqa: E402
from tests.test_limits import reply, scripted  # noqa: E402


class ProgressTest(unittest.TestCase):
    def test_what_progress_lines_say(self):
        self.assertAlmostEqual(jobs.progress("loading\nEpoch 5/25 loss 0.3\n"), 0.2)
        self.assertAlmostEqual(jobs.progress("Epoch 3 of 4\n"), 0.75)
        self.assertAlmostEqual(jobs.progress("Chain 1: Iteration: 200 / 2000 [ 10%]  (Warmup)\n"), 0.1)
        self.assertAlmostEqual(jobs.progress(" 40%|████      | 400/1000\r 50%|█████     | 500/1000"), 0.5)
        self.assertIsNone(jobs.progress("compiling model\nstarting\n"))


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.watch = jobs.Jobs(self.root)

    def test_only_background_jobs_with_a_log(self):
        self.watch.saw("python3 train.py > train.log")  # not in the background
        self.watch.saw("sleep 100 &")                    # no log
        self.watch.saw("nohup python3 train.py > train.log 2>&1 &")
        self.watch.saw("nohup python3 train.py > train.log 2>&1 &")  # the same job again
        self.assertEqual([j["log"] for j in self.watch.jobs], [self.root / "train.log"])

    def test_a_job_that_wont_make_it_is_said_once(self):
        self.watch.saw("nohup python3 train.py > train.log 2>&1 &")
        (self.root / "train.log").write_text("Epoch 2/25\n")
        self.watch.jobs[0]["started"] = time.time() - 600  # 10 minutes for 2 epochs: 115 more
        self.assertEqual(self.watch.notes(seconds_left=3 * 3600), [])  # it fits
        notes = self.watch.notes(seconds_left=1200)
        self.assertEqual(len(notes), 1)
        self.assertIn("about 8% done after 10 min", notes[0])
        self.assertIn("about 115 more, and you have about 20", notes[0])
        self.assertEqual(self.watch.notes(seconds_left=1000), [])  # once

    def test_a_log_that_stopped_moving(self):
        self.watch.saw("nohup ./sample > /tmp/purr-test-sample.log 2>&1 &")
        log = Path("/tmp/purr-test-sample.log")
        self.addCleanup(lambda: log.unlink(missing_ok=True))
        log.write_text("Chain 1: 30%\n")
        old = time.time() - 900
        os.utime(log, (old, old))
        self.watch.jobs[0]["started"] = old - 60
        notes = self.watch.notes(seconds_left=None)
        self.assertEqual(len(notes), 1)
        self.assertIn("hasn't changed for 15 min", notes[0])
        log.write_text("done 100%\n")  # finished: nothing to say
        self.assertEqual(jobs.Jobs(self.root).notes(None), [])


class AgentTest(unittest.TestCase):
    def test_the_agent_watches_jobs_a_run_starts(self):
        a = one_shot(time_limit=1800)
        log = Path(a.root, "fit.log")
        script = ("python3 -c \"print('Epoch 1/50', flush=True)\" > fit.log 2>&1 &")
        scripted(a, [reply("", tool=("run", {"command": script})),
                     reply("", tool=("run", {"command": "sleep 1; cat fit.log"})),
                     reply("Done."), reply("Checked everything."), reply("ok"), reply("ok")])
        orig = jobs.Jobs.notes

        def aged(watch, left, now=None):  # as if the epoch took 5 minutes
            for job in watch.jobs:
                job["started"] = min(job["started"], time.time() - 300)
            return orig(watch, left, now)
        jobs.Jobs.notes = aged
        self.addCleanup(setattr, jobs.Jobs, "notes", orig)
        a.turn("fit the model")
        self.assertTrue(log.exists())
        said = [u for u in users(a) if "at this pace it needs about" in u]
        self.assertEqual(len(said), 1, users(a))
        self.assertIn(prompts.PILOT, users(a)[0])

    def test_job_watch_false_turns_it_off(self):
        a = one_shot(time_limit=1800)
        a.config = dict(a.config, job_watch=False)
        scripted(a, [reply("Done."), reply("Checked."), reply("ok"), reply("ok")])
        a.turn("say hi")
        self.assertIsNone(a.jobs)
        self.assertNotIn(prompts.PILOT, users(a)[0])
