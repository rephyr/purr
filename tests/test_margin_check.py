"""The margin check: the final reply's MEASURE lines against the limits, backed by commands purr saw
run (runs stopped at 0.497 vs 0.5, 1.2x vs 1.05x, 0.595 vs 0.62 with time left).

Run: python3 -m unittest discover -s tests -t .
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import checks  # noqa: E402
from tests.test_bench_fixes import one_shot, users  # noqa: E402
from tests.test_limits import reply, scripted  # noqa: E402


class LimitWordsTest(unittest.TestCase):
    def test_limits_in_real_requests(self):
        found = checks.stated_limits("Train a fasttext model with accuracy at least 0.62 on the test set. "
                                     "The model file must be no more than 150MB. sol.sql must be no more than "
                                     "1.05 times as slow as the reference.")
        self.assertEqual([(op, v) for _, op, v in found], [(">=", 0.62), ("<=", 150.0), ("<=", 1.05)])
        self.assertEqual(checks.stated_limits("Write hello to out.txt and commit it."), [])

    def test_measure_lines_and_margins(self):
        lines = checks.measures("Done.\nMEASURE accuracy | 0.611 | >= 0.62 | python3 eval.py\n"
                                "MEASURE size | 120MB | <= 150 MB | du -m model.bin\n")
        self.assertEqual([(m["name"], m["value"], m["op"], m["target"]) for m in lines],
                         [("accuracy", 0.611, ">=", 0.62), ("size", 120.0, "<=", 150.0)])
        self.assertEqual(checks.margin(0.611, ">=", 0.62)[0], False)
        ok, room = checks.margin(1.04, "<=", 1.05)
        self.assertTrue(ok)
        self.assertLess(room, 0.02)  # met, at the edge


def run_and_finish(final, *, model="small", out="accuracy: 0.6400", **config):
    a = one_shot(model, time_limit=900)
    a.config = dict(a.config, **config)
    scripted(a, [reply("", tool=("write_file", {"path": "train.py", "content": "print(1)\n"})),
                 reply("", tool=("run", {"command": f"echo '{out}'"})),
                 reply("Done."), reply(final), reply("Fixed."), reply("ok")])
    a.turn("Train a model with accuracy at least 0.62.")
    return a, [u for u in users(a) if u.startswith("(purr compared your measurements")]


class MarginFlowTest(unittest.TestCase):
    def test_the_final_check_asks_for_measure_lines(self):
        a, _ = run_and_finish("MEASURE accuracy | 0.64 | >= 0.62 | echo 'accuracy: 0.6400'")
        check = next(u for u in users(a) if "before you finish" in u)
        self.assertIn("MEASURE name | value | op target | command", check)
        self.assertTrue(check.rstrip().endswith("value.)"))

    def test_a_backed_measurement_with_room_just_finishes(self):
        _, notes = run_and_finish("Done.\nMEASURE accuracy | 0.64 | >= 0.62 | echo 'accuracy: 0.6400'")
        self.assertEqual(notes, [])

    def test_a_miss_or_the_edge_goes_back(self):
        _, notes = run_and_finish("MEASURE accuracy | 0.611 | >= 0.62 | echo 'accuracy: 0.611'", out="accuracy: 0.611")
        self.assertIn("MISSES it", notes[0])
        _, notes = run_and_finish("MEASURE accuracy | 0.621 | >= 0.62 | echo 'accuracy: 0.621'", out="accuracy: 0.621")
        self.assertIn("at the edge", notes[0])

    def test_a_number_no_command_showed_is_not_backed(self):
        _, notes = run_and_finish("MEASURE accuracy | 0.70 | >= 0.62 | echo 'accuracy: 0.6400'")
        self.assertIn("its output does not show 0.7", notes[0])
        _, notes = run_and_finish("MEASURE accuracy | 0.70 | >= 0.62 | python3 eval.py")
        self.assertIn("was never run", notes[0])

    def test_a_stated_limit_left_unmeasured_goes_back(self):
        _, notes = run_and_finish("All done.")
        self.assertIn("accuracy at least 0.62", notes[0])
        self.assertIn("no MEASURE line", notes[0])

    def test_api_models_get_it_too_unless_turned_off(self):
        _, notes = run_and_finish("All done.", model="api")  # cheap: every model gets it
        self.assertIn("no MEASURE line", notes[0])
        a, notes = run_and_finish("All done.", model="api", margin_check=False)
        self.assertEqual(notes, [])
        self.assertFalse(any("MEASURE name" in u for u in users(a)))


if __name__ == "__main__":
    unittest.main()
