import os
import unittest
from pathlib import Path

from sales import monthly_totals

HERE = Path(__file__).resolve().parent


class Check(unittest.TestCase):
    def test_the_tills_file(self):
        got = [(m, round(t, 2)) for m, t in monthly_totals("data/sales.csv")]
        self.assertEqual(got, [("2026-01", 30.30), ("2026-02", 61.10), ("2026-03", 28.10)])

    def test_another_file(self):
        # US dates, € with a space, a quote in a name, a blank line, a refund, an empty qty
        got = [(m, round(t, 2)) for m, t in monthly_totals(str(HERE / "hidden.csv"))]
        self.assertEqual(got, [("2025-12", 28.40), ("2026-04", 19.80)])
