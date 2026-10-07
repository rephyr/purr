"""Your own agents (harness/agents.py): the files, the modes they become, /agent, and the window.
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

from harness import agents, commands, settings  # noqa: E402
from harness.agent import Agent  # noqa: E402
from harness.tools import TOOL_NAMES  # noqa: E402
from tests.test_limits import CONFIG, FakeView, reply, scripted  # noqa: E402

REVIEWER = """---
description: reviews code for bugs, never edits
tools: read
model: big
temperature: 0.2
colour: cyan
icon: ◎
---
You are a careful reviewer. Point at the line, say what breaks.
"""
CLAUDE = """---
name: test-runner
description: Use this agent to run the tests and fix failures
tools: Read, Grep, Glob, Bash
model: sonnet
---
You run the project's tests and fix what fails.
"""
OPENCODE = """---
description: Writes docs only
mode: subagent
temperature: 0.5
tools:
  bash: false
  write: true
---
You write documentation.
"""


class Folders(unittest.TestCase):
    """Fresh folders for yours, the project's and other tools' agents."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.root = Path(tempfile.mkdtemp())
        for target, name, value in ((settings, "CONFIG_DIR", self.home / ".config" / "purr"),):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def put(self, folder, name, text):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.md").write_text(text)

    def agent(self):
        return Agent(CONFIG, str(self.root), "small", FakeView())


class FilesTest(Folders):
    def test_the_header_in_three_dialects(self):
        head, body = agents.parse_header(REVIEWER)
        self.assertEqual((head["tools"], head["temperature"], head["icon"]), ("read", 0.2, "◎"))
        self.assertTrue(body.startswith("You are a careful reviewer"))
        claude = agents.load_file(self.write("c.md", CLAUDE), "claude code", TOOL_NAMES)
        self.assertEqual(claude["name"], "test-runner")
        self.assertEqual(claude["tools"], {"read_file", "grep", "list_files", "run"})
        self.assertIsNone(claude["model"])  # "sonnet" isn't a model purr has
        opencode = agents.load_file(self.write("docs-writer.md", OPENCODE), "opencode", TOOL_NAMES)
        self.assertEqual(opencode["name"], "docs-writer")  # named after its file
        self.assertNotIn("run", opencode["tools"])
        self.assertIn("write_file", opencode["tools"])
        self.assertEqual(opencode["temperature"], 0.5)

    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        return path

    def test_where_they_come_from_and_who_wins(self):
        self.put(self.home / ".claude" / "agents", "reviewer", CLAUDE.replace("test-runner", "reviewer"))
        self.put(settings.CONFIG_DIR / "agents", "reviewer", REVIEWER)
        self.put(self.root / ".purr" / "agents", "reviewer", REVIEWER.replace("never edits", "this project's"))
        self.put(self.home / ".claude" / "agents", "test-runner", CLAUDE)
        found = agents.load(self.root, TOOL_NAMES)
        self.assertEqual(found["reviewer"]["description"], "reviews code for bugs, this project's")  # the project's
        self.assertEqual(found["test-runner"]["origin"], "claude code")

    def test_purrs_own_mode_names_stay_purrs(self):
        self.put(settings.CONFIG_DIR / "agents", "code", REVIEWER)
        self.assertEqual(agents.load(self.root, TOOL_NAMES), {})

    def test_a_saved_agent_is_named_like_its_file(self):
        first = agents.save("---\nname: helper\ndescription: x\n---\nDo it.\n")
        second = agents.save("---\nname: helper\ndescription: y\n---\nDo it too.\n")
        self.assertEqual((first.name, second.name), ("helper.md", "helper-2.md"))
        self.assertEqual(agents.parse_header(second.read_text())[0]["name"], "helper-2")
        self.assertEqual(agents.save("---\nname: ask\n---\nHi.\n").name, "ask-agent.md")


