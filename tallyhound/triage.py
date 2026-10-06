"""Keeping the review queue worth a person's time.

- Cases: findings that share a line (same row, or one row's evidence supports another) are one case, decided once.
- Priority: severity x money at stake x the Skeptic's confidence, so the top of the list is what matters.
- Minor items: below the materiality threshold (and not High), grouped at the bottom instead of mixed in.
- Rule health: each rule's reject rate from real reviewer decisions. A rule rejected most of the time is demoted
  to minor items automatically (never deleted), and an admin is told under Settings > Rules.
- Shadow mode: rules listed as "shadow" run and record what they would flag, but stay out of the queue until a
  person has marked enough of their findings to show they are worth it.
"""
from __future__ import annotations

import math

import pandas as pd
import streamlit as st

SEV_W = {"High": 3.0, "Medium": 2.0, "Low": 1.0}
DEMOTE_MIN_DECISIONS = 5
DEMOTE_REJECT_RATE = 0.6
PROMOTE_MIN_MARKS = 5
PROMOTE_PRECISION = 0.8
CLEARS = {
    "5.1": "a credit note or agreed price change for the difference exists",
    "5.2": "the first payment was returned or reversed",
    "5.3": "an approved prepayment agreement exists",
    "5.4": "the bank instruction was sent on a working day",
    "5.5": "the bank line is matched to an approved payment (or is a known non-supplier debit)",
    "1.1": "a purchase order dated before the invoice exists, or the vendor is PO-exempt",
    "1.2": "a second, authorised person re-approves it",
    "1.3": "a director's approval is recorded",
    "1.4": "the invoices are for separate, unrelated jobs",
    "1.5": "the approver was authorised on that date",
    "4.1": "the vendor was active when the payment was agreed",
    "4.2": "the records are the same supplier's remit-to address, or a parent and subsidiary",
    "4.3": "a call-back verification of the new bank details is recorded",
    "4.4": "a W-9 or W-8 form is on file",
    "6.1": "the attendee list shows enough people",
    "6.2": "the receipt is provided",
    "6.3": "the item had a business purpose",
    "6.4": "the business reason is recorded",
    "6.5": "the receipt was split between the two claims",
    "7.1": "a signed variation allows the charge",
    "7.2": "a rate change was agreed in writing",
    "7.3": "a written extension covers the period",
    "7.4": "a decision to keep the contract is recorded",
    "8.1": "the invoice matches an approved record",
    "8.2": "the new bank details were verified with the supplier",
    "8.3": "the second copy is marked as a duplicate and not paid",
}


def priority(r) -> float:
    conf = {"Confirmed": 1.0, "Doubtful": 0.5}.get(str(r.skeptic_verdict), 0.8)
    try:
        amt = float(r.amount)
    except (TypeError, ValueError):
        amt = 0.0
    amt = amt if math.isfinite(amt) else 0.0
    return round(SEV_W.get(r.severity, 1.0) * (1 + math.log10(max(amt, 1.0))) * conf, 2)


def cases(f: pd.DataFrame) -> list[list]:
    """Group findings into cases: two findings belong together when they point at a shared line of the same file."""
    rows = list(f.itertuples())
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner = {}
    for i, r in enumerate(rows):
        for ln in set(r.matched_lines) | {int(r.line_number)}:
            k = (r.source_file, ln)
            if k in owner:
                parent[find(i)] = find(owner[k])
            else:
                owner[k] = i
    groups: dict[int, list] = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    out = [sorted(g, key=lambda r: (-SEV_W.get(r.severity, 1), -float(r.amount))) for g in groups.values()]
    return sorted(out, key=lambda g: -max(priority(r) for r in g))


# ---------------------------------------------------------------- rule health from real decisions
def rule_health() -> pd.DataFrame:
    """Per clause: how many of its findings reviewers approved and rejected, across every dataset this browser has."""
    S = st.session_state
    stats: dict[str, list[int]] = {}
    sets = [(S.get("dataset"), S.decisions)] + [(k, v.get("decisions", {})) for k, v in S.get("by_dataset", {}).items()
                                                if k != (S.get("dataset") or "__sample__")]
    for label, decisions in sets:
        recs = S.get("custom", {}).get(label) if label and label != "__sample__" else None
        if not recs:
            continue
        clause_of = {r["id"]: str(r["clause"]) for r in recs}
        for fid, d in decisions.items():
            c = clause_of.get(fid)
            if c:
                s = stats.setdefault(c, [0, 0])
                s[0 if d.get("status") == "Approved" else 1] += 1
    rows = []
    for c, (a, r) in sorted(stats.items()):
        n = a + r
        rows.append(dict(Clause=c, Approved=a, Rejected=r, Decisions=n, **{"Reject rate": r / n if n else 0.0},
                         Status="Demoted" if demoted(c, a, r) else "Normal"))
    return pd.DataFrame(rows, columns=["Clause", "Approved", "Rejected", "Decisions", "Reject rate", "Status"])


def demoted(clause: str, approved: int | None = None, rejected: int | None = None) -> bool:
    S = st.session_state
    if S.get("rule_override", {}).get(str(clause)) == "normal":
        return False
    if approved is None:
        h = S.get("_health", {})
        approved, rejected = h.get(str(clause), (0, 0))
    n = approved + rejected
    return n >= DEMOTE_MIN_DECISIONS and rejected / n >= DEMOTE_REJECT_RATE


def refresh_health() -> None:
    df = rule_health()
    st.session_state["_health"] = {r.Clause: (r.Approved, r.Rejected) for r in df.itertuples()}


# ---------------------------------------------------------------- shadow mode
def shadow_clauses() -> set[str]:
    return set(st.session_state.get("shadow", []))


def shadow_precision(clause: str) -> tuple[int, float]:
    marks = [v for k, v in st.session_state.get("shadow_marks", {}).items() if k.split("|")[0] == clause]
    return len(marks), (sum(marks) / len(marks) if marks else 0.0)


def ready_to_promote(clause: str) -> bool:
    n, p = shadow_precision(clause)
    return n >= PROMOTE_MIN_MARKS and p >= PROMOTE_PRECISION
