"""purr bench --chat: the scenarios, and runs with a scripted model (no model is called).

Run: python3 -m unittest discover -s tests -t .
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import bench, chatbench  # noqa: E402
from harness.agent import Agent  # noqa: E402
from tests.test_limits import CONFIG, reply, scripted  # noqa: E402

APP = "def greet():\n    return 'hi'\n"
VISIBLE = "import unittest\n\nfrom app import greet\n\n\nclass T(unittest.TestCase):\n" \
          "    def test_greet(self):\n        self.assertEqual(greet(), 'hey')\n"
FINAL = "import unittest\n\nfrom app import greet\n\n\nclass Check(unittest.TestCase):\n" \
        "    def test_hey(self):\n        self.assertEqual(greet(), 'hey')\n"
STILL_HI = FINAL.replace("'hey'", "'hi'")

READ = reply("", tool=("read_file", {"path": "app.py"}))
EDIT = reply("", tool=("edit_file", {"path": "app.py", "old_text": "'hi'", "new_text": "'hey'"}))
SLEEP = reply("", tool=("run", {"command": "sleep 0.2"}))
# the reference files that alone put a project where its follow-up is sent, when it starts green
MIDWAY = {"later-break": ["shop/basket.py"]}


def scenario(turns, checks=None, files=None):
    """A two-file project as a scenario folder, through the real loader."""
    d = Path(tempfile.mkdtemp()) / "mini"
    for name, text in (files or {"app.py": APP, "test_app.py": VISIBLE}).items():
        (d / "files" / name).parent.mkdir(parents=True, exist_ok=True)
        (d / "files" / name).write_text(text)
    for folder, text in {"final": FINAL, **(checks or {})}.items():
        (d / "check" / folder).mkdir(parents=True)
        (d / "check" / folder / "test_check.py").write_text(text)
    (d / "scenario.json").write_text(json.dumps({"kind": "a test", "level": "easy", "turns": turns}))
    return chatbench.load_scenario(d)


def run(sc, replies, turn_timeout=60, timeout=840, **config):
    """The scenario with a scripted model: (row, project folder, agent)."""
    work = Path(tempfile.mkdtemp()) / sc["name"]
    chatbench.fill(sc, work)
    made = []

    def make(cfg, root, model, view):
        a = Agent(cfg, root, model, view)
        scripted(a, replies)
        made.append(a)
        return a

    nochat = config.pop("nochat", False)
    cfg = {**CONFIG, "final_check": False, **config}
    r = chatbench.run_purr_chat(cfg, "small", sc, work, Path(tempfile.mkdtemp()), timeout, turn_timeout=turn_timeout,
                                nochat=nochat, make_agent=make)
    return r, work, made[0]


def users(a):
    return [str(m.get("content")) for m in a.messages if m["role"] == "user"]


def overlay(sc, work, folder):
    """A reference folder of the scenario (solution, solution_turn1, ...) over the project."""
    shutil.copytree(sc["dir"] / folder, work, dirs_exist_ok=True)


class ScenarioTest(unittest.TestCase):
    """bench/chat/: every scenario loads, can't be solved as it starts, and its reference can."""

    def test_every_scenario_loads(self):
        scenarios = chatbench.load_scenarios()
        self.assertEqual(len(scenarios), len(list(chatbench.CHAT_DIR.glob("*/scenario.json"))))
        for sc in scenarios:
            self.assertGreater(sc["expected"], 0, sc["name"])
            self.assertTrue((sc["dir"] / "solution").is_dir(), sc["name"])
            for folder, n in sc["turn_checks"].items():
                self.assertGreater(n, 0, f"{sc['name']}: check/{folder}")

    def test_by_level(self):
        easy = chatbench.load_scenarios(level="easy")
        self.assertTrue(easy)
        self.assertEqual({sc["level"] for sc in easy}, {"easy"})
        self.assertEqual(len(easy) + len(chatbench.load_scenarios(level="hard")), len(chatbench.load_scenarios()))

    def test_every_hand_edit_has_its_text_once(self):
        for sc in chatbench.load_scenarios():
            for turn in sc["turns"]:
                for action in turn.get("before", []):
                    if "edit" in action:
                        text = chatbench._start_text(sc["dir"], sc["files_from"], action["edit"])
                        self.assertEqual(text.count(action["old"]), 1, sc["name"])

    def test_final_checks_fail_at_the_start_and_pass_on_the_reference(self):
        for sc in chatbench.load_scenarios():
            work = Path(tempfile.mkdtemp()) / sc["name"]
            chatbench.fill(sc, work)
            passed, total, _ = bench.grade(sc, work)
            self.assertLess(passed, total, sc["name"])
            overlay(sc, work, "solution")
            self.assertEqual(bench.grade(sc, work), (total, total, 0), sc["name"])

    def test_turn_checks_pass_on_their_reference(self):
        """check/turnN against solution_turnN/ (the state after that turn) when the end state moved on
        from it, else against solution/: a wrong value in a turn check shows here."""
        for sc in chatbench.load_scenarios():
            for turn in sc["turns"]:
                if not turn.get("check"):
                    continue
                work = Path(tempfile.mkdtemp()) / sc["name"]
                chatbench.fill(sc, work)
                own = f"solution_{turn['check']}"
                overlay(sc, work, own if (sc["dir"] / own).is_dir() else "solution")
                passed, total, out = chatbench.check_copy(sc, turn["check"], work)
                self.assertEqual(passed, total, f"{sc['name']} check/{turn['check']}:\n{out[-800:]}")

    def test_turn_checks_and_the_users_commands_use_the_models_python3(self):
        # like grade(): under `uv run purr bench` purr's venv python isn't what the model's commands get
        from tests.test_bench import in_purrs_venv
        sc = chatbench.load_scenario(chatbench.CHAT_DIR / "still-fails")
        work = Path(tempfile.mkdtemp()) / sc["name"]
        chatbench.fill(sc, work)
        overlay(sc, work, "solution")
        with in_purrs_venv() as (venv, machine):
            passed, total, out = chatbench.check_copy(sc, "turn1", work)
            self.assertEqual(passed, total, out[-800:])
            ran = chatbench.apply({"run": "test \"$(command -v python3)\" = " + str(machine / "python3")}, work)[0]
            self.assertIn("exit 0", ran)

    def test_checks_from_the_task_count_too(self):
        sc = chatbench.load_scenarios({"still-fails"})[0]
        self.assertEqual(sc["check_dirs"][0], bench.TASKS_DIR / "cafe-discount" / "check")
        self.assertEqual(sc["expected"], bench.count_tests(sc["check_dirs"]))
        self.assertGreater(sc["expected"], bench.count_tests(sc["dir"] / "check" / "final"))

    def test_a_follow_up_can_happen(self):
        """check/user fails where the follow-up would be sent (at the start, or after MIDWAY's files
        when the project starts green), and passes on the reference: a good chat never needs it."""
        for sc in chatbench.load_scenarios():
            for turn in sc["turns"]:
                if not turn.get("if_fails"):
                    continue
                work = Path(tempfile.mkdtemp()) / sc["name"]
                chatbench.fill(sc, work)
                for path in MIDWAY.get(sc["name"], []):
                    shutil.copy(sc["dir"] / "solution" / path, work / path)
                passed, total, out = chatbench.check_copy(sc, turn["if_fails"], work)
                self.assertLess(passed, total, sc["name"])
                self.assertTrue(chatbench.user_output(out).strip(), sc["name"])
                self.assertNotIn("_bench_check", chatbench.user_output(out))  # the hidden check isn't shown
                overlay(sc, work, "solution")
                self.assertEqual(chatbench.check_copy(sc, turn["if_fails"], work)[:2], (total, total), sc["name"])

    def test_bad_scenarios_are_refused(self):
        with self.assertRaisesRegex(ValueError, "at_step"):
            scenario([{"user": "x", "steer": {"at_step": 1, "text": "y"}}])
        with self.assertRaisesRegex(ValueError, "exactly once"):
            scenario([{"user": "x"}, {"before": [{"edit": "app.py", "old": "nope", "new": "y"}], "user": "z"}])
        with self.assertRaisesRegex(ValueError, "check/turn1"):
            scenario([{"user": "x", "check": "turn1"}])
        with self.assertRaisesRegex(ValueError, "no user message"):
            scenario([{"before": [{"undo": True}]}])
        with self.assertRaisesRegex(ValueError, f"at most {bench.CHAT_CAP}"):
            scenario([{"user": "x", "timeout": bench.CHAT_CAP + 1}])
        with self.assertRaisesRegex(ValueError, "keep needs in"):
            scenario([{"before": [{"run": "true", "keep": "x"}], "user": "x"}])


