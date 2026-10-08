"""Column matching help: suggest which of a person's columns holds each expected field, and remember matchings.

Suggestions come from common names used by accounting exports (QuickBooks, Xero, NetSuite, bank downloads).
They are only pre-filled guesses - the person confirms them. A saved matching is reused automatically the next
time a file with exactly the same header is uploaded.
"""
from __future__ import annotations

import re

SYNONYMS = {
    "payment_id": ["payment id", "payment no", "payment number", "num", "no", "txn id", "transaction id", "check no",
                   "cheque no", "reference", "ref", "document number"],
    "pay_date": ["payment date", "date paid", "paid date", "paid on", "date", "txn date", "transaction date", "value date"],
    "invoice_date": ["invoice date", "bill date", "doc date", "document date", "inv date"],
    "vendor_id": ["vendor id", "supplier id", "vendor no", "vendor number", "supplier no", "contact id", "internal id"],
    "supplier": ["vendor", "supplier", "payee", "vendor name", "supplier name", "contact", "contact name", "name",
                 "merchant"],
    "invoice_no": ["invoice no", "invoice number", "invoice #", "bill no", "bill number", "inv no", "doc no", "ref no"],
    "invoice_amount": ["invoice amount", "bill amount", "original amount", "invoice total", "total", "gross",
                       "amount due"],
    "paid_amount": ["amount paid", "payment amount", "paid amount", "invoice amount paid", "paid", "amount", "debit"],
    "bank_last4": ["bank last4", "account last 4", "bank account", "account no", "iban"],
    "approved_by": ["approved by", "approver", "authorised by", "authorized by"],
    "record_id": ["record id", "id", "line id", "internal id"],
    "type": ["type", "transaction type", "doc type", "document type"],
    "doc_no": ["doc no", "document number", "invoice no", "bill no", "num", "number"],
    "date": ["date", "document date", "txn date", "transaction date", "posting date"],
    "vendor": ["vendor", "supplier", "payee", "vendor name", "supplier name", "contact name", "name"],
    "amount": ["amount", "total", "gross", "value", "debit"],
    "requested_by": ["requested by", "requester", "raised by", "created by", "preparer"],
    "approver_role": ["approver role", "role", "approval level", "title"],
    "po_no": ["po no", "po number", "purchase order", "po", "po #"],
    "name": ["name", "vendor name", "supplier name", "contact name", "company"],
    "tax_id": ["tax id", "tin", "ein", "vat number", "vat no", "abn", "tax number"],
    "status": ["status", "active", "state", "contact status", "vendor status"],
    "bank_acct": ["bank account", "bank account number", "account no", "account number", "iban", "bank acct"],
    "bank_changed_on": ["bank changed on", "bank change date", "bank updated", "account changed"],
    "bank_verified": ["bank verified", "verified", "callback done", "account verified"],
    "created_on": ["created on", "created", "date created", "setup date"],
    "w9_on_file": ["w9 on file", "w9", "tax form", "w-9"],
    "last_paid_on": ["last paid on", "last payment date", "last paid"],
    "last_paid_amount": ["last paid amount", "last payment amount", "last payment"],
    "claim_id": ["claim id", "expense id", "report id", "id"],
    "employee": ["employee", "employee name", "claimant", "name"],
    "category": ["category", "expense type", "type", "account"],
    "receipt_ref": ["receipt ref", "receipt", "receipt no", "receipt id", "attachment"],
    "notes": ["notes", "description", "memo", "purpose", "comment"],
    "people": ["people", "attendees", "guests", "headcount"],
    "line": ["line", "line no", "#", "seq"],
    "invoice": ["invoice", "invoice no", "invoice number", "bill no"],
    "description": ["description", "details", "narrative", "memo", "payee", "particulars"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9#]+", " ", s.lower()).strip()


def norm_header(s: str) -> str:
    """A header as suggest() compares it: Xero's "*InvoiceNumber" reads as "invoice number"."""
    return _norm(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s))


def suggest(missing: list[str], header: list[str]) -> dict[str, str]:
    """{expected column: best matching header column} for the missing columns. Each header column is used once."""
    taken, out = set(), {}
    norm = {h: norm_header(h) for h in header}
    for col in missing:
        cands = [_norm(col.replace("_", " "))] + SYNONYMS.get(col, [])
        for c in cands:
            hit = next((h for h, n in norm.items() if n == c and h not in taken), None)
            if hit:
                out[col] = hit
                taken.add(hit)
                break
    return out


def signature(name: str, header: list[str]) -> str:
    return name + "|" + ",".join(_norm(h) for h in header)
