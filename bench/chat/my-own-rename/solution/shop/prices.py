"""Prices for the yarn shop."""

PRICES = {"merino": 7.5, "cotton": 4.0, "mohair": 11.0, "wool": 6.0}


def calc_total(items):
    """items: list of (yarn name, number of balls)."""
    return round(sum(PRICES[name] * count for name, count in items), 2)


def price_list():
    """One line per yarn, cheapest first: "cotton 4.00"."""
    return [f"{name} {price:.2f}" for name, price in sorted(PRICES.items(), key=lambda item: item[1])]
