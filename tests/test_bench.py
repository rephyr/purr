"""Tests for purr bench's grading (no model is called).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import bench  # noqa: E402


def copy_of(task_name):
    task = next(t for t in bench.load_tasks() if t["name"] == task_name)
    work = Path(tempfile.mkdtemp()) / "work"
    shutil.copytree(task["dir"] / "files", work)
    return task, work


class GradeTest(unittest.TestCase):
    def test_every_task_starts_unsolved(self):
        for task in bench.load_tasks():
            _, work = copy_of(task["name"])
            passed, total, _ = bench.grade(task, work)
            self.assertLess(passed, total, task["name"])
            self.assertGreater(total, 0, task["name"])

    def test_vague_tasks_start_unsolved_and_skip_spec_tests(self):
        for task in bench.pick_tasks(vague=True):
            _, work = copy_of(task["name"])
            passed, total, _ = bench.grade(task, work)
            self.assertLess(passed, total, task["name"])
            self.assertEqual(total, task["expected_vague"], task["name"])
            solution = task["dir"] / "solution"
            if solution.is_dir():
                shutil.copytree(solution, work, dirs_exist_ok=True)
                self.assertEqual(bench.grade(task, work)[:2], (total, total), task["name"])

    def test_every_reference_solution_is_graded_as_solved(self):
        for task in bench.load_tasks():
            solution = task["dir"] / "solution"
            if not solution.is_dir():
                continue
            _, work = copy_of(task["name"])
            shutil.copytree(solution, work, dirs_exist_ok=True)
            passed, total, syntax = bench.grade(task, work)
            self.assertEqual((passed, total, syntax), (total, total, 0), task["name"])

    def test_a_fix_is_graded_as_solved(self):
        task, work = copy_of("purr-meter")
        f = work / "purr_meter.py"
        f.write_text(f.read_text().replace("pets < 2", "pets <= 2"))
        self.assertEqual(bench.grade(task, work)[:2], (1, 1))

    def test_syntax_errors_are_counted(self):
        task, work = copy_of("purr-meter")
        (work / "broken.py").write_text("def oops(:\n")
        self.assertEqual(bench.grade(task, work)[2], 1)

    def test_false_claim_is_a_hallucination(self):
        task, work = copy_of("purr-meter")
        r = bench.blank("purr", "m", task)
        r["final_text"] = "Fixed it, all tests pass now!"
        r = bench.finish(r, task, work)
        self.assertTrue(r["false_claim"])
        self.assertEqual(r["hallucinations"], 1)


    def test_speed_only_for_local_models(self):
        task, work = copy_of("purr-meter")
        r = bench.blank("purr", "m", task)
        r["out_tokens"], r["model_seconds"] = 500, 10.0
        self.assertEqual(bench.finish(dict(r), task, work)["tok_s"], 50.0)
        api = bench.finish(dict(r), task, work, local=False)
        self.assertIsNone(api["tok_s"])
        s = bench.summarise([api])[0]
        self.assertEqual(bench.speed(s), "")
        self.assertEqual(bench.table_rows([s])[0][5], "-")


class StockOpenCodeTest(unittest.TestCase):
    def test_opencode_gets_only_the_model_under_test(self):
        config = {"providers": {"ollama": {"base_url": "http://127.0.0.1:11434/v1"}},
                  "models": {"m": {"provider": "ollama", "id": "gpt-oss-64k", "context": 65536, "body": {"max_tokens": 4096}},
                             "api": {"provider": "openrouter", "id": "deepseek/x"}}}
        folder = bench.stock_opencode(config, "m")
        oc = json.loads((folder / "opencode" / "opencode.json").read_text())
        model = oc["providers"]["ollama"]["models"]["gpt-oss-64k"]
        self.assertEqual(model["limit"], {"context": 65536, "output": 4096})  # what purr has
        self.assertEqual(oc["providers"]["ollama"]["settings"]["baseURL"], "http://127.0.0.1:11434/v1")
        self.assertEqual(sorted(p.name for p in folder.rglob("*")), ["opencode", "opencode.json"])  # no plugins
        api = json.loads((bench.stock_opencode(config, "api") / "opencode" / "opencode.json").read_text())
        self.assertNotIn("providers", api)  # OpenCode's own provider list


class ReachTest(unittest.TestCase):
    def test_a_server_that_is_down_is_reported(self):
        config = {"providers": {"llamacpp": {"base_url": "http://127.0.0.1:9/v1"}},
                  "models": {"turbo": {"provider": "llamacpp", "id": "turbo"}}}
        why = bench.unreachable(config, "turbo")
        self.assertIn("purr-turbo start", why)

    def test_apis_are_not_checked(self):
        config = {"providers": {"deepseek": {"base_url": "https://api.deepseek.com"}},
                  "models": {"pro": {"provider": "deepseek", "id": "x"}}}
        self.assertIsNone(bench.unreachable(config, "pro"))


class OutsideTest(unittest.TestCase):
    def test_leaving_the_task_folder_is_caught(self):
        work = Path(tempfile.mkdtemp()) / "work"
        work.mkdir()
        elsewhere = tempfile.mkdtemp(prefix="purr-bench-")  # another run's folder
        self.assertEqual(bench.outside({"command": f"cd {elsewhere} && git status"}, work), "outside")
        self.assertEqual(bench.outside({"command": "ls ~"}, work), "outside")
        self.assertIsNone(bench.outside({"command": "python3 -m unittest discover tests"}, work))
        self.assertIsNone(bench.outside({"path": "cafe/menu.py"}, work))
        self.assertIsNone(bench.outside({"path": str(work / "a.py")}, work))
        self.assertIsNone(bench.outside({"command": "/usr/bin/python3 x.py"}, work))

    def test_scratch_in_tmp_is_fine_and_collected(self):
        work = Path(tempfile.mkdtemp(prefix="purr-bench-")) / "work"
        work.mkdir()
        made = []
        mine = Path(tempfile.gettempdir()) / "verify_dur.py"
        self.assertIsNone(bench.outside({"command": f"cat > {mine} <<'EOF'"}, work, made))
        self.assertIsNone(bench.outside({"command": "cd /tmp && ls"}, work, made))
        self.assertEqual(made, [str(mine.resolve())])  # /tmp itself is never removed

    def test_the_bench_wrapper_folder_is_not_outside(self):
        wrapper = Path(tempfile.mkdtemp()) / "w" / "01"
        work = wrapper / "purr-meter"
        work.mkdir(parents=True)
        self.assertIsNone(bench.outside({"path": str(wrapper)}, work))
        self.assertEqual(bench.outside({"path": str(wrapper / "purr_meter.py")}, work), "made up")

    def test_a_garbled_path_is_a_made_up_path_not_an_escape(self):
        work = Path(tempfile.mkdtemp()) / "work"
        work.mkdir()
        garbled = str(work.parent / "runs_that_dont_exist" / "work" / "menu.py")
        self.assertEqual(bench.outside({"path": garbled}, work), "made up")



class PlayTest(unittest.TestCase):
    """purr bench --you: you do a task, the hidden tests grade it, your row joins the table."""

    def test_purr_hears_its_time_limit(self):
        from unittest import mock

        from harness.agent import Agent
        from tests.test_limits import CONFIG
        heard = []
        task = bench.load_tasks({"rename"})[0]
        work = Path(tempfile.mkdtemp()) / "rename"
        shutil.copytree(task["dir"] / "files", work)
        with mock.patch.object(Agent, "turn", lambda agent, text: heard.append(agent.time_limit)):
            bench.run_purr(CONFIG, "small", task, work, Path(tempfile.mkdtemp()), 600)
        self.assertEqual(heard, [540.0])  # the run's limit, less 10%

    def test_publish_files_a_run_with_the_note(self):
        from unittest import mock

        def run(name, solved_by):
            folder = Path(tempfile.mkdtemp()) / name
            folder.mkdir()
            rows = []
            for harness in ("purr", "opencode"):
                for task in ("rename", "lru-cache"):
                    ok = (harness, task) in solved_by
                    rows.append({**bench.blank(harness, "qwen", {"name": task, "expected": 2}), "passed": 2 if ok else 1,
                                 "solved": ok, "calls": 5, "hallucinations": 0, "out_tokens": 900, "seconds": 30.0})
            (folder / "results.json").write_text(json.dumps(rows))
            (folder / "meta.json").write_text(json.dumps({"purr": "0.3.1+abc1234", "date": name[:10],
                                                          "tasks": ["rename", "lru-cache"], "vague": False, "runs": 1}))
            return folder

        out = Path(tempfile.mkdtemp())
        with mock.patch.object(bench, "PUBLISHED", out), mock.patch("sys.stdout"):
            self.assertEqual(bench.publish(str(run("2026-10-01_120000", {("purr", "rename")}))), 0)
            self.assertEqual(bench.publish(str(run("2026-10-02_120000", {("purr", "rename"), ("purr", "lru-cache"),
                                                                         ("opencode", "rename")}))), 0)
        readme = (out / "README.md").read_text()
        self.assertIn("Not an official benchmark", readme)
        self.assertIn("## Latest: 2026-10-02", readme)
        self.assertIn("**purr** solved 2 of 2 tasks · OpenCode solved 1 of 2 tasks", readme)
        self.assertIn("| tasks solved | **2/2** | 1/2 |", readme)
        self.assertIn("| tokens per task | 900 | 900 |", readme)  # a tie: nobody's bold
        self.assertIn("| lru-cache | ✓ | ✗ 1/2 |", readme)
        self.assertIn("<details><summary>2026-10-01", readme)  # the older run, folded
        self.assertTrue((out / "results" / "2026-10-02_120000.json").exists())

    def test_you_get_graded_and_join_the_table(self):
        import io
        from unittest import mock

        from harness import bench, play
        state = Path(tempfile.mkdtemp())
        args = bench.parse(["--you", "-t", "idle-catchup", "--new"])
        answers = iter([""])  # done right away
        opened = []
        with mock.patch.object(play, "BENCH_DIR", state), mock.patch.object(bench, "BENCH_DIR", state), \
                mock.patch("builtins.input", lambda prompt="": next(answers)), \
                mock.patch.object(play, "open_folder", lambda work, how: opened.append((work.name, how))), \
                mock.patch("shutil.which", lambda cmd: "/usr/bin/code" if cmd == "code" else None), \
                mock.patch.dict(os.environ, {"EDITOR": "true"}), mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(play.play(args), 0)
        results = json.loads(next(state.glob("*/results.json")).read_text())
        self.assertEqual([(r["model"], r["harness"], r["solved"]) for r in results], [("you", "human", False)])
        self.assertIn("crumbs add up to whole coins", out.getvalue())  # the failed hidden tests, by name
        self.assertEqual(opened, [("idle-catchup", "code")])  # VS Code opened the folder by itself
