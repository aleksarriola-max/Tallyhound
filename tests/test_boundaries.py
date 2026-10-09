"""Edges of every limit and window: one step inside raises nothing, one step past raises the finding.

Mutation testing (changing a constant or a comparison in rules.py and importer.py and checking that a test fails)
showed these edges were not pinned: a rule could drift by a day or a cent and every other test would still pass.
"""
import datetime as dt

import pytest

from tallyhound import importer, rules

PAY = "payment_id,pay_date,invoice_date,vendor_id,supplier,invoice_no,invoice_amount,paid_amount"
APPR = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no"
VEN = "vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount"
EXP = "claim_id,date,employee,category,amount,receipt_ref,notes,people"


def clauses(hits, clause):
    return [h for h in hits if h.clause == clause]


# ---- payments
@pytest.mark.parametrize("paid,flag", [("1001.00", False), ("1001.01", True)])
def test_overpayment_tolerance_is_one_dollar(paid, flag):
    lines = [PAY, f"P-1,2026-09-02,2026-09-01,V-1,A,INV-1,1000.00,{paid}"]
    assert bool(clauses(rules.payments(lines), "5.1")) is flag


@pytest.mark.parametrize("second,flag", [("500.00", False), ("501.01", True)])
def test_a_second_payment_is_a_duplicate_only_beyond_the_invoice(second, flag):
    lines = [PAY, "P-1,2026-09-02,2026-09-01,V-1,A,INV-1,1000.00,500.00",
             f"P-2,2026-09-09,2026-09-01,V-1,A,INV-1,1000.00,{second}"]
    assert bool(clauses(rules.payments(lines), "5.2")) is flag


@pytest.mark.parametrize("a,b,similar", [("ASH-6100", "ASH-6010", True), ("ASH-4150", "ASH-4105", False),
                                         ("INV-2071", "INV-2017", True), ("AB12", "AB21", False)])
def test_swapped_digits_count_only_far_apart_in_the_series(a, b, similar):
    # 6100/6010 are 90 apart, 4150/4105 are 45, 2071/2017 are 54, 12/21 are 9
    assert rules.similar_invoice(a, b) is similar


@pytest.mark.parametrize("days,flag", [(7, True), (8, False)])
def test_near_duplicates_without_invoice_dates_must_be_paid_within_a_week(days, flag):
    d2 = (dt.date(2026, 9, 2) + dt.timedelta(days=days)).isoformat()
    lines = [PAY, "P-1,2026-09-02,,V-1,A,ASH-6100,300.00,300.00", f"P-2,{d2},,V-1,A,ASH-6100A,300.00,300.00"]
    assert bool(clauses(rules.payments(lines), "5.6")) is flag


def test_near_duplicates_ignore_amounts_under_materiality():
    lines = [PAY, "P-1,2026-09-02,2026-09-01,V-1,A,ASH-6100,49.99,49.99", "P-2,2026-09-03,2026-09-01,V-1,A,ASH-6100A,49.99,49.99"]
    assert not clauses(rules.payments(lines), "5.6")
    lines = [ln.replace("49.99", "50.00") for ln in lines]
    assert clauses(rules.payments(lines), "5.6")


# ---- approvals
@pytest.mark.parametrize("amount,flag", [("2500.00", False), ("2500.01", True)])
def test_po_limit(amount, flag):
    lines = [APPR, f"A-1,INVOICE,X-1,2026-09-01,Arden,{amount},Ada,Bo,Manager,"]
    assert bool(clauses(rules.approvals(lines), "1.1")) is flag


@pytest.mark.parametrize("amount,flag", [("10000.00", False), ("10000.01", True)])
def test_director_limit(amount, flag):
    lines = [APPR, f"A-1,INVOICE,X-1,2026-09-01,Arden,{amount},Ada,Bo,Manager,PO-1"]
    assert bool(clauses(rules.approvals(lines), "1.3")) is flag


