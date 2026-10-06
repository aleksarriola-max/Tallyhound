"""Built-in rule checks: fixed, repeatable tests of the audit policy. No AI involved.

Each check returns Hit objects that point at exact lines of the uploaded files. Nothing here decides anything;
every hit is only a proposal for a person to review.

The checks are written to stay quiet on things that look odd but are normal in real books: instalments and
voided-and-reissued payments, early-payment discounts and rounding, bills that never carry a PO (rent, utilities,
insurance), batch bank transfers and non-supplier bank debits, day-first dates, supplier-name spellings, remit-to
vendor records, vendors closed after their last payment, team meals, professional memberships and weekend travel.
The challenge generator plants each of these as a trap, and the test suite fails if a rule flags one.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from itertools import combinations

FILES = {"Payments": "payments.csv", "Approvals": "approvals.csv", "Vendors": "vendors.csv",
         "Contracts": "contracts.txt", "Expenses": "expenses.csv", "Invoices": "invoices.txt"}
REQUIRED = {
    "payments.csv": ["payment_id", "pay_date", "invoice_date", "supplier", "invoice_no", "invoice_amount", "paid_amount"],
    "approvals.csv": ["record_id", "doc_no", "date", "vendor", "amount", "requested_by", "approved_by", "approver_role", "po_no"],
    "vendors.csv": ["vendor_id", "name", "tax_id", "status", "bank_changed_on", "bank_verified", "w9_on_file", "last_paid_on",
                    "last_paid_amount"],
    "payment_run.csv": ["line", "supplier", "invoice", "amount"],
    "bank_statement.csv": ["date", "description", "amount"],
    "expenses.csv": ["claim_id", "date", "employee", "category", "amount", "receipt_ref", "notes", "people"],
}

LIMITS = dict(po_limit=2500.0, director_limit=10000.0, meal_limit=75.0, receipt_limit=25.0, split_days=3, bank_days=5,
              tolerance=1.0, materiality=50.0)
LIMIT_LABELS = dict(po_limit="Purchase order needed above ($)", director_limit="Director approval needed above ($)",
                    meal_limit="Meal limit per person ($)", receipt_limit="Receipt needed above ($)",
                    split_days="Split-order window (days)", bank_days="Bank clearing window (days)",
                    tolerance="Ignore amount differences up to ($)", materiality="Minor-item threshold ($)")
# Bills that normally have no purchase order. Editable on the Policy page.
PO_EXEMPT_WORDS = ["rent", "lease", "property", "properties", "utility", "utilities", "power", "electric", "water",
                   "gas", "energy", "insurance", "telecom", "broadband", "subscription", "council", "tax", "legal"]
NON_AP_BANK = re.compile(r"\b(bank charges?|fees?|interest|payroll|salar(y|ies)|wages|irs|eftps|hmrc|vat|tax|"
                         r"card settlement|amex|visa|mastercard|transfer to|own account|savings|loan)\b", re.I)
PERSONAL = re.compile(r"\b(gym|membership|personal|netflix|spa|haircut|for myself)\b", re.I)
PROFESSIONAL = re.compile(r"\b(society|institute|association|professional|licen[cs]e|certif\w*|chamber|cpa|cima|acca|"
                          r"bar council|union dues)\b", re.I)
TRAVEL = re.compile(r"\b(travel|trip|flight|hotel|conference|client site|airport|mileage)\b", re.I)
TRAVEL_SPEND = re.compile(r"\b(taxi|meal|hotel|travel|mileage|parking|fuel|train|airfare)\b", re.I)
SUFFIXES = {"ltd", "limited", "inc", "incorporated", "llc", "llp", "co", "corp", "corporation", "plc", "gmbh", "sa",
            "bv", "pty", "the", "company"}


class Row(dict):
    """A CSV row; a column that is not in the file reads as an empty string. d() reads a date column using the
    date order detected for the whole file (day-first or month-first)."""
    dayfirst = False

    def __missing__(self, key):
        return ""

    def d(self, col: str) -> date | None:
        return _d(self[col], self.dayfirst)


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


SLASH = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")


def date_order(lines: list[str]) -> str:
    """'day-first', 'month-first', 'iso' or 'ambiguous' for the slash dates in a CSV."""
    first = second = slash = 0
    for ln in lines[1:]:
        for cell in next(csv.reader([ln])) if ln.strip() else []:
            m = SLASH.match(cell.strip())
            if m:
                slash += 1
                first += int(m.group(1)) > 12
                second += int(m.group(2)) > 12
    if not slash:
        return "iso"
    if first and not second:
        return "day-first"
    if second and not first:
        return "month-first"
    return "ambiguous"


def rows(lines: list[str]) -> list[tuple[int, Row]]:
    """(1-based line number, row) for every data row of a CSV given as exact lines."""
    if not lines:
        return []
    head = next(csv.reader([lines[0]]))
    if "tallyhound_dayfirst" in head:             # set by the person on the data check (see custom.view)
        dayfirst = True
    elif "tallyhound_monthfirst" in head:
        dayfirst = False
    else:
        dayfirst = date_order(lines) == "day-first"
    out = []
    for i, ln in enumerate(lines[1:], start=2):
        if not ln.strip():
            continue
        vals = next(csv.reader([ln]))
        r = Row(zip(head, vals + [""] * (len(head) - len(vals))))
        r.dayfirst = dayfirst
        out.append((i, r))
    return out


def missing_columns(name: str, lines: list[str]) -> list[str]:
    if name not in REQUIRED or not lines:
        return []
    head = set(next(csv.reader([lines[0]])))
    return [c for c in REQUIRED[name] if c not in head]


@lru_cache(maxsize=65536)
def _d(s: str, dayfirst: bool = False) -> date | None:
    """Dates as YYYY-MM-DD, YYYY/MM/DD, or slash/dot/dash dates in the file's day or month order."""
    s = str(s).strip()[:10]
    fmts = ("%Y-%m-%d", "%Y/%m/%d") + (("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y") if dayfirst else ("%m/%d/%Y", "%d.%m.%Y"))
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


