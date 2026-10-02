"""Recipes: what goes in, what comes out."""

RECIPES = {
    "plank": {"wood": 2},
    "sword": {"plank": 2, "stone": 1},
    "potion": {"apple": 3},
}


def craft(inv, recipe):
    """Use up the ingredients and add the result to the inventory."""
    for item, needed in RECIPES[recipe].items():
        for _ in range(needed):
            if not inv.remove(item):
                raise ValueError(f"not enough {item}")
    inv.add(recipe)