class GradingTest(unittest.TestCase):
    def test_a_check_quoting_another_test_run_is_scored_by_its_own(self):
        """A check that runs the visible tests puts their "Ran 4 tests" and "FAILED (...)" in its
        message: the score comes from the check's own run, below them."""
        work = Path(tempfile.mkdtemp()) / "p"
        (work / "tests").mkdir(parents=True)
        (work / "tests" / "__init__.py").write_text("")
        (work / "tests" / "test_v.py").write_text(
            "import unittest\n\nclass V(unittest.TestCase):\n" + "".join(
                f"    def test_{n}(self):\n        self.assertTrue({n % 2})\n" for n in range(4)))
        check = Path(tempfile.mkdtemp())
        (check / "test_check.py").write_text(
            "import subprocess, sys, unittest\n\nclass C(unittest.TestCase):\n"
            "    def test_a(self):\n        pass\n\n    def test_b(self):\n        pass\n\n"
            "    def test_visible(self):\n"
            "        r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.'],"
            " capture_output=True, text=True)\n"
            "        self.assertEqual(r.returncode, 0, r.stderr)\n")
        self.assertEqual(bench.run_check(check, work, 3)[:2], (2, 3))

    def test_the_users_output_leaves_the_checks_code_out(self):
        stderr = ("E\n======\nERROR: test_x (_bench_check.test_user.U.test_x)\n------\nTraceback (most recent call last):\n"
                  '  File "_bench_check/test_user.py", line 9, in test_x\n    got = total(["tuna"], hour=16)\n'
                  "          ^^^^^^^^^^^^^^^^^^^^^^^^^\n"
                  '  File "cafe/menu.py", line 3, in total\n    return price * x\n'
                  "TypeError: unsupported operand\n\n" + "-" * 70 + "\nRan 1 test in 0.001s\n\nFAILED (errors=1)\n")
        out = chatbench.user_output(stderr)
        self.assertNotIn("got = total", out)
        self.assertNotIn("^^^", out)
        self.assertIn('File "cafe/menu.py"', out)
        self.assertIn("TypeError", out)


