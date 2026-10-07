"""Turns an order into a cute receipt."""

from . import pricing
from .menu import MENU, total


def receipt(order, hour=12):
    lines = ["~ cat café ~", ""]
    for item in order:
        lines.append(f"{item:<16}{MENU[item]:>6.2f}")
    lines.append("-" * 22)
    if hour in pricing.HAPPY_HOUR:
        lines.append(f"happy hour -{round(pricing.HAPPY_HOUR_DISCOUNT * 100)}%")
    lines.append(f"{'total':<16}{total(order, hour):>6.2f}")
    return "\n".join(lines)
