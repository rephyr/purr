from .prices import calc_total


class Basket:
    def __init__(self):
        self.items = []

    def add(self, name, count=1):
        self.items.append((name, count))

    def total(self):
        return calc_total(self.items)
