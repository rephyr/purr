"""purr bench --real: reading Harbor's job folder, the score, Mochi's verdict, and the window.

Run: python3 -m unittest discover tests
No model is called and nothing is benchmarked: the job folders are made up.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import bench, realbench  # noqa: E402

try:
    from tui import realbench_app
except ImportError:  # textual isn't there in a plain install
    realbench_app = None


def make_job():
    job = Path(tempfile.mkdtemp()) / "2026-10-02__20-00-00"
    job.mkdir(parents=True)
    (job / "config.json").write_text("{}")
    return job


def graded(job, name, reward, ex=None, cost=0.01):
    d = job / name
    d.mkdir()
    (d / "result.json").write_text(json.dumps({
        "task_name": "terminal-bench/" + name.split("__")[0],
        "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
        "exception_info": {"exception_type": ex} if ex else None, "agent_result": {"cost_usd": cost},
        "agent_execution": {"started_at": "2026-10-02T10:00:00Z", "finished_at": "2026-10-02T10:02:30Z"}}))


class JobFolderTest(unittest.TestCase):
    def test_every_state_a_trial_can_be_in(self):
        job = make_job()
        (job / "a__1").mkdir()                                     # container still starting
        (job / "b__1" / "agent").mkdir(parents=True)
        (job / "b__1" / "agent" / "purr.txt").write_text("  ◆ run ls\n")
        (job / "c__1" / "verifier").mkdir(parents=True)
        (job / "c__1" / "verifier" / "test-stdout.txt").write_text("...")
        graded(job, "d__1", 1.0)
        graded(job, "e__1", 0.0)
        graded(job, "f__1", None, ex="NonZeroAgentExitCodeError")
        graded(job, "g__1", 0.0, ex="AgentTimeoutError")
        graded(job, "h__1", 1.0, ex="AgentTimeoutError")           # out of time, but the files were right
        states = {t["task"]: t["state"] for t in realbench.scan(job)}
        self.assertEqual(states, {"a": "setup", "b": "working", "c": "grading", "d": "passed", "e": "failed",
                                  "f": "error", "g": "timeout", "h": "passed"})
        d = next(t for t in realbench.scan(job) if t["task"] == "d")
        self.assertEqual((d["seconds"], d["cost"]), (150.0, 0.01))

    def test_score_counts_only_what_is_graded(self):
        job = make_job()
        self.assertEqual(realbench.score(realbench.scan(job)), (None, None))
        graded(job, "a__1", 1.0)
        graded(job, "a__2", 0.0)
        graded(job, "b__1", 1.0)
        graded(job, "c__1", None, ex="RuntimeError")  # an error is a fail, like on the leaderboards
        (job / "d__1").mkdir()
        pct, se = realbench.score(realbench.scan(job))
        self.assertEqual(pct, 50.0)  # (0.5 + 1 + 0) / 3
        self.assertGreater(se, 0)

    def test_the_last_tool_in_purrs_log(self):
        lines = ["\x1b[38;2;200;162;240m  ◆ terminal send vm root\x1b[0m", "thinking about it", "  ◆ run make"]
        self.assertEqual(realbench.last_tool(lines), "run")
        self.assertIsNone(realbench.last_tool(["no tools here"]))

    def test_finds_its_own_job_folder(self):
        jobs = Path(tempfile.mkdtemp())
        old = jobs / "old"
        old.mkdir()
        (old / "config.json").write_text("{}")
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        run = realbench.Run("terminal-bench", "quick")
        with mock.patch.object(realbench, "JOBS", jobs):
            self.assertIsNone(run.find_job())
            new = jobs / "new"
            new.mkdir()
            (new / "config.json").write_text("{}")
            self.assertEqual(run.find_job(), new)


class PlanTest(unittest.TestCase):
    def test_commands_go_through_fair_sh(self):
        self.assertEqual(realbench.command("terminal-bench", "quick")[1:], ["--quick"])
        self.assertEqual(realbench.command("deepswe", "one")[1:], ["--deepswe", "--one"])
        self.assertEqual(realbench.command("deepswe", "full", edge_cases=True)[1:],
                         ["--deepswe", "--ak", "edge_cases=true"])
        self.assertTrue(realbench.command("deepswe", "full")[0].endswith("tbench/fair.sh"))

    def test_sizes(self):
        self.assertEqual(realbench.trials_in("terminal-bench", "quick"), 20)
        self.assertEqual(realbench.trials_in("terminal-bench", "full"), 267)
        self.assertEqual(realbench.trials_in("deepswe", "one"), 113)

    def test_references_are_the_same_models_scores(self):
        refs = realbench.references("deepswe")
        self.assertEqual(refs[0], ("mini-SWE-agent", 74.2))
        self.assertEqual(len(refs), 8)
        self.assertEqual(len(realbench.references("terminal-bench")), 9)

    def test_purr_bench_takes_real(self):
        args = bench.parse(["--real", "deepswe", "--size", "one", "--jobs", "8"])
        self.assertEqual((args.real, args.size, args.jobs), ("deepswe", "one", 8))
        self.assertEqual(bench.parse(["--real"]).real, "pick")


class VerdictTest(unittest.TestCase):
    def test_against_the_published_harnesses(self):
        mood, head, _ = realbench.verdict("deepswe", "one", 80.0, 113, 113)
        self.assertEqual(mood, "celebrating")
        self.assertIn("every harness", head)
        mood, head, _ = realbench.verdict("deepswe", "one", 70.0, 113, 113)
        self.assertEqual(mood, "proud")
        self.assertIn("5 of 8", head)
        mood, head, _ = realbench.verdict("deepswe", "one", 66.0, 113, 113)
        self.assertEqual(mood, "happy")
        mood, head, _ = realbench.verdict("deepswe", "one", 62.0, 113, 113)
        self.assertEqual(mood, "purring")  # within 5 of the last one
        mood, head, _ = realbench.verdict("deepswe", "one", 40.0, 113, 113)
        self.assertEqual(mood, "sad")

    def test_quick_runs_compare_with_the_last_one(self):
        with mock.patch.object(realbench, "previous", return_value=[]):
            self.assertEqual(realbench.verdict("terminal-bench", "quick", 70.0, 20, 20)[0], "happy")
        with mock.patch.object(realbench, "previous", return_value=[("0.4.0", "2026-10-02", 65.0)]):
            self.assertEqual(realbench.verdict("terminal-bench", "quick", 75.0, 20, 20)[0], "celebrating")
            self.assertEqual(realbench.verdict("terminal-bench", "quick", 67.0, 20, 20)[0], "purring")
            self.assertEqual(realbench.verdict("terminal-bench", "quick", 50.0, 20, 20)[0], "sad")

    def test_stopped_or_nothing_graded(self):
        self.assertEqual(realbench.verdict("terminal-bench", "quick", None, 0, 20)[0], "oops")
        self.assertEqual(realbench.verdict("terminal-bench", "quick", 80.0, 8, 20, stopped=True)[0], "startled")


class ImageCleanerTest(unittest.TestCase):
    """tbench/clean_images.py: a graded task's prebuilt image goes, one still in use stays for later."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "clean_images", Path(__file__).resolve().parent.parent / "tbench" / "clean_images.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        root = Path(tempfile.mkdtemp())
        self.mod.JOBS, self.mod.TASKS = root / "jobs", root / "tasks"
        for task, image in (("datacurve/a", "img-a"), ("datacurve/b", "img-b"), ("terminal-bench/c", None)):
            d = self.mod.TASKS / task / "sha1"
            d.mkdir(parents=True)
            (d / "task.toml").write_text(f'[environment]\ndocker_image = "{image}"\n' if image else "[environment]\n")
        job = self.mod.JOBS / "2026-10-02__20-00-00"
        for trial, task in (("a__1", "datacurve/a"), ("c__1", "terminal-bench/c")):
            (job / trial).mkdir(parents=True)
            (job / trial / "result.json").write_text(json.dumps({"task_name": task}))
        (job / "b__1").mkdir()  # still running: no result yet

    def test_only_graded_tasks_with_a_prebuilt_image(self):
        self.assertEqual(self.mod.graded_images(time.time() - 60), {"img-a"})
        self.assertEqual(self.mod.graded_images(time.time() + 3600), set())  # an older job isn't touched

    def test_an_image_in_use_is_tried_again_later(self):
        calls = []

        def docker(cmd, **kw):
            calls.append(cmd[-1])
            in_use = len(calls) == 1
            return mock.Mock(returncode=1 if in_use else 0, stderr="image is being used" if in_use else "")
        with mock.patch.object(self.mod.subprocess, "run", docker), mock.patch.object(self.mod.time, "sleep"), \
                mock.patch.object(self.mod, "alive", side_effect=[True, False]):
            self.mod.main(["1", str(time.time() - 60)])
        self.assertEqual(calls, ["img-a", "img-a"])  # refused while in use, removed on the next round


