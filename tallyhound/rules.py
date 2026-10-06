"""Built-in rule checks: fixed, repeatable tests of the audit policy. No AI involved.

Each check returns Hit objects that point at exact lines of the uploaded files. Nothing here decides anything;
every hit is only a proposal for a person to review.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date

FILES = {"Payments": "payments.csv", "Approvals": "approvals.csv", "Vendors": "vendors.csv",
         "Contracts": "contracts.txt", "Expenses": "expenses.csv"}
REQUIRED = {
    "payments.csv": ["payment_id", "pay_date", "invoice_date", "supplier", "invoice_no", "invoice_amount", "paid_amount"],
    "approvals.csv": ["record_id", "doc_no", "date", "vendor", "amount", "requested_by", "approved_by", "approver_role", "po_no"],
    "vendors.csv": ["vendor_id", "name", "tax_id", "status", "bank_changed_on", "bank_verified", "w9_on_file", "last_paid_on",
                    "last_paid_amount"],
    "expenses.csv": ["claim_id", "date", "employee", "category", "amount", "receipt_ref", "notes", "people"],
}


@dataclass
class Hit:
    area: str
    clause: str
    severity: str
    amount: float
    title: str
    source_file: str
    line_number: int
    related: list[tuple[str, int]] = field(default_factory=list)   # (file, line) of supporting lines


def rows(lines: list[str]) -> list[tuple[int, dict]]:
    """(1-based line number, row dict) for every data row of a CSV given as exact lines."""
    if not lines:
        return []
    head = next(csv.reader([lines[0]]))
    out = []
    for i, ln in enumerate(lines[1:], start=2):
        if not ln.strip():
            continue
        vals = next(csv.reader([ln]))
        out.append((i, dict(zip(head, vals + [""] * (len(head) - len(vals))))))
    return out


def missing_columns(name: str, lines: list[str]) -> list[str]:
    if name not in REQUIRED or not lines:
        return []
    head = set(next(csv.reader([lines[0]])))
    return [c for c in REQUIRED[name] if c not in head]


def _d(s: str) -> date | None:
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


def _f(s: str) -> float:
    try:
        return float(str(s).replace(",", "").replace("$", ""))
    except ValueError:
        return 0.0


def payments(lines: list[str]) -> list[Hit]:
    R, out, seen = rows(lines), [], {}
    for ln, r in R:
        key = (r["supplier"].strip().lower(), r["invoice_no"].strip().lower())
        if r["invoice_no"].strip() and key in seen:
            first = seen[key]
            out.append(Hit("Payments", "5.2", "High", _f(r["paid_amount"]),
                           f"{r['invoice_no'].upper()} was paid more than once", "payments.csv", ln, [("payments.csv", first)]))
        else:
            seen.setdefault(key, ln)
        pa, ia = _f(r["paid_amount"]), _f(r["invoice_amount"])
        if r["invoice_no"].strip() and abs(pa - ia) > 0.005:
            out.append(Hit("Payments", "5.1", "Medium", round(abs(pa - ia), 2),
                           f"{r['invoice_no']} paid ${pa:,.2f} against an invoice of ${ia:,.2f}", "payments.csv", ln))
        pd_, idt = _d(r["pay_date"]), _d(r["invoice_date"])
        if not r["invoice_no"].strip():
            out.append(Hit("Payments", "5.3", "Medium", pa, f"Payment to {r['supplier']} has no invoice reference", "payments.csv", ln))
        elif pd_ and idt and pd_ < idt:
            out.append(Hit("Payments", "5.3", "Low", pa, f"{r['invoice_no']} paid before the invoice date", "payments.csv", ln))
        if pd_ and pd_.weekday() >= 5:
            out.append(Hit("Payments", "5.4", "Low", pa, f"Payment released on a {pd_.strftime('%A')}", "payments.csv", ln))
    return out


def approvals(lines: list[str]) -> list[Hit]:
    out = []
    for ln, r in rows(lines):
        amt = _f(r["amount"])
        label = r["doc_no"] or r["record_id"]
        if r["requested_by"].strip() and r["requested_by"].strip().lower() == r["approved_by"].strip().lower():
            out.append(Hit("Approvals", "1.2", "High", amt, f"{label} raised and approved by the same person", "approvals.csv", ln))
        if amt > 2500 and not r["po_no"].strip():
            out.append(Hit("Approvals", "1.1", "Medium", amt, f"{label} for ${amt:,.2f} has no purchase order", "approvals.csv", ln))
        if amt > 10000 and r["approver_role"].strip().lower() != "director":
            out.append(Hit("Approvals", "1.3", "Medium", amt,
                           f"{label} for ${amt:,.2f} approved by a {r['approver_role'] or 'non-director'}, not a director",
                           "approvals.csv", ln))
    return out


def vendors(lines: list[str]) -> list[Hit]:
    R, out, tax = rows(lines), [], {}
    for ln, r in R:
        paid = _f(r["last_paid_amount"])
        if r["bank_changed_on"].strip() and r["bank_verified"].strip().upper() == "NO" and r["last_paid_on"].strip():
            out.append(Hit("Vendors", "4.3", "High", paid, f"{r['vendor_id']} paid to an unverified new bank account", "vendors.csv", ln))
        if r["status"].strip().upper() != "ACTIVE" and r["last_paid_on"].strip():
            out.append(Hit("Vendors", "4.1", "Medium", paid,
                           f"{r['status'].title()} vendor {r['vendor_id']} was paid ${paid:,.2f} on {r['last_paid_on']}", "vendors.csv", ln))
        if r["w9_on_file"].strip().upper() == "NO" and r["last_paid_on"].strip():
            out.append(Hit("Vendors", "4.4", "Low", paid, f"{r['vendor_id']} paid ${paid:,.2f} with no tax form on file", "vendors.csv", ln))
        t = r["tax_id"].strip()
        if t:
            if t in tax:
                out.append(Hit("Vendors", "4.2", "High", paid + _f(rows_by_line(R, tax[t])["last_paid_amount"]),
                               f"Vendor records {rows_by_line(R, tax[t])['vendor_id']} and {r['vendor_id']} share tax ID {t}",
                               "vendors.csv", ln, [("vendors.csv", tax[t])]))
            else:
                tax[t] = ln
    return out


def rows_by_line(R: list[tuple[int, dict]], ln: int) -> dict:
    return next(r for n, r in R if n == ln)


def contracts(lines: list[str]) -> list[Hit]:
    out = []
    contract = {}   # vendor -> list of (line, text)
    for i, ln in enumerate(lines, start=1):
        m = re.match(r"\[CONTRACT (\S+) \| ([^|]+?) \| ([^\]]+)\] (.*)", ln)
        if m:
            contract.setdefault(m.group(2).strip(), []).append((i, m.group(3), m.group(4)))
    for i, ln in enumerate(lines, start=1):
        m = re.match(r"\[INVOICE (\S+) \| ([^|]+?) \| ([^\]]+)\] (.*)", ln)
        if not m:
            continue
        inv, vendor, _, body = m.groups()
        vendor = vendor.strip()
        for cline, where, text in contract.get(vendor, []):
            amt = _f(re.findall(r"(\d[\d,]*\.\d{2})\s*$", body)[0]) if re.findall(r"(\d[\d,]*\.\d{2})\s*$", body) else 0.0
            if "surcharge" in body.lower() and re.search(r"surcharge.*excluded|not listed|may not be billed", text, re.I):
                out.append(Hit("Contracts", "7.1", "Medium", amt, f"{inv} adds a ${amt:,.2f} charge the contract does not allow",
                               "contracts.txt", i, [("contracts.txt", cline)]))
            rate_c = re.search(r"rate is (\d+(?:\.\d+)?) per hour", text, re.I)
            rate_i = re.search(r"at (\d+(?:\.\d+)?) per hour", body, re.I)
            hrs = re.search(r"(\d+(?:\.\d+)?) hours", body)
            if rate_c and rate_i and hrs and float(rate_i.group(1)) > float(rate_c.group(1)):
                over = round((float(rate_i.group(1)) - float(rate_c.group(1))) * float(hrs.group(1)), 2)
                out.append(Hit("Contracts", "7.2", "High", over,
                               f"{vendor} billed {rate_i.group(1)} per hour against a contracted {rate_c.group(1)}",
                               "contracts.txt", i, [("contracts.txt", cline)]))
            if re.search(r"auto-renews", text, re.I) and re.search(r"no notice of cancellation", body, re.I):
                out.append(Hit("Contracts", "7.4", "Low", amt, f"{vendor} auto-renewed with no cancellation decision on file",
                               "contracts.txt", i, [("contracts.txt", cline)]))
            end = re.search(r"ends on (\d{4}-\d{2}-\d{2})", text, re.I)
            svc = re.search(r"Service period (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", body)
            if end and svc and _d(svc.group(2)) and _d(end.group(1)) and _d(svc.group(2)) > _d(end.group(1)):
                out.append(Hit("Contracts", "7.3", "Medium", amt, f"{vendor} billed for service after the contract ended",
                               "contracts.txt", i, [("contracts.txt", cline)]))
    return out


def expenses(lines: list[str]) -> list[Hit]:
    out, seen = [], {}
    for ln, r in rows(lines):
        amt = _f(r["amount"])
        people = int(_f(r["people"])) if r["people"].strip() else 1
        if r["category"].strip().upper() == "MEAL" and people and amt / max(people, 1) > 75:
            out.append(Hit("Expenses", "6.1", "Low", amt, f"Meal at ${amt / people:,.2f} per person (limit $75.00)", "expenses.csv", ln))
        if amt > 25 and not r["receipt_ref"].strip():
            out.append(Hit("Expenses", "6.2", "Medium", amt, f"Claim {r['claim_id']} of ${amt:,.2f} has no receipt", "expenses.csv", ln))
        if re.search(r"gym|membership|personal|netflix|spa\b|haircut", r["notes"], re.I):
            out.append(Hit("Expenses", "6.3", "Low", amt, f"Personal item claimed: {r['notes'].strip().lower()}", "expenses.csv", ln))
        ref = r["receipt_ref"].strip()
        if ref:
            if ref in seen:
                out.append(Hit("Expenses", "6.5", "High", amt, f"The same receipt {ref} was claimed twice", "expenses.csv", ln,
                               [("expenses.csv", seen[ref])]))
            else:
                seen[ref] = ln
        d = _d(r["date"])
        if d and d.weekday() >= 5 and not r["notes"].strip():
            out.append(Hit("Expenses", "6.4", "Low", amt, f"Weekend expense {r['claim_id']} with no reason given", "expenses.csv", ln))
    return out


CHECKS = {"Payments": payments, "Approvals": approvals, "Vendors": vendors, "Contracts": contracts, "Expenses": expenses}

INNOCENT = {
    "5.1": "A credit note or agreed price change may explain the difference.",
    "5.2": "The first payment may have bounced and been legitimately re-sent.",
    "5.3": "An advance payment may have been agreed with the supplier.",
    "5.4": "The bank may have processed an earlier instruction on a non-working day.",
    "1.1": "The purchase order may exist outside this export.",
    "1.2": "A deputy may have approved under an agreed cover arrangement.",
    "1.3": "A director may have approved by email outside the system.",
    "4.1": "The vendor record may have been closed after the payment was agreed.",
    "4.2": "Two entities may legitimately share a tax ID (a parent and a branch).",
    "4.3": "The supplier may have been verified by phone outside the system.",
    "4.4": "The tax form may be held on paper.",
    "7.1": "A variation order may have been agreed verbally.",
    "7.2": "A rate change may have been agreed after the contract copy was filed.",
    "7.3": "The contract may have been extended in writing.",
    "7.4": "A renewal may have been agreed on purpose.",
    "6.3": "The item may have been bought for a business purpose.",
    "6.1": "The meal may have included guests not recorded in the claim.",
    "6.2": "The receipt may exist but was not entered in the claim.",
    "6.4": "The employee may have worked a weekend shift.",
    "6.5": "Two people may have shared one receipt for a shared journey.",
}
FIXES = {
    "5.1": "Ask the supplier for a credit note or recover the difference.",
    "5.2": "Ask the bank to recall the second payment and block repeat payment of this invoice.",
    "5.3": "Check the invoice and the approval date before releasing similar payments.",
    "5.4": "Remind Treasury that payments release on working days only.",
    "1.1": "Ask the requester for the purchase order, or record an approved exception.",
    "1.2": "Have a different, authorised person re-approve it.",
    "1.3": "Get director sign-off recorded against the document.",
    "4.1": "Check why an inactive vendor was paid; recover the payment if it was wrong.",
    "4.2": "Merge the vendor records and check no payment was made twice.",
    "4.3": "Hold payments until Treasury records a call-back verification.",
    "4.4": "Obtain the tax form before any further payment.",
    "7.1": "Dispute the charge and ask for a credit note.",
    "7.2": "Dispute the difference and ask for a corrected invoice.",
    "7.3": "Check for a written extension; otherwise refuse the invoice.",
    "7.4": "Decide whether to keep the contract and record the decision.",
    "6.3": "Do not reimburse personal items.",
    "6.1": "Ask the employee for the attendee list or reduce the claim to the limit.",
    "6.2": "Ask for the receipt before reimbursing.",
    "6.4": "Ask the employee for the business reason.",
    "6.5": "Reimburse the receipt once only and ask both employees to explain.",
}


def run_area(area: str, files: dict[str, list[str]]) -> list[Hit]:
    name = FILES[area]
    lines = files.get(name)
    if not lines or missing_columns(name, lines):
        return []
    return CHECKS[area](lines)


def analyze(files: dict[str, list[str]]) -> list[Hit]:
    out = []
    for area in FILES:
        out += run_area(area, files)
    return out
