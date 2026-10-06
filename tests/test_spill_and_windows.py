"""Cut or pruned output is kept in a file (the model was told "run it again" after it was gone),
and an edit shows its lines while older reads of the file are marked stale (small models).
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

from harness import tools  # noqa: E402
from tests.test_limits import agent, known, reply, scripted  # noqa: E402


class NoRipgrepTest(unittest.TestCase):
    """A fresh Mac or CI box has no rg: search and file lists fall back to plain Python."""

    def test_search_and_lists_without_rg(self):
        root = Path(tempfile.mkdtemp())
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / ".git").mkdir()
        (root / "src" / "shop.py").write_text("PRICE = 3\nprice = 4\n")
        (root / "tests" / "test_shop.py").write_text("price\n")
        (root / ".git" / "config").write_text("price\n")
        (root / "big.bin").write_bytes(b"price\0" * 10)
        with mock.patch.object(tools.shutil, "which", return_value=None):
            files = sorted(os.path.relpath(f, root) for f in tools.files_under(root))
            self.assertEqual(files, ["big.bin", "src/shop.py", "tests/test_shop.py"])  # no hidden folders
            code, hits = tools.search("price", root, glob="!tests")
            self.assertEqual((code, [h.replace(str(root) + "/", "") for h in hits]),
                             (0, ["src/shop.py:1:PRICE = 3", "src/shop.py:2:price = 4"]))  # smart case, no binary
            self.assertEqual(tools.search("PRICE", root / "src" / "shop.py"), (0, ["1:PRICE = 3"]))
            self.assertEqual(tools.search("nope", root)[0], 1)
            self.assertEqual(tools.search("x", root / "missing")[0], 2)
            self.assertEqual(tools.search("(", root)[0], 2)


class SpillTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(tools, "SPILL_DIR", Path(tempfile.mkdtemp()) / "purr-out")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_cut_result_names_the_file_with_all_of_it(self):
        a = agent()
        a.tools.trust_all = True
        out = a.tools.call("run", json.dumps({"command": "seq 1 200000"}))
        self.assertIn("the whole output is in", out)
        path = a.tools.last_spill
        self.assertTrue(path and "\n200000\n" in Path(path).read_text())
        self.assertIn(path, out)

    def test_pruned_output_is_kept_not_lost(self):
        a = agent()
        big = "line\n" * 2000
        for n in range(8):
            a.messages += [{"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{n}", "type": "function", "function": {"name": "run", "arguments": json.dumps({"command": f"make {n}"})}}]},
                {"role": "tool", "tool_call_id": f"c{n}", "content": big}]
        a._prune_old_tools(keep=2)
        stub = next(m["content"] for m in a.messages if m.get("tool_call_id") == "c0")
        self.assertIn("it's kept in", stub)
        self.assertNotIn("run it again", stub)
        kept = stub.split("it's kept in ")[1].split(":")[0]
        self.assertEqual(Path(kept).read_text(), big)

    def test_private_mode_keeps_nothing(self):
        a = agent()
        a.tools.is_private = lambda: True
        self.assertIsNone(a.tools.spill("run", "secret"))

    def test_the_folder_stays_small(self):
        a = agent()
        with mock.patch.object(tools, "SPILL_CAP", 3000):
            for _ in range(5):
                a.tools.step += 1
                a.tools.spill("run", "x" * 1000)
        self.assertLessEqual(sum(f.stat().st_size for f in tools.SPILL_DIR.iterdir()), 3000)


class EditWindowTest(unittest.TestCase):
    def test_a_small_model_sees_its_edit_and_its_old_reads_go_stale(self):
        a = agent("small")  # 32k context: edit windows on
        a.tools.trust_all = True
        Path(a.root, "app.py").write_text("".join(f"x{n} = {n}\n" for n in range(100)))
        scripted(a, [reply("", tool=("read_file", {"path": "app.py"})),
                     reply("", tool=("edit_file", {"path": "app.py", "old_text": "x50 = 50\n", "new_text": "x50 = 'fifty'\n"})),
                     reply("done")])
        a.turn("make x50 a word")
        results = [m["content"] for m in a.messages if m["role"] == "tool"]
        self.assertTrue(results[0].startswith("[an earlier read of app.py, taken out: you changed the file"))
        self.assertIn("now (lines", results[1])
        self.assertIn("   51\tx50 = 'fifty'", results[1])

    def test_a_big_model_keeps_things_as_they_were(self):
        a = agent("big")  # 1M context
        a.tools.trust_all = True
        Path(a.root, "app.py").write_text("a = 1\n")
        known(a)
        out = a.tools.call("edit_file", json.dumps({"path": "app.py", "old_text": "a = 1", "new_text": "a = 2"}))
        self.assertNotIn("now (lines", out)