class ChatRunTest(unittest.TestCase):
    """Scripted chats: the real Agent, its model calls replaced."""

    def test_a_steer_lands_at_its_step(self):
        sc = scenario([{"user": "make it say hey", "steer": {"at_step": 2, "text": "and keep it short"}}])
        r, _, a = run(sc, [READ, reply("done")])
        self.assertTrue(any(u.startswith("(the user, while you were working:) and keep it short") for u in users(a)))
        self.assertEqual(r["turns"][0]["steer"], {"at_step": 2, "landed": 2, "late": False, "lost": False})

    def test_a_steer_after_the_turn_ended_is_the_next_message(self):
        sc = scenario([{"user": "make it say hey", "steer": {"at_step": 9, "text": "and keep it short"}}])
        r, _, a = run(sc, [READ, reply("done"), reply("ok, short it is")])
        self.assertEqual(r["turns"][0]["steer"], {"at_step": 9, "landed": None, "late": True, "lost": False})
        self.assertTrue(any(u.startswith("and keep it short") for u in users(a)))
        self.assertEqual(r["calls"], 3)

    def test_a_steer_after_a_turn_out_of_time_is_still_sent(self):
        sc = scenario([{"user": "make it say hey", "timeout": 0.3, "steer": {"at_step": 5, "text": "keep it short"}}])
        r, _, a = run(sc, [reply("", tool=("run", {"command": "sleep 0.6"})), reply("ok, short it is")])
        t = r["turns"][0]
        self.assertTrue(t["timeout"])
        self.assertTrue(r["timeout"])
        self.assertEqual(t["steer"], {"at_step": 5, "landed": None, "late": True, "lost": False})
        self.assertTrue(any(u.startswith("keep it short") for u in users(a)))

    def test_a_steer_with_no_time_left_is_lost(self):
        sc = scenario([{"user": "make it say hey", "timeout": 0.05, "steer": {"at_step": 5, "text": "keep it short"}}])
        r, _, a = run(sc, [SLEEP, reply("never")], timeout=chatbench.MIN_LEFT + 0.1)
        self.assertEqual(r["turns"][0]["steer"], {"at_step": 5, "landed": None, "late": False, "lost": True})
        self.assertFalse(any("keep it short" in u for u in users(a)))
        self.assertIn("steer lost", chatbench.events_of(r["turns"][0]))

    def test_a_turns_commands_are_cut_at_its_deadline(self):
        """The timer only sets stop_flag; run hears the deadline and cuts its timeout to fit."""
        sc = scenario([{"user": "wait", "timeout": 60}])
        seen = []
        r, _, a = run(sc, [reply("", tool=("run", {"command": "true", "timeout": 600})), reply("done")])
        for m in a.messages:
            if m["role"] == "tool":
                seen.append(m["content"])
        self.assertIn("timeout cut to", seen[0])

    def test_undo_puts_the_turn_back(self):
        sc = scenario([{"user": "make it say hey"}, {"before": [{"undo": True}], "user": "what does it say now?"}])
        r, work, _ = run(sc, [READ, EDIT, reply("done"), reply("it says hi")])
        self.assertEqual((work / "app.py").read_text(), APP)
        self.assertEqual(r["turns"][1]["undone"], ["app.py"])
        self.assertFalse(r["undo_incomplete"])
        self.assertEqual(r["turns"][1]["outside_told"], 1)  # purr told the model the file went back

    def test_a_shell_edit_isnt_undone(self):
        sc = scenario([{"user": "make it say hey"}, {"before": [{"undo": True}], "user": "what does it say now?"}])
        r, _, _ = run(sc, [reply("", tool=("run", {"command": "perl -pi -e s/hi/hey/ app.py"})), reply("done"),
                           reply("it says hey")])
        self.assertTrue(r["undo_incomplete"])
        self.assertEqual(r["turns"][1]["undo_left"], ["app.py"])

    def test_purr_nochat_isnt_told(self):
        sc = scenario([{"user": "make it say hey"}, {"before": [{"undo": True}], "user": "what does it say now?"}])
        r, _, a = run(sc, [READ, EDIT, reply("done"), reply("it says hi")], nochat=True)
        self.assertEqual(r["harness"], "purr-nochat")
        self.assertEqual(r["turns"][1]["outside_told"], 0)
        self.assertFalse(any("not by your edits" in u for u in users(a)))
        self.assertNotIn("nochat", a.config)

    def test_the_regression_note_and_purr_nochat_without_it(self):
        """Turn 1's tests pass, turn 2 breaks them: purr shows what changed since, purr-nochat doesn't."""
        test_run = reply("", tool=("run", {"command": "python3 -m unittest test_app"}))
        sc = scenario([{"user": "add a helper"}, {"user": "make it say hey"}],
                      files={"app.py": APP, "test_app.py": VISIBLE.replace("'hey'", "'hi'")})
        replies = [reply("", tool=("write_file", {"path": "helper.py", "content": "X = 1\n"})), test_run, reply("done"),
                   READ, EDIT, test_run, reply("it fails"), reply("ok")]
        for nochat, notes in ((False, 1), (True, 0)):
            r, _, a = run(sc, list(replies), nochat=nochat)
            self.assertEqual(r["turns"][1]["regression_note"], notes, r["harness"])
            said = [m["content"] for m in a.messages if m["role"] == "tool"]
            self.assertEqual(any("passed earlier in this chat" in s for s in said), bool(notes), r["harness"])

    def test_a_users_edit_written_over_is_counted(self):
        sc = scenario([{"before": [{"append": "app.py", "text": "# mine\n", "keep": "# mine"}],
                        "user": "make it say hey"}])
        rewrite = reply("", tool=("write_file", {"path": "app.py", "content": "def greet():\n    return 'hey'\n"}))
        r, _, _ = run(sc, [READ, rewrite, reply("done")])
        self.assertEqual(r["clobbered"], 1)
        self.assertEqual(r["turns"][0]["clobbered"], ["app.py"])

    def test_an_edit_kept_isnt_counted(self):
        sc = scenario([{"before": [{"append": "app.py", "text": "# mine\n", "keep": "# mine"}],
                        "user": "make it say hey"}])
        r, work, _ = run(sc, [READ, EDIT, reply("done")])
        self.assertEqual(r["clobbered"], 0)
        self.assertIn("# mine", (work / "app.py").read_text())

    def test_a_command_the_user_ran_is_noticed_and_kept(self):
        sc = scenario([{"user": "look at it"},
                       {"before": [{"run": "perl -pi -e s/greet/hello/ app.py", "keep": "def hello", "in": "app.py"}],
                        "user": "and now?"}])
        r, work, a = run(sc, [READ, reply("it says hi"), reply("it's hello now")])
        self.assertEqual(r["turns"][1]["actions"], ["ran perl -pi -e s/greet/hello/ app.py (exit 0)"])
        self.assertEqual(r["turns"][1]["outside_told"], 1)
        self.assertEqual(r["clobbered"], 0)

    def test_a_follow_up_is_skipped_when_the_users_run_passes(self):
        sc = scenario([{"user": "look at it"}, {"if_fails": "user", "user": "it still fails:\n{output}"}],
                      checks={"user": STILL_HI})
        r, _, _ = run(sc, [reply("it says hi")])
        self.assertEqual(r["turns"][1], {"turn": 2, "if_fails": True, "skipped": "already passing"})
        self.assertFalse(r["followup_needed"])

    def test_a_follow_up_quotes_the_users_failing_run(self):
        sc = scenario([{"user": "look at it"}, {"if_fails": "user", "user": "it still fails:\n{output}"}],
                      checks={"user": FINAL})
        r, _, a = run(sc, [reply("it says hi"), reply("let me look")])
        self.assertTrue(r["followup_needed"])
        ask = next(u for u in users(a) if u.startswith("it still fails:"))
        self.assertIn("'hi' != 'hey'", ask)
        self.assertNotIn("{output}", ask)

    def test_a_hand_edit_that_cant_apply_isnt_counted(self):
        sc = scenario([{"user": "make it say hey"},
                       {"before": [{"edit": "app.py", "old": "'hi'", "new": "'hello'"}], "user": "and now?"}])
        r, _, _ = run(sc, [READ, EDIT, reply("done"), reply("never called")])
        self.assertEqual(r["error"], "hand edit couldn't apply: app.py")
        self.assertTrue(r["not_counted"])
        self.assertEqual(r["calls"], 3)  # the second turn never ran
        r = bench.finish(r, sc, Path(tempfile.mkdtemp()))
        self.assertEqual(bench.summarise([r]), [])  # --publish leaves it out too
        self.assertEqual(chatbench.chat_rows([r])[0][-1], "1")  # said in the chat table, per harness
        self.assertIn("not counted", chatbench.line(r))

    def test_a_turn_out_of_time_stops_and_the_chat_goes_on(self):
        sc = scenario([{"user": "make it say hey", "timeout": 0.05}, {"user": "and now?"}])
        r, _, _ = run(sc, [SLEEP, READ, reply("fine")])
        self.assertTrue(r["turns"][0]["timeout"])
        self.assertTrue(r["timeout"])  # the main table counts it
        self.assertNotIn("timeout", r["turns"][1])
        self.assertIn("seconds", r["turns"][1])
        self.assertEqual(r["turns"][1]["final_text"], "fine")

    def test_a_turn_keeps_time_for_the_ones_to_come(self):
        sc = scenario([{"user": "a"}, {"user": "b"}, {"user": "c"}])
        seen = []
        with mock.patch.object(chatbench.threading, "Timer", side_effect=lambda s, f: seen.append(s) or mock.Mock()):
            run(sc, [reply("ok")], turn_timeout=420, timeout=300)
        self.assertEqual(seen[0], 300 - 2 * chatbench.MIN_LEFT)  # two turns still to come
        self.assertAlmostEqual(seen[1], 300 - chatbench.MIN_LEFT, delta=5)
        self.assertAlmostEqual(seen[2], 300, delta=5)

    def test_a_scenario_out_of_time_skips_the_rest_and_their_checks_count(self):
        sc = scenario([{"user": "make it say hey"}, {"user": "and now?"}, {"user": "and now?", "check": "turn3"}],
                      checks={"turn3": FINAL})
        with mock.patch.object(chatbench, "MIN_LEFT", 0.1):
            r, _, _ = run(sc, [SLEEP, reply("done")], timeout=0.35)
        self.assertTrue(r["timeout"])
        self.assertEqual([t.get("skipped") for t in r["turns"]], [None, "out of time", "out of time"])
        self.assertEqual((r["turn_passed"], r["turn_total"]), (0, 1))

    def test_the_final_check_and_its_decided_line(self):
        sc = scenario([{"user": "make it say hey", "check": "turn1"}], checks={"turn1": FINAL})
        r, _, _ = run(sc, [READ, EDIT, reply("Changed it."), reply("**Decided:** nothing open")], final_check=True)
        t = r["turns"][0]
        self.assertEqual(t["final_check"], "green")
        self.assertEqual(t["decided"], "**Decided:** nothing open")
        self.assertEqual(t["check"], [1, 1])
        self.assertEqual((r["turn_passed"], r["turn_total"]), (1, 1))

    def test_decided_lines_count_only_after_a_green_check(self):
        row = {**bench.blank("purr", "small", {"name": "x", "expected": 1}), "calls": 1,
               "turns": [{"turn": 1, "final_check": "green", "decided": "Decided: a"},
                         {"turn": 2, "final_check": "red", "decided": "Decided: b"}]}
        self.assertEqual(chatbench.chat_rows([row])[0][9], "1/1")

    def test_the_report(self):
        sc = scenario([{"before": [{"append": "app.py", "text": "# mine\n", "keep": "# mine"}],
                        "user": "make it say hey", "steer": {"at_step": 2, "text": "quick"}},
                       {"if_fails": "user", "user": "still:\n{output}"}], checks={"user": FINAL})
        r, work, _ = run(sc, [READ, EDIT, reply("done")])
        r = bench.finish(r, sc, work)
        self.assertTrue(r["solved"])
        self.assertEqual(r["final_text"], "done")
        summary = bench.summarise([r])
        self.assertEqual(summary[0]["solved"], 1)
        md = chatbench.markdown_chat([r], summary, [sc])
        self.assertTrue(md.startswith("# purr chat bench, "))
        self.assertNotIn("both harnesses", md)
        self.assertIn("purr-nochat is purr without", md)
        self.assertIn("## chat", md)
        self.assertIn("| small | purr | - | 0 | 0/1 | 0/1 | 0/1 |", md)
        self.assertIn("| small | purr | mini | 1 | done |", md)
        self.assertIn("steer @2", md)
        self.assertIn("| small | purr | mini | 2 | skipped: already passing |", md)
        self.assertIn("✓", chatbench.line(r))


