import unittest

from game.crafting import craft
from game.inventory import Inventory


class InventoryTest(unittest.TestCase):
    def test_add_and_count(self):
        inv = Inventory(2)
        self.assertTrue(inv.add("wood"))
        self.assertEqual(inv.count("wood"), 1)

    def test_craft_plank(self):
        inv = Inventory(4)
        inv.add("wood")
        inv.add("wood")
        craft(inv, "plank")
        self.assertEqual(inv.count("plank"), 1)
        self.assertEqual(inv.count("wood"), 0)