EU_NUMBER = re.compile(r"^-?\(?[$€£]?\d{1,3}(\.\d{3})*,\d{2}\)?$")


def _f(s: str) -> float:
    t = str(s).strip().replace("$", "").replace("£", "").replace("€", "").replace(" ", "")
    if EU_NUMBER.match(t):                       # 1.234,50 -> 1234.50
        t = t.replace(".", "").replace(",", ".")
    t = t.replace(",", "")
    neg = t.startswith("(") and t.endswith(")")
    try:
        return -float(t.strip("()")) if neg else float(t)
    except ValueError:
        return 0.0


@lru_cache(maxsize=65536)
def norm_name(s: str) -> str:
    """'CALDER LOGISTICS LTD.' and 'Calder Logistics' compare equal."""
    words = re.sub(r"[^a-z0-9 ]+", " ", str(s).lower().replace("&", " and ")).split()
    while words and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(w for w in words if w != "the")


def same_person(a: str, b: str) -> bool:
    """'K. Lowe' and 'Kate Lowe' are the same person; 'K. Lowe' and 'K. Lowry' are not."""
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = re.findall(r"[a-z]+", a), re.findall(r"[a-z]+", b)
    return bool(ta and tb) and ta[-1] == tb[-1] and ta[0][0] == tb[0][0]


def po_exempt(vendor: str, L: dict) -> bool:
    n = norm_name(vendor)
    if n in {norm_name(v) for v in L.get("po_exempt_vendors", [])}:
        return True
    words = L.get("po_exempt_words", PO_EXEMPT_WORDS)
    return any(re.search(rf"\b{re.escape(w.strip().lower())}\b", n) for w in words if w.strip())


