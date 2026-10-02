"""Monthly sales report for the café. The till exports a messy CSV: see data/sales.csv."""


def monthly_totals(path):
    """[("2026-01", 123.45), ...] for the CSV at path. Not written yet."""
    raise NotImplementedError


if __name__ == "__main__":
    for month, total in monthly_totals("data/sales.csv"):
        print(f"{month}  {total:>10.2f}")
