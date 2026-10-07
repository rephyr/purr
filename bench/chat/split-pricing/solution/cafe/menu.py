"""What the café sells, and what an order costs."""

from .pricing import happy_hour_price

MENU = {
    "latte": 4.20,
    "matcha": 4.80,
    "cat cookie": 2.50,
    "tuna toastie": 6.90,
}


def total(order, hour=12):
    """Price of an order (a list of item names). Happy hour takes 20% off."""
    return round(happy_hour_price(sum(MENU[item] for item in order), hour), 2)