def payments(lines: list[str], L: dict = LIMITS) -> list[Hit]:
    """Duplicates count money, not rows: instalments and void-and-reissue add up to the invoice and are fine;
    a payment that takes the total above the invoice is a duplicate. Only overpayments beyond the tolerance are
    flagged - paying less (a discount, a short payment) is not money lost."""
    R, out, groups = rows(lines), [], {}
    tol = L.get("tolerance", 1.0)
    for ln, r in R:
        pa, ia = _f(r["paid_amount"]), _f(r["invoice_amount"])
        inv = r["invoice_no"].strip()
        if inv:
            g = groups.setdefault((norm_name(r["supplier"]), inv.lower()), dict(net=0.0, inv=0.0, lines=[]))
            g["net"] += pa
            g["inv"] = max(g["inv"], ia)
            if pa > 0 and g["lines"] and g["net"] > g["inv"] + tol:
                out.append(Hit("Payments", "5.2", "High", pa, f"{inv.upper()} was paid more than once", "payments.csv", ln,
                               [("payments.csv", x) for x in g["lines"]]))
            if pa > 0:
                g["lines"].append(ln)
            if pa > ia + tol and len(g["lines"]) == 1:
                out.append(Hit("Payments", "5.1", "Medium", round(pa - ia, 2),
                               f"{inv} paid ${pa:,.2f} against an invoice of ${ia:,.2f}", "payments.csv", ln))
        pd_, idt = r.d("pay_date"), r.d("invoice_date")
        if pa <= 0:
            continue                                  # reversals and credits are not releases of money
        if not inv:
            out.append(Hit("Payments", "5.3", "Medium", pa, f"Payment to {r['supplier']} has no invoice reference", "payments.csv", ln))
        elif pd_ and idt and pd_ < idt:
            out.append(Hit("Payments", "5.3", "Low", pa, f"{inv} paid before the invoice date", "payments.csv", ln))
        if pd_ and pd_.weekday() >= 5:
            out.append(Hit("Payments", "5.4", "Low", pa, f"Payment released on a {pd_.strftime('%A')}", "payments.csv", ln))
    return out


def approvals(lines: list[str], L: dict = LIMITS) -> list[Hit]:
    out = []
    R = rows(lines)
    for ln, r in R:
        amt = _f(r["amount"])
        label = r["doc_no"] or r["record_id"]
        if same_person(r["requested_by"], r["approved_by"]):
            out.append(Hit("Approvals", "1.2", "High", amt, f"{label} raised and approved by the same person", "approvals.csv", ln))
        if amt > L["po_limit"] and not r["po_no"].strip() and r["type"].strip().upper() in ("", "INVOICE") \
                and not po_exempt(r["vendor"], L):
            out.append(Hit("Approvals", "1.1", "Medium", amt, f"{label} for ${amt:,.2f} has no purchase order", "approvals.csv", ln))
        if amt > L["director_limit"] and r["approver_role"].strip().lower() != "director":
            out.append(Hit("Approvals", "1.3", "Medium", amt,
                           f"{label} for ${amt:,.2f} approved by a {r['approver_role'] or 'non-director'}, not a director",
                           "approvals.csv", ln))
    out += _split_orders(R, L) + _po_after_invoice(R)
    return out


def _split_orders(R: list[tuple[int, Row]], L: dict) -> list[Hit]:
    """Several invoices from one vendor raised by the same person, each under the director limit and without a PO,
    close together in time, adding up to more than the director limit. Bills that never carry a PO do not count."""
    out, groups = [], {}
    for ln, r in R:
        if r["type"].strip().upper() in ("", "INVOICE") and not r["po_no"].strip() and r.d("date") \
                and _f(r["amount"]) <= L["director_limit"] and not po_exempt(r["vendor"], L):
            groups.setdefault((norm_name(r["vendor"]), r["requested_by"].strip().lower()), []).append((ln, r))
    for items in groups.values():
        items.sort(key=lambda x: x[1].d("date"))
        for i in range(len(items)):
            win = [x for x in items[i:] if (x[1].d("date") - items[i][1].d("date")).days <= L["split_days"]]
            total = sum(_f(x[1]["amount"]) for x in win)
            if len(win) >= 2 and total > L["director_limit"]:
                last = max(win, key=lambda x: x[0])
                out.append(Hit("Approvals", "1.4", "Medium", round(total, 2),
                               f"{len(win)} {last[1]['vendor']} invoices without a PO within {L['split_days']} days, "
                               f"total ${total:,.2f} - possible split to avoid director approval", "approvals.csv", last[0],
                               [("approvals.csv", x[0]) for x in win if x[0] != last[0]]))
                break
    return out


def _po_after_invoice(R: list[tuple[int, Row]]) -> list[Hit]:
    """A purchase order dated after the invoice it covers."""
    out = []
    pos = {r["doc_no"].strip().lower(): (ln, r) for ln, r in R if r["type"].strip().upper() == "PO"}
    for ln, r in R:
        if r["type"].strip().upper() != "INVOICE":
            continue
        hit = pos.get(r["po_no"].strip().lower())
        if hit and hit[1].d("date") and r.d("date") and hit[1].d("date") > r.d("date"):
            days = (hit[1].d("date") - r.d("date")).days
            out.append(Hit("Approvals", "1.1", "Medium", _f(r["amount"]),
                           f"{hit[1]['doc_no']} raised {days} days after invoice {r['doc_no']}", "approvals.csv", hit[0],
                           [("approvals.csv", ln)]))
    return out


