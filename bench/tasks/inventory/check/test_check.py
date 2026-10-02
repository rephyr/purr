import unittest

from game import inventory as inv_mod
from game.crafting import craft
from game.inventory import Inventory
from game.save import from_dict, to_dict


class Check(unittest.TestCase):
    def test_add_fills_stacks_then_slots(self):
        inv = Inventory(3)
        self.assertEqual(inv.add("apple", 7), 0)
        self.assertEqual(inv.add("wood", 3), 0)
        self.assertEqual(inv.add("apple", 8), 0)  # 3 top up the first stack, 5 go in slot 3
        self.assertEqual(inv.slots, [("apple", 10), ("wood", 3), ("apple", 5)])

    def test_add_returns_what_did_not_fit(self):
        inv = Inventory(2)
        self.assertEqual(inv.add("potion", 12), 2)
        self.assertEqual(inv.slots, [("potion", 5), ("potion", 5)])
        self.assertEqual(inv.add("sword"), 1)

    def test_unknown_item(self):
        with self.assertRaises(KeyError):
            Inventory(1).add("dragon")

    def test_remove_from_the_last_stack(self):
        inv = Inventory(3)
        inv.add("apple", 15)
        inv.remove("apple", 7)
        self.assertEqual(inv.slots, [("apple", 8), None, None])
        self.assertEqual(inv.count("apple"), 8)

    def test_remove_too_many_changes_nothing(self):
        inv = Inventory(2)
        inv.add("wood", 3)
        with self.assertRaises(inv_mod.NotEnough):
            inv.remove("wood", 4)
        self.assertEqual(inv.slots, [("wood", 3), None])

    def test_craft_uses_ingredients(self):
        inv = Inventory(4)
        inv.add("plank", 3)
        inv.add("stone", 2)
        craft(inv, "sword")
        self.assertEqual((inv.count("plank"), inv.count("stone"), inv.count("sword")), (1, 1, 1))

    def test_craft_missing_ingredient_is_all_or_nothing(self):
        inv = Inventory(4)
        inv.add("plank", 2)
        before = list(inv.slots)
        with self.assertRaises(inv_mod.NotEnough):
            craft(inv, "sword")
        self.assertEqual(inv.slots, before)

    def test_craft_when_the_result_does_not_fit(self):
        inv = Inventory(2)
        inv.add("apple", 10)
        inv.add("potion", 5)
        before = list(inv.slots)
        with self.assertRaises(inv_mod.InventoryFull):
            craft(inv, "potion")
        self.assertEqual(inv.slots, before)

    def test_save_round_trip_and_old_saves(self):
        inv = Inventory(3)
        inv.add("wood", 60)
        data = to_dict(inv)
        self.assertEqual(data["slots"], [["wood", 50], ["wood", 10], None])
        self.assertEqual(from_dict(data).slots, inv.slots)
        old = from_dict({"size": 2, "slots": ["sword", None]})
        self.assertEqual(old.slots, [("sword", 1), None])
