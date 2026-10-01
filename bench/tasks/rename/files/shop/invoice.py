from . import prices


def invoice(items, customer):
    lines = [f"invoice for {customer}"]
    for name, count in items:
        lines.append(f"{count} x {name}")
    lines.append(f"total: {prices.calc_total(items):.2f}")
    return "\n".join(lines)
