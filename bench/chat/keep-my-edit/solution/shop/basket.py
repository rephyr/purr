from .prices import order_total


class Basket:
    def __init__(self):
        self.items = []

    def add(self, name, count=1):
        self.items.append((name, count))

    def total(self):
        return order_total(self.items)