def cross_file(files: dict[str, list[str]]) -> list[Hit]:
    """Checks that need two files: payments to vendors that are missing from, or inactive in, the vendor master."""
    P, V = files.get("payments.csv"), files.get("vendors.csv")
    if not P or not V or missing_columns("payments.csv", P) or missing_columns("vendors.csv", V):
        return []
    master = {r["vendor_id"].strip(): (ln, r) for ln, r in rows(V)}
    out = []
    for ln, r in rows(P):
        vid = r.get("vendor_id", "").strip()
        if not vid or _f(r["paid_amount"]) <= 0:
            continue
        if vid not in master:
            out.append(Hit("Payments", "4.1", "Medium", _f(r["paid_amount"]),
                           f"{r['payment_id']} paid vendor {vid}, which is not in the vendor master", "payments.csv", ln))
        elif master[vid][1]["status"].strip().upper() != "ACTIVE":
            changed, paid_on = master[vid][1].d("status_changed_on"), r.d("pay_date")
            if not (changed and paid_on and paid_on <= changed):
                out.append(Hit("Payments", "4.1", "Medium", _f(r["paid_amount"]),
                               f"{r['payment_id']} paid {master[vid][1]['status'].lower()} vendor {vid}", "payments.csv", ln))
    return out


def reconcile(files: dict[str, list[str]], L: dict = LIMITS) -> list[Hit]:
    """Match the bank statement to recorded payments: one-to-one first, then credits to reversals, then batch
    transfers that pay several invoices at once. Bank debits that are clearly not supplier payments (fees, payroll,
    tax, card settlements, own-account transfers) are left out. What remains is money that left with no record
    (High), and recorded payments missing from the statement (Low - usually timing)."""
    B, P = files.get("bank_statement.csv"), files.get("payments.csv")
    if not B or not P or missing_columns("bank_statement.csv", B) or missing_columns("payments.csv", P):
        return []
    bank = [(ln, r) for ln, r in rows(B) if r.d("date")]
    if not bank:
        return []
    signed = any(_f(r["amount"]) < 0 for _, r in bank)
    # money out is negative when the file has signs; when every amount is positive, every line is money out
    lines_ = [(ln, r, -_f(r["amount"]) if signed else _f(r["amount"])) for ln, r in bank]
    pays = [(ln, r, _f(r["paid_amount"])) for ln, r in rows(P) if r.d("pay_date")]
    first, last = min(r.d("date") for _, r in bank), max(r.d("date") for _, r in bank)
    by_cents: dict[int, list] = {}
    for pl, p, pa in pays:                      # index by amount so big files stay fast
        by_cents.setdefault(round(pa * 100), []).append((pl, p))
    used: set[int] = set()
    unmatched = []
    for ln, r, amt in lines_:
        if amt > 0 and NON_AP_BANK.search(r["description"] + " " + r["reference"]):
            continue
        d, ref = r.d("date"), (r["reference"] + " " + r["description"]).lower()
        cands = [(pl, p) for pl, p in by_cents.get(round(amt * 100), []) if pl not in used
                 and -L["bank_days"] <= (d - p.d("pay_date")).days <= L["bank_days"]]
        cands.sort(key=lambda c: (c[1]["payment_id"].lower() not in ref, c[1]["invoice_no"].lower() not in ref,
                                  abs((d - c[1].d("pay_date")).days)))
        if cands:
            used.add(cands[0][0])
        elif amt > 0:
            unmatched.append((ln, r, amt))
    out = []
    for ln, r, amt in unmatched:                 # batch transfers: several open payments adding up to the debit
        d = r.d("date")
        pool = [(pl, pa) for pl, p, pa in pays if pl not in used and pa > 0
                and 0 <= (d - p.d("pay_date")).days <= L["bank_days"]][:18]
        target = round(amt * 100)
        combo = next((c for k in range(2, min(6, len(pool)) + 1) for c in combinations(pool, k)
                      if sum(round(pa * 100) for _, pa in c) == target), None)
        if combo:
            used.update(pl for pl, _ in combo)
        else:
            out.append(Hit("Payments", "5.5", "High", amt,
                           f"Bank payment of ${amt:,.2f} on {r['date']} ({r['description'].strip()}) has no recorded payment",
                           "bank_statement.csv", ln))
    for pl, p, pa in pays:
        pdte = p.d("pay_date")
        if pl not in used and pa > 0 and first <= pdte and pdte + timedelta(days=L["bank_days"]) <= last:
            out.append(Hit("Payments", "5.5", "Low", pa,
                           f"{p['payment_id']} (${pa:,.2f} to {p['supplier']}) is not on the bank statement", "payments.csv", pl))
    return out


