"""Plan mode: a big model writes tickets, the user approves them, a small one does them.
The model calls are faked, so no model and no network are needed.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import agent as agent_module  # noqa: E402
from harness.agent import (LOG_DIR, Agent, parse_tickets, write_tickets)
from tests.test_limits import FakeView, reply  # noqa: E402

TICKETS = """Here is the plan.

## Add a wishlist model
Files: shop/wishlist.py
Change: add an add_item function
Done when: python3 -m unittest passes

## Wire it into the basket
Files: shop/basket.py, tests/test_shop.py
Change: call the wishlist from the basket
Done when: the basket test passes
"""

CONFIG = {
    "providers": {"ollama": {"base_url": "http://127.0.0.1:1/v1"}},
    "models": {
        "planner": {"provider": "ollama", "id": "planner", "context": 1_000_000},
        "executor": {"provider": "ollama", "id": "executor", "context": 32768},
    },
    "plan": {"planner": "planner", "executor": "executor"},
}


class PlanView(FakeView):
    """A FakeView that answers the plan review with a scripted list of choices."""

    def __init__(self, answers=("run",)):
        super().__init__()
        self.answers = list(answers)
        self.reviews = []

    def plan_review(self, tickets, folder):
        self.reviews.append((tickets, folder))
        return self.answers.pop(0) if self.answers else "run"


def make_agent(view, model="executor"):
    a = Agent(CONFIG, tempfile.mkdtemp(), model, view)
    a.set_mode("plan")
    return a


def fake_calls(plan_text=TICKETS, executor_steps=None, prompts=None):
    """A fake Agent._call. The planner (ask mode) returns plan_text; each executor agent gets
    the next script from executor_steps (a list of reply lists), then "done". Captures the
    executor prompts when asked."""
    step_of, worker_of = {}, {}

    def call(self, messages=None, tools=True, quiet=False):
        if self.mode == "ask":
            return reply(plan_text)
        if id(self) not in worker_of:
            worker_of[id(self)] = len(worker_of)
        n = step_of.get(id(self), 0)
        step_of[id(self)] = n + 1
        if prompts is not None:
            prompts.append((messages or self.messages)[-1]["content"])
        scripts = executor_steps or []
        w = worker_of[id(self)]
        script = scripts[w] if w < len(scripts) else []
        return script[n] if n < len(script) else reply("done")

    return call


def notes(view):
    return " · ".join(view.notes)


class ParseTest(unittest.TestCase):
    def test_only_double_hash_splits(self):
        text = ("# Plan\nintro\n\n## Add model\nbody\n### Files\n- a.py\n### Done when\ntests pass\n"
                "\n## Wire it\nbody")
        tickets = parse_tickets(text)
        self.assertEqual([t[0] for t in tickets], ["Add model", "Wire it"])
        # text before the first "##" is dropped, and "###" stays inside its ticket
        self.assertNotIn("intro", tickets[0][1])
        self.assertIn("### Files", tickets[0][1])
        self.assertIn("- a.py", tickets[0][1])
        self.assertIn("### Done when", tickets[0][1])
        self.assertNotIn("Wire it", tickets[0][1])

    def test_the_example_gives_exactly_two(self):
        text = ("# Plan\nintro\n\n## Add model\nbody\n### Files\n- a.py\n### Done when\ntests pass\n"
                "\n## Wire it\nbody")
        self.assertEqual(len(parse_tickets(text)), 2)

    def test_no_headings_is_one_ticket(self):
        tickets = parse_tickets("Just do the thing.\nThen check it.")
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0][0], "Just do the thing.")

    def test_empty_reply_is_no_tickets(self):
        self.assertEqual(parse_tickets(""), [])


class TicketFilesTest(unittest.TestCase):
    def test_numbered_files_and_gitignore(self):
        root = Path(tempfile.mkdtemp())
        paths = write_tickets(root, parse_tickets(TICKETS))
        self.assertEqual([p.name for p in paths],
                         ["01-add-a-wishlist-model.md", "02-wire-it-into-the-basket.md"])
        self.assertEqual((root / ".purr/.gitignore").read_text(), "*\n")

    def test_old_plan_is_archived_not_deleted(self):
        root = Path(tempfile.mkdtemp())
        write_tickets(root, [("One", "body one")])
        write_tickets(root, [("Two", "body two")])
        archived = list((root / ".purr/tickets/old").glob("*/01-one.md"))
        self.assertEqual(len(archived), 1)
        self.assertIn("body one", archived[0].read_text())
        self.assertFalse((root / ".purr/tickets/01-one.md").exists())
        self.assertTrue((root / ".purr/tickets/01-two.md").exists())

    def test_existing_gitignore_is_kept(self):
        root = Path(tempfile.mkdtemp())
        (root / ".purr").mkdir()
        (root / ".purr/.gitignore").write_text("!commands/\n")
        write_tickets(root, [("One", "body")])
        self.assertEqual((root / ".purr/.gitignore").read_text(), "!commands/\n")


class ReviewTest(unittest.TestCase):
    def test_plan_turn_cancel_runs_nothing(self):
        view = PlanView(answers=("cancel",))
        a = make_agent(view)
        prompts = []
        with mock.patch.object(Agent, "_call", fake_calls(prompts=prompts)):
            a.turn("add wishlists to the shop")
        self.assertEqual(prompts, [])  # never reached an executor
        self.assertEqual(len(list(Path(a.root, ".purr/tickets").glob("*.md"))), 2)
        self.assertIn("kept the tickets; nothing was run", notes(view))
        self.assertIn("■ plan · 0 done, 2 not run", notes(view))

    def test_edit_then_run_reviews_again(self):
        view = PlanView(answers=("edit", "run"))
        a = make_agent(view)
        with mock.patch.object(Agent, "_call", fake_calls()):
            a.turn("add wishlists to the shop")
        self.assertEqual(len(view.reviews), 2)  # shown, edited, shown again
        self.assertIn("✓ plan · 2 done", notes(view))


class PlanTurnTest(unittest.TestCase):
    def test_full_flow_records_and_saves(self):
        view = PlanView(answers=("run",))
        a = make_agent(view)
        prompts = []
        before = set(LOG_DIR.glob("plan_*_ticket-*.json"))
        with mock.patch.object(Agent, "_call", fake_calls(prompts=prompts)):
            a.turn("add wishlists to the shop")
        self.assertIn("✓ plan · 2 done", notes(view))
        self.assertEqual(len(prompts), 2)
        # the parent keeps a record: the request, the ticket list and a line per ticket
        text = json.dumps(json.loads(a.log_path.read_text())["messages"])
        self.assertIn("add wishlists to the shop", text)
        self.assertIn("Wire it into the basket", text)
        self.assertIn("ticket 1/2: Add a wishlist model", text)
        # each executor chat is saved too, with a title
        files = sorted(set(LOG_DIR.glob("plan_*_ticket-*.json")) - before)
        self.assertEqual(len(files), 2)
        self.assertIn("ticket 1/2:", json.loads(files[0].read_text())["title"])
        self.assertIn("ticket 2/2:", json.loads(files[1].read_text())["title"])

    def test_prompts_carry_request_and_ticks(self):
        view = PlanView(answers=("run",))
        a = make_agent(view)
        write_tickets(a.root, [("One", "Files: x.py\nChange: add x\nDone when: x is 1"),
                               ("Two", "Files: y.py\nChange: add y\nDone when: y is 1")])
        prompts = []
        with mock.patch.object(Agent, "_call", fake_calls(prompts=prompts)):
            a.run_tickets(1, "make x and y work")
        self.assertIn("The whole plan is for: make x and y work", prompts[0])
        self.assertIn("○ 1. One", prompts[0])
        self.assertIn("○ 2. Two", prompts[0])
        self.assertIn("✓ 1. One", prompts[1])  # the second ticket sees the first is done
        self.assertIn("○ 2. Two", prompts[1])

    def test_reads_the_ticket_files_fresh(self):
        view = PlanView(answers=("run",))
        a = make_agent(view)
        write_tickets(a.root, [("One", "Files: x.py\nChange: add x\nDone when: x is 1")])
        ticket = Path(a.root, ".purr/tickets/01-one.md")
        ticket.write_text("# One\nFiles: x.py\nChange: EDITED BY HAND\nDone when: x is 1\n")
        prompts = []
        with mock.patch.object(Agent, "_call", fake_calls(prompts=prompts)):
            a.run_tickets(1, "make x")
        self.assertIn("EDITED BY HAND", prompts[0])


class ResultTest(unittest.TestCase):
    def failing(self, root, code):
        (Path(root) / ".purr").mkdir(exist_ok=True)
        (Path(root) / ".purr/test_command").write_text(f"python3 -c \"import sys; sys.exit({code})\"\n")

    def test_a_failing_ticket_stops_the_plan(self):
        view = PlanView()
        a = make_agent(view)
        write_tickets(a.root, [("One", "body"), ("Two", "body")])
        self.failing(a.root, 0)
        prompts = []
        runs = [("1 passed", 0), ("FAILED tests/test_x.py::test_a - boom\n1 failed", 1)]
        with mock.patch.object(Agent, "_call", fake_calls(prompts=prompts)), \
                mock.patch.object(agent_module, "run_shell", side_effect=runs):
            a.run_plan(1)
        self.assertEqual(len(prompts), 1)  # stopped after the first ticket's tests failed
        self.assertIn("left the tests failing", notes(view))
        self.assertIn("✗ plan · 0 done, 1 failed, 1 not run", notes(view))  # not counted as done

    def test_passing_tickets_are_counted_done(self):
        view = PlanView()
        a = make_agent(view)
        write_tickets(a.root, [("One", "body"), ("Two", "body")])
        self.failing(a.root, 0)
        with mock.patch.object(Agent, "_call", fake_calls()):
            a.run_plan(1)
        self.assertIn("✓ plan · 2 done", notes(view))

    def test_no_test_command_still_finishes(self):
        view = PlanView()
        a = make_agent(view)
        write_tickets(a.root, [("One", "body")])
        with mock.patch.object(Agent, "_call", fake_calls()):
            a.run_plan(1)
        self.assertIn("✓ plan · 1 done", notes(view))
        self.assertIn("not checked: no tests found", json.dumps(a.messages))


class BaselineTest(unittest.TestCase):
    """Tests that already failed before the plan aren't blamed on a ticket."""

    def run_with(self, runs, n=2):
        view = PlanView()
        a = make_agent(view)
        write_tickets(a.root, [(f"T{i}", "body") for i in range(1, n + 1)])
        (Path(a.root) / ".purr/test_command").write_text("python3 -m pytest -q -x --tb=short\n")
        prompts = []
        with mock.patch.object(Agent, "_call", fake_calls(prompts=prompts)), \
                mock.patch.object(agent_module, "run_shell", side_effect=runs) as shell:
            a.run_plan(1)
        return a, view, prompts, shell

    def test_old_failures_dont_stop_the_plan(self):
        old = ("FAILED tests/test_x.py::test_old - nope\n1 failed", 1)
        a, view, prompts, shell = self.run_with([old, old, old])
        self.assertEqual(len(prompts), 2)
        self.assertIn("already fail before the plan", notes(view))
        self.assertIn("✓ plan · 2 done", notes(view))
        self.assertIn("no new test failures", json.dumps(a.messages))
        self.assertNotIn(" -x", shell.call_args_list[0].args[0])  # every test runs, not just the first failure

    def test_a_new_failure_is_still_caught(self):
        old = ("FAILED tests/test_x.py::test_old - nope\n1 failed", 1)
        worse = ("FAILED tests/test_x.py::test_old - nope\nFAILED tests/test_x.py::test_new - boom\n2 failed", 1)
        a, view, prompts, _ = self.run_with([old, worse])
        self.assertEqual(len(prompts), 1)
        self.assertIn("broke tests/test_x.py::test_new", json.dumps(a.messages))
        self.assertIn("✗ plan · 0 done, 1 failed, 1 not run", notes(view))

    def test_unnamed_old_failure_is_marked_unchecked(self):
        a, view, prompts, _ = self.run_with([("SyntaxError", 1), ("SyntaxError", 1)], n=1)
        self.assertIn("not checked: the tests already failed before the plan", json.dumps(a.messages))

    def test_failed_tests_reads_pytest_and_unittest(self):
        out = ("FAILED tests/t.py::test_a - x\nERROR tests/t.py::test_b\n"
               "FAIL: test_c (t.T.test_c)\nERROR: test_d (t.T.test_d)\n")
        self.assertEqual(agent_module.failed_tests(out),
                         {"tests/t.py::test_a", "tests/t.py::test_b", "test_c (t.T.test_c)", "test_d (t.T.test_d)"})


