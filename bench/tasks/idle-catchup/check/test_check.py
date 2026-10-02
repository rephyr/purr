"""Hidden checks for idle-catchup (both prompts).

With the game's own numbers (0.1, 0.05 ...) these only compare one way of splitting time with
another, so any exact approach passes (fractions, integer micro-units, decimals). Exact coin
counts are only checked with numbers that are exact in binary (0.5, 2, ...), where every
correct approach agrees.
"""

import random
import unittest

from pawtime import Pet

CLOSE = 1e-9


def tiny(**over):
    """Small numbers that are exact in binary, so the right answers are exact too."""
    data = {"floor": 0, "drain_per_second": {"food": 0.5, "mood": 0},
            "buffs": [{"id": "full_tummy", "stat": "food", "above": 60, "x": 2}],
            "income_per_second": 1,
            "errands": {"walk": {"seconds": 2, "reward": 10}, "hop": {"seconds": 3, "reward": 1}}}
    data.update(over)
    return data


def same(test, a, b, events_a=None, events_b=None):
    test.assertEqual(a.coins, b.coins)
    test.assertAlmostEqual(a.carry, b.carry, delta=CLOSE)
    test.assertAlmostEqual(a.food, b.food, delta=CLOSE)
    test.assertAlmostEqual(a.mood, b.mood, delta=CLOSE)
    test.assertAlmostEqual(a.clock, b.clock, delta=CLOSE)
    test.assertEqual(a.errands, b.errands)
    test.assertAlmostEqual(a.errand_time, b.errand_time, delta=1e-6)
    if events_a is not None:
        test.assertEqual([e[:2] for e in events_a], [e[:2] for e in events_b])
        for x, y in zip(events_a, events_b):
            test.assertAlmostEqual(x[2], y[2], delta=1e-6)


def busy_pet():
    pet = Pet(food=70, mood=75)  # food's buff ends near 200 s, mood's near 250 s
    for e in ("fetch_acorn", "chase_butterfly", "nap_in_sunbeam", "fetch_acorn"):
        pet.add_errand(e)
    return pet


class Check(unittest.TestCase):
    def test_crumbs_add_up_to_whole_coins(self):
        one, many = Pet(food=50, mood=50), Pet(food=50, mood=50)
        one.advance(10)
        for _ in range(80):
            many.advance(0.125)
        self.assertEqual((one.coins, many.coins), (1, 1))
        self.assertAlmostEqual(one.carry, many.carry, delta=CLOSE)

    def test_one_big_step_equals_frame_by_frame(self):
        slept, live = busy_pet(), busy_pet()
        events_slept = slept.advance(600)
        events_live = []
        for _ in range(600 * 32):
            events_live += live.advance(1 / 32)
        same(self, slept, live, events_slept, events_live)
        self.assertEqual([e[:2] for e in events_slept],
                         [("errand_done", "fetch_acorn"), ("errand_done", "chase_butterfly"),
                          ("buff_off", "full_tummy"), ("buff_off", "happy"), ("errand_done", "nap_in_sunbeam"),
                          ("errand_done", "fetch_acorn")])

    def test_any_split_gives_the_same_result(self):
        whole = busy_pet()
        events_whole = whole.advance(1200)
        for seed in (1, 2, 3):
            rng, pet, events, left = random.Random(seed), busy_pet(), [], 1200 * 64
            while left:
                k = min(left, rng.randint(1, 3000))
                events += pet.advance(k / 64)
                left -= k
            same(self, whole, pet, events_whole, events)

    def test_a_buff_ends_in_the_middle_of_a_step(self):
        pet = Pet(food=61, mood=0, data=tiny())
        events = pet.advance(4)
        self.assertEqual(events, [("buff_off", "full_tummy", 2.0)])
        self.assertEqual(pet.coins, 6)  # 2 s at x2, then 2 s at x1
        self.assertAlmostEqual(pet.food, 59.0, delta=CLOSE)

    def test_an_errand_done_as_the_buff_ends_gets_no_buff(self):
        pet = Pet(food=61, mood=0, data=tiny())
        pet.add_errand("walk")  # done at 2 s, the moment food reaches 60
        events = pet.advance(4)
        self.assertEqual(events, [("buff_off", "full_tummy", 2.0), ("errand_done", "walk", 2.0)])
        self.assertEqual(pet.coins, 16)  # 4 + 2 from income, and 10 (not 20) for the walk

    def test_errands_follow_each_other_without_losing_time(self):
        pet = Pet(food=0, mood=0, data=tiny())
        for _ in range(3):
            pet.add_errand("hop")
        self.assertEqual(pet.advance(7.5), [("errand_done", "hop", 3.0), ("errand_done", "hop", 6.0)])
        self.assertAlmostEqual(pet.errand_time, 1.5, delta=CLOSE)
        self.assertEqual(pet.advance(1.5), [("errand_done", "hop", 9.0)])

    def test_draining_never_raises_a_stat(self):
        pet = Pet(food=10, mood=15)  # already under the floor (20)
        pet.advance(100)
        self.assertEqual((pet.food, pet.mood), (10, 15))

    def test_at_the_line_a_buff_is_off(self):
        pet = Pet(food=60, mood=0, data=tiny(drain_per_second={"food": 0, "mood": 0}))
        pet.advance(10)
        self.assertEqual(pet.coins, 10)

    def test_a_line_under_the_floor_is_never_reached(self):
        data = tiny(floor=20, drain_per_second={"food": 1, "mood": 0},
                    buffs=[{"id": "full_tummy", "stat": "food", "above": 10, "x": 2}])
        pet = Pet(food=50, mood=0, data=data)
        self.assertEqual(pet.advance(100), [])
        self.assertEqual((pet.coins, pet.food), (200, 20))

    def test_closed_time_doesnt_count(self):
        pet, fresh = busy_pet(), busy_pet()
        pet.advance(100)
        pet.close()
        self.assertEqual(pet.advance(5000), [])
        pet.open()
        pet.advance(100)
        fresh.advance(200)
        same(self, pet, fresh)


if __name__ == "__main__":
    unittest.main()
