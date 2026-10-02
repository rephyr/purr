from .inventory import InventoryFull, NotEnough

RECIPES = {
    "plank": {"wood": 2},
    "sword": {"plank": 2, "stone": 1},
    "potion": {"apple": 3},
}


def craft(inv, recipe):
    for item, needed in RECIPES[recipe].items():
        if inv.count(item) < needed:
            raise NotEnough(item)
    before = list(inv.slots)
    for item, needed in RECIPES[recipe].items():
        inv.remove(item, needed)
    if inv.add(recipe, 1):
        inv.slots = before
        raise InventoryFull(recipe)