def vendors(lines: list[str], L: dict = LIMITS) -> list[Hit]:
    R, out, tax = rows(lines), [], {}
    for ln, r in R:
        paid = _f(r["last_paid_amount"])
        if r["bank_changed_on"].strip() and r["bank_verified"].strip().upper() == "NO" and r["last_paid_on"].strip():
            out.append(Hit("Vendors", "4.3", "High", paid, f"{r['vendor_id']} paid to an unverified new bank account", "vendors.csv", ln))
        if r["status"].strip().upper() != "ACTIVE" and r["last_paid_on"].strip():
            changed, paid_on = r.d("status_changed_on"), r.d("last_paid_on")
            if not (changed and paid_on and paid_on <= changed):       # closed after its final payment is normal
                out.append(Hit("Vendors", "4.1", "Medium", paid,
                               f"{r['status'].title()} vendor {r['vendor_id']} was paid ${paid:,.2f} on {r['last_paid_on']}",
                               "vendors.csv", ln))
        if r["w9_on_file"].strip().upper() == "NO" and r["last_paid_on"].strip():
            out.append(Hit("Vendors", "4.4", "Low", paid, f"{r['vendor_id']} paid ${paid:,.2f} with no tax form on file", "vendors.csv", ln))
        t = re.sub(r"[^0-9a-z]", "", r["tax_id"].lower())
        if t:
            remit = re.search(r"remit[ -]?to", r["name"], re.I)
            if t in tax and not remit and not re.search(r"remit[ -]?to", tax[t][1]["name"], re.I):
                other = tax[t][1]
                out.append(Hit("Vendors", "4.2", "High", paid + _f(other["last_paid_amount"]),
                               f"Vendor records {other['vendor_id']} and {r['vendor_id']} share tax ID {r['tax_id'].strip()}",
                               "vendors.csv", ln, [("vendors.csv", tax[t][0])]))
            elif t not in tax and not remit:
                tax[t] = (ln, r)
    return out


def rows_by_line(R: list[tuple[int, dict]], ln: int) -> dict:
    return next(r for n, r in R if n == ln)


def contracts(lines: list[str], L: dict = LIMITS) -> list[Hit]:
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


def expenses(lines: list[str], L: dict = LIMITS) -> list[Hit]:
    out, seen = [], {}
    R = rows(lines)
    trips: dict[str, list[date]] = {}
    for _, r in R:
        if r.d("date") and (re.search(r"\b(travel|airfare|hotel)\b", r["category"], re.I) or TRAVEL.search(r["notes"])) \
                and r["notes"].strip().lower() != "business purpose recorded":
            trips.setdefault(r["employee"].strip().lower(), []).append(r.d("date"))
    for ln, r in R:
        amt = _f(r["amount"])
        if r["category"].strip().upper() == "MEAL":
            people = int(_f(r["people"])) if r["people"].strip() else 0
            if not people:                            # head count may be in the notes: "Team dinner - 5 people"
                m = re.search(r"(\d+)\s*(people|persons|guests|pax|attendees)|\((\d+)\)", r["notes"], re.I)
                people = int(m.group(1) or m.group(3)) if m else 0
            if people and amt / people > L["meal_limit"]:
                out.append(Hit("Expenses", "6.1", "Low", amt,
                               f"Meal at ${amt / people:,.2f} per person (limit ${L['meal_limit']:,.2f})", "expenses.csv", ln))
        if amt > L["receipt_limit"] and not r["receipt_ref"].strip():
            out.append(Hit("Expenses", "6.2", "Medium", amt, f"Claim {r['claim_id']} of ${amt:,.2f} has no receipt", "expenses.csv", ln))
        if PERSONAL.search(r["notes"]) and not PROFESSIONAL.search(r["notes"]):
            out.append(Hit("Expenses", "6.3", "Low", amt, f"Personal item claimed: {r['notes'].strip().lower()}", "expenses.csv", ln))
        ref = r["receipt_ref"].strip()
        if ref:
            if ref in seen:
                out.append(Hit("Expenses", "6.5", "High", amt, f"The same receipt {ref} was claimed twice", "expenses.csv", ln,
                               [("expenses.csv", seen[ref])]))
            else:
                seen[ref] = ln
        d = r.d("date")
        if d and d.weekday() >= 5 and not r["notes"].strip():
            # a taxi, meal or hotel on the weekend of a business trip is normal; a weekend purchase of supplies is not
            near_trip = TRAVEL_SPEND.search(r["category"]) and any(
                0 < abs((d - t).days) <= 1 for t in trips.get(r["employee"].strip().lower(), []))
            if not near_trip:
                out.append(Hit("Expenses", "6.4", "Low", amt, f"Weekend expense {r['claim_id']} with no reason given", "expenses.csv", ln))
    return out


