"""Real accounting exports: Excel workbooks, report-style CSVs (QuickBooks, Xero, Sage, NetSuite) and files whose
names are not Tallyhound's.

Three things happen here, each reported to the person as a note so nothing changes silently:

1. An .xlsx workbook becomes CSV lines: the visible sheet with the most rows, the values Excel saved (formulas are
   never run), dates as YYYY-MM-DD.
2. A report-style export (title lines above the column names, or a blank first column for group headings) is
   tidied: the title lines, "Total ..." lines and the printed footer are dropped, and vendor group headings
   ("Ashby Components" on a line of its own, then its rows) are written into a Vendor column on every row of the
   group - unless the file already has a vendor or name column, when the headings are only dropped. A plain table
   is never changed.
3. A file whose name says nothing ("Bill Payment List.csv", "Export (3).xlsx") is recognised from its columns, and
   only when columns that belong to one kind of file are there - a date, a name and an amount fit almost anything.

Tidied lines are stored as tidied, so every quote is still an exact line of the file the checks read and the
source viewer shows; the notes count what was dropped or filled in.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import zlib
from datetime import date, datetime, time

from . import columns, rules

MAX_SHEET_ROWS = 300_000
MAX_CELL_CHARS = 4000               # the same cut uploads applies to a line: no real cell or heading is longer
LINE_BREAKS = re.compile(r"[\r\n\x0b\x0c\x1c\x1d\x1e\x85  ]+")     # everything str.splitlines splits on

# words in real export names -> Tallyhound file. A tie-breaker only: the columns decide.
NAME_HINTS = [
    (r"bill ?payment|vendor ?payment|supplier ?payment|payments? (?:list|made|report)|check detail|cheque detail|"
     r"paid bills|payment history", "payments.csv"),
    (r"payment ?run|payment ?proposal|pay ?run|bacs|ach batch|payment batch", "payment_run.csv"),
    (r"approv|purchase ?orders?|bills? (?:list|awaiting|to approve)|unpaid bills|invoice register|ap aging|payables",
     "approvals.csv"),
    (r"vendor|supplier|contact list|contacts|payee list", "vendors.csv"),
    (r"expense|reimburse|claims|mileage|t ?& ?e|travel and", "expenses.csv"),
    (r"bank|statement|account transactions", "bank_statement.csv"),
]
# columns a file must have, besides scoring well, before it is taken for that kind of file. Each inner tuple is
# "any one of these". A payment run or a bank statement has only generic columns, so it also needs its name.
SIGNATURE: dict[str, list[tuple[str, ...]]] = {
    "payments.csv": [("paid_amount",), ("invoice_no",)],
    "approvals.csv": [("approved_by",), ("requested_by", "po_no", "approver_role")],
    "vendors.csv": [("name",), ("tax_id", "bank_changed_on", "w9_on_file", "bank_verified")],
    "expenses.csv": [("employee",), ("receipt_ref", "category", "people")],
    "bank_statement.csv": [("date",), ("amount",)],
    "payment_run.csv": [("invoice",), ("amount",)],
}
NEEDS_NAME = {"bank_statement.csv", "payment_run.csv"}
# "Total", "TOTAL:", "Total for Ashby Components", "Grand total" - but not a supplier called "Total Office Supplies"
# (matched against stripped text, and written so that a long run of spaces can never make them slow)
TOTAL = re.compile(r"^(?:grand\s)?\s*totals?(?:\s+for\b.*|:)?$", re.I)
NUMERIC = re.compile(r"^[$€£(+-]{0,3}\d[\d,.']*(?: [\d,.']+)*\)?(?: ?(?:CR|DR))?$", re.I)
DATEISH = re.compile(r"^\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}")
NAME_COLUMNS = {"vendor", "supplier", "payee", "name", "vendor name", "supplier name", "payee name", "contact",
                "contact name", "merchant", "employee"}


# ---------------------------------------------------------------- Excel
def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0) else v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.2f}" if abs(v - round(v, 2)) < 1e-9 else repr(v)
    return LINE_BREAKS.sub(" ", str(v)[:MAX_CELL_CHARS])


def _formulas_without_value(zf: zipfile.ZipFile) -> int:
    """Formula cells with no saved result (a workbook written by a script, never opened and saved in Excel). They
    read as blank, so the person is told."""
    n = 0
    for info in zf.infolist():
        if info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml"):
            xml = zf.read(info)
            n += len(re.findall(rb"<f[ >/]", xml)) - len(re.findall(rb"(?:</f>|<f [^<>]{0,300}/>)<v>[^<]", xml))
    return max(n, 0)


def xlsx_lines(raw: bytes, max_unpacked_mb: float = 100, max_cells: int = 3_000_000) -> tuple[list[str] | None, list[str]]:
    """(CSV lines of the fullest visible sheet, notes). None when the workbook cannot be read safely."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        # the sheets are read row by row and stopped at max_cells, so they may be larger; everything else (shared
        # text, styles) is read whole and must fit the normal limit
        sheets_mb = sum(i.file_size for i in zf.infolist() if i.filename.startswith("xl/worksheets/")) / 2 ** 20
        rest_mb = sum(i.file_size for i in zf.infolist()) / 2 ** 20 - sheets_mb
        if rest_mb > max_unpacked_mb or sheets_mb > 4 * max_unpacked_mb:
            return None, [f"it unpacks to more than {max_unpacked_mb:g} MB"]
        if any(i.flag_bits & 0x1 for i in zf.infolist()):
            return None, ["it is password-protected"]
        blank_formulas = _formulas_without_value(zf)
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError, EOFError, zlib.error):
        return None, ["it is not a readable .xlsx workbook (an old .xls, or protected with a password? Save it as "
                      ".xlsx without a password, or as CSV)"]
    import warnings

    import openpyxl
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")            # openpyxl warns about styles and data validation it skips
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:                             # any malformed workbook is a skipped file, never a crash
        return None, [f"it could not be opened ({type(e).__name__})"]
    best: list[list[str]] = []
    best_name, sheets, cut = "", 0, False
    try:
        sheets_all = list(wb.worksheets)
        visible = [ws for ws in sheets_all if getattr(ws, "sheet_state", "visible") == "visible"] or sheets_all
        cells = chars = 0
        char_budget = int(2 * max_unpacked_mb * 2 ** 20)   # text read out, however much one shared string repeats
        for ws in visible:
            sheets += 1
            if hasattr(ws, "reset_dimensions"):
                ws.reset_dimensions()                  # never trust the stored size: it can be stale and cut columns
            rows: list[list[str]] = []
            for r in ws.iter_rows(values_only=True):
                row = [_cell(v) for v in r]
                rows.append(row)
                cells += len(r)
                chars += sum(map(len, row))
                if len(rows) > MAX_SHEET_ROWS or cells > max_cells or chars > char_budget:
                    cut = True
                    break
            while rows and not any(c.strip() for c in rows[-1]):
                rows.pop()
            width = max((max((i + 1 for i, c in enumerate(r) if c.strip()), default=0) for r in rows), default=0)
            rows = [r[:width] + [""] * (width - len(r)) for r in rows]
            if sum(any(c.strip() for c in r) for r in rows) > sum(any(c.strip() for c in r) for r in best):
                best, best_name = rows, ws.title
            if cut:
                break
    except Exception as e:
        return None, [f"it could not be read ({type(e).__name__})"]
    finally:
        wb.close()
    if not best:
        return [], ["it has no data"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in best:
        w.writerow(r)
    hidden = len(sheets_all) - len(visible)
    notes = [f"read sheet \"{best_name}\"" + (f" (the fullest of {sheets} visible sheets)" if sheets > 1 else "")
             + (f"; {hidden} hidden sheet(s) ignored" if hidden else "")]
    if cut:
        notes.append("the workbook is too large; only its first part was read")
    if blank_formulas:
        notes.append(f"{blank_formulas} formula cell(s) have no saved value and read as blank - open the workbook "
                     "in Excel and save it again")
    return buf.getvalue().splitlines(), notes


# ---------------------------------------------------------------- report-style CSVs
def _cells(line: str) -> list[str]:
    try:
        return next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return [line]


def _filled(cells: list[str]) -> list[str]:
    return [c for c in cells if c.strip()]


def _looks_like_header(cells: list[str]) -> bool:
    f = _filled(cells)
    if len(f) < 3:
        return False
    text = [c for c in f if not NUMERIC.match(c.strip()) and not DATEISH.match(c.strip())]
    return len(text) >= 0.8 * len(f) and len(set(c.strip().lower() for c in f)) == len(f)


def _looks_like_data(cells: list[str]) -> bool:
    return sum(bool(NUMERIC.match(c.strip()) or DATEISH.match(c.strip())) for c in _filled(cells)) >= 2


def _header_index(lines: list[str]) -> int:
    """Index of the column-names line: the widest line that looks like column names before the first line of data.
    Title lines (company, report name, "Report period:, From ..., To ...") come first and are narrower. Any other
    wide line before the data means an unfamiliar layout, and the file is taken as it is."""
    if lines and _looks_like_header(_cells(lines[0])) and len(lines) > 1 and lines[1].strip():
        return 0          # column names on the first line with rows straight under them: a plain table, whatever follows
    best, width = 0, 0
    top = lines[:25]
    for i, ln in enumerate(top):
        cells = _cells(ln)
        if _looks_like_data(cells):
            # "From, 09/01/2026, To, 09/30/2026" has dates but is a title if wider column names follow it
            later = [len(_filled(_cells(x))) for x in top[i + 1:] if _looks_like_header(_cells(x))]
            if not best and later and max(later) > len(_filled(cells)) and len(_filled(cells)) <= 4:
                continue
            break
        if _looks_like_header(cells):
            if len(_filled(cells)) > width:
                best, width = i, len(_filled(cells))
        elif len(_filled(cells)) > 2:
            return 0
    return best


def _row(cells: list[str], width: int) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(cells + [""] * (width - len(cells)))
    return buf.getvalue()


def tidy(lines: list[str]) -> tuple[list[str], list[str]]:
    """(lines, notes) for a CSV. A plain table - column names on the first line, a name in the first column - is
    returned unchanged with no notes."""
    if not lines:
        return lines, []
    h = _header_index(lines)
    head = _cells(lines[h])
    if len(head) < 3 or (h == 0 and head[0].strip()):
        return lines, []                       # not a report export: leave it exactly as it is
    notes: list[str] = []
    if h:
        notes.append(f"dropped {h} title line(s) above the column names")
    width = len(head)
    body = lines[h + 1:]
    one_cell = any(len(f := _filled(c := _cells(x))) == 1 and c[0].strip() == f[0].strip()
                   and not NUMERIC.match(f[0].strip()) for x in body)              # a heading is a name, not a number
    if h == 0 and not one_cell:
        return lines, []                       # a blank first column name and no headings (a pandas index): as it is
    # group headings go into the first column: a blank one, or one that names the supplier ("Supplier") but is
    # left empty on the rows under each heading
    grouped = not head[0].strip() or (columns.norm_header(head[0]) in NAME_COLUMNS and one_cell)
    named = any(columns.norm_header(c) in NAME_COLUMNS for c in head[1:])
    tail = len(body)
    while tail and len(_filled(_cells(body[tail - 1]))) < 2:          # printed footer, blank lines
        tail -= 1
    footer = len([x for x in body[tail:] if x.strip()])
    kept: list[str] = []
    totals = headings = filled = 0
    current, seen = "", set()
    budget = 2 * sum(map(len, lines)) + 2 ** 20      # filling headings in may not multiply the file's size
    for ln in body[:tail]:
        cells = _cells(ln)
        f = _filled(cells)
        if not f:
            continue
        first = cells[0].strip()
        if len(f) == 1 and first and not (TOTAL.match(first) and first.lower().rstrip(":") not in
                                          ("total", "totals")):     # a heading: its own line, first column only
            current = first[:200] if grouped else ""          # a vendor name, not a paragraph
            seen.add(first.lower())
            headings += 1
            continue
        low = first.lower()
        if first and (TOTAL.match(first) or (low.startswith("total ") and low[6:].strip() in seen)):
            totals += 1                                              # "Total for X", "TOTAL", "Total X" after X
            current = ""
            continue
        if grouped and not first and current and not named and budget > 0:
            cells = [current] + cells[1:]
            filled += 1
            ln = _row(cells, width)
            budget -= len(ln)
        kept.append(ln)
    if headings and not grouped:
        notes.append(f"dropped {headings} section heading line(s)")
    elif headings:
        if named:
            notes.append(f"dropped {headings} group heading line(s) (the file has its own name column)")
        elif head[0].strip():
            notes.append(f"wrote {headings} group heading(s) into the {head[0].strip()} column on {filled} row(s)")
        else:
            head = ["Vendor"] + head[1:]
            notes.append(f"wrote {headings} vendor heading(s) into a Vendor column on {filled} row(s)")
    if totals:
        notes.append(f"dropped {totals} total line(s)")
    if footer:
        notes.append(f"dropped {footer} footer line(s)")
    if not notes:
        return lines, []
    return [_row(head, 0)] + kept, notes


# ---------------------------------------------------------------- recognising a file from its columns
def matched(name: str, header: list[str]) -> set[str]:
    """Which of a Tallyhound file's required columns this header has, by name or common synonym."""
    need = rules.REQUIRED.get(name, [])
    have = {columns.norm_header(h) for h in header}
    exact = {c for c in need if columns._norm(c.replace("_", " ")) in have}
    return exact | set(columns.suggest([c for c in need if c not in exact], header))


def score(name: str, header: list[str]) -> float:
    need = rules.REQUIRED.get(name, [])
    return len(matched(name, header)) / len(need) if need else 0.0


def guess(base: str, lines: list[str], taken: frozenset[str] | set[str] = frozenset()) -> tuple[str | None, float]:
    """(Tallyhound file name, confidence) for an export with an unfamiliar name, or (None, 0). Kinds already read
    from a properly named file are never guessed."""
    if not lines:
        return None, 0.0
    header = _cells(lines[0])
    stem = re.sub(r"[_\-]+", " ", base.rsplit(".", 1)[0].lower())
    hint = next((dst for pat, dst in NAME_HINTS if re.search(pat, stem)), None)
    balance = any(columns.norm_header(h) in ("balance", "running balance") for h in header)
    ok = {}
    for n in rules.REQUIRED:
        m = matched(n, header)
        if n in taken or not all(any(c in m for c in group) for group in SIGNATURE.get(n, [])):
            continue
        if n in NEEDS_NAME and hint != n and not (n == "bank_statement.csv" and balance):
            continue
        ok[n] = len(m) / len(rules.REQUIRED[n])
    if not ok:
        return None, 0.0
    ranked = sorted(ok.items(), key=lambda x: (x[1], x[0] == hint), reverse=True)
    best, top = ranked[0]
    if hint in ok and best != hint:
        return None, 0.0          # the name fits one kind and the columns fit better another: let the person rename it
    floor = 0.4 if best == hint else 0.6       # a vendor list called "Contacts" with fewer columns is still one
    if top < floor or (len(ranked) > 1 and top - ranked[1][1] < 0.1 and ranked[0][0] != hint):
        return None, 0.0
    return best, round(top, 2)


def explain(base: str, lines: list[str]) -> str:
    """Why an unfamiliar file was not read: the kind it came closest to and what it lacks for it."""
    if not lines:
        return "it is empty"
    header = _cells(lines[0])
    best = max(rules.REQUIRED, key=lambda n: (score(n, header), n))
    m = matched(best, header)
    if not m:
        return "its columns do not look like any of the audit files"
    lacks = [g for g in SIGNATURE.get(best, []) if not any(c in m for c in g)]
    what = (" and ".join(" or ".join(c.replace("_", " ") for c in g) for g in lacks) if lacks else
            "enough of its columns" if score(best, header) < 0.6 else "a name that says so (or a balance column)")
    return f"it comes closest to {best}, but has no {what}" if lacks else f"it may be {best}, but it lacks {what}"


LINE_ITEM = re.compile(r"\b(description|quantity|qty|unit (?:amount|price|cost)|line (?:amount|total|description)|"
                       r"item|account code|tax (?:type|amount)|tracking|discount)\b")


QTY = re.compile(r"\b(quantity|qty|unit (?:amount|price|cost)|line (?:amount|total))\b")


def collapse_line_items(lines: list[str]) -> tuple[list[str], int]:
    """A bill export with one row per line item repeats the bill's number and totals on every line; read as
    payments, each repeat would look like the bill paid again. Rows that agree on every column except the line-item
    ones (description, quantity, unit amount ...) are one bill: the first row is kept, as it is."""
    if len(lines) < 3:
        return lines, 0
    head = _cells(lines[0])
    item = {i for i, h in enumerate(head) if LINE_ITEM.search(columns.norm_header(h))}
    # a description or memo column alone is not a line-item export: two payments that differ only in their memo
    # are two payments
    if not any(QTY.search(columns.norm_header(head[i])) for i in item) or len(item) == len(head):
        return lines, 0
    out, seen, dropped = [lines[0]], set(), 0
    for ln in lines[1:]:
        cells = _cells(ln)
        key = tuple(c.strip() for i, c in enumerate(cells) if i not in item)
        if key in seen and any(key):
            dropped += 1
            continue
        seen.add(key)
        out.append(ln)
    return out, dropped
