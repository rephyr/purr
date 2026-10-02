"""MCP: purr's client (harness/mcp.py) and its own servers (servers/codebase.py, servers/stack.py).

Run: python3 -m unittest discover tests
No model is called. The servers run for real, as separate programs.
"""

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
PURR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PURR))

from harness.agent import Agent  # noqa: E402
from harness.mcp import Mcp  # noqa: E402
from tests.test_limits import CONFIG, FakeView  # noqa: E402

SERVERS = {"mcp": {"codebase": {"command": ["python3", "{purr}/servers/codebase.py"]},
                   "stack": {"command": ["python3", "{purr}/servers/stack.py"]}}}


def project(files):
    root = Path(tempfile.mkdtemp())
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(textwrap.dedent(text))
    return root


CAFE = {
    "pyproject.toml": '[project]\nname = "cafe"\nrequires-python = ">=3.11"\ndependencies = ["textual"]\n',
    "cafe/inventory.py": """\
        MAX_STACK = 99


        class Inventory:
            def add(self, name, count=1):
                self.slots.append([name, count])

            def count(self, name):
                return 0
        """,
    "cafe/shop.py": "from cafe.inventory import Inventory, MAX_STACK\n\n\ndef buy(inv: Inventory):\n    inv.add('fish')\n",
    "tests/test_inv.py": "import unittest\n",
}

GODOT = {
    "project.godot": """\
        config_version=5

        [application]

        config/name="Tiny"
        run/main_scene="res://main.tscn"
        config/features=PackedStringArray("4.7", "Forward Plus")

        [autoload]

        Game="*res://game.gd"
        """,
    "game.gd": "extends Node\n\nvar score := 0\n\nfunc add_score(n: int) -> void:\n\tscore += n\n",
    "player.gd": """\
        class_name Player
        extends CharacterBody2D

        signal died(reason: String)
        @export var speed: float = 200.0

        func _physics_process(delta: float) -> void:
        \tmove_and_slide()
        \tGame.add_score(1)
        """,
    "main.tscn": '[gd_scene format=3]\n[ext_resource type="Script" path="res://player.gd" id="1"]\n',
}


class ClientTest(unittest.TestCase):
    def test_tools_list_and_call(self):
        root = project(CAFE)
        m = Mcp(SERVERS, root)
        names = [s["function"]["name"] for s in m.schemas()]
        self.assertIn("outline", names)
        self.assertIn("python_api", names)
        self.assertNotIn("godot_class", names)  # not a Godot project
        self.assertIn("def add(self, name, count=1)", m.call("outline", {"path": "cafe/inventory.py"}))
        m.stop()

    def test_restart_for_private_mode(self):
        m = Mcp(SERVERS, project(CAFE))
        m.schemas()
        m.offline(True)  # restarts the servers with PURR_OFFLINE=1
        self.assertIn("class Inventory", m.call("outline", {"path": "cafe/inventory.py"}))
        self.assertEqual(m.servers[0].proc.args and m.servers[0].env, {"PURR_OFFLINE": "1"})
        m.stop()

    def test_a_broken_server_is_skipped(self):
        notes = []
        m = Mcp({"mcp": {"nope": {"command": ["/no/such/program"]}}}, project({}), note=notes.append)
        self.assertEqual(m.schemas(), [])
        self.assertIn("nope", notes[0])

    def test_tools_allowlist(self):
        m = Mcp({"mcp": {"codebase": {**SERVERS["mcp"]["codebase"], "tools": ["outline"]}}}, project(CAFE))
        self.assertEqual([s["function"]["name"] for s in m.schemas()], ["outline"])
        m.stop()