class StopTest(unittest.TestCase):
    def test_an_earlier_esc_doesnt_block_plan_run(self):
        view = PlanView()
        a = make_agent(view)
        write_tickets(a.root, [("One", "body")])
        a.stop_flag = True  # left over from stopping an earlier answer
        prompts = []
        with mock.patch.object(Agent, "_call", fake_calls(prompts=prompts)):
            a.run_plan(1)
        self.assertEqual(len(prompts), 1)
        self.assertIn("✓ plan · 1 done", notes(view))

    def test_a_stopped_ticket_isnt_checked_or_done(self):
        view = PlanView()
        a = make_agent(view)
        write_tickets(a.root, [("One", "body"), ("Two", "body")])
        calls = fake_calls()

        def stop_mid_ticket(self, messages=None, tools=True, quiet=False):
            self.parent.stop_flag = True  # Esc while the executor works
            return calls(self, messages, tools, quiet)

        with mock.patch.object(Agent, "_call", stop_mid_ticket), \
                mock.patch.object(agent_module, "run_shell") as shell, \
                mock.patch("harness.checks.test_command", return_value=None):
            a.run_plan(1)
        shell.assert_not_called()
        self.assertIn("stopped, not checked", json.dumps(a.messages))
        self.assertIn("■ plan · 0 done, 2 stopped/not run", notes(view))

    def test_stopping_while_planning_writes_nothing(self):
        view = PlanView()
        a = make_agent(view)
        calls = fake_calls()

        def stop_planner(self, messages=None, tools=True, quiet=False):
            self.parent.stop_flag = True
            return calls(self, messages, tools, quiet)

        with mock.patch.object(Agent, "_call", stop_planner):
            a.turn("add wishlists")
        self.assertEqual(view.reviews, [])
        self.assertFalse(list(Path(a.root, ".purr/tickets").glob("*.md")))
        self.assertIn("■ plan · stopped while planning", notes(view))