class ModeTest(Folders):
    def setUp(self):
        super().setUp()
        self.put(settings.CONFIG_DIR / "agents", "reviewer", REVIEWER)
        self.put(self.root / ".purr" / "agents", "test-runner", CLAUDE)
        self.put(self.home / ".claude" / "agents", "imported", CLAUDE.replace("test-runner", "imported"))

    def test_a_look_only_agent_is_enforced_and_brings_its_model(self):
        a = self.agent()
        self.assertIn("reviewer", a.modes())
        moved = a.switch_mode("reviewer")
        self.assertEqual((a.model_name, moved), ("big", "model: big"))  # its model
        self.assertTrue(a.tools.read_only)                              # enforced, like ask mode
        self.assertFalse(a.coding)
        system = a.messages[0]["content"]
        self.assertIn('the user\'s agent "reviewer"', system)
        self.assertIn("Point at the line", system)
        self.assertEqual(a._body(None, True)["temperature"], 0.2)
        names = [t["function"]["name"] for t in a._body(None, True)["tools"]]
        self.assertNotIn("edit_file", names)

    def test_a_tool_list_is_what_it_gets(self):
        a = self.agent()
        a.set_mode("test-runner")
        names = {t["function"]["name"] for t in a._body(None, True)["tools"]}
        self.assertEqual(names & TOOL_NAMES, {"read_file", "grep", "list_files", "run"})
        self.assertTrue(a.coding)  # it may run things: the checks that come with work apply

    def test_shift_tab_goes_through_yours_not_other_tools(self):
        a = self.agent()
        cycle = a.cycle_modes()
        self.assertEqual(cycle[:7], ["code", "ask", "learn", "pair", "plan", "chat", "create"])
        self.assertIn("reviewer", cycle)
        self.assertNotIn("imported", cycle)
        self.assertIn("imported", a.modes())  # still /agent imported

    def test_the_agent_command(self):
        a = self.agent()
        listing = "\n".join(line for _, line in commands.run(a, "/agent"))
        self.assertIn("reviewer", listing)
        self.assertIn("(from claude code)", listing)
        self.assertEqual(commands.run(a, "/agent new writes tests for new code"), {"agent_new": "writes tests for new code"})
        self.assertIn("agent: reviewer", commands.run(a, "/agent reviewer")[0][1])
        self.assertEqual(a.mode, "reviewer")
        self.assertIn("reviewer", "\n".join(line for _, line in commands.run(a, "/mode")))
        self.assertEqual(commands.run(a, "/agent nobody")[0][0], "error")

    def test_a_drafted_agent(self):
        a = self.agent()
        scripted(a, [reply("```\n---\nname: Doc Writer\ndescription: writes docs\ntools: all\n---\nYou write docs.\n```")])
        path = agents.save(agents.draft(a, "writes docs"))
        self.assertEqual(path.name, "doc-writer.md")
        a.reload_agents()
        self.assertEqual(a.agents["doc-writer"]["prompt"], "You write docs.")

    def test_a_saved_chat_in_an_agents_mode_comes_back_in_it(self):
        a = self.agent()
        a.set_mode("reviewer")
        a.messages.append({"role": "user", "content": "hi"})
        a.save_log()
        b = self.agent()
        b.load(a.log_path)
        self.assertEqual(b.mode, "reviewer")
        self.assertTrue(b.tools.read_only)
        json.loads(Path(a.log_path).read_text())


try:
    from tui.app import PurrApp
except ImportError:
    PurrApp = None


@unittest.skipIf(PurrApp is None, "needs textual (uv sync)")
class WindowTest(Folders, unittest.IsolatedAsyncioTestCase):
    async def test_shift_tab_reaches_your_agent_in_its_colour(self):
        self.put(settings.CONFIG_DIR / "agents", "reviewer", REVIEWER)
        app = PurrApp(CONFIG, str(self.root), "small")
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.3)
            for _ in range(7):  # code -> ... -> create -> reviewer
                await pilot.press("shift+tab")
            await pilot.pause(0.2)
            self.assertEqual(app.agent.mode, "reviewer")
            self.assertEqual(app.mode_look(), ("◎", "#8fd8e8"))
            self.assertIn("reviewer: reviews code for bugs", app.prompt.placeholder)
            self.assertIn("◎ reviewer", str(app.query_one("#model").render()))
            await pilot.press("shift+tab")  # and round again to code
            await pilot.pause(0.2)
            self.assertEqual(app.agent.mode, "code")


@unittest.skipIf(PurrApp is None, "needs textual (uv sync)")
class SteerWindowTest(Folders, unittest.IsolatedAsyncioTestCase):
    async def test_typing_while_it_works_queues_a_steer(self):
        app = PurrApp(CONFIG, str(self.root), "small")
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.3)
            app.busy, app.job = True, "turn"  # as if a turn were running
            await pilot.press(*"use pathlib")
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertEqual(app.agent.steers, ["use pathlib"])
            app.job = "compact"  # anything but a turn still waits
            await pilot.press(*"later")
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertEqual(app.agent.steers, ["use pathlib"])
