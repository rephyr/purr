"""/stats: fun numbers counted from saved chats.

Run: python3 -m unittest discover tests
No model is called.
"""

import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import stats  # noqa: E402


def chat(log_dir, stamp, messages, **extra):
    Path(log_dir, f"{stamp}.json").write_text(json.dumps(
        {"model": "small", "mode": "code", "folder": "/home/me/cafe", "messages": messages, **extra}))


EDIT = {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {
    "name": "edit_file", "arguments": json.dumps({"path": "cat.py", "old_text": "a", "new_text": "a\nb\nc"})}}]}


class StatsTest(unittest.TestCase):
    def test_counts(self):
        d = tempfile.mkdtemp()
        chat(d, "2026-10-01_230000", [{"role": "user", "content": "fix the cat please"}, EDIT], out=1200)
        chat(d, "2026-10-02_231500", [{"role": "user", "content": "why is it broken"},
                                      {"role": "user", "content": "(purr: a reminder)"}])
        chat(d, "2026-10-02_100000", [{"role": "user", "content": "x"}], folder="/tmp/test")  # a test run
        s = stats.gather(d, today=datetime.date(2026, 10, 2))
        self.assertEqual(s["chats"], 2)
        self.assertEqual(s["said"], 2)  # purr's own note isn't yours
        self.assertEqual((s["nice"], s["oops"]), (1, 2))
        self.assertEqual((s["added"], s["removed"]), (3, 1))
        self.assertEqual(s["files"].most_common(1)[0], ("cafe/cat.py", 1))
        self.assertEqual(s["streak"], 2)
        text = "\n".join(line for _, line in stats.report(s, {"name": "Mochi", "pets": 3, "tasks": 1}))
        self.assertIn("2-day streak", text)
        self.assertIn("night owl", text)
        self.assertIn("Mochi got 3 pets", text)

    def test_tokens_exact_or_guessed(self):
        d = tempfile.mkdtemp()
        chat(d, "2026-10-01_100000", [{"role": "user", "content": "hi"}], out=500)
        s = stats.gather(d)
        self.assertEqual((s["out"], s["guessed"]), (500, False))
        chat(d, "2026-10-01_110000", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "x" * 400}])
        s = stats.gather(d)
        self.assertEqual((s["out"], s["guessed"]), (600, True))  # an old chat: ~4 characters a token
        self.assertIn("≈600 tokens generated", "\n".join(line for _, line in stats.report(s)))

    def test_card_draws(self):
        from rich.console import Console
        from tui import statcard
        d = tempfile.mkdtemp()
        chat(d, "2026-10-01_230000", [{"role": "user", "content": "fix the cat please"}, EDIT], out=12_345)
        console = Console(width=110, record=True, color_system=None)
        console.print(statcard.render(stats.gather(d), {"name": "Mochi", "pets": 9, "tasks": 2}))
        text = console.export_text()
        self.assertIn("tokens made", text)
        self.assertIn("night owl", text)
        self.assertIn("Mochi got 9 pets", text)
        self.assertEqual(statcard.short(12_345), "12k")

    def test_no_chats(self):
        self.assertIn("no chats yet", stats.report(stats.gather(tempfile.mkdtemp()))[0][1])


if __name__ == "__main__":
    unittest.main()
