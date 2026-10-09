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
import math
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import cast

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
              tolerance=1.0, materiality=50.0, new_vendor_days=30)
DAY_LIMITS = ("split_days", "bank_days", "new_vendor_days")       # whole days; the other limits are money
LIMIT_LABELS = dict(po_limit="Purchase order needed above ($)", director_limit="Director approval needed above ($)",
                    meal_limit="Meal limit per person ($)", receipt_limit="Receipt needed above ($)",
                    split_days="Split-order window (days)", bank_days="Bank clearing window (days)",
                    tolerance="Ignore amount differences up to ($)", materiality="Minor-item threshold ($)",
                    new_vendor_days="New vendor: second review for big payments within (days)")
# Bills that normally have no purchase order. Editable under Settings > Policy.
# Phrases, not single words, where a single word also names ordinary suppliers ("Power Tools Direct", "Northern Gas
# Turbine Parts" must still need a PO).
PO_EXEMPT_WORDS = ["rent", "lease", "landlord", "property management", "properties", "utility", "utilities",
                   "power and light", "power company", "electricity", "electric company", "water company", "water board",
                   "water utility", "gas company", "gas and electric", "gas board", "energy", "insurance", "telecom",
                   "broadband", "subscription", "council", "tax", "legal"]
NO_PO = {"", "N/A", "NA", "-", "--", "NONE", "TBC", "TBA", "0", "NIL", "PENDING"}
INVOICE_TYPES = {"", "INVOICE", "INV", "BILL"}
DIRECTOR = re.compile(r"\b(director|cfo|ceo|chief (financial|executive|operating) officer|managing partner)\b", re.I)


def has_po(r) -> bool:
    return r["po_no"].strip().upper() not in NO_PO
# bank lines that are not supplier payments. Each phrase is specific on purpose: a bare "fees", "tax", "visa" or
# "transfer to" also describes "Consulting fees - Smith", "Visa Logistics Ltd" or "TRANSFER TO J SMITH", and an
# unrecorded payment to a person is exactly what the bank check exists to catch.
NON_AP_BANK = re.compile(
    r"\b(bank (?:charges?|fees?)|account (?:fees?|charges?)|service charges?|overdraft|interest (?:charges?|paid)|"
    r"payroll|salar(?:y|ies)|wages|irs|eftps|hmrc|paye|vat (?:payment|return)|(?:federal|sales|corporation|payroll) tax|"
    r"tax payment|(?:card|amex|visa|mastercard) settlement|amex card|credit card payment|"
    r"transfer to (?:own|our|savings|deposit|reserve)\b|own account|internal transfer|savings acc(?:oun)?t|"
    r"loan (?:repayment|payment|instal?ments?)|(?:wire|merchant|card|transaction|processing|stripe|paypal) fees?|"
    r"amex (?:e-?payment|payment)|state tax|tax board|franchise tax|^\s*interest(?=\s*(?:$|\d|charge|paid|debit))|"
    r"interest (?:debit|charged))\b(?!\s+(?:partners|ltd|limited|inc|llc|group|media|consulting|services))", re.I)
PERSONAL = re.compile(r"\b(gym|membership|personal(?! protective)|netflix|spa|haircut|for myself)\b", re.I)
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
    for col in (c for c in head if "amount" in c.lower()):
        if decimal_comma([r[col] for _, r in out]):
            for _, r in out:                       # in a decimal-comma file "1.234" is one thousand two hundred ...
                if EU_THOUSANDS.match(r[col].strip()):
                    r[col] = r[col].strip().replace(".", "")
    return out


EU_THOUSANDS = re.compile(r"^-?\d{1,3}(\.\d{3})+$")


def decimal_comma(values: list[str]) -> bool:
    """True when a column writes decimals with a comma (1.234,56 / 99,00) and never with a point (99.00)."""
    v = [str(x).strip() for x in values if str(x).strip()]
    return any(re.search(r"\d,\d{2}(\s*[A-Za-z€£$)-]*)?$", x) for x in v) and \
        not any(re.search(r"\d\.\d{2}(\s*[A-Za-z€£$)-]*)?$", x) for x in v)


def missing_columns(name: str, lines: list[str]) -> list[str]:
    if name not in REQUIRED or not lines:
        return []
    head = set(next(csv.reader([lines[0]])))
    return [c for c in REQUIRED[name] if c not in head]


MONTH_DATE = re.compile(r"^(\d{1,2})[ -]([A-Za-z]{3,9})\.?[ -](\d{4})\b|^([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})\b")


@lru_cache(maxsize=65536)
def _d(s: str, dayfirst: bool = False) -> date | None:
    """Dates as YYYY-MM-DD, YYYY/MM/DD, slash/dot/dash dates in the file's day or month order, or with the month
    as a word ('02 Sep 2026', '2-Sep-2026', 'Sep 2, 2026'). A time after the date is ignored."""
    t = str(s).strip()
    m = MONTH_DATE.match(t)
    if m:
        day, mon, year = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(5), m.group(4), m.group(6))
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(f"{int(day)} {mon[:3] if fmt == '%d %b %Y' else mon} {year}", fmt).date()
            except ValueError:
                pass
        return None
    t = t[:10]
    fmts = ("%Y-%m-%d", "%Y/%m/%d") + (("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y") if dayfirst else ("%m/%d/%Y", "%d.%m.%Y"))
    for fmt in fmts:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None


