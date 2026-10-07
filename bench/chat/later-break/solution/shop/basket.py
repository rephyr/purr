from .prices import calc_total


class Basket:
    def __init__(self):
        self.items = {}  # yarn name -> balls

    def add(self, name, count=1):
        self.items[name] = self.items.get(name, 0) + count

    def remove(self, name, count=1):
        if self.items.get(name, 0) < count:
            raise ValueError(f"there are only {self.items.get(name, 0)} balls of {name}")
        self.items[name] -= count
        if not self.items[name]:
            del self.items[name]

    def total(self):
        return calc_total(self.items.items())
