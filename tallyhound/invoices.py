"""Supplier invoices as PDFs: read their text, then check them against approvals and the vendor master.

All PDFs in a zip become one text file, invoices.txt: a "[PDF name]" line, then that invoice's text lines. Findings
quote lines of invoices.txt, so the usual quote check still applies. The checks catch the classic invoice frauds:
an invoice nobody approved, a total that differs from the approved amount, the same invoice number twice, and - the
most expensive one - bank details on the invoice that differ from the vendor master.
"""
from __future__ import annotations

import io
import re
import threading
from typing import Any

from . import llm

NAME = "invoices.txt"
MATCH_CHARS = 400
MAX_PDFS = 300                  # per upload
MAX_PDF_PAGES = 200             # a PDF claiming more pages than this is not an invoice (and may be a "page-tree bomb")
HEAD = re.compile(r"^\[PDF (.+)\]$")
AMT = r"(?<![\d.,])(-?\d{1,3}(?:[.,' ]\d{3})+[.,]\d{2}|-?\d{1,12}[.,]\d{2})(?!\d)"      # 1,234.56 / 1.234,56 / 1 234,56 / 99.00
# "Invoice No: 2026/045", "Inv. No. INV 77", "INVOICE #A-12", "Invoice: INV-5" - but not "Invoice: 01/09/2026" (a date)
INV_NO = re.compile(r"\binv(?:oice)?\.?\s*(?:no\.?|number|num\.?|#)\s*[:#]?\s*([A-Z]{0,6}[ -]?\d[A-Z0-9/-]*)"
                    r"|\binvoice\s*:\s*([A-Z]{1,6}[ -]?\d[A-Z0-9/-]*)", re.I)
DATE_LIKE = re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$")
# Each gap between label, qualifier, currency and amount is ONE character class, so no two parts compete for the same
# spaces: overlapping \s* runs made a 250-character line take seconds (catastrophic backtracking).
GAP = r"[\s:]*(?:\([^)]{0,30}\)[\s:]*)?(?:(?:USD|EUR|GBP)[\s:]*)?(?:[$€£][\s]*)?"
TOTAL_DUE = re.compile(r"\b(?:total due|amount due|balance due|amount payable|total payable)\b" + GAP + AMT, re.I)
TOTAL = re.compile(r"\b(?:invoice total|total)\b" + GAP + AMT, re.I)
BANK = re.compile(r"\b(?:account|acct)\b\.?[\s:#*xX-]*(?:(?:no|number)\b\.?[\s:#*xX-]*)?(\d(?:[ -]?\d){3,33})", re.I)
IBAN = re.compile(r"\bIBAN\b\s*:?\s*([A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,30})", re.I)
NOT_BANK = re.compile(r"\b(your|customer|client|cust\.?)\s+(account|acct)|account\s+(ref|reference|manager)", re.I)
BANK_CONTEXT = re.compile(r"\b(bank|iban|sort code|routing|swift|bic|pay(?:ment)?s? to|remit|beneficiary)\b", re.I)
VENDOR = re.compile(r"^\s*(?:from|supplier|vendor)\s*[:]\s*(.+)$", re.I)


