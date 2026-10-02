"""The player's bag: a fixed number of slots. Right now every slot holds one item."""

from .items import ITEMS


class Inventory:
    def __init__(self, size=8):
        self.size = size
        self.slots = [None] * size  # an item id, or None for an empty slot

    def add(self, item):
        """Put one item in the first empty slot. Returns False when the bag is full."""
        if item not in ITEMS:
            raise KeyError(item)
        for i, slot in enumerate(self.slots):
            if slot is None:
                self.slots[i] = item
                return True
        return False

    def remove(self, item):
        """Take one item out. Returns False when there isn't one."""
        for i, slot in enumerate(self.slots):
            if slot == item:
                self.slots[i] = None
                return True
        return False

    def count(self, item):
        return sum(1 for slot in self.slots if slot == item)