MAX_AMOUNT = 1e13
EU_NUMBER = re.compile(r"^-?\(?[$€£]?\d{1,3}(\.\d{3})*,\d{2}\)?$")


CURRENCY = re.compile(r"^(?:USD|EUR|GBP|CAD|AUD|CHF)|(?:USD|EUR|GBP|CAD|AUD|CHF)$", re.I)


def _f(s: str | float) -> float:
    t = str(s).strip().replace("\u2212", "-").replace("$", "").replace("£", "").replace("€", "").replace(" ", "").replace("\u00a0", "")
    t = t.replace("'", "")                       # 1'234.50 (Swiss)
    t = CURRENCY.sub("", t)                      # USD 1,200.00 / 1.200,00 EUR
    sign = 1
    if t.upper().endswith("CR"):                 # 120.00 CR: a credit
        t, sign = t[:-2], -1
    elif t.upper().endswith("DR"):
        t = t[:-2]
    if t.endswith("-") and not t.startswith("-"):   # 120.00- (trailing minus, common in ERP exports)
        t, sign = t[:-1], -sign
    if EU_NUMBER.match(t) or re.fullmatch(r"-?\(?\d+,\d{2}\)?", t):   # 1.234,50 or 1234,50 -> 1234.50
        t = t.replace(".", "").replace(",", ".")
    t = t.replace(",", "")
    neg = t.startswith("(") and t.endswith(")")
    try:
        v = -float(t.strip("()")) if neg else float(t)
    except ValueError:
        return 0.0
    v *= sign
    return v if math.isfinite(v) and abs(v) < MAX_AMOUNT else 0.0    # 'nan', 'inf' or a 40-digit typo is not money


@lru_cache(maxsize=65536)
def norm_name(s: str) -> str:
    """'CALDER LOGISTICS LTD.' and 'Calder Logistics' compare equal."""
    words = re.sub(r"[^a-z0-9 ]+", " ", str(s).lower().replace("&", " and ")).split()
    while words and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(w for w in words if w != "the")


NAME_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "mr", "mrs", "ms", "dr"}


