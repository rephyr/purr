"""tbench/fair.sh and tbench/local.sh: what they hand to Harbor, for each way of calling them.

Run: python3 -m unittest discover tests
Nothing is benchmarked: harbor, docker, curl and the helpers' python3 are fakes on PATH, and
fair.sh's test request to OpenRouter is skipped (PURR_SKIP_PREFLIGHT=1).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKES = {
    "harbor": '#!/bin/sh\nfor a in "$@"; do echo "$a"; done > "$HARBOR_ARGS"\n',
    "docker": "#!/bin/sh\nexit 0\n",  # docker buildx version: there
    "curl": '#!/bin/sh\necho \'{"models":[{"name":"ornith-9b-128k:latest","model":"ornith-9b-128k:latest"}]}\'\n',
    "python3": "#!/bin/sh\nexit 0\n",  # the bridge and the image cleaner: not started
}


def run(script, *args, **env):
    """(exit code, Harbor's arguments or None, stderr)."""
    fake = Path(tempfile.mkdtemp())
    for name, body in FAKES.items():
        (fake / name).write_text(body)
        (fake / name).chmod(0o755)
    out = fake / "harbor-args"
    environ = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "HARBOR_ARGS": str(out),
               "PURR_ALLOW_DIRTY": "1", "PURR_SKIP_PREFLIGHT": "1", "OPENROUTER_API_KEY": "test-key",
               "PURR_JOB": "test-job", **env}
    for name in ("PURR_DATASET", "PURR_MODEL", "PURR_LOCAL_MODEL"):
        if name not in env:
            environ.pop(name, None)
    res = subprocess.run(["sh", str(ROOT / "tbench" / script), *args], capture_output=True, text=True,
                         env=environ, cwd=ROOT, timeout=60)
    return res.returncode, (out.read_text().split("\n")[:-1] if out.exists() else None), res.stderr


def after(argv, flag):
    return argv[argv.index(flag) + 1]


def picked(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == "-i"]


def dirty_repo():
    """GIT_DIR/GIT_WORK_TREE for a throwaway repo with an uncommitted change: what the scripts' git sees."""
    repo = Path(tempfile.mkdtemp())
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["init", "-q"], check=True)
    (repo / "f").write_text("a")
    subprocess.run(git + ["add", "f"], check=True)
    subprocess.run(git + ["commit", "-qm", "f"], check=True)
    (repo / "f").write_text("b")
    return {"GIT_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(repo)}


class CommonShTest(unittest.TestCase):
    def test_both_refuse_uncommitted_changes_unless_allowed(self):
        for script in ("fair.sh", "local.sh"):
            rc, argv, err = run(script, "--one", PURR_ALLOW_DIRTY="", **dirty_repo())
            self.assertNotEqual(rc, 0, script)
            self.assertIsNone(argv, script)
            self.assertIn("uncommitted changes", err)
            rc, argv, err = run(script, "--one", **dirty_repo())  # PURR_ALLOW_DIRTY=1: a warning only
            self.assertEqual(rc, 0, err)
            self.assertIn("uncommitted changes", err)

    def test_the_quick_set_and_hand_picked_tasks_get_the_org_once(self):
        for script, n in (("fair.sh", 22), ("local.sh", 2)):  # local.sh: tasks picked by hand replace the quick set
            rc, argv, err = run(script, "--quick", "-i", "terminal-bench/already", "-i", "bare")
            self.assertEqual(rc, 0, err)
            tasks = picked(argv)
            self.assertEqual(len(tasks), n, script)
            self.assertTrue(all(t.startswith("terminal-bench/") and t.count("/") == 1 for t in tasks), tasks)
            self.assertEqual(tasks[-2:], ["terminal-bench/already", "terminal-bench/bare"])


class FairShTest(unittest.TestCase):
    def test_quick_runs_the_quick_20_once(self):
        rc, argv, err = run("fair.sh", "--quick")
        self.assertEqual(rc, 0, err)
        self.assertEqual(after(argv, "-k"), "1")
        self.assertEqual(len(picked(argv)), 20)
        self.assertTrue(all(t.startswith("terminal-bench/") for t in picked(argv)))
        self.assertEqual(after(argv, "--job-name"), "test-job")

    def test_options_in_any_order(self):
        for args in (("--one", "--deepswe"), ("--deepswe", "--one")):
            rc, argv, err = run("fair.sh", *args)
            self.assertEqual(rc, 0, err)
            self.assertEqual(after(argv, "-d"), "datacurve/deep-swe-1-1")
            self.assertEqual(after(argv, "-k"), "1")
            self.assertNotIn("--deepswe", argv)

    def test_submit_is_five_tries_with_the_web_tool_off(self):
        rc, argv, err = run("fair.sh", "--submit")
        self.assertEqual(rc, 0, err)
        self.assertEqual(after(argv, "-d"), "terminal-bench/terminal-bench-2")
        self.assertEqual(after(argv, "-k"), "5")
        self.assertIn("web=false", argv)

    def test_impossible_combinations_stop(self):
        for args in (("--submit", "--one"), ("--quick", "--one"), ("--submit", "--deepswe")):
            rc, argv, err = run("fair.sh", *args)
            self.assertNotEqual(rc, 0, args)
            self.assertIsNone(argv, args)

    def test_tasks_picked_by_hand_get_the_datasets_prefix(self):
        rc, argv, err = run("fair.sh", "-i", "mteb-retrieve")
        self.assertEqual(rc, 0, err)
        self.assertEqual(picked(argv), ["terminal-bench/mteb-retrieve"])
        self.assertEqual(after(argv, "-k"), "3")

    def test_only_openrouter_models(self):
        rc, argv, err = run("fair.sh", "--quick", PURR_MODEL="ollama/ornith-9b-128k")
        self.assertNotEqual(rc, 0)
        self.assertIsNone(argv)


class LocalShTest(unittest.TestCase):
    def test_the_quick_20_unless_told_otherwise(self):
        for args in ((), ("-n", "2"), ("--ak", "edge_cases=true")):
            rc, argv, err = run("local.sh", *args)
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(picked(argv)), 20, args)

    def test_tasks_picked_by_hand_or_every_task(self):
        rc, argv, err = run("local.sh", "-i", "git-leak-recovery")
        self.assertEqual(rc, 0, err)
        self.assertEqual(picked(argv), ["terminal-bench/git-leak-recovery"])
        rc, argv, err = run("local.sh", "--one")
        self.assertEqual(rc, 0, err)
        self.assertEqual(picked(argv), [])

    def test_the_model_name_must_match_exactly(self):
        rc, argv, err = run("local.sh", PURR_LOCAL_MODEL="ornith-9b")  # only ornith-9b-128k exists
        self.assertNotEqual(rc, 0)
        self.assertIsNone(argv)
        rc, argv, err = run("local.sh", PURR_LOCAL_MODEL="ornith-9b-128k")
        self.assertEqual(rc, 0, err)
        self.assertEqual(after(argv, "-m"), "ollama/ornith-9b-128k")


if __name__ == "__main__":
    unittest.main()
