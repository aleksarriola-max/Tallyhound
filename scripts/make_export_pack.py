"""Write tests/exports/pack/: fictional files laid out the way common accounting packages and banks export them.

tests/test_export_pack.py reads each one and checks what the importer makes of it (which audit file, how many
rows, what it tidied) - or that it refuses the file with a clear reason rather than misread it. Every name and
number is invented. Run this only to change the pack; the files are committed.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path

import openpyxl

OUT = Path(__file__).resolve().parent.parent / "tests" / "exports" / "pack"
V = ["Ashby Components", "Bexley Plastics", "Calder Logistics", "Dunmore Tooling"]


def w(name: str, text: str, enc: str = "utf-8", crlf: bool = False) -> None:
    data = text.replace("\n", "\r\n") if crlf else text
    (OUT / name).write_bytes(data.encode(enc))


def book(name: str, sheets: list[tuple[str, list[list], str]]) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows, state in sheets:
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
        ws.sheet_state = state
    wb.properties.created = wb.properties.modified = datetime(2026, 10, 1)
    buf = io.BytesIO()
    wb.save(buf)
    (OUT / name).write_bytes(buf.getvalue())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # 1 QuickBooks Online: Bill Payment List, vendor group headings, totals, footer
    w("qbo_bill_payment_list.csv", "\n".join([
        "Ashgrove Instruments Ltd (fictional)", "Bill Payment List", "September 2026", "",
        ",Date,Num,Invoice Date,Invoice No,Original Amount,Amount Paid,Memo",
        "Ashby Components,,,,,,,", ",09/02/2026,1001,09/01/2026,ASH-6100,\"1,960.17\",\"1,960.17\",",
        ",09/16/2026,1009,09/14/2026,ASH-6140,800.00,800.00,", "Total for Ashby Components,,,,,,\"$2,760.17\",",
        "Bexley Plastics,,,,,,,", ",09/05/2026,1003,09/03/2026,BEX-221,\"9,366.95\",\"9,366.95\",\"Paid, by cheque\"",
        "Total for Bexley Plastics,,,,,,\"$9,366.95\",", "TOTAL,,,,,,\"$12,127.12\",", "", "",
        "\"Thursday, October 08, 2026 09:12 AM GMT-04:00\""]) + "\n")
    # 2 QuickBooks Desktop: Check Detail, grouped by bank account, with its own Name column - no invoice numbers
    w("qbd_check_detail.csv", "\n".join([
        "Ashgrove Instruments Ltd (fictional)", "Check Detail", "September 2026", "",
        ",Type,Date,Num,Name,Memo,Account,Paid Amount,Original Amount", "Checking,,,,,,,,",
        ",Bill Pmt -Check,09/02/2026,1001,Ashby Components,,Accounts Payable,-1960.17,1960.17",
        ",Bill Pmt -Check,09/05/2026,1003,Bexley Plastics,,Accounts Payable,-9366.95,9366.95",
        "Total Checking,,,,,,,-11327.12,", "", "TOTAL,,,,,,,-11327.12,"]) + "\n", crlf=True)
    # 3 Xero bills export: one row per line item, totals repeated on every line
    head = "*ContactName,EmailAddress,*InvoiceNumber,Reference,*InvoiceDate,*DueDate,PaidDate,Total,InvoiceAmountPaid," \
           "InvoiceAmountDue,*Description,*Quantity,*UnitAmount,LineAmount,*AccountCode,Currency"
    rows = [
        "Ashby Components,ap@ashby.example,ASH-6100,PO-2100,2026-09-01,2026-09-30,2026-09-02,1960.17,1960.17,0.00,Brackets,10,96.00,960.00,300,USD",
        "Ashby Components,ap@ashby.example,ASH-6100,PO-2100,2026-09-01,2026-09-30,2026-09-02,1960.17,1960.17,0.00,Housings,4,250.04,1000.17,300,USD",
        "Bexley Plastics,ar@bexley.example,BEX-221,PO-2101,2026-09-03,2026-10-03,2026-09-05,9366.95,9366.95,0.00,Resin,1,9366.95,9366.95,300,USD",
        "Calder Logistics,billing@calder.example,CAL-77,,2026-09-04,2026-10-04,2026-09-08,640.00,640.00,0.00,Freight,1,640.00,640.00,310,USD",
        "Calder Logistics,billing@calder.example,CAL-77,,2026-09-04,2026-10-04,2026-09-08,640.00,640.00,0.00,Fuel note,1,0.00,0.00,310,USD"]
    w("Bills export.csv", "\n".join([head, *rows]) + "\n", enc="utf-8-sig")
    # 4 Xero contacts export: fewer columns than Tallyhound's vendor file, still a vendor list
    w("Contacts.csv", "\n".join([
        "*ContactName,AccountNumber,EmailAddress,FirstName,LastName,TaxNumber,BankAccountName,BankAccountNumber,ContactStatus",
        "Ashby Components,ASH01,ap@ashby.example,Jo,Ashby,55-1000000,Ashby Components,****9567,ACTIVE",
        "Bexley Plastics,BEX01,ar@bexley.example,Sam,Lee,55-1007919,Bexley Plastics Ltd,****6897,ACTIVE"]) + "\n")
    # 5 Xero Account Transactions (bank) report: title lines, Debit/Credit, running balance
    w("Account Transactions.csv", "\n".join([
        "Account Transactions", "Ashgrove Instruments Ltd (fictional)", "For the period 1 September 2026 to 30 September 2026",
        "", "Date,Source,Description,Reference,Debit,Credit,Running Balance",
        "Business Bank Account,,,,,,", "02 Sep 2026,Payable Payment,Ashby Components,ASH-6100,1960.17,,48039.83",
        "05 Sep 2026,Payable Payment,Bexley Plastics,BEX-221,9366.95,,38672.88",
        "10 Sep 2026,Receive Money,Customer receipt,INV-9001,,4000.00,42672.88",
        "Total Business Bank Account,,,,11327.12,4000.00,"]) + "\n")
    # 6 Sage 50 purchase day book: invoices posted, no approver - not enough to be an approvals file
    w("Purchase Day Book.csv", "\n".join([
        "Type,Account,Date,Ref,Details,Net,Tax,Gross",
        "PI,ASH001,01/09/2026,ASH-6100,Brackets and housings,1633.48,326.69,1960.17",
        "PI,BEX001,03/09/2026,BEX-221,Resin,7805.79,1561.16,9366.95"]) + "\n", enc="cp1252", crlf=True)
    # 7 NetSuite vendor payments saved search: quoted everything, BOM, CRLF
    w("VendorPaymentsSearchResults.csv", "\n".join([
        '"Internal ID","Date","Document Number","Vendor","Bill Number","Bill Date","Bill Amount","Amount","Memo"',
        '"8811","9/2/2026","VP-1001","Ashby Components","ASH-6100","9/1/2026","1,960.17","1,960.17",""',
        '"8812","9/5/2026","VP-1003","Bexley Plastics","BEX-221","9/3/2026","9,366.95","9,366.95","Resin"']) + "\n",
      enc="utf-8-sig", crlf=True)
    # 8 US bank download (Chase-style): uppercase extension, signed amounts, balance
    w("Chase1234_Activity_20260930.CSV", "\n".join([
        "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #",
        "DEBIT,09/03/2026,ACH PMT ASHBY COMPONENTS,-1960.17,ACH_DEBIT,48039.83,",
        "DEBIT,09/08/2026,ACH PMT BEXLEY PLASTICS,-9366.95,ACH_DEBIT,38672.88,",
        "CREDIT,09/10/2026,DEPOSIT,4000.00,ACH_CREDIT,42672.88,"]) + "\n")
    # 9 UK bank download with no balance and a meaningless name: refused, not guessed
    w("data.csv", "\n".join([
        "Number,Date,Account,Amount,Subcategory,Memo",
        ",03/09/2026,20-00-00 12345678,-1960.17,Bill Payment,ASHBY COMPONENTS",
        ",08/09/2026,20-00-00 12345678,-9366.95,Bill Payment,BEXLEY PLASTICS"]) + "\n")
    # 10 Expensify report export, with a submitter column
    w("Expensify_Export.csv", "\n".join([
        "Report Name,Submitter,Timestamp,Merchant,Amount,Category,Comment,Receipt,Attendees",
        "Sept travel,priya.n@ashgrove.example,2026-09-04 08:12,Union Taxis,46.00,Taxi,Airport,RC-70011,",
        "Sept travel,priya.n@ashgrove.example,2026-09-04 20:40,Harbour Grill,118.00,Meals,Client dinner,RC-70012,2"]) + "\n")
    # 11 QuickBooks report saved to Excel: title rows, vendor headings, a totals row
    book("A_P Payments.xlsx", [("Sheet1", [
        ["Ashgrove Instruments Ltd (fictional)"], ["Bill Payment List"], ["September 2026"], [],
        [None, "Date", "Num", "Invoice Date", "Invoice No", "Original Amount", "Amount Paid"],
        ["Ashby Components"], [None, date(2026, 9, 2), "1001", date(2026, 9, 1), "ASH-6100", 1960.17, 1960.17],
        ["Total for Ashby Components", None, None, None, None, None, 1960.17],
        ["Bexley Plastics"], [None, date(2026, 9, 5), "1003", date(2026, 9, 3), "BEX-221", 9366.95, 9366.95],
        ["Total for Bexley Plastics", None, None, None, None, None, 9366.95]], "visible")])
    # 12 Workbook with a small summary, the data sheet and a hidden lookup
    book("expenses_september.xlsx", [
        ("Summary", [["Claims", 3], ["Total", 210.10]], "visible"),
        ("Claims", [["claim_id", "date", "employee", "category", "amount", "receipt_ref", "notes", "people"],
                    ["E-1", date(2026, 9, 1), "A. Patel", "TRAVEL", 160.43, "RC-1", "Client site", None],
                    ["E-2", date(2026, 9, 3), "R. Chen", "MEAL", 25.00, "RC-2", "Lunch", 1],
                    ["E-3", date(2026, 9, 6), "R. Chen", "PARKING", 24.67, None, "Client visit", None]], "visible"),
        ("Lookup", [[f"cat{i}", i] for i in range(40)], "hidden")])
    # 13 European Excel "Save as CSV": semicolons and decimal commas, day-first dates
    w("zahlungen_payments.csv", "\n".join([
        "payment_id;pay_date;invoice_date;supplier;invoice_no;invoice_amount;paid_amount",
        "P-1;02.09.2026;01.09.2026;Ashby Components;ASH-6100;1.960,17;1.960,17",
        "P-2;15.09.2026;01.09.2026;Ashby Components;ASH-6100;1.960,17;1.960,17"]) + "\n", enc="cp1252", crlf=True)
    # 14 Tab-separated export with a Tallyhound name
    w("approvals.tsv", "\n".join([
        "record_id\ttype\tdoc_no\tdate\tvendor\tamount\trequested_by\tapproved_by\tapprover_role\tpo_no",
        "A-1\tINVOICE\tASH-6100\t2026-09-01\tAshby Components\t1960.17\tT. Brandt\tT. Brandt\tManager\tPO-2100"]) + "\n")
    # 15 UTF-16 "Unicode text" from Excel: tab-separated, BOM
    w("vendors.txt", "\n".join([
        "vendor_id\tname\ttax_id\tstatus\tbank_acct\tbank_changed_on\tbank_verified\tw9_on_file\tlast_paid_on\tlast_paid_amount",
        "V-1\tCafé Müller GmbH\t55-1\tACTIVE\t****1\t2026-09-10\tNO\tYES\t2026-09-12\t4200.00"]) + "\n", enc="utf-16")
    print(f"Wrote {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