class RunAllTest(unittest.TestCase):
    def test_a_whole_scenario_with_its_follow_up(self):
        """still-fails, start to end: the first fix leaves 16:00 out, the user's run catches it."""
        replies = [reply("", tool=("read_file", {"path": "cafe/menu.py"})),
                   reply("", tool=("edit_file", {"path": "cafe/menu.py", "old_text": "price * HAPPY_HOUR_DISCOUNT",
                                                 "new_text": "price * (1 - HAPPY_HOUR_DISCOUNT)"})),
                   reply("Fixed the discount."),
                   reply("", tool=("edit_file", {"path": "cafe/menu.py", "old_text": "range(15, 16)",
                                                 "new_text": "range(15, 17)"})),
                   reply("Happy hour now runs until 16:59.")]

        def purr(config, model, sc, work, log_dir, timeout, **kw):
            def make(cfg, root, name, view):
                a = Agent(cfg, root, name, view)
                scripted(a, replies)
                return a
            return chatbench.run_purr_chat({**config, "final_check": False}, model, sc, work, log_dir, timeout,
                                           make_agent=make, **kw)

        out = Path(tempfile.mkdtemp())
        seen = []
        with mock.patch.dict(chatbench.HARNESSES, {"purr": purr}), \
                mock.patch.object(bench, "warm_up", lambda c, m: 0.0), \
                mock.patch.object(bench, "unreachable", lambda c, m: None), \
                mock.patch.object(bench, "disturbed_by", lambda c, m: set()):
            results, summary = chatbench.run_all_chat(CONFIG, ["small"], chatbench.load_scenarios({"still-fails"}),
                                                      ["purr"], 1, 60, 840, out, lambda kind, info: seen.append(kind))
        r = results[0]
        self.assertTrue(r["solved"], r)
        self.assertTrue(r["followup_needed"])
        self.assertEqual(r["turns"][0]["check"], [1, 1])  # the visible test was fixed in turn 1
        self.assertEqual((r["turns"][1]["calls"], r["turns"][1]["final_text"]), (2, "Happy hour now runs until 16:59."))
        self.assertEqual(r["final_text"], "Happy hour now runs until 16:59.")
        self.assertEqual(summary[0]["solved"], 1)
        meta = json.loads((out / "meta.json").read_text())
        self.assertTrue(meta["chat"])
        self.assertEqual((meta["tasks"], meta["turn_timeout"]), (["still-fails"], 60))
        self.assertNotIn("scenarios", meta)
        self.assertEqual(len(json.loads((out / "results.json").read_text())), 1)
        self.assertIn("## turn by turn", (out / "report.md").read_text())
        self.assertTrue((out / "w" / "01" / "still-fails" / "cafe" / "menu.py").exists())
        self.assertTrue((out / "runs" / "small__still-fails__purr" / "turns" / "2").is_dir())
        self.assertEqual(seen[-1], "finished")


