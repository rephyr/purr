from .inventory import Inventory


def to_dict(inv):
    return {"size": inv.size, "slots": [list(s) if s else None for s in inv.slots]}


def from_dict(data):
    inv = Inventory(data["size"])
    inv.slots = [None if s is None else (s, 1) if isinstance(s, str) else (s[0], s[1]) for s in data["slots"]]
    return inv
