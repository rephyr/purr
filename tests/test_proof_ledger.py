"""The proof ledger: todo items carry a check command; purr runs them again itself in a fresh shell
before the end, and again after later changes (late fixes broke checked work: filter-js, doom).

Run: python3 -m unittest discover -s tests -t .
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_bench_fixes import one_shot, users  # noqa: E402
from tests.test_limits import reply, scripted  # noqa: E402


def todo(*items):
    return reply("", tool=("todo", {"items": [dict(text=t, status="done", check=c) for t, c in items]}))


class LedgerTest(unittest.TestCase):
    def test_a_late_change_that_breaks_a_check_goes_back(self):
        a = one_shot("small", time_limit=900)  # a small model: the ledger is on
        scripted(a, [
            reply("", tool=("write_file", {"path": "out.txt", "content": "42\n"})),
            todo(("out.txt says 42", "grep -qx 42 out.txt"), ("there is an out.txt", "test -f out.txt")),
            reply("Done."),                                          # -> the final check
            reply("", tool=("write_file", {"path": "out.txt", "content": "41\n"})),  # a late "fix" breaks it
            reply("Checked again, all good."),                       # -> purr runs the checks: one fails
            reply("", tool=("write_file", {"path": "out.txt", "content": "42\n"})),
            reply("Fixed."), reply("ok"), reply("ok")])
        a.turn("write 42 to out.txt")
        notes = [u for u in users(a) if u.startswith("(purr ran your checks again")]
        self.assertEqual(len(notes), 1)
        self.assertIn("✗ out.txt says 42: `grep -qx 42 out.txt` exit 1", notes[0])
        self.assertIn("✓ there is an out.txt  (proves little", notes[0])
        self.assertEqual(Path(a.root, "out.txt").read_text(), "42\n")

    def test_checks_run_in_a_fresh_shell(self):
        a = one_shot("small", time_limit=900)
        os.environ["PURR_LEDGER_SECRET"] = "only-in-purr"
        try:
            scripted(a, [reply("", tool=("write_file", {"path": "a.txt", "content": "x\n"})),
                         todo(("needs no outside environment", 'test -z "$PURR_LEDGER_SECRET"')),
                         reply("Done."), reply("All good."), reply("ok")])
            a.turn("make a.txt")
        finally:
            del os.environ["PURR_LEDGER_SECRET"]
        self.assertFalse([u for u in users(a) if u.startswith("(purr ran your checks again")])  # it passed: env -i

    def test_every_model_gets_it_unless_turned_off(self):
        for on in (True, False):
            a = one_shot("api", time_limit=900)  # cheap: API models get it too
            if not on:
                a.config = dict(a.config, proof_ledger=False)
            scripted(a, [reply("", tool=("write_file", {"path": "out.txt", "content": "41\n"})),
                         todo(("out.txt says 42", "grep -qx 42 out.txt")), reply("Done."), reply("ok"), reply("ok")])
            a.turn("write 42 to out.txt")
            self.assertEqual(bool([u for u in users(a) if u.startswith("(purr ran your checks again")]), on)

    def test_the_todo_list_keeps_its_checks(self):
        a = one_shot("small")
        a.tools.call("todo", '{"items": [{"text": "x", "status": "doing", "check": " true "}, {"text": "y", "status": "pending"}]}')
        self.assertEqual(a.tools.todo_list, [{"text": "x", "status": "doing", "check": "true"}, {"text": "y", "status": "pending"}])


if __name__ == "__main__":
    unittest.main()