class ReviewBodyTest(unittest.TestCase):
    def test_review_body_has_no_repeated_title(self):
        view = PlanView(answers=("cancel",))
        a = make_agent(view)
        with mock.patch.object(Agent, "_call", fake_calls()):
            a.turn("add wishlists")
        first = view.reviews[0][0][0]
        self.assertEqual(first["title"], "Add a wishlist model")
        self.assertTrue(first["body"].startswith("Files: shop/wishlist.py"))

    def test_plan_asks_for_passing_tests_after_each_ticket(self):
        self.assertIn("tests must still pass after every ticket", agent_module.PLAN)


class RunCommandTest(unittest.TestCase):
    def test_plan_run_parses_the_number(self):
        from harness import commands
        a = make_agent(PlanView())
        self.assertEqual(commands.run(a, "/plan run 3"), {"plan_run": 3})
        self.assertEqual(commands.run(a, "/plan run"), {"plan_run": 1})
        self.assertEqual(commands.run(a, "/plan run x")[0][0], "error")

    def test_plan_run_uses_the_span(self):
        view = PlanView()
        a = make_agent(view)
        write_tickets(a.root, [("One", "body"), ("Two", "body"), ("Three", "body")])
        prompts = []
        with mock.patch.object(Agent, "_call", fake_calls(prompts=prompts)):
            a.run_plan(2)
        self.assertEqual(len(prompts), 2)  # tickets 2 and 3 only
        self.assertIn("ticket 2/3", prompts[0])
        self.assertIn("✓ plan · 2 done", notes(view))  # both attempted tickets done


