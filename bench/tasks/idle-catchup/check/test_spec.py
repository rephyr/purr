"""Only the clear prompt asks for saves that carry on exactly."""

import json
import unittest

from pawtime import Pet

from .test_check import CLOSE, busy_pet, same


class Spec(unittest.TestCase):
    def test_a_save_carries_on_exactly(self):
        saved, never = busy_pet(), busy_pet()
        saved.advance(123.375)
        back = Pet.load(json.loads(json.dumps(saved.save())))
        back.advance(500)
        never.advance(623.375)
        same(self, back, never)

    def test_a_save_keeps_the_errand_going(self):
        pet = Pet(food=50, mood=50)
        pet.add_errand("fetch_acorn")
        pet.advance(30)
        back = Pet.load(json.loads(json.dumps(pet.save())))
        self.assertEqual(back.advance(15), [("errand_done", "fetch_acorn", 45.0)])
        self.assertAlmostEqual(back.carry, 0.5, delta=CLOSE)  # 4.5 coins from income, plus 7
