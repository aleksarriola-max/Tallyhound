"""Learning from reviewers.

Suppressions: when a reviewer rejects a finding they can say "don't flag this again for this supplier/employee".
Later runs still find it, but it is set aside under "Suppressed" instead of landing in the review queue - nothing is
deleted, and the suppression list is visible and reversible.

Limit hints: when reviewers keep rejecting findings that are only just over a policy limit, the Policy page
suggests a new limit. A person decides whether to apply it.
"""
from __future__ import annotations

import csv
import re

import streamlit as st

ENTITY_COL = {"payments.csv": "supplier", "approvals.csv": "vendor", "vendors.csv": "vendor_id",
              "expenses.csv": "employee", "bank_statement.csv": "description"}
LIMIT_OF = {"1.1": "po_limit", "1.3": "director_limit", "6.1": "meal_limit", "6.2": "receipt_limit"}


def entity(source_file: str, evidence: str, header: str = "") -> str:
    """Who a finding is about: supplier, employee or vendor id - read from the evidence line."""
    col = ENTITY_COL.get(source_file)
    if col and header:
        head, vals = next(csv.reader([header])), next(csv.reader([evidence]))
        if col in head and len(vals) == len(head):
            return vals[head.index(col)].strip()
    m = re.match(r"\[(?:CONTRACT|INVOICE) \S+ \| ([^|]+?) \|", evidence)
    if m:
        return m.group(1).strip()
    return evidence.strip()[:60]


def suppress(clause: str, source_file: str, who: str, reason: str) -> None:
    S = st.session_state
    S.setdefault("suppressions", [])
    if not any(x["clause"] == clause and x["source_file"] == source_file and x["entity"] == who for x in S.suppressions):
        S.suppressions.append(dict(clause=clause, source_file=source_file, entity=who, reason=reason))


def unsuppress(i: int) -> None:
    S = st.session_state
    if 0 <= i < len(S.get("suppressions", [])):
        S.suppressions.pop(i)


def is_suppressed(clause: str, source_file: str, who: str) -> bool:
    return any(x["clause"] == clause and x["source_file"] == source_file and x["entity"] == who
               for x in st.session_state.get("suppressions", []))


def limit_hints(findings, decisions: dict, limits: dict) -> list[dict]:
    """Suggest a higher limit when two or more findings under one limit clause were rejected and all sit within
    25% of the limit."""
    out = []
    for clause, key in LIMIT_OF.items():
        rej = findings[(findings.clause.astype(str) == clause) &
                       findings.id.map(lambda i: decisions.get(i, {}).get("status") == "Rejected")]
        if len(rej) < 2:
            continue
        lim = float(limits[key])
        amts = [float(a) for a in rej.amount]
        if clause == "6.1":
            continue    # meal findings carry the claim total, not the per-person amount; no safe suggestion
        if all(lim < a <= lim * 1.25 for a in amts):
            out.append(dict(clause=clause, key=key, current=lim, suggested=round(max(amts) + 1, 0), n=len(rej)))
    return out