class TrustUndoTest(unittest.TestCase):
    def test_executors_share_trust_and_undo(self):
        view = PlanView()
        a = make_agent(view)
        a.tools.trust_all = True
        a.tools.always.add("write_file")
        a.tools.always_run.add("true")
        write_tickets(a.root, [("One", "Files: x.py\nChange: add x\nDone when: x is 1")])
        steps = [[reply("", tool=("write_file", {"path": "x.py", "content": "x = 1\n"})),
                  reply("wrote x.py")]]
        made = []
        real = agent_module.Agent

        def spy(*args, **kwargs):
            worker = real(*args, **kwargs)
            made.append(worker)
            return worker

        with mock.patch.object(agent_module, "Agent", spy), \
                mock.patch.object(Agent, "_call", fake_calls(executor_steps=steps)):
            a.run_tickets(1, "add x")
        worker = made[0]
        self.assertTrue(worker.tools.trust_all)
        self.assertIs(worker.tools.always, a.tools.always)
        self.assertIs(worker.tools.always_run, a.tools.always_run)
        self.assertIsNotNone(worker.log_path)  # the executor chat is saved
        # the change lands on the parent's undo stack: /undo puts it back
        self.assertEqual((Path(a.root) / "x.py").read_text(), "x = 1\n")
        self.assertIn("x.py", a.undo())
        self.assertFalse((Path(a.root) / "x.py").exists())

    def test_undo_after_two_tickets_keeps_the_earliest_text(self):
        view = PlanView()
        a = make_agent(view)
        a.tools.trust_all = True
        (Path(a.root) / "x.py").write_text("x = 0\n")
        write_tickets(a.root, [("One", "body"), ("Two", "body")])
        steps = [[reply("", tool=("read_file", {"path": "x.py"})),
                  reply("", tool=("edit_file", {"path": "x.py", "old_text": "x = 0", "new_text": "x = 1"})),
                  reply("did one")],
                 [reply("", tool=("read_file", {"path": "x.py"})),
                  reply("", tool=("edit_file", {"path": "x.py", "old_text": "x = 1", "new_text": "x = 2"})),
                  reply("did two")]]
        with mock.patch.object(Agent, "_call", fake_calls(executor_steps=steps)):
            a.run_tickets(1, "count x")
        self.assertEqual((Path(a.root) / "x.py").read_text(), "x = 2\n")
        a.undo()
        self.assertEqual((Path(a.root) / "x.py").read_text(), "x = 0\n")  # back to the very start


class PromptTest(unittest.TestCase):
    def test_plan_demands_the_parts_and_small_tickets(self):
        for part in ("Files:", "Change:", "Done when:"):
            self.assertIn(part, agent_module.PLAN)
        self.assertIn("one to three files", agent_module.PLAN)
        self.assertIn("fresh chat", agent_module.PLAN)

    def test_ticket_work_has_the_plan_and_the_titles(self):
        self.assertIn("{request}", agent_module.TICKET_WORK)
        self.assertIn("{titles}", agent_module.TICKET_WORK)

    def test_purrs_own_settings_name_no_model_only_one_machine_has(self):
        import tomllib
        defaults = tomllib.loads((Path(__file__).resolve().parent.parent / "harness" / "defaults.toml").read_text())
        self.assertNotIn("plan", defaults)  # the planner and executor are the model you're on unless you set them
        self.assertNotIn("mode_models", defaults)
        self.assertNotIn("default_model", defaults)  # purr setup picks it, or the roomiest local model found
        self.assertFalse([n for n, m in defaults["models"].items() if m["provider"] in ("ollama", "llamacpp")])