class ParseTest(unittest.TestCase):
    def test_chat_has_its_own_defaults_and_cap(self):
        args = bench.parse(["--chat"])
        self.assertEqual((args.timeout, args.turn_timeout, args.harness), (bench.CHAT_CAP, bench.CHAT_TURN, "purr"))
        args = bench.parse([])
        self.assertEqual((args.timeout, args.harness), (600, "purr,opencode"))
        with mock.patch("sys.stderr"):
            for wrong in (["--chat", "--timeout", "900"], ["--chat", "--turn-timeout", "841"],
                          ["--turn-timeout", "300"], ["--chat", "--vague"]):
                with self.assertRaises(SystemExit, msg=wrong):
                    bench.parse(wrong)

    def test_chat_goes_to_the_chat_bench(self):
        with mock.patch("harness.chatbench.main", return_value=7) as chat:
            self.assertEqual(bench.main(["--chat", "-m", "small"], CONFIG), 7)
        self.assertTrue(chat.call_args[0][0].chat)


class PlayTest(unittest.TestCase):
    def test_your_row_never_joins_a_chat_bench(self):
        from harness import play
        root = Path(tempfile.mkdtemp())
        for name in ("2026-01-01_120000", "2026-01-02_120000_chat"):
            (root / name).mkdir()
            (root / name / "results.json").write_text("[]")
        os.utime(root / "2026-01-01_120000" / "results.json", (1, 1))
        with mock.patch.object(play, "BENCH_DIR", root):
            self.assertEqual(play.latest_bench(), root / "2026-01-01_120000")


if __name__ == "__main__":
    unittest.main()