@unittest.skipIf(realbench_app is None, "needs textual (uv sync)")
class WindowTest(unittest.IsolatedAsyncioTestCase):
    def test_the_window_can_still_start(self):
        # it once kept its run in self.run, which hid App.run: purr bench --real crashed at once
        app = realbench_app.RealBenchApp({"models": {}, "providers": {}})
        self.assertTrue(callable(app.run))

    async def test_from_setup_to_mochis_verdict(self):
        job = make_job()
        quick = realbench.PUBLISH.QUICK

        class FakeProc:
            returncode, pid = None, 0

            def poll(self):
                return self.returncode

        class FakeRun(realbench.Run):
            def start(self):
                self.proc, self.job = FakeProc(), job

            def read_output(self):
                pass

        app = realbench_app.RealBenchApp({"models": {}, "providers": {}})
        with mock.patch.object(realbench_app.realbench, "Run", FakeRun), \
                mock.patch.object(realbench, "previous", return_value=[]):
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                self.assertIn("earlier quick runs", str(app.query_one("#preview").render()))
                app.choice["size"] = "one"
                app.draw_preview()
                self.assertIn("to beat", str(app.query_one("#preview").render()))
                app.choice["size"] = "quick"
                app.start()
                (job / f"{quick[0]}__w" / "agent").mkdir(parents=True)
                (job / f"{quick[0]}__w" / "agent" / "purr.txt").write_text("  ◆ edit_file app.py\n")
                await pilot.pause(1.3)
                self.assertIn(f"{quick[0]}__w", app.rows)
                self.assertEqual(app.selected, f"{quick[0]}__w")  # follows what's working
                self.assertEqual(app.cat_mood, "building")         # Mochi mirrors purr's edit
                for i, task in enumerate(quick):
                    (job / f"{task}__w").exists() and __import__("shutil").rmtree(job / f"{task}__w")
                    graded(job, f"{task}__{i}", 1.0 if i % 4 else 0.0)
                app.job_run.proc.returncode = 0
                await pilot.pause(1.3)
                self.assertIsNotNone(app.final)
                self.assertEqual(app.final[0], "happy")  # the first quick run: the baseline
                self.assertTrue(app.query_one("#run").has_class("results"))
                self.assertIn("75.0% on the quick set", app.cat_label)


if __name__ == "__main__":
    unittest.main()