def _person(s: str) -> list[str]:
    """Name parts, lower case, accents removed: 'García, José Jr.' -> ['jose', 'garcia']."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()
    if "," in t:                                   # "Smith, John" -> "John Smith"
        last, _, first = t.partition(",")
        t = f"{first} {last}"
    return [w for w in re.findall(r"[a-z]+", t) if w not in NAME_SUFFIX]


def same_person(a: str, b: str) -> bool:
    """'K. Lowe', 'Kate Lowe', 'Lowe, Kate' and 'klowe' are one person; 'Kate Lowe' and 'Kim Lowe' are not, nor
    'K. Lowe' and 'K. Lowry'."""
    ta, tb = _person(a), _person(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    if len(ta) == 1 or len(tb) == 1:               # a user name such as "klowe" or "kate.lowe" written as one word
        one, full = (ta[0], tb) if len(ta) == 1 else (tb[0], ta)
        return len(full) >= 2 and one in (full[0][0] + full[-1], full[0] + full[-1], full[-1] + full[0][0])
    if ta[-1] != tb[-1]:
        return False
    fa, fb = ta[0], tb[0]
    return fa == fb or ((len(fa) == 1 or len(fb) == 1) and fa[0] == fb[0])     # an initial matches its full name


def po_exempt(vendor: str, L: dict) -> bool:
    n = norm_name(vendor)
    if n in {norm_name(v) for v in L.get("po_exempt_vendors", [])}:
        return True
    words = L.get("po_exempt_words", PO_EXEMPT_WORDS)

    def phrase(w: str) -> str:        # "power and light" also matches "Power & Light" and "Power Light"
        parts = [re.escape(x) for x in re.findall(r"[a-z0-9]+", w.lower()) if x != "and"]
        return r"\b" + r"\s+(?:and\s+)?".join(parts) + r"\b"
    full = " ".join(re.findall(r"[a-z0-9]+", str(vendor).lower().replace("&", " and ")))   # keeps "company", "co"
    return any(re.search(phrase(w), full) for w in words if re.search(r"[a-z0-9]", w.lower()))


def inv_key(s: str) -> str:
    """'INV-001', 'inv 1' and ' INV001 ' are the same invoice number."""
    parts = re.findall(r"[a-z]+|\d+", str(s).lower())
    return "-".join(p.lstrip("0") or "0" if p.isdigit() else p for p in parts)


def payments(lines: list[str], L: dict = LIMITS) -> list[Hit]:
    """Duplicates count money, not rows: instalments and void-and-reissue add up to the invoice and are fine;
    a payment that takes the total above the invoice is a duplicate. Only overpayments beyond the tolerance are
    flagged - paying less (a discount, a short payment) is not money lost."""
    R, out = rows(lines), []
    groups: dict[tuple, dict] = {}
    tol = L.get("tolerance", 1.0)
    for ln, r in R:
        pa, ia = _f(r["paid_amount"]), _f(r["invoice_amount"])
        inv = r["invoice_no"].strip()
        if inv:
            who = r["vendor_id"].strip().upper() or norm_name(r["supplier"])     # one vendor id, however it is spelt
            g = groups.setdefault((who, inv_key(inv)), dict(net=0.0, inv=0.0, lines=[], cand=[]))
            g["net"] += pa
            if ia > 0 and g["inv"] == 0:
                # the invoice amount on its first line: later lines may show an open balance (instalments), and a
                # later line claiming a bigger invoice must not hide a duplicate
                g["inv"] = ia
            if pa > 0 and g["lines"] and g["net"] > g["inv"] + tol:
                g["cand"].append(Hit("Payments", "5.2", "High", pa, f"{inv.upper()} was paid more than once", "payments.csv",
                                     ln, [("payments.csv", x) for x in g["lines"][-10:]]))   # the latest 10 is enough
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
    for g in groups.values():          # judged on the whole file: a reversal further down undoes an earlier re-issue
        if g["cand"] and g["net"] > g["inv"] + tol:
            out += g["cand"]
    out += _near_duplicates(R, groups, L)
    return out


CONFUSABLE = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"})
PATTERN_CLAUSES = {"1.6", "4.5", "5.6", "6.6"}   # worth a look, not a breach on their own: never "Confirmed" by rules


def _inv_text(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def _num(s: str) -> int | None:
    d = re.findall(r"\d+", s)
    return int(d[-1]) if d and len(d[-1]) <= 12 else None


def similar_invoice(a: str, b: str) -> bool:
    """Two invoice numbers that are probably one invoice keyed twice: one inside the other ('6100' and 'ASH-6100',
    'ASH-6100' and 'ASH-6100A'), two neighbouring digits swapped far apart in the series ('ASH-6100' and 'ASH-6010'),
    or a letter typed for a look-alike digit ('INV-1O01'). Numbers close together in a series are NOT similar:
    'INV-1001' and 'INV-1002', or 'SC-4101' and 'SC-4110', are normally the next invoices."""
    return _similar_text(_inv_text(a), _inv_text(b))


def _similar_text(x: str, y: str) -> bool:
    if not x or not y or x == y:
        return False
    if min(len(x), len(y)) >= 4 and (x in y or y in x):
        return True
    if len(x) == len(y) >= 4:
        diff = [i for i in range(len(x)) if x[i] != y[i]]
        if len(diff) == 2 and diff[1] == diff[0] + 1 and x[diff[0]] == y[diff[1]] and x[diff[1]] == y[diff[0]]:
            nx, ny = _num(x), _num(y)
            return nx is None or ny is None or abs(nx - ny) > 50
        if x.translate(CONFUSABLE) == y.translate(CONFUSABLE):
            return True
    return False


NEAR_WINDOW = 7          # days between two payments of a near-duplicate pair (when there is no invoice date)
NEAR_NEIGHBOURS = 50     # payments compared on each side: keeps a file of thousands of equal amounts fast


def _near_duplicates(R, groups: dict, L: dict) -> list[Hit]:
    """The same supplier paid the same amount twice under invoice numbers that differ only the way a re-keyed number
    does, with the same invoice date (or, without one, paid within a week). Exact repeats are caught above. Left
    alone: a series of equal bills (numbers run on), instalments under suffixed numbers (together they do not exceed
    the invoice), and anything voided."""
    tol = L.get("tolerance", 1.0)
    buckets: dict[tuple, list] = {}
    for ln, r in R:
        pa, inv = _f(r["paid_amount"]), r["invoice_no"].strip()
        if pa < L.get("materiality", 50.0) or not inv:
            continue
        who = r["vendor_id"].strip().upper() or norm_name(r["supplier"])
        key = inv_key(inv)
        g = groups.get((who, key))
        if g and g["net"] <= tol:
            continue                                  # voided or reversed: nothing was paid on this number
        # a re-keyed invoice keeps its invoice date, so rows with one are only compared with the same date
        idate = r.d("invoice_date")
        buckets.setdefault((who, round(pa, 2), idate), []).append(
            (r.d("pay_date") or date.max, ln, r, key, _inv_text(inv), _f(r["invoice_amount"])))
    out, pairs = [], set()
    for (who, pa, idate), items in buckets.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda x: (x[0], x[1]))
        for i, (pday, ln, r, key, text, billed) in enumerate(items):
            for opday, oln, o, okey, otext, obilled in items[i + 1:i + 1 + NEAR_NEIGHBOURS]:
                if idate is None and pday != date.max and opday != date.max and (opday - pday).days > NEAR_WINDOW:
                    break                              # sorted by payment date: nothing further on is close enough
                if key == okey or not _similar_text(text, otext):
                    continue
                pair = (who, frozenset((key, okey)))
                if pair in pairs:
                    continue
                bill = max(billed, obilled)
                if bill and pa * 2 <= bill + tol:
                    continue                           # two parts of one bill: instalments, not a repeat
                pairs.add(pair)
                (fl, fa), (sl, sa) = sorted([(ln, r["invoice_no"].strip()), (oln, o["invoice_no"].strip())])
                out.append(Hit("Payments", "5.6", "Medium", pa,
                               f"{fa} and {sa} look like one invoice paid twice "
                               f"(${pa:,.2f} each to {r['supplier'].strip() or who})", "payments.csv", sl,
                               [("payments.csv", fl)]))
    return out


def approvals(lines: list[str], L: dict = LIMITS) -> list[Hit]:
    out = []
    R = rows(lines)
    for ln, r in R:
        amt = _f(r["amount"])
        label = r["doc_no"] or r["record_id"]
        if same_person(r["requested_by"], r["approved_by"]):
            out.append(Hit("Approvals", "1.2", "High", amt, f"{label} raised and approved by the same person", "approvals.csv", ln))
        if amt > L["po_limit"] and not has_po(r) and r["type"].strip().upper() in INVOICE_TYPES \
                and not po_exempt(r["vendor"], L):
            out.append(Hit("Approvals", "1.1", "Medium", amt, f"{label} for ${amt:,.2f} has no purchase order", "approvals.csv", ln))
        if amt > L["director_limit"] and not DIRECTOR.search(r["approver_role"]):
            out.append(Hit("Approvals", "1.3", "Medium", amt,
                           f"{label} for ${amt:,.2f} approved by a {r['approver_role'] or 'non-director'}, not a director",
                           "approvals.csv", ln))
    out += _split_orders(R, L) + _po_after_invoice(R) + _just_under(R, L)
    return out


def person_key(s: str) -> str:
    """One key per spelling of a name ('Lowe, Kate' and 'Kate Lowe' agree; 'K. Lowe' and 'Kim Lowe' do not), so
    grouping never merges two people the way a looser match could."""
    return " ".join(sorted(_person(s)))


def _just_under(R: list[tuple[int, Row]], L: dict) -> list[Hit]:
    """One requester raising several approvals just under the director limit (within 4%), to different suppliers,
    none approved by a director: each passes, together they look like the limit being steered around. Same-supplier
    splits are caught by the split check."""
    lim = L["director_limit"]
    by: dict[str, list[tuple[int, Row]]] = {}
    for ln, r in R:
        amt = _f(r["amount"])
        if r["type"].strip().upper() in INVOICE_TYPES and lim * 0.96 <= amt < lim and person_key(r["requested_by"]) \
                and not DIRECTOR.search(r["approver_role"]):
            by.setdefault(person_key(r["requested_by"]), []).append((ln, r))
    out = []
    for items in by.values():
        if len(items) >= 2 and len({norm_name(r["vendor"]) for _, r in items}) >= 2:
            last = max(items, key=lambda x: x[0])
            out.append(Hit("Approvals", "1.6", "Low", round(sum(_f(r["amount"]) for _, r in items), 2),
                           f"{last[1]['requested_by'].strip()} raised {len(items)} approvals just under the "
                           f"${lim:,.0f} director limit", "approvals.csv", last[0],
                           [("approvals.csv", ln) for ln, _ in items if ln != last[0]]))
    return out


def _split_orders(R: list[tuple[int, Row]], L: dict) -> list[Hit]:
    """Several invoices from one vendor raised by the same person, each under the director limit and without a PO,
    close together in time, adding up to more than the director limit. Bills that never carry a PO do not count."""
    out = []
    groups: dict[tuple, list] = {}
    for ln, r in R:
        if r["type"].strip().upper() in INVOICE_TYPES and not has_po(r) and r.d("date") \
                and _f(r["amount"]) <= L["director_limit"] and not po_exempt(r["vendor"], L):
            v = norm_name(r["vendor"])
            who = next((k[1] for k in groups if k[0] == v and same_person(k[1], r["requested_by"])),
                       r["requested_by"].strip())      # "K. Lowe" and "Kate Lowe" are one requester
            groups.setdefault((v, who), []).append((ln, r))
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
        if hit and (po_d := hit[1].d("date")) and (inv_d := r.d("date")) and po_d > inv_d:
            days = (po_d - inv_d).days
            out.append(Hit("Approvals", "1.1", "Medium", _f(r["amount"]),
                           f"{hit[1]['doc_no']} raised {days} days after invoice {r['doc_no']}", "approvals.csv", hit[0],
                           [("approvals.csv", ln)]))
    return out


def cross_file(files: dict[str, list[str]]) -> list[Hit]:
    """Checks that need two files: payments to vendors that are missing from, or inactive in, the vendor master."""
    P, V = files.get("payments.csv"), files.get("vendors.csv")
    if not P or not V or missing_columns("payments.csv", P) or missing_columns("vendors.csv", V):
        return []
    VR = rows(V)
    master = {r["vendor_id"].strip().upper(): (ln, r) for ln, r in VR}
    by_name: dict[str, list] = {}
    for _ln, r in VR:
        by_name.setdefault(norm_name(r["name"]), []).append(r["vendor_id"].strip().upper())
    out = []
    for ln, r in rows(P):
        vid = r.get("vendor_id", "").strip().upper()
        if not vid:                              # no id on the payment: use the name, when it fits exactly one vendor
            n = norm_name(r["supplier"])
            same = by_name.get(n, [])
            vid = same[0] if len(same) == 1 else ""
            near = same or [k for k in by_name if k and n and (n.startswith(k + " ") or k.startswith(n + " "))]
            if not near and n and _f(r["paid_amount"]) > 0:
                # not even a longer or shorter form of a known name: money went to someone the master does not know
                out.append(Hit("Payments", "4.1", "Low", _f(r["paid_amount"]),
                               f"{r['payment_id']} paid {r['supplier'].strip()}, who has no vendor id and is not in the "
                               "vendor master", "payments.csv", ln))
                continue
        if not vid or _f(r["paid_amount"]) <= 0:
            continue
        if vid not in master:
            out.append(Hit("Payments", "4.1", "Medium", _f(r["paid_amount"]),
                           f"{r['payment_id']} paid vendor {vid}, which is not in the vendor master", "payments.csv", ln))
        elif master[vid][1]["status"].strip() and not master[vid][1]["status"].strip().upper().startswith("ACTIVE"):
            changed, paid_on = master[vid][1].d("status_changed_on"), r.d("pay_date")
            if not (changed and paid_on and paid_on <= changed):
                out.append(Hit("Payments", "4.1", "Medium", _f(r["paid_amount"]),
                               f"{r['payment_id']} paid {master[vid][1]['status'].lower()} vendor {vid}", "payments.csv", ln))
    return out


BATCH_WORDS = re.compile(r"\b(bacs|batch|bulk|payment run|pay run|ach|multiple payments)\b", re.I)
BANK_CENTS = 5          # a bank line within 5 cents of a payment is that payment (rounding, not a missing payment)
BATCH_POOL, BATCH_MAX = 40, 25     # a batch transfer may pay up to 25 of the 40 open payments nearest its date


def _subset(pool: list[tuple[int, int]], target: int, kmin: int = 2, kmax: int = BATCH_MAX, budget: int = 300_000) -> list[int] | None:
    """Line numbers of 2-6 payments (cents) that add up to target, or None. Depth-first over amounts sorted
    small to large, so a branch stops as soon as it overshoots; a step budget keeps odd files from taking minutes."""
    items = sorted(pool, key=lambda x: x[1])
    steps = 0

    def go(i: int, left: int, picked: list[int]) -> list[int] | None:
        nonlocal steps
        if left == 0 and len(picked) >= kmin:
            return list(picked)
        if len(picked) == kmax or left <= 0:
            return None
        for j in range(i, len(items)):
            steps += 1
            if steps > budget or items[j][1] > left:
                return None
            picked.append(items[j][0])
            hit = go(j + 1, left - items[j][1], picked)
            picked.pop()
            if hit:
                return hit
        return None

    return go(0, target, []) if target > 0 else None


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
    drcr = any(re.search(r"(DR|CR)\s*$", r["amount"].strip(), re.I) for _, r in bank)
    if drcr:      # "500.00 DR" is money out, "250.00 CR" money in: the suffix decides, not the sign _f gives CR
        lines_ = [(ln, r, abs(_f(r["amount"])) * (-1 if r["amount"].strip().upper().endswith("CR") else 1)) for ln, r in bank]
    signed = any(_f(r["amount"]) < 0 for _, r in bank)
    # money out is negative when the file has signs; when every amount is positive, every line is money out
    if not drcr:
        lines_ = [(ln, r, -_f(r["amount"]) if signed else _f(r["amount"])) for ln, r in bank]
    pays = [(ln, r, _f(r["paid_amount"])) for ln, r in rows(P) if r.d("pay_date")]
    first, last = min(cast(date, r.d("date")) for _, r in bank), max(cast(date, r.d("date")) for _, r in bank)
    by_cents: dict[int, list] = {}
    for pl, p, pa in pays:                      # index by amount, then date, so big files stay fast
        by_cents.setdefault(round(pa * 100), []).append((p.d("pay_date"), pl, p))
    for v in by_cents.values():
        v.sort(key=lambda x: (x[0], x[1]))
    when = {k: [x[0] for x in v] for k, v in by_cents.items()}
    used: set[int] = set()

    def match(ln, r, amt, deltas) -> bool:
        d, ref = cast(date, r.d("date")), (r["reference"] + " " + r["description"]).lower()
        k = round(amt * 100)
        for dk in deltas:
            bucket = by_cents.get(k + dk, [])
            if not bucket:
                continue
            lo = bisect_left(when[k + dk], d - timedelta(days=L["bank_days"]))
            hi = bisect_right(when[k + dk], d + timedelta(days=L["bank_days"]))
            best, best_key = None, None
            for pd_, pl, p in bucket[lo:hi]:     # one pass; a reference match wins, then the nearest date
                if pl in used:
                    continue
                pid, inv = p["payment_id"].strip().lower(), p["invoice_no"].strip().lower()
                key = (not (pid and pid in ref), not (inv and inv in ref), abs((d - pd_).days), pl)
                if best_key is None or key < best_key:
                    best, best_key = pl, key
                    if key[:3] == (False, False, 0):
                        break
            if best is not None:
                used.add(best)
                return True
        return False
    # every exact amount first, across the whole statement, then a few cents off: otherwise a batch transfer that
    # happens to be within cents of one supplier's payment takes it, and that supplier's own bank line is left over
    todo = [(ln, r, amt) for ln, r, amt in lines_ if not (amt > 0 and NON_AP_BANK.search(r["description"] + " " + r["reference"]))]
    left = [x for x in todo if not match(*x, (0,))]
    near = [dk for dk in sorted(range(-BANK_CENTS, BANK_CENTS + 1), key=abs) if dk]
    unmatched = [(ln, r, amt) for ln, r, amt in left
                 if not (not BATCH_WORDS.search(f"{r['description']} {r['reference']}") and match(ln, r, amt, near))
                 and amt > 0]
    out = []
    by_day = sorted((p.d("pay_date"), pl, round(pa * 100), norm_name(p["supplier"])) for pl, p, pa in pays if pa > 0)
    days = [cast(date, x[0]) for x in by_day]       # pays only holds rows with a pay date
    for ln, r, amt in unmatched:                 # batch transfers: several open payments adding up to the debit
        d = cast(date, r.d("date"))
        lo, hi = bisect_left(days, d - timedelta(days=L["bank_days"])), bisect_right(days, d)
        desc = f"{r['description']} {r['reference']}"
        if BATCH_WORDS.search(desc):        # a payment-run batch: many suppliers in one debit
            pool = [(pl, c) for _, pl, c, _s in reversed(by_day[lo:hi]) if pl not in used][:BATCH_POOL]
            kmax = BATCH_MAX
        else:                               # otherwise only several invoices of the one supplier the line names
            named = norm_name(desc)
            pool = [(pl, c) for _, pl, c, sup in reversed(by_day[lo:hi])
                    if pl not in used and sup and re.search(rf"\b{re.escape(sup)}\b", named)][:18]
            kmax = 6
        combo = _subset(pool, round(amt * 100), kmax=kmax)
        if combo:
            used.update(combo)
        else:
            out.append(Hit("Payments", "5.5", "High", amt,
                           f"Bank payment of ${amt:,.2f} on {r['date']} ({r['description'].strip()}) has no recorded payment",
                           "bank_statement.csv", ln))
    for pl, p, pa in pays:
        pdte = cast(date, p.d("pay_date"))         # pays only holds rows with a pay date
        if pl not in used and pa > 0 and first <= pdte and pdte + timedelta(days=L["bank_days"]) <= last:
            out.append(Hit("Payments", "5.5", "Low", pa,
                           f"{p['payment_id']} (${pa:,.2f} to {p['supplier']}) is not on the bank statement", "payments.csv", pl))
    return out


YES = {"YES", "Y", "TRUE", "1", "VERIFIED", "DONE", "OK"}
NO = {"NO", "N", "FALSE", "0", "MISSING", "NONE", "PENDING"}
PLACEHOLDER_ID = {"na", "none", "unknown", "pending", "tbc", "tba", "notapplicable", "exempt"}


def vendors(lines: list[str], L: dict = LIMITS) -> list[Hit]:
    R, out = rows(lines), []
    tax: dict[str, tuple] = {}
    made_on: dict[date, int] = {}
    for _, r in R:
        if d := r.d("created_on"):
            made_on[d] = made_on.get(d, 0) + 1
    # a set-up date shared by many vendors is when the vendor list was imported into this system, not when they
    # became suppliers: a "new vendor" check against it would flag every big payment of the following month
    migrated = {d for d, n in made_on.items() if n >= 5 and n >= 0.3 * len(R)}
    for ln, r in R:
        paid = _f(r["last_paid_amount"])
        # anything but a clear yes is unverified: blank, "N", "PENDING", "FALSE" are as risky as "NO"
        new_vendor = r["bank_changed_on"].strip() and r["bank_changed_on"].strip() == r["created_on"].strip()
        if r["bank_changed_on"].strip() and r["bank_verified"].strip().upper() not in YES and not new_vendor:
            changed, paid_on = r.d("bank_changed_on"), r.d("last_paid_on")
            if r["last_paid_on"].strip() and not (changed and paid_on and paid_on < changed):
                out.append(Hit("Vendors", "4.3", "High", paid, f"{r['vendor_id']} paid to an unverified new bank account",
                               "vendors.csv", ln))
            else:                                  # changed after the last payment: nothing lost yet, the next one would be
                out.append(Hit("Vendors", "4.3", "Medium", 0.0,
                               f"{r['vendor_id']} changed bank account on {r['bank_changed_on'].strip()} with no "
                               "verification; the next payment would go to it", "vendors.csv", ln))
        status = r["status"].strip().upper()
        if status and not status.startswith("ACTIVE") and r["last_paid_on"].strip():
            changed, paid_on = r.d("status_changed_on"), r.d("last_paid_on")
            if not (changed and paid_on and paid_on <= changed):       # closed after its final payment is normal
                out.append(Hit("Vendors", "4.1", "Medium", paid,
                               f"{r['status'].title()} vendor {r['vendor_id']} was paid "
                               + (f"${paid:,.2f} " if paid else "") + f"on {r['last_paid_on']}",
                               "vendors.csv", ln))
        if r["w9_on_file"].strip().upper() in NO and r["last_paid_on"].strip():
            out.append(Hit("Vendors", "4.4", "Low", paid, f"{r['vendor_id']} paid " + (f"${paid:,.2f} " if paid else "")
                           + "with no tax form on file", "vendors.csv", ln))
        made, paid_on = r.d("created_on"), r.d("last_paid_on")
        if made and paid_on and 0 <= (paid_on - made).days <= L.get("new_vendor_days", 30) \
                and paid > L["director_limit"] and status.startswith("ACTIVE") and made not in migrated:
            out.append(Hit("Vendors", "4.5", "Medium", paid,
                           f"{r['vendor_id']} was paid ${paid:,.2f} {(paid_on - made).days} days after it was set up",
                           "vendors.csv", ln))
        t = re.sub(r"[^0-9a-z]", "", r["tax_id"].lower())
        if t and t not in PLACEHOLDER_ID and set(t) != {"0"} and len(t) >= 4:      # "N/A" or "000000000" is not an ID
            remit = re.search(r"remit[ -]?to", r["name"], re.I)
            if t in tax and not remit and not re.search(r"remit[ -]?to", tax[t][1]["name"], re.I):
                other = tax[t][1]
                out.append(Hit("Vendors", "4.2", "High", paid + _f(other["last_paid_amount"]),
                               f"Vendor records {other['vendor_id']} and {r['vendor_id']} share tax ID {r['tax_id'].strip()}",
                               "vendors.csv", ln, [("vendors.csv", tax[t][0])]))
            elif t not in tax and not remit:
                tax[t] = (ln, r)
    return out



def contracts(lines: list[str], L: dict = LIMITS) -> list[Hit]:
    out = []
    contract: dict[str, list[tuple[int, str, str]]] = {}   # vendor -> list of (line, text)
    for i, ln in enumerate(lines, start=1):
        m = re.match(r"\[CONTRACT (\S+) \| ([^|]+?) \| ([^\]]+)\] (.*)", ln)
        if m:
            contract.setdefault(norm_name(m.group(2)), []).append((i, m.group(3), m.group(4)))
    for i, ln in enumerate(lines, start=1):
        m = re.match(r"\[INVOICE (\S+) \| ([^|]+?) \| ([^\]]+)\] (.*)", ln)
        if not m:
            continue
        inv, vendor, _, body = m.groups()
        vendor = vendor.strip()
        for cline, _where, text in contract.get(norm_name(vendor), []):     # "Calder Logistics Ltd" = "Calder Logistics"
            tail = re.search(r"(\d[\d,]{0,20}\.\d{2})\s*$", body[-60:])       # the amount at the end of the line
            amt = _f(tail.group(1)) if tail else 0.0
            if "surcharge" in body.lower() and re.search(r"surcharge.*excluded|not listed|may not be billed", text, re.I):
                out.append(Hit("Contracts", "7.1", "Medium", amt, f"{inv} adds a ${amt:,.2f} charge the contract does not allow",
                               "contracts.txt", i, [("contracts.txt", cline)]))
            # (?<![\d.]) makes each number start once: without it a long run of digits is retried from every position
            text_, body_ = text[:1000], body[:1000]
            rate_c = re.search(r"rate is [$€£]?(\d{1,7}(?:\.\d{1,4})?)\s*(?:per|an|/)\s*(?:hour|hr)", text_, re.I)
            rate_i = re.search(r"(?:at|@)\s*[$€£]?(\d{1,7}(?:\.\d{1,4})?)\s*(?:per|an|/)\s*(?:hour|hr)", body_, re.I)
            hrs = re.search(r"(?<![\d.])(\d{1,7}(?:\.\d{1,4})?)\s*(?:hours|hrs)\b", body_, re.I)
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
            if end and svc and (svc_end := _d(svc.group(2))) and (c_end := _d(end.group(1))) and svc_end > c_end:
                out.append(Hit("Contracts", "7.3", "Medium", amt, f"{vendor} billed for service after the contract ended",
                               "contracts.txt", i, [("contracts.txt", cline)]))
    return out


MEAL_CATEGORIES = {"MEAL", "MEALS", "DINNER", "LUNCH", "BREAKFAST", "FOOD", "SUBSISTENCE", "MEALS & ENTERTAINMENT",
                   "MEALS AND ENTERTAINMENT", "BUSINESS MEAL", "BUSINESS MEALS"}
NO_RECEIPT = {"", "N/A", "NA", "NONE", "-", "--", "MISSING", "NO RECEIPT", "LOST", "0", "NIL", "TBC"}
WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12}


def _count(s) -> int:
    """A head count: '4', '4.0', 'four'. Anything else (blank, 'team', 2026) is unknown, 0."""
    t = str(s or "").strip().lower()
    if t in WORD_NUMBERS:
        return WORD_NUMBERS[t]
    n = _f(t)
    return int(n) if n == int(n) and 0 < n <= 200 else 0


def expenses(lines: list[str], L: dict = LIMITS) -> list[Hit]:
    out = []
    seen: dict[str, int] = {}
    R = rows(lines)
    trips: dict[str, list[date]] = {}
    for _, r in R:
        if (trip_d := r.d("date")) \
                and (re.search(r"\b(travel|airfare|hotel)\b", r["category"], re.I) or TRAVEL.search(r["notes"])) \
                and r["notes"].strip().lower() != "business purpose recorded":
            trips.setdefault(r["employee"].strip().lower(), []).append(trip_d)
    for ln, r in R:
        amt = _f(r["amount"])
        if r["category"].strip().upper() in MEAL_CATEGORIES:
            people = _count(r["people"])
            if not people:                            # head count may be in the notes: "Team dinner - 5 people"
                m = re.search(r"\b(\d{1,3}|" + "|".join(WORD_NUMBERS) + r")\s*(people|persons|guests|pax|attendees|diners)"
                              r"|\((\d{1,2})\)", r["notes"], re.I)
                people = _count(m.group(1) or m.group(3)) if m else 0
            if people and amt / people > L["meal_limit"]:
                out.append(Hit("Expenses", "6.1", "Low", amt,
                               f"Meal at ${amt / people:,.2f} per person (limit ${L['meal_limit']:,.2f})", "expenses.csv", ln))
            elif not people and amt > L["meal_limit"]:       # no head count anywhere: over the limit even for one
                out.append(Hit("Expenses", "6.1", "Low", amt,
                               f"Meal of ${amt:,.2f} with no head count recorded (limit ${L['meal_limit']:,.2f} per "
                               "person)", "expenses.csv", ln))
        ref = "" if r["receipt_ref"].strip().upper() in NO_RECEIPT else r["receipt_ref"].strip()
        if amt > L["receipt_limit"] and not ref:
            out.append(Hit("Expenses", "6.2", "Medium", amt, f"Claim {r['claim_id']} of ${amt:,.2f} has no receipt", "expenses.csv", ln))
        if PERSONAL.search(r["notes"]) and not PROFESSIONAL.search(r["notes"]):
            out.append(Hit("Expenses", "6.3", "Low", amt, f"Personal item claimed: {r['notes'].strip().lower()}", "expenses.csv", ln))
        if ref:
            k = re.sub(r"\s+", "", ref.upper())          # "RC-1" and "rc-1 " are the same receipt
            if k in seen:
                out.append(Hit("Expenses", "6.5", "High", amt, f"The same receipt {ref} was claimed twice", "expenses.csv", ln,
                               [("expenses.csv", seen[k])]))
            else:
                seen[k] = ln
        d = r.d("date")
        if d and d.weekday() >= 5 and not r["notes"].strip():
            # a taxi, meal or hotel on the weekend of a business trip is normal; a weekend purchase of supplies is not
            near_trip = TRAVEL_SPEND.search(r["category"]) and any(
                0 < abs((d - t).days) <= 1 for t in trips.get(r["employee"].strip().lower(), []))
            if not near_trip:
                out.append(Hit("Expenses", "6.4", "Low", amt, f"Weekend expense {r['claim_id']} with no reason given", "expenses.csv", ln))
    out += _under_receipt_limit(R, L)
    return out


NO_RECEIPT_KINDS = re.compile(r"\b(mileage|per ?diem|allowance|subsistence)\b", re.I)   # never have a receipt


def _under_receipt_limit(R: list[tuple[int, Row]], L: dict) -> list[Hit]:
    """Three or more claims by one person within 30 days, each within 10% under the receipt limit and without a
    receipt. Mileage and allowances, which never carry a receipt, do not count."""
    lim = L["receipt_limit"]
    by: dict[str, list[tuple[int, Row]]] = {}
    for ln, r in R:
        amt = _f(r["amount"])
        ref = "" if r["receipt_ref"].strip().upper() in NO_RECEIPT else r["receipt_ref"].strip()
        if lim * 0.9 <= amt <= lim and not ref and person_key(r["employee"]) and r.d("date") \
                and not NO_RECEIPT_KINDS.search(r["category"] + " " + r["notes"]):
            by.setdefault(person_key(r["employee"]), []).append((ln, r))
    out = []
    for items in by.values():
        items.sort(key=lambda x: cast(date, x[1].d("date")))
        days = [cast(date, r.d("date")) for _, r in items]
        j = 0
        for i in range(len(items)):                    # a window of 30 days sliding over the sorted claims
            while (days[i] - days[j]).days > 30:
                j += 1
            if i - j + 1 >= 3:
                win = items[j:i + 1]
                last = max(win, key=lambda x: x[0])
                out.append(Hit("Expenses", "6.6", "Low", round(sum(_f(r["amount"]) for _, r in win), 2),
                               f"{last[1]['employee'].strip()} made {len(win)} claims without a receipt just under "
                               f"the ${lim:,.2f} receipt limit within 30 days", "expenses.csv", last[0],
                               [("expenses.csv", ln) for ln, _ in win if ln != last[0]]))
                break
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
    "4.5": "A new supplier may have been set up for an urgent, approved purchase.",
    "5.6": "The supplier may really have sent two invoices for the same amount on the same day.",
    "1.6": "Prices near a round limit happen; several from one person are worth a look, not proof.",
    "6.6": "Small, regular costs such as parking can sit just under the limit.",
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
    "4.5": "Have someone outside purchasing confirm the vendor is real (registration, address, phone) and the work was done.",
    "5.6": "Check both invoices against the supplier's statement; if it shows one, recover the second payment.",
    "1.6": "Ask the requester's manager to review the orders together.",
    "6.6": "Ask for the receipts, or lower the receipt limit for this kind of claim.",
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