@pytest.mark.parametrize("gap,flag", [(3, True), (4, False)])
def test_split_order_window_is_three_days(gap, flag):
    d2 = (dt.date(2026, 9, 1) + dt.timedelta(days=gap)).isoformat()
    lines = [APPR, "A-1,INVOICE,X-1,2026-09-01,Arden,6000.00,Ada,Bo,Manager,", f"A-2,INVOICE,X-2,{d2},Arden,6000.00,Ada,Bo,Manager,"]
    assert bool(clauses(rules.approvals(lines), "1.4")) is flag


@pytest.mark.parametrize("amount,flag", [("9599.99", False), ("9600.00", True)])
def test_just_under_the_director_limit_means_within_four_percent(amount, flag):
    lines = [APPR, f"A-1,INVOICE,X-1,2026-09-01,Arden,{amount},Ada,Bo,Manager,PO-1",
             "A-2,INVOICE,Y-1,2026-09-09,Bexley,9900.00,Ada,Bo,Manager,PO-2"]
    assert bool(clauses(rules.approvals(lines), "1.6")) is flag


@pytest.mark.parametrize("a,b,same", [("klowe", "Kate Lowe", True), ("kate.lowe", "Kate Lowe", True),
                                      ("lowek", "Kate Lowe", True), ("kl", "Kate Lowe", False),
                                      ("K. Lowe", "Kate Lowe", True), ("K. Lowe", "K. Lowry", False), ("", "Ada", False)])
def test_same_person(a, b, same):
    assert rules.same_person(a, b) is same


# ---- vendors
@pytest.mark.parametrize("paid,days,flag", [("10000.00", 5, False), ("10000.01", 5, True), ("15000.00", 30, True),
                                            ("15000.00", 31, False)])
def test_new_vendor_window_and_amount(paid, days, flag):
    made = dt.date(2026, 8, 1)
    lines = [VEN, f"V-1,Nova,11-1,ACTIVE,****1,,N/A,{made},YES,{made + dt.timedelta(days=days)},{paid}"]
    assert bool(clauses(rules.vendors(lines), "4.5")) is flag


@pytest.mark.parametrize("others,flag", [(3, True), (4, False)])
def test_an_import_date_is_one_shared_by_five_vendors_and_thirty_percent(others, flag):
    """10 vendors. The new one plus 3 others set up the same day: 4 share it, a real set-up date, so it is flagged.
    Plus 4 others: 5 share it (50%), the date the list was imported, so it is not."""
    rows = [f"V-{i},Old {i},55-10000{i:02d},ACTIVE,****1,,N/A,2024-0{1 + i % 9}-1{i % 9},YES,2026-09-20,100.00"
            for i in range(9)]
    for i in range(others):
        rows[i] = rows[i].replace(rows[i].split(",")[7], "2026-09-01", 1)
    rows.append("V-9,Nova,55-1000099,ACTIVE,****1,,N/A,2026-09-01,YES,2026-09-10,15000.00")
    assert bool(clauses(rules.vendors([VEN] + rows), "4.5")) is flag


def test_remit_to_records_never_count_as_a_duplicate_tax_id_either_way():
    for a, b in (("Arden Ltd", "Arden Ltd - REMIT TO LEEDS"), ("Arden Ltd - Remit-to", "Arden Ltd")):
        lines = [VEN, f"V-1,{a},55-1000001,ACTIVE,****1,,N/A,2024-01-01,YES,2026-09-01,100.00",
                 f"V-2,{b},55-1000001,ACTIVE,****1,,N/A,2024-01-01,YES,2026-09-01,100.00"]
        assert not clauses(rules.vendors(lines), "4.2")
    lines = [VEN, "V-1,Arden,55-1000001,ACTIVE,****1,,N/A,2024-01-01,YES,2026-09-01,100.00",
             "V-2,Bexley,55-1000001,ACTIVE,****1,,N/A,2024-01-01,YES,2026-09-01,100.00"]
    assert clauses(rules.vendors(lines), "4.2")


# ---- expenses
@pytest.mark.parametrize("amount,flag", [("25.00", False), ("25.01", True)])
def test_receipt_limit(amount, flag):
    lines = [EXP, f"E-1,2026-09-01,Ada,TAXI,{amount},,x,"]
    assert bool(clauses(rules.expenses(lines), "6.2")) is flag


