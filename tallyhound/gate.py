"""Payment gate for an uploaded payment run: eight checks per line decide HOLD or RELEASE.

The checks compare the proposed run (payment_run.csv) with the uploaded vendor master, approvals and past payments.
A check that needs a file which was not uploaded is skipped (counted as passed) and said so in the reason, so a
missing file never silently releases money without the reviewer knowing which checks ran.
"""
from __future__ import annotations

import pandas as pd

from . import rules

CHECKS = ["Vendor active", "Invoice open and approved", "Vendor matches invoice", "Not already paid",
          "Not duplicated in run", "Amount within invoice", "Bank change verified", "Bank matches master"]
REASONS = {1: "Vendor is not active or not in the vendor master", 2: "Invoice is not in approvals or not approved",
           3: "Invoice belongs to another vendor", 4: "Invoice already paid", 5: "Same invoice earlier in this run",
           6: "Amount is more than the approved invoice", 7: "Bank account changed with no verification",
           8: "Bank account differs from the vendor master"}


def evaluate(files: dict[str, list[str]]) -> pd.DataFrame:
    run = files.get("payment_run.csv")
    if not run or rules.missing_columns("payment_run.csv", run):
        return pd.DataFrame()
    V = {r["vendor_id"].strip(): r for _, r in rules.rows(files.get("vendors.csv", []))} if files.get("vendors.csv") else None
    names = {r["name"].strip().lower(): r for r in (V or {}).values()}
    A = {r["doc_no"].strip().lower(): r for _, r in rules.rows(files.get("approvals.csv", []))
         if r["type"].strip().upper() in ("", "INVOICE")} if files.get("approvals.csv") else None
    P = {r["invoice_no"].strip().lower() for _, r in rules.rows(files.get("payments.csv", []))
         if r["invoice_no"].strip()} if files.get("payments.csv") else None
    skipped = sorted({n for n, ok in (("vendors.csv", V is not None), ("approvals.csv", A is not None),
                                      ("payments.csv", P is not None)) if not ok})
    out, seen = [], set()
    for ln, r in rules.rows(run):
        inv, amt = r["invoice"].strip(), rules._f(r["amount"])
        v = (V or {}).get(r["vendor_id"].strip()) or names.get(r["supplier"].strip().lower())
        a = (A or {}).get(inv.lower())
        fail = []
        if V is not None and (v is None or v["status"].strip().upper() != "ACTIVE"):
            fail.append(1)
        if A is not None and (a is None or not a["approved_by"].strip()):
            fail.append(2)
        if a is not None and a["vendor"].strip().lower() != r["supplier"].strip().lower():
            fail.append(3)
        if P is not None and inv.lower() in P:
            fail.append(4)
        if inv.lower() in seen:
            fail.append(5)
        seen.add(inv.lower())
        if a is not None and amt > rules._f(a["amount"]) + 0.005:
            fail.append(6)
        if v is not None and v["bank_changed_on"].strip() and v["bank_verified"].strip().upper() != "YES":
            fail.append(7)
        last4 = r["bank_last4"].strip()
        if v is not None and last4 and v["bank_acct"].strip()[-4:] != last4[-4:]:
            fail.append(8)
        reason = "; ".join(REASONS[c] for c in fail) if fail else f"All {8} checks passed"
        if skipped:
            reason += f" (not checked: no {', '.join(skipped)})"
        out.append(dict(line=str(r["line"] or ln - 1), supplier=r["supplier"], invoice=inv, amount=amt,
                        decision="HOLD" if fail else "RELEASE", reason=reason,
                        failed_checks=",".join(map(str, fail)) or "-",
                        **{f"c{i}": "0" if i in fail else "1" for i in range(1, 9)}))
    return pd.DataFrame(out)
