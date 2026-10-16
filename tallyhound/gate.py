"""Payment gate for an uploaded payment run: eight checks per line decide HOLD or RELEASE.

The checks compare the proposed run (payment_run.csv) with the uploaded vendor master, approvals and past payments.
Vendors are matched by vendor id (whose name must agree with the supplier), else by a name that matches exactly one
vendor. A check that needs a file which was not uploaded is skipped (counted as passed) and said so in the reason, so
a missing file never silently releases money without the reviewer knowing which checks ran.
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
    V = {r["vendor_id"].strip().upper(): r for _, r in rules.rows(files.get("vendors.csv", []))} \
        if files.get("vendors.csv") else None
    names: dict[str, list] = {}
    for r in (V or {}).values():
        names.setdefault(rules.norm_name(r["name"]), []).append(r)
    # invoice numbers compared as the rules compare them ("INV-0042" = "INV42"), and per vendor: two suppliers can
    # both send an invoice 1001
    A: dict[str, list] | None = None
    if files.get("approvals.csv"):
        A = {}
        for _, r in rules.rows(files["approvals.csv"]):
            if r["type"].strip().upper() in rules.INVOICE_TYPES and r["doc_no"].strip():
                A.setdefault(rules.inv_key(r["doc_no"]), []).append(r)
    P: dict[str, list] | None = None
    if files.get("payments.csv"):
        P = {}
        for _, r in rules.rows(files["payments.csv"]):
            if r["invoice_no"].strip():
                P.setdefault(rules.inv_key(r["invoice_no"]), []).append(
                    (r["vendor_id"].strip().upper(), rules.norm_name(r["supplier"])))
    skipped = sorted({n for n, ok in (("vendors.csv", V is not None), ("approvals.csv", A is not None),
                                      ("payments.csv", P is not None)) if not ok})
    out, used_lines = [], set()
    seen: dict[str, list] = {}           # invoice key -> (vendor id, names) of the lines already in this run
    for ln, r in rules.rows(run):
        inv, amt = r["invoice"].strip(), rules._f(r["amount"])
        sup = rules.norm_name(r["supplier"])
        extra: dict[int, str] = {}
        v = (V or {}).get(r["vendor_id"].strip().upper()) if r["vendor_id"].strip() else None
        if v is not None and rules.norm_name(v["name"]) != sup:
            extra[3] = f"Vendor {v['vendor_id']} is {v['name'].strip()}, not {r['supplier'].strip()}"
        if v is None and V is not None and not r["vendor_id"].strip():
            same = names.get(sup, []) if sup else []
            if len(same) == 1:
                v = same[0]
            elif len(same) > 1:
                extra[1] = (f"{r['supplier'].strip()} matches {len(same)} vendors "
                            f"({', '.join(x['vendor_id'] for x in same)}); add the vendor id to the run")
        key = rules.inv_key(inv)
        vid = v["vendor_id"].strip().upper() if v is not None else r["vendor_id"].strip().upper()
        known = {sup} | ({rules.norm_name(v["name"])} if v is not None else set())
        cands = (A or {}).get(key, []) if inv else []
        # the vendor's own record; listed twice (entered, then approved), the latest approved one
        mine = [x for x in cands if rules.norm_name(x["vendor"]) in known]
        a = next((x for x in reversed(mine) if x["approved_by"].strip()), mine[-1] if mine else cands[0] if cands else None)
        fail = []
        # a blank status reads as active, as in the vendor rules (4.1)
        status = v["status"].strip().upper() if v is not None else ""
        if V is not None and (v is None or (status and not status.startswith("ACTIVE"))):
            fail.append(1)
        if A is not None and (a is None or not a["approved_by"].strip()):
            fail.append(2)
        if (a is not None and rules.norm_name(a["vendor"]) != sup) or 3 in extra:
            fail.append(3)
        # paid to the same vendor: by vendor id when both have one, else by name
        if P is not None and inv and any(pid == vid if pid and vid else pname in known for pid, pname in P.get(key, [])):
            fail.append(4)
        # earlier in this run for the same vendor, compared the same way (one line may give the vendor id, one not)
        if any(oid == vid if oid and vid else bool(onames & known) for oid, onames in seen.get(key, [])):
            fail.append(5)
        seen.setdefault(key, []).append((vid, known))
        if amt <= 0:                                  # unreadable, zero or negative: nothing a payment run should send
            fail.append(6)
            extra[6] = f"Amount \"{r['amount'].strip()}\" is missing, zero or negative"
        elif a is not None and amt > rules._f(a["amount"]) + 0.005:
            fail.append(6)
        # verified as the vendor check reads it ("Y", "TRUE", "VERIFIED"), and a new vendor's first account is no change
        changed = v["bank_changed_on"].strip() if v is not None else ""
        if v is not None and changed and v["bank_verified"].strip().upper() not in rules.YES \
                and changed != v["created_on"].strip():
            fail.append(7)
        last4, on_file = r["bank_last4"].strip(), (v["bank_acct"].strip() if v is not None else "")
        if v is not None and last4 and on_file and on_file[-4:] != last4[-4:]:
            fail.append(8)
        reason = "; ".join(extra.get(c, REASONS[c]) for c in fail) if fail else (
            "All 8 checks passed" if (last4 and on_file) or v is None else
            "7 of 8 checks passed; the run gives no bank account, so it is paid to the account on file" if not last4
            else "7 of 8 checks passed; the vendor master has no bank account to compare with")
        if fail and v is not None and last4 and not on_file:
            reason += "; bank account not compared: the vendor master has none"
        if skipped:
            reason += f" (not checked: no {', '.join(skipped)})"
        line = str(r["line"]).strip() or str(ln - 1)
        if line in used_lines:                      # two rows with the same line number stay two separate rows
            line = f"{line} (row {ln - 1})"
        used_lines.add(line)
        out.append(dict(line=line, supplier=r["supplier"], invoice=inv, amount=amt,
                        decision="HOLD" if fail else "RELEASE", reason=reason,
                        failed_checks=",".join(map(str, fail)) or "-",
                        **{f"c{i}": "0" if i in fail else "1" for i in range(1, 9)}))
    return pd.DataFrame(out)
