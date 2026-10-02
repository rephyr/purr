"""Saving the inventory into the save file (as plain data) and loading it back."""

from .inventory import Inventory


def to_dict(inv):
    return {"size": inv.size, "slots": list(inv.slots)}


def from_dict(data):
    inv = Inventory(data["size"])
    inv.slots = list(data["slots"])
    return inv
