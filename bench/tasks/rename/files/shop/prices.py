"""Prices for the yarn shop."""

YARN = {"merino": 7.5, "cotton": 4.0, "mohair": 11.0}


def calc_total(items):
    """items: list of (yarn name, number of balls)."""
    return round(sum(YARN[name] * count for name, count in items), 2)