def pdf_lines(data: bytes, max_pages: int = 5) -> list[str] | None:
    """Text lines of a PDF (first pages only). Empty when the PDF has no text layer (a scan); None when it is refused
    (it claims more than MAX_PDF_PAGES pages)."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        count = reader.trailer["/Root"]["/Pages"].get("/Count", 0)   # read the claim before walking the page tree
        if not isinstance(count, int) or count < 1 or count > MAX_PDF_PAGES:
            return None                                       # refused: not an invoice-sized PDF
        text = "\n".join((reader.pages[i].extract_text() or "")[:20000] for i in range(min(max_pages, count)))
    except Exception:          # noqa: BLE001  a broken PDF must not stop the upload
        return []
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()]


PDF_SECONDS = 8.0        # one PDF may take this long to read (includes starting the worker)
PDF_TOTAL_SECONDS = 90.0  # all PDFs of one upload together
PDF_MEMORY_MB = 256 if llm.public() else 512
_PDF_SLOTS = None          # one batch of PDFs is read at a time per server (each worker may use PDF_MEMORY_MB)


def read_pdfs(items: list[tuple[str, bytes]]) -> dict[str, list[str] | None]:
    """Text lines of each PDF, read by a separate worker process (tallyhound/pdfworker.py) with a time and memory
    limit, so a hostile PDF (a page-tree or decompression bomb) can never freeze the app for everyone else. A PDF that
    takes longer than PDF_SECONDS gives None and the worker is restarted for the rest. Falls back to reading in this
    process, with the page-count guard only, if a worker cannot be started."""
    global _PDF_SLOTS
    if _PDF_SLOTS is None:
        _PDF_SLOTS = threading.BoundedSemaphore(1)
    with _PDF_SLOTS:
        return _read_pdfs(items)


def _read_pdfs(items: list[tuple[str, bytes]]) -> dict[str, list[str] | None]:
    import base64
    import json
    import queue
    import subprocess
    import sys
    import time as _t
    from pathlib import Path
    out: dict[str, list[str] | None] = {}
    todo = list(items)
    deadline = _t.time() + PDF_TOTAL_SECONDS
    root = str(Path(__file__).resolve().parent.parent)
    while todo:
        if _t.time() >= deadline:
            out.update({n: None for n, _ in todo})
            break
        try:
            proc = subprocess.Popen([sys.executable, "-m", "tallyhound.pdfworker", str(PDF_MEMORY_MB)], cwd=root,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            out.update({n: pdf_lines(b) for n, b in todo})
            break
        got: queue.Queue = queue.Queue()

        def drain(p=proc, q=got):
            for x in p.stdout:
                q.put(x)
            q.put(None)                                # the worker ended

        def feed(p=proc, batch=tuple(todo)):
            try:
                for n, b in batch:
                    p.stdin.write(json.dumps({"name": n, "data": base64.b64encode(b).decode()}) + "\n")
                    p.stdin.flush()
                p.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        threading.Thread(target=drain, daemon=True).start()
        threading.Thread(target=feed, daemon=True).start()
        try:
            while todo:
                wait = min(PDF_SECONDS, deadline - _t.time())
                try:
                    msg = got.get(timeout=max(wait, 0.01))
                except queue.Empty:
                    msg = None
                if msg is None:                        # stuck or crashed on the PDF it was reading: skip that one
                    out[todo[0][0]] = None
                    todo = todo[1:]
                    break                              # and start a fresh worker for the rest
                try:
                    res = json.loads(msg)
                    name, lines = res["name"], res["lines"]
                except (ValueError, KeyError, TypeError):
                    continue                           # stray output (a library printing): not a result
                out[name] = lines
                todo = [x for x in todo if x[0] != name]
        finally:
            proc.kill()
            try:
                proc.wait(timeout=2)                   # reap it: no zombie processes left behind
            except subprocess.TimeoutExpired:
                pass
    return out


def combine(docs: list[tuple[str, list[str]]]) -> list[str]:
    out = []
    for name, lines in docs:
        out.append(f"[PDF {name}]")
        out += lines
    return out


def _number(ln: str) -> str | None:
    m = INV_NO.search(ln)
    if not m:
        return None
    no = (m.group(1) or m.group(2) or "").strip()
    return None if not no or DATE_LIKE.match(no) else no


def _bank(ln: str) -> str | None:
    """Last digits of a bank account on this line, or None. A customer's own account number is not a bank account."""
    if NOT_BANK.search(ln):
        return None
    m = IBAN.search(ln)
    if m:
        return re.sub(r"[^0-9A-Za-z]", "", m.group(1))
    m = BANK.search(ln)
    return re.sub(r"\D", "", m.group(1)) if m else None


def blocks(lines: list[str]) -> list[dict]:
    """One dict per invoice: its name, and the line number (1-based) and value of each field found. A PDF that holds
    several invoices gives several blocks: a new invoice number after a total starts the next one."""
    out: list[dict] = []
    cur: dict[str, Any] | None = None
    for n, full in enumerate(lines, start=1):
        ln = full[:MATCH_CHARS]                   # fields sit at the start of short lines; never scan a huge one
        m = HEAD.match(ln)
        if m:
            cur = dict(name=m.group(1), start=n)
            out.append(cur)
            continue
        if cur is None:
            continue
        no = _number(ln)
        if no and "number" in cur and ("total" in cur or "total_due" in cur) and no != cur["number"][1]:
            k = sum(b["name"].startswith(cur["name"].split(" #")[0]) for b in out) + 1
            cur = dict(name=f"{cur['name'].split(' #')[0]} #{k}", start=n - 1, vendor=cur.get("vendor"),
                       vendor_guess=cur.get("vendor_guess"))
            cur = {k2: v for k2, v in cur.items() if v is not None}
            out.append(cur)
        if no and "number" not in cur:
            cur["number"] = (n, no)
        for field, rx in (("total_due", TOTAL_DUE), ("total", TOTAL)):
            if field not in cur:
                hit = rx.search(ln)
                if hit and not (field == "total" and re.search(r"\b(net|sub)\s*-?\s*total", ln, re.I)):
                    cur[field] = (n, hit.group(1).strip())
        acct = _bank(ln)
        if acct and len(acct) >= 4 and ("bank" not in cur or (BANK_CONTEXT.search(ln) and not cur.get("_bank_ctx"))):
            cur["bank"], cur["_bank_ctx"] = (n, acct), bool(BANK_CONTEXT.search(ln))
        if "vendor" not in cur:
            v = VENDOR.search(ln)
            if v:
                cur["vendor"] = (n, v.group(1).strip())
        if "vendor" not in cur and "vendor_guess" not in cur and n == cur["start"] + 1:   # usually the supplier name
            cur["vendor_guess"] = (n, ln.strip())
    for b in out:
        if "total_due" in b:
            b["total"] = b["total_due"]            # the amount payable wins over any other "total" line
        b.pop("_bank_ctx", None)
    return out