CHECKS = {"Payments": payments, "Approvals": approvals, "Vendors": vendors, "Contracts": contracts, "Expenses": expenses}

INNOCENT = {
    "5.1": "A credit note or agreed price change may explain the difference.",
    "5.2": "The first payment may have bounced and been legitimately re-sent.",
    "5.3": "An advance payment may have been agreed with the supplier.",
    "5.4": "The bank may have processed an earlier instruction on a non-working day.",
    "5.5": "The payment may be recorded under another reference, or the bank date may fall outside the window.",
    "1.1": "The purchase order may exist outside this export.",
    "1.2": "A deputy may have approved under an agreed cover arrangement.",
    "1.3": "A director may have approved by email outside the system.",
    "1.4": "The invoices may be for separate, unrelated jobs.",
    "4.1": "The vendor record may have been closed after the payment was agreed.",
    "4.2": "Two entities may legitimately share a tax ID (a parent and a branch).",
    "4.3": "The supplier may have been verified by phone outside the system.",
    "4.4": "The tax form may be held on paper.",
    "7.1": "A variation order may have been agreed verbally.",
    "7.2": "A rate change may have been agreed after the contract copy was filed.",
    "7.3": "The contract may have been extended in writing.",
    "7.4": "A renewal may have been agreed on purpose.",
    "8.3": "The second copy may be a reminder rather than a new bill.",
    "8.2": "The supplier may really have changed banks and told Treasury separately.",
    "8.1": "An approval may exist under a slightly different invoice number.",
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
    "5.5": "Find who authorised the bank payment; if nobody did, ask the bank to recall it.",
    "1.1": "Ask the requester for the purchase order, or record an approved exception.",
    "1.2": "Have a different, authorised person re-approve it.",
    "1.3": "Get director sign-off recorded against the document.",
    "1.4": "Treat the invoices as one order and get director approval.",
    "4.1": "Check why an inactive vendor was paid; recover the payment if it was wrong.",
    "4.2": "Merge the vendor records and check no payment was made twice.",
    "4.3": "Hold payments until Treasury records a call-back verification.",
    "4.4": "Obtain the tax form before any further payment.",
    "7.1": "Dispute the charge and ask for a credit note.",
    "7.2": "Dispute the difference and ask for a corrected invoice.",
    "7.3": "Check for a written extension; otherwise refuse the invoice.",
    "7.4": "Decide whether to keep the contract and record the decision.",
    "8.3": "Pay the invoice once and mark the copy as a duplicate.",
    "8.2": "Do not pay; call the supplier on the number in the vendor master to confirm the bank details.",
    "8.1": "Hold the invoice until it is matched to an approved record.",
    "6.3": "Do not reimburse personal items.",
    "6.1": "Ask the employee for the attendee list or reduce the claim to the limit.",
    "6.2": "Ask for the receipt before reimbursing.",
    "6.4": "Ask the employee for the business reason.",
    "6.5": "Reimburse the receipt once only and ask both employees to explain.",
}


def run_area(area: str, files: dict[str, list[str]], limits: dict | None = None) -> list[Hit]:
    L = {**LIMITS, **(limits or {})}
    name = FILES[area]
    lines = files.get(name)
    if not lines or missing_columns(name, lines):
        return []
    if area == "Invoices":
        from . import invoices
        return invoices.check(files)
    out = CHECKS[area](lines, L)
    if area == "Payments":
        out += cross_file(files) + reconcile(files, L)
    return out


def analyze(files: dict[str, list[str]], limits: dict | None = None) -> list[Hit]:
    out = []
    for area in FILES:
        out += run_area(area, files, limits)
    return out
