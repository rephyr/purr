"""Only the clear prompt says these: alphabetical ties, the exact cycle, tests written."""
import unittest

from build_order import CycleError, build_order


class Spec(unittest.TestCase):
    def test_pipeline(self):
        deps = {"game.pck": ["scenes", "atlas"], "scenes": ["atlas", "fonts"], "atlas": ["textures"],
                "textures": [], "fonts": []}
        self.assertEqual(build_order(deps), ["fonts", "textures", "atlas", "scenes", "game.pck"])

    def test_targets_only_named_as_dependencies(self):
        self.assertEqual(build_order({"b": ["a", "c"]}), ["a", "c", "b"])

    def test_alphabetical_when_free(self):
        self.assertEqual(build_order({"z": [], "y": [], "x": ["z"]}), ["y", "z", "x"])

    def test_empty(self):
        self.assertEqual(build_order({}), [])

    def test_cycle_is_reported_in_order(self):
        with self.assertRaises(CycleError) as e:
            build_order({"c": ["a"], "a": ["b"], "b": ["c"], "d": []})
        self.assertEqual(e.exception.cycle, ["a", "b", "c"])

    def test_cycle_behind_other_targets(self):
        with self.assertRaises(CycleError) as e:
            build_order({"app": ["lib"], "lib": ["util"], "util": ["mid"], "mid": ["lib"], "z": []})
        self.assertEqual(e.exception.cycle, ["lib", "util", "mid"])

    def test_self_dependency(self):
        with self.assertRaises(CycleError) as e:
            build_order({"a": ["a"]})
        self.assertEqual(e.exception.cycle, ["a"])

    def test_they_wrote_tests(self):
        import os
        self.assertTrue(os.path.exists("test_build_order.py"))
