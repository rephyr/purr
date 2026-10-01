"""Turns an order into a cute receipt."""

from .menu import MENU, total


def receipt(order, hour=12):
    lines = ["~ cat café ~", ""]
    for item in order:
        lines.append(f"{item:<16}{MENU[item]:>6.2f}")
    lines.append("-" * 22)
    lines.append(f"{'total':<16}{total(order, hour):>6.2f}")
    return "\n".join(lines)
