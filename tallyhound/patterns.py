"""Patterns worth a look: first-digit (Benford) test, round amounts, and amounts bunched just under a limit.

These are not findings. They describe the whole population of amounts, so a person can decide whether to sample
deeper. Nothing here goes into Review, the workbook or the memo.
"""
from __future__ import annotations

import csv
import math

from . import rules

SOURCES = {"payments.csv": "paid_amount", "approvals.csv": "amount", "expenses.csv": "amount"}
BENFORD = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
MIN_BENFORD = 300          # below this a first-digit test is mostly noise
# Nigrini's mean-absolute-deviation bands for the first-digit test
MAD_BANDS = [(0.006, "close conformity"), (0.012, "acceptable conformity"), (0.015, "marginal conformity"),
             (math.inf, "nonconformity - worth a closer look")]


def amounts(files: dict[str, list[str]]) -> dict[str, list[float]]:
    """Positive amounts per file, from the files the checks read (after column matching)."""
    out = {}
    for name, col in SOURCES.items():
        lines = files.get(name)
        if lines and col in next(csv.reader([lines[0]])):
            vals = [rules._f(r[col]) for _, r in rules.rows(lines)]
            out[name] = [v for v in vals if v > 0]
    return out


def first_digit(v: float) -> int | None:
    if v <= 0 or not math.isfinite(v):
        return None
    s = f"{v:.10e}"                    # scientific notation: the first character is the first significant digit
    return int(s[0]) if s[0] in "123456789" else None


def benford(values: list[float]) -> dict:
    """Observed vs expected share of each first digit, the mean absolute deviation and what it means.
    usable is False when there are too few amounts, or they do not span two orders of magnitude."""
    digits = [d for d in (first_digit(v) for v in values if v >= 1) if d]
    n = len(digits)
    big = [v for v in values if v >= 1]
    span = math.log10(max(big) / min(big)) if big else 0.0
    obs = {d: (digits.count(d) / n if n else 0.0) for d in range(1, 10)}
    mad = sum(abs(obs[d] - BENFORD[d]) for d in range(1, 10)) / 9 if n else 0.0
    verdict = next(label for limit, label in MAD_BANDS if mad < limit)
    return dict(n=n, observed=obs, expected=dict(BENFORD), mad=round(mad, 4), verdict=verdict,
                usable=n >= MIN_BENFORD and span >= 2, span=round(span, 1))


def round_share(values: list[float], unit: float = 100.0, floor: float = 500.0) -> dict:
    """Share of amounts of at least `floor` that are whole multiples of `unit` (5,000.00 / 1,200.00). Invoices for
    goods rarely land on round numbers; estimates, advances and made-up amounts often do."""
    big = [v for v in values if v >= floor]
    rnd = [v for v in big if abs(v / unit - round(v / unit)) < 1e-9]
    return dict(n=len(big), round=len(rnd), share=len(rnd) / len(big) if big else 0.0)


def just_under(values: list[float], limit: float, band: float = 0.04) -> dict:
    """How many amounts sit in the band just under a limit, against the same-width band just above it. Many more
    below than above suggests amounts being steered under the limit."""
    lo, hi = limit * (1 - band), limit * (1 + band)
    below = sum(lo <= v < limit for v in values)
    above = sum(limit <= v < hi for v in values)
    return dict(limit=limit, below=below, above=above, flag=below >= 3 and below >= 3 * max(above, 1))


def summary(files: dict[str, list[str]], limits: dict) -> dict:
    a = amounts(files)
    every = [v for vs in a.values() for v in vs]
    pay = a.get("payments.csv", []) + a.get("approvals.csv", [])
    return dict(per_file={k: len(v) for k, v in a.items()}, benford=benford(every), round=round_share(pay),
                under=[dict(name=label, **just_under(vals, limits[key]))
                       for key, label, vals in (("po_limit", "purchase-order limit", a.get("approvals.csv", [])),
                                                ("director_limit", "director limit", a.get("approvals.csv", [])),
                                                ("receipt_limit", "receipt limit", a.get("expenses.csv", [])))])