def _vendor(V: dict, name: str):
    """The vendor-master record for a name on an invoice: exact after normalising, else the one master name that the
    invoice name starts with ('Ashby Components (UK) Ltd' -> 'Ashby Components'). None when unsure."""
    from . import rules
    n = rules.norm_name(name)
    if not n:
        return None
    if n in V:
        return V[n]
    near = [v for k, v in V.items() if k and (n.startswith(k + " ") or k.startswith(n + " "))]
    return near[0] if len(near) == 1 else None


def check(files: dict[str, list[str]]) -> list:
    """Hits for the Invoices area. Needs invoices.txt; uses approvals.csv and vendors.csv when present."""
    from . import rules
    lines = files.get(NAME)
    if not lines:
        return []
    A: dict[str, list] | None = None
    if files.get("approvals.csv"):
        A = {}
        for _, r in rules.rows(files["approvals.csv"]):
            if r["type"].strip().upper() in rules.INVOICE_TYPES:
                A.setdefault(rules.inv_key(r["doc_no"]), []).append(r)
    V = {rules.norm_name(r["name"]): r for _, r in rules.rows(files.get("vendors.csv", []))} if files.get("vendors.csv") else None
    out = []
    seen: dict[str, list[tuple]] = {}
    for b in blocks(lines):
        if "number" not in b:
            continue
        nl, no = b["number"]
        total = rules._f(b["total"][1]) if "total" in b else 0.0
        vendor = (b.get("vendor") or b.get("vendor_guess") or (0, ""))[1]
        # the same number from two KNOWN, different suppliers is two invoices; when the supplier cannot be told
        # (a first line like "TAX INVOICE"), the same number is treated as the same invoice
        known = _vendor(V, vendor) if V else None
        who = known["vendor_id"] if known else (rules.norm_name(b["vendor"][1]) if "vendor" in b else "")
        prev = [(w, line) for w, line in seen.get(rules.inv_key(no), []) if not (w and who and w != who)]
        if prev:
            out.append(rules.Hit("Invoices", "8.3", "High", total, f"Invoice {no} was submitted twice ({b['name']})",
                                 NAME, nl, [(NAME, prev[0][1])]))
        seen.setdefault(rules.inv_key(no), []).append((who, nl))
        if A is not None:
            cands = A.get(rules.inv_key(no), [])
            mine = [r for r in cands if rules.norm_name(r["vendor"]) == rules.norm_name(vendor)] or cands
            a = mine[0] if mine else None
            if a is None:
                out.append(rules.Hit("Invoices", "8.1", "Medium", total, f"Invoice {no} ({b['name']}) has no approval record",
                                     NAME, nl))
            elif "total" in b and abs(total - rules._f(a["amount"])) > 0.005:
                out.append(rules.Hit("Invoices", "8.1", "Medium", round(abs(total - rules._f(a["amount"])), 2),
                                     f"Invoice {no} total ${total:,.2f} differs from the approved ${rules._f(a['amount']):,.2f}",
                                     NAME, b["total"][0], [(NAME, nl)]))
        if V is not None and "bank" in b and vendor:
            v = _vendor(V, vendor)
            on_file = re.sub(r"\D", "", v["bank_acct"])[-4:] if v else ""
            if v and on_file and on_file != b["bank"][1][-4:]:
                out.append(rules.Hit("Invoices", "8.2", "High", total,
                                     f"Invoice {no} asks for payment to account ending {b['bank'][1][-4:]}; "
                                     f"{v['vendor_id']} is on file with {on_file}",
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
