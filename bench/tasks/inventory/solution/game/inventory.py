from .items import ITEMS


class NotEnough(Exception):
    pass


class InventoryFull(Exception):
    pass


class Inventory:
    def __init__(self, size=8):
        self.size = size
        self.slots = [None] * size

    def add(self, item, count=1):
        if item not in ITEMS:
            raise KeyError(item)
        cap = ITEMS[item]["max_stack"]
        for i, slot in enumerate(self.slots):
            if count and slot and slot[0] == item and slot[1] < cap:
                put = min(cap - slot[1], count)
                self.slots[i] = (item, slot[1] + put)
                count -= put
        for i, slot in enumerate(self.slots):
            if count and slot is None:
                put = min(cap, count)
                self.slots[i] = (item, put)
                count -= put
        return count

    def remove(self, item, count=1):
        if self.count(item) < count:
            raise NotEnough(item)
        for i in reversed(range(self.size)):
            slot = self.slots[i]
            if count and slot and slot[0] == item:
                take = min(slot[1], count)
                self.slots[i] = (item, slot[1] - take) if slot[1] > take else None
                count -= take

    def count(self, item):
        return sum(s[1] for s in self.slots if s and s[0] == item)
