"""The workbench (ctrl+t): the session's changes, the project tree and the small editor.

Run: python3 -m unittest discover tests
No model is called.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_limits import agent  # noqa: E402

try:
    from textual.app import App
    from tui import themes, workbench
except ImportError:  # textual isn't there in a plain install
    workbench = None


class SessionChangesTest(unittest.TestCase):
    def test_first_before_wins_and_undo_drops_a_turn(self):
        a = agent()
        p = Path(a.root) / "x.py"
        p.write_text("one\n")
        t = a.tools
        t.begin_turn()
        t._remember(p)
        p.write_text("two\n")
        t.begin_turn()
        t._remember(p)
        p.write_text("three\n")
        self.assertEqual(t.session_changes(), {p: "one\n"})
        t.undo()
        self.assertEqual(t.session_changes(), {p: "one\n"})
        t.undo()
        self.assertEqual(t.session_changes(), {})


@unittest.skipIf(workbench is None, "needs textual (uv sync)")
class WorkbenchTest(unittest.IsolatedAsyncioTestCase):
    def test_changed_lines(self):
        self.assertEqual(workbench.changed_lines("a\nb\nc\n", "a\nB\nc\nd\n"), {2, 4})
        self.assertEqual(workbench.changed_lines(None, "new\nfile\n"), {1, 2})

    async def test_summary_diff_code_and_editing(self):
        root = Path(tempfile.mkdtemp())
        (root / "cat.py").write_text("def meow():\n    return 'mew'\n")
        (root / "new.py").write_text("x = 1\n")
        (root / "__pycache__").mkdir()
        turns = [{root / "cat.py": "def meow():\n    return 'meow'\n"}, {root / "new.py": None}]

        class Host(App):
            def __init__(self):
                super().__init__()
                self.register_theme(themes.make("plum"))  # the workbench's CSS uses purr's colours
                self.theme = "purr-plum"

            def on_mount(self):
                self.push_screen(workbench.Workbench(root, turns))

        app = Host()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            wb = app.screen
            self.assertIn("what changed", str(wb.query_one("#wb-main").border_title))
            self.assertEqual(wb.query_one("#wb-changed").option_count, 2)
            self.assertIsNone(wb.path)  # the summary first
            await pilot.press("enter")  # the first changed file, as a diff
            await pilot.pause()
            self.assertEqual((wb.path.name, wb.view), ("cat.py", "diff"))
            await pilot.press("d")
            self.assertEqual(wb.view, "code")
            await pilot.press("e")
            await pilot.pause()
            self.assertTrue(wb.editing)
            editor = wb.query_one("#wb-editor")
            editor.text = "def meow():\n    return 'purr'\n"
            await pilot.press("escape")  # unsaved: warns first
            self.assertTrue(wb.editing)
            await pilot.press("ctrl+s")
            self.assertEqual((root / "cat.py").read_text(), "def meow():\n    return 'purr'\n")
            await pilot.press("escape")
            self.assertFalse(wb.editing)
            tree = wb.query_one("#wb-tree")
            self.assertNotIn("__pycache__", [n.data.path.name for n in tree.root.children if n.data])
            await pilot.press("escape")
            self.assertIsNot(app.screen, wb)


if __name__ == "__main__":
    unittest.main()