@pytest.mark.parametrize("amount,flag", [("22.49", False), ("22.50", True), ("25.00", True)])
def test_just_under_the_receipt_limit_means_within_ten_percent(amount, flag):
    lines = [EXP] + [f"E-{i},2026-09-0{i},Ada,PARKING,{amount if i == 1 else '24.00'},,x," for i in range(1, 4)]
    assert bool(clauses(rules.expenses(lines), "6.6")) is flag


@pytest.mark.parametrize("gap,flag", [(30, True), (31, False)])
def test_receipt_pattern_window_is_thirty_days(gap, flag):
    last = (dt.date(2026, 8, 1) + dt.timedelta(days=gap)).isoformat()
    lines = [EXP, "E-1,2026-08-01,Ada,PARKING,24.00,,x,", "E-2,2026-08-10,Ada,PARKING,24.00,,x,",
             f"E-3,{last},Ada,PARKING,24.00,,x,"]
    assert bool(clauses(rules.expenses(lines), "6.6")) is flag


@pytest.mark.parametrize("people,flag", [("2", False), ("1", True), ("200", False), ("201", True)])
def test_meal_head_counts(people, flag):
    amount = "150.00" if people in ("1", "2") else "15000.00"
    lines = [EXP, f"E-1,2026-09-01,Ada,MEAL,{amount},RC-1,Client dinner,{people}"]
    assert bool(clauses(rules.expenses(lines), "6.1")) is flag


# ---- reading files
def test_header_text_share_is_eighty_percent():
    assert importer._looks_like_header(["date", "vendor", "ref", "memo", "2026"])           # 4 of 5 are text
    assert not importer._looks_like_header(["date", "vendor", "ref", "100", "2026"])       # 3 of 5


def test_big_whole_numbers_in_excel_stay_whole():
    assert importer._cell(123456789012345.0) == "123456789012345"
    assert importer._cell(1e15) != "1000000000000000.0"


@pytest.mark.parametrize("cols,kind", [
    (["payment_id", "pay_date", "invoice_date", "supplier", "invoice_no", "invoice_amount", "paid_amount"], "payments.csv"),
    (["Date", "Vendor", "Invoice No", "Amount Paid"], None),                       # 4 of 7: not enough without a name
])
def test_recognising_needs_sixty_percent_of_the_columns(cols, kind):
    assert importer.guess("export.csv", [",".join(cols)])[0] == kind


def test_a_matching_name_lowers_the_bar_to_forty_percent():
    head = "*ContactName,AccountNumber,TaxNumber,ContactStatus"              # 4 of 9 vendor columns
    assert importer.guess("Contacts.csv", [head])[0] == "vendors.csv"
    assert importer.guess("export.csv", [head])[0] is None


def test_a_footer_is_lines_with_fewer_than_two_cells():
    lines = ["Report", "", ",Date,Num,Amount Paid", "Ashby,,,", ",2026-09-01,1,10.00", ",2026-09-02,2,", "Printed today"]
    out, notes = importer.tidy(lines)
    assert out[-1] == "Ashby,2026-09-02,2,," or out[-1].startswith("Ashby,2026-09-02,2")      # one value + date: kept
    assert any("1 footer" in n for n in notes)


def test_header_text_share_edge():
    seven_of_nine = ["a", "b", "c", "d", "e", "f", "g", "1", "2"]      # 78%: under 80%, so not column names
    assert not importer._looks_like_header(seven_of_nine)


def test_five_sharing_a_date_in_a_long_list_is_not_an_import():
    """5 of 18 vendors (28%) share a set-up date: under 30%, so it is a real set-up date and the check runs."""
    rows = [f"V-{i},Old {i},55-10000{i:02d},ACTIVE,****1,,N/A,2024-0{1 + i % 9}-1{i % 9},YES,2026-09-20,100.00"
            for i in range(17)]
    for i in range(4):
        rows[i] = rows[i].replace(rows[i].split(",")[7], "2026-09-01", 1)
    rows.append("V-99,Nova,55-1000099,ACTIVE,****1,,N/A,2026-09-01,YES,2026-09-10,15000.00")
    assert clauses(rules.vendors([VEN] + rows), "4.5")
