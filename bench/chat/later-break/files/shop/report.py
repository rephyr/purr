def packing_list(basket):
    return [f"{count} x {name}" for name, count in basket.items]
