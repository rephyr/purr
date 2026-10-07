"""What the café sells, and what an order costs."""

MENU = {
    "latte": 4.20,
    "matcha": 4.80,
    "cat cookie": 2.50,
    "tuna toastie": 6.90,
}

HAPPY_HOUR = range(15, 17)  # 15:00-16:59
HAPPY_HOUR_DISCOUNT = 0.20  # 20% off


def total(order, hour=12):
    """Price of an order (a list of item names). Happy hour takes 20% off."""
    price = sum(MENU[item] for item in order)
    if hour in HAPPY_HOUR:
        price = price * (1 - HAPPY_HOUR_DISCOUNT)
    return round(price, 2)
