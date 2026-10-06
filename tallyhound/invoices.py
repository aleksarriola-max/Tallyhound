"""Supplier invoices as PDFs: read their text, then check them against approvals and the vendor master.

All PDFs in a zip become one text file, invoices.txt: a "[PDF name]" line, then that invoice's text lines. Findings
quote lines of invoices.txt, so the usual quote check still applies. The checks catch the classic invoice frauds:
an invoice nobody approved, a total that differs from the approved amount, the same invoice number twice, and - the
most expensive one - bank details on the invoice that differ from the vendor master.
"""
from __future__ import annotations

import io
import re

NAME = "invoices.txt"
HEAD = re.compile(r"^\[PDF (.+)\]$")
INV_NO = re.compile(r"\binvoice\s*(?:no\.?|number|#|:)\s*[:#]?\s*([A-Z0-9/-]*\d[A-Z0-9/-]*)", re.I)
TOTAL_DUE = re.compile(r"\b(?:total due|amount due|balance due|amount payable|total payable)\b\s*[:]?\s*(?:USD|EUR|GBP)?\s*[$€£]?\s*(-?[\d,]+\.\d{2})", re.I)
TOTAL = re.compile(r"\b(?:total due|amount due|balance due|invoice total|total)\b\s*[:]?\s*(?:USD|EUR|GBP)?\s*[$€£]?\s*(-?[\d,]+\.\d{2})", re.I)
BANK = re.compile(r"\b(?:account|acct)\s*(?:no\.?|number|#)?\s*[:#]?\s*[*xX\s-]*(\d{4,})", re.I)
VENDOR = re.compile(r"^\s*(?:from|supplier|vendor)\s*[:]\s*(.+)$", re.I)


def pdf_lines(data: bytes, max_pages: int = 5) -> list[str]:
    """Text lines of a PDF (first pages only). Empty when the PDF has no text layer (a scan)."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    except Exception:          # a broken PDF must not stop the upload
        return []
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()]


def combine(docs: list[tuple[str, list[str]]]) -> list[str]:
    out = []
    for name, lines in docs:
        out.append(f"[PDF {name}]")
        out += lines
    return out


def blocks(lines: list[str]) -> list[dict]:
    """One dict per invoice: its name, and the line number (1-based) and value of each field found."""
    out, cur = [], None
    for n, ln in enumerate(lines, start=1):
        m = HEAD.match(ln)
        if m:
            cur = dict(name=m.group(1), start=n)
            out.append(cur)
            continue
        if cur is not None and "total_due" in cur:
            cur["total"] = cur["total_due"]           # the amount payable wins over any other "total" line
        if cur is None:
            continue
        for field, rx in (("number", INV_NO), ("total_due", TOTAL_DUE), ("total", TOTAL), ("bank", BANK), ("vendor", VENDOR)):
            if field not in cur:
                hit = rx.search(ln)
                if hit and not (field == "total" and re.search(r"\b(net|sub)\s*-?\s*total", ln, re.I)):
                    cur[field] = (n, hit.group(1).strip())
        if "vendor" not in cur and n == cur["start"] + 1:     # first text line is usually the supplier name
            cur["vendor_guess"] = (n, ln.strip())
    return out


def check(files: dict[str, list[str]]) -> list:
    """Hits for the Invoices area. Needs invoices.txt; uses approvals.csv and vendors.csv when present."""
    from . import rules
    lines = files.get(NAME)
    if not lines:
        return []
    A = {r["doc_no"].strip().lower(): r for _, r in rules.rows(files.get("approvals.csv", []))
         if r["type"].strip().upper() in ("", "INVOICE")} if files.get("approvals.csv") else None
    V = {rules.norm_name(r["name"]): r for _, r in rules.rows(files.get("vendors.csv", []))} if files.get("vendors.csv") else None
    out, seen = [], {}
    for b in blocks(lines):
        if "number" not in b:
            continue
        nl, no = b["number"]
        total = rules._f(b["total"][1]) if "total" in b else 0.0
        vendor = (b.get("vendor") or b.get("vendor_guess") or (0, ""))[1]
        if no.lower() in seen:
            out.append(rules.Hit("Invoices", "8.3", "High", total, f"Invoice {no} was submitted twice ({b['name']})",
                                 NAME, nl, [(NAME, seen[no.lower()])]))
        else:
            seen[no.lower()] = nl
        if A is not None:
            a = A.get(no.lower())
            if a is None:
                out.append(rules.Hit("Invoices", "8.1", "Medium", total, f"Invoice {no} ({b['name']}) has no approval record",
                                     NAME, nl))
            elif "total" in b and abs(total - rules._f(a["amount"])) > 0.005:
                out.append(rules.Hit("Invoices", "8.1", "Medium", round(abs(total - rules._f(a["amount"])), 2),
                                     f"Invoice {no} total ${total:,.2f} differs from the approved ${rules._f(a['amount']):,.2f}",
                                     NAME, b["total"][0], [(NAME, nl)]))
        if V is not None and "bank" in b and vendor:
            v = V.get(rules.norm_name(vendor))
            if v and v["bank_acct"].strip()[-4:] and v["bank_acct"].strip()[-4:] != b["bank"][1][-4:]:
                out.append(rules.Hit("Invoices", "8.2", "High", total,
                                     f"Invoice {no} asks for payment to account ending {b['bank'][1][-4:]}; "
                                     f"{v['vendor_id']} is on file with {v['bank_acct'].strip()[-4:]}",
                                     NAME, b["bank"][0], [(NAME, nl)]))
    return out


def render_pdf(lines: list[str]) -> bytes:
    """A simple one-page invoice PDF (used by the challenge generator)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for i, ln in enumerate(lines):
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 13 if i == 0 else 10)
        c.drawString(60, y, ln)
        y -= 22 if i == 0 else 16
    c.showPage()
    c.save()
    return buf.getvalue()
