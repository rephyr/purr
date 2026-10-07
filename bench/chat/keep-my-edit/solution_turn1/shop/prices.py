"""Prices for the yarn shop."""

YARN = {"merino": 7.5, "cotton": 4.0, "mohair": 11.0}
BULK_BALLS = 10       # balls in an order, all yarns together, for the bulk discount
BULK_DISCOUNT = 0.10


def calc_total(items):
    """items: list of (yarn name, number of balls). 10 or more balls in all: 10% off."""
    price = sum(YARN[name] * count for name, count in items)
    if sum(count for _, count in items) >= BULK_BALLS:
        price *= 1 - BULK_DISCOUNT
    return round(price, 2)
