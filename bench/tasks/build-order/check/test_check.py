"""What any build order must do: every target after what it depends on, cycles refused."""
import unittest

from build_order import CycleError, build_order


def valid(deps, order):
    names = set(deps) | {d for ds in deps.values() for d in ds}
    if sorted(order) != sorted(names):
        return False
    seen = set()
    for t in order:
        if any(d not in seen for d in deps.get(t, [])):
            return False
        seen.add(t)
    return True


class Check(unittest.TestCase):
    def test_pipeline_order_is_valid(self):
        deps = {"game.pck": ["scenes", "atlas"], "scenes": ["atlas", "fonts"], "atlas": ["textures"],
                "textures": [], "fonts": []}
        self.assertTrue(valid(deps, build_order(deps)))

    def test_targets_only_named_as_dependencies_are_built_too(self):
        deps = {"b": ["a", "c"], "d": ["b"]}
        self.assertTrue(valid(deps, build_order(deps)))

    def test_a_cycle_is_refused(self):
        with self.assertRaises(CycleError):
            build_order({"c": ["a"], "a": ["b"], "b": ["c"], "d": []})

    def test_a_cycle_behind_other_targets_is_refused(self):
        with self.assertRaises(CycleError):
            build_order({"app": ["lib"], "lib": ["util"], "util": ["mid"], "mid": ["lib"]})

    def test_depending_on_itself_is_a_cycle(self):
        with self.assertRaises(CycleError):
            build_order({"a": ["a"]})