class CodebaseServerTest(unittest.TestCase):
    def setUp(self):
        self.m = Mcp(SERVERS, project(CAFE))

    def tearDown(self):
        self.m.stop()

    def test_overview(self):
        text = self.m.call("project_overview", {})
        self.assertIn("Python >=3.11 project cafe", text)
        self.assertIn("textual", text)
        self.assertIn("unittest", text)

    def test_folder_outline_and_find_symbol(self):
        self.assertIn("class Inventory  :4", self.m.call("outline", {"path": "."}))
        found = self.m.call("find_symbol", {"name": "MAX_STACK"})
        self.assertIn("defined:\n  cafe/inventory.py:1", found)
        self.assertIn("cafe/shop.py:1", found)

    def test_outline_says_who_uses_the_file(self):
        text = self.m.call("outline", {"path": "cafe/inventory.py"})
        self.assertIn("used by: cafe/shop.py", text)


class GodotTest(unittest.TestCase):
    def setUp(self):
        self.m = Mcp(SERVERS, project(GODOT))

    def tearDown(self):
        self.m.stop()

    def test_overview_knows_godot(self):
        text = self.m.call("project_overview", {})
        self.assertIn('Godot 4.7 project "Tiny"', text)
        self.assertIn("Game (res://game.gd)", text)
        self.assertNotIn("uses C#", text)

    def test_gdscript_outline(self):
        text = self.m.call("outline", {"path": "player.gd"})
        for want in ("class_name Player", "signal died(reason: String)", "@export var speed: float",
                     "func _physics_process(delta: float) -> void"):
            self.assertIn(want, text)

    def test_autoload_and_scenes(self):
        self.assertIn("Game is a Godot autoload", self.m.call("find_symbol", {"name": "Game"}))
        self.assertIn("used by: main.tscn", self.m.call("outline", {"path": "player.gd"}))
        self.assertIn("autoload Game", self.m.call("outline", {"path": "game.gd"}))

    def test_only_godot_tools(self):
        names = [s["function"]["name"] for s in self.m.schemas()]
        self.assertIn("godot_class", names)
        self.assertNotIn("python_api", names)


class AgentTest(unittest.TestCase):
    def test_prompt_gets_the_overview_and_the_tools(self):
        a = Agent({**CONFIG, **SERVERS}, project(CAFE), "small", FakeView())
        prompt = a.messages[0]["content"]
        self.assertIn("Project overview", prompt)
        self.assertIn("outline, find_symbol", prompt)
        offered = [t["function"]["name"] for t in a.mcp.schemas(taken=a.mcp_taken)]
        self.assertNotIn("project_overview", offered)  # its answer is already in the prompt
        a.tools.trust_all = True
        self.assertIn("class Inventory", a.tools.call("outline", json.dumps({"path": "cafe/inventory.py"})))
        a.mcp.stop()

    def test_overview_can_be_switched_off(self):
        a = Agent({**CONFIG, **SERVERS, "overview_in_prompt": False}, project(CAFE), "small", FakeView())
        self.assertNotIn("Project overview", a.messages[0]["content"])
        a.mcp.stop()

    def test_a_tool_that_changes_things_asks_first(self):
        root = project({})
        server = root / "writer.py"
        server.write_text(textwrap.dedent(f"""\
            import sys
            sys.path.insert(0, {str(PURR / 'servers')!r})
            from mcpserver import Server
            s = Server("writer")

            @s.tool("write a note", {{"text": {{"type": "string"}}}}, read_only=False)
            def note(text):
                open("note.txt", "w").write(text)
                return "written"
            s.run()
            """))
        view = FakeView()
        asked = []
        view.ask = lambda q, **k: (asked.append(q), ("n", "not now"))[1]
        a = Agent({**CONFIG, "mcp": {"writer": {"command": ["python3", str(server)]}}}, root, "small", view)
        self.assertIn("the user said no", a.tools.call("note", json.dumps({"text": "hi"})))
        self.assertTrue(asked and not (root / "note.txt").exists())
        a.set_mode("ask")  # look-only: not even offered
        self.assertNotIn("note", [s["function"]["name"] for s in a.mcp.schemas(read_only=True)])
        a.mcp.stop()


if __name__ == "__main__":
    unittest.main()
