import csv
import re
from collections import defaultdict


def _date(text):
    text = text.strip()
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        y, mo = m.group(1), m.group(2)
    elif m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text):
        y, mo = m.group(3), m.group(2)
    elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):
        y, mo = m.group(3), m.group(1)
    else:
        raise ValueError(text)
    return f"{y}-{int(mo):02d}"


def _price(text):
    return float(text.replace("€", "").replace(" ", "").replace(",", "."))


def monthly_totals(path):
    totals = defaultdict(float)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("date") or not (row.get("qty") or "").strip():
                continue
            totals[_date(row["date"])] += int(row["qty"]) * _price(row["price"])
    return [(m, round(t, 2)) for m, t in sorted(totals.items())]
