"""Real exports: Excel workbooks, report-style CSVs from QuickBooks and Xero, loose files and unfamiliar names."""
import datetime
import io
import zipfile
from pathlib import Path

import openpyxl

from tallyhound import challenge, columns, importer, rules, uploads

ROOT = Path(__file__).resolve().parent.parent
QB = ROOT / "tests" / "exports" / "Transaction List by Vendor.csv"


def _xlsx(rows, title="Sheet1", extra_sheet=None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    if extra_sheet:
        wb.create_sheet("Notes").append(extra_sheet)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _mapped(name: str, lines: list[str]) -> list[str]:
    """The file as the checks see it once the suggested column matching is accepted."""
    import csv
    head = next(csv.reader([lines[0]]))
    m = columns.suggest(rules.missing_columns(name, lines), head)
    back = {src: dst for dst, src in m.items()}
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow([back.get(h, h) for h in head])
    return [buf.getvalue()] + lines[1:]


def test_quickbooks_report_is_tidied_and_recognised():
    files, notes = uploads.read_uploads([(QB.name, QB.read_bytes())])
    lines = files["payments.csv"]
    assert lines[0].startswith("Vendor,Date,")
    assert len(lines) == 4                                          # three payments; titles, totals, footer gone
    assert all(ln.split(",")[0] for ln in lines[1:])                # every row carries its vendor
    note = next(n for n in notes if n.startswith(QB.name))
    assert "title line" in note and "total line" in note and "read as payments.csv, recognised from its columns" in note


def test_a_tidied_export_still_finds_the_duplicate():
    files, _ = uploads.read_uploads([(QB.name, QB.read_bytes())])
    hits = rules.payments(_mapped("payments.csv", files["payments.csv"]))
    dup = [h for h in hits if h.clause == "5.2"]
    assert len(dup) == 1 and "ASH-6100" in dup[0].title and dup[0].line_number == 3


def test_xero_workbook_with_an_unhelpful_name_is_read():
    raw = _xlsx([["*ContactName", "VendorID", "TaxNumber", "ContactStatus", "BankAccountNumber", "BankChangedOn",
                  "BankVerified", "W9OnFile", "LastPaidOn", "LastPaidAmount"],
                 ["Ashby Components", "V-2201", "55-1000000", "ACTIVE", "****9567", None, "N/A", "YES",
                  datetime.datetime(2026, 9, 10), 8430.27]], title="Contacts", extra_sheet=["read me"])
    files, notes = uploads.read_uploads([("Export (3).xlsx", raw)])
    assert files["vendors.csv"][1] == "Ashby Components,V-2201,55-1000000,ACTIVE,****9567,,N/A,YES,2026-09-10,8430.27"
    assert any('sheet "Contacts"' in n and "fullest of 2" in n for n in notes)
    assert rules.missing_columns("vendors.csv", _mapped("vendors.csv", files["vendors.csv"])) == []


def test_excel_values_become_plain_text():
    assert importer._cell(datetime.datetime(2026, 9, 1)) == "2026-09-01"
    assert importer._cell(1960.17) == "1960.17" and importer._cell(12.0) == "12" and importer._cell(None) == ""
    assert importer._cell("a\nb") == "a b"


def test_plain_tallyhound_files_are_never_changed():
    files = challenge.generate_full(5, "hard")[0]
    for p in (ROOT / "data" / "source").iterdir():
        files[p.name] = p.read_text(encoding="utf-8").splitlines()
    for name, lines in files.items():
        if name.endswith(".csv"):
            assert importer.tidy(lines) == (lines, []), name


def test_a_supplier_called_total_is_not_a_total_line():
    lines = ["vendor_id,name,amount", "V-1,Total Office Supplies,40.00", "V-2,Totalis Ltd,5.00"]
    assert importer.tidy(lines) == (lines, [])


def test_unknown_files_are_ignored_with_a_reason():
    files, notes = uploads.read_uploads([("holiday list.csv", b"name,start,end\nAda,2026-01-01,2026-01-05\n")])
    assert files == {} and any("do not clearly match one of the audit files" in n for n in notes)


def test_old_xls_and_broken_xlsx_are_skipped_politely():
    files, notes = uploads.read_uploads([("payments.xls", b"\xd0\xcf\x11\xe0"), ("approvals.xlsx", b"nope")])
    assert files == {}
    assert any("old .xls" in n for n in notes) and any("approvals.xlsx" in n and "not a readable" in n for n in notes)


def test_a_workbook_that_unpacks_too_far_is_refused(monkeypatch):
    lines, why = importer.xlsx_lines(_xlsx([["a", "b", "c"], [1, 2, 3]]), max_unpacked_mb=0)
    assert lines is None and "unpacks" in why[0]


def test_tab_separated_exports_are_read():
    tsv = b"payment_id\tpay_date\tinvoice_date\tsupplier\tinvoice_no\tinvoice_amount\tpaid_amount\n" \
          b"P-1\t2026-09-01\t2026-09-01\tAshby, Ltd\tA-1\t10.00\t10.00\n"
    files, _ = uploads.read_uploads([("payments.tsv", tsv)])
    assert files["payments.csv"][1] == 'P-1,2026-09-01,2026-09-01,"Ashby, Ltd",A-1,10.00,10.00'


def test_loose_files_and_a_zip_together():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("m/approvals.csv", "\n".join(challenge.generate(1, "easy")[0]["approvals.csv"]))
    pay = "\n".join(challenge.generate(1, "easy")[0]["payments.csv"]).encode()
    files, _ = uploads.read_uploads([("payments.csv", pay), ("more.zip", buf.getvalue())])
    assert {"payments.csv", "approvals.csv"} <= set(files)


def test_camel_case_headers_match():
    assert columns.suggest(["invoice_no", "paid_amount"], ["InvoiceNumber", "InvoiceAmountPaid"]) == \
        {"invoice_no": "InvoiceNumber", "paid_amount": "InvoiceAmountPaid"}
