"""Blind acceptance checks: written from the request alone before any code exists, so they don't
share the solver's blind spots; purr runs them at the final check (advisory, never a gate).

Run: python3 -m unittest discover -s tests -t .
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import Agent  # noqa: E402
from tests.test_bench_fixes import users  # noqa: E402
from tests.test_limits import CONFIG, FakeView, reply, scripted  # noqa: E402

BLIND = """SPEC:
- the limit is across BOTH files together, not per file
- out.txt must hold exactly 42, nothing else
CHECKS:
```sh
# "write 42 to out.txt"
if [ "$(cat out.txt 2>/dev/null)" = "42" ]; then echo "PASS 1"; else echo "FAIL 1: out.txt is not exactly 42"; fi
# "and a log"
if [ -f run.log ]; then echo "PASS 2"; else echo "SKIP 2"; fi
```"""


def blind_agent(model="small", **config):
    a = Agent({**CONFIG, "blind_checks": True, **config}, tempfile.mkdtemp(), model, FakeView())
    a.tools.trust_all = True
    a.set_one_shot()
    a.time_limit = 900
    return a


class BlindChecksTest(unittest.TestCase):
    def test_the_card_comes_first_and_the_checks_run_at_the_final_check(self):
        a = blind_agent()
        Path(a.root, "given.py").write_text("LIMIT = 8\n")
        calls = scripted(a, [reply(BLIND),  # the independent reader: before any code
                             reply("", tool=("write_file", {"path": "out.txt", "content": "41\n"})),
                             reply("Done."), reply("Fixed."), reply("ok"), reply("ok")])
        a.turn("write 42 to out.txt")
        first = users(a)[0]
        self.assertIn("independent reading of the request, written before any code", first)
        self.assertIn("across BOTH files together", first)
        check = next(u for u in users(a) if "before you finish" in u)
        self.assertIn("purr ran the checks an independent reader wrote", check)
        self.assertIn("FAIL 1: out.txt is not exactly 42", check)
        self.assertIn("SKIP 2", check)
        self.assertIn("proves nothing", check)
        self.assertGreaterEqual(calls["n"], 4)

    def test_the_reader_sees_the_files_not_a_solution(self):
        a = blind_agent()
        Path(a.root, "given.py").write_text("LIMIT = 8  # shapes across both buckets\n")
        prompts = []
        real = a._call

        def spy(messages=None, **kw):
            prompts.append(messages)
            return reply(BLIND) if len(prompts) == 1 else reply("done")
        a._call = spy
        a.turn("write 42 to out.txt")
        self.assertIn("given.py", prompts[0][0]["content"])
        self.assertIn("LIMIT = 8", prompts[0][0]["content"])
        self.assertTrue(callable(real))

    def test_api_models_and_chats_dont_get_it(self):
        a = blind_agent("api")
        scripted(a, [reply("", tool=("write_file", {"path": "out.txt", "content": "42\n"})), reply("Done."), reply("ok")])
        a.turn("write 42 to out.txt")
        self.assertNotIn("independent reading", users(a)[0])

    def test_a_reader_with_nothing_useful_changes_nothing(self):
        a = blind_agent()
        scripted(a, [reply("I can't help with that."),
                     reply("", tool=("write_file", {"path": "out.txt", "content": "42\n"})), reply("Done."), reply("ok")])
        a.turn("write 42 to out.txt")
        self.assertNotIn("independent reading", users(a)[0])
        self.assertFalse([u for u in users(a) if "independent reader wrote" in u])
