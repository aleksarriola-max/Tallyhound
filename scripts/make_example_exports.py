"""Write docs/example-exports/: one fictional month laid out the way accounting packages export it, to try the importer.

- Bill Payment List.csv   a QuickBooks-style report: title lines, vendor group headings, "Total for ..." lines, a footer
- Contacts.xlsx           a Xero-style contacts workbook with camelCase column names
- approvals.csv, expenses.csv   plain Tallyhound files

Upload all four together under Home > Check new files. Every name and number is invented (challenge seed 7).
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

from tallyhound import challenge  # noqa: E402

OUT = ROOT / "docs" / "example-exports"


def us(d: str) -> str:
    y, m, dd = d.split("-")
    return f"{m}/{dd}/{y}"


def main() -> None:
    files, _ = challenge.generate(7, "medium")
    OUT.mkdir(parents=True, exist_ok=True)
    pay = list(csv.DictReader(files["payments.csv"]))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerows([["Ashgrove Instruments Ltd (fictional)"], ["Bill Payment List"], ["September 2026"], []])
    w.writerow(["", "Date", "Num", "Invoice Date", "Invoice No", "Original Amount", "Amount Paid", "Memo"])
    total = 0.0
    for vendor in sorted({p["supplier"] for p in pay}):
        rows = [p for p in pay if p["supplier"] == vendor]
        w.writerow([vendor] + [""] * 7)
        for p in rows:
            w.writerow(["", us(p["pay_date"]), p["payment_id"], us(p["invoice_date"]) if p["invoice_date"] else "",
                        p["invoice_no"], f"{float(p['invoice_amount']):,.2f}", f"{float(p['paid_amount']):,.2f}", ""])
        sub = sum(float(p["paid_amount"]) for p in rows)
        total += sub
        w.writerow([f"Total for {vendor}", "", "", "", "", "", f"${sub:,.2f}", ""])
    w.writerow(["TOTAL", "", "", "", "", "", f"${total:,.2f}", ""])
    w.writerows([[], [], ["Thursday, October 08, 2026 09:12 AM GMT-04:00"]])
    (OUT / "Bill Payment List.csv").write_text(buf.getvalue(), encoding="utf-8", newline="\n")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws.append(["*ContactName", "VendorID", "TaxNumber", "ContactStatus", "BankAccountNumber", "BankChangedOn",
               "BankVerified", "CreatedOn", "W9OnFile", "LastPaidOn", "LastPaidAmount"])
    for v in csv.DictReader(files["vendors.csv"]):
        def day(s: str):
            return date.fromisoformat(s) if s else None
        ws.append([v["name"], v["vendor_id"], v["tax_id"], v["status"], v["bank_acct"], day(v["bank_changed_on"]),
                   v["bank_verified"], day(v["created_on"]), v["w9_on_file"], day(v["last_paid_on"]),
                   float(v["last_paid_amount"]) if v["last_paid_amount"] else None])
    for col in "FHJ":
        for c in ws[col][1:]:
            c.number_format = "yyyy-mm-dd"
    wb.properties.creator = "Tallyhound example"
    wb.properties.created = wb.properties.modified = __import__("datetime").datetime(2026, 10, 1)
    wb.save(OUT / "Contacts.xlsx")
    for name in ("approvals.csv", "expenses.csv"):
        (OUT / name).write_text("\n".join(files[name]) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
