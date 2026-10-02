import unittest

from pawtime import Pet


class PetTest(unittest.TestCase):
    def test_feeding_tops_out_at_100(self):
        pet = Pet(food=90)
        pet.feed(30)
        self.assertEqual(pet.food, 100)

    def test_earns_without_buffs(self):
        pet = Pet(food=50, mood=50)  # below both lines: no buffs
        pet.advance(100)
        self.assertEqual(pet.coins, 10)  # 0.1 coins a second

    def test_closed_pets_dont_change(self):
        pet = Pet()
        pet.close()
        self.assertEqual(pet.advance(500), [])
        self.assertEqual((pet.coins, pet.food, pet.clock), (0, 100, 0))

    def test_an_errand_pays(self):
        pet = Pet(food=50, mood=50)
        pet.add_errand("fetch_acorn")
        events = pet.advance(45)
        self.assertEqual(events, [("errand_done", "fetch_acorn", 45)])

    def test_catch_up_after_sleep_matches_live_play(self):
        live, slept = Pet(), Pet()
        for p in (live, slept):
            p.add_errand("fetch_acorn")
            p.add_errand("chase_butterfly")
        for _ in range(60 * 10):
            live.advance(1)  # a second at a time
        slept.advance(600)  # ten minutes in one go
        self.assertEqual(live.coins, slept.coins)
        self.assertEqual(live.errands, slept.errands)


if __name__ == "__main__":
    unittest.main()
