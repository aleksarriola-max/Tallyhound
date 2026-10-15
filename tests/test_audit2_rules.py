"""Second audit of the rule checks and the payment gate: each test is one bug that was found and fixed."""
from tallyhound import gate, learn, rules

APP = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no"
VEN = "vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount"
PAY = "payment_id,pay_date,invoice_date,vendor_id,supplier,invoice_no,invoice_amount,paid_amount"
EXP = "claim_id,date,employee,category,amount,receipt_ref,notes,people"
RUN = "line,vendor_id,supplier,invoice,amount,bank_last4"


def test_gate_releases_a_vendor_with_a_blank_status_as_the_rules_do():
    # 4.1 and the payments-to-vendor check read a blank status as active; the gate held it as "not active"
    v = "V1,Acme Parts,11-111,,****1234,,N/A,2024-01-01,YES,2026-08-10,500.00"
    assert not rules.vendors([VEN, v])
    df = gate.evaluate({"payment_run.csv": [RUN, "1,V1,Acme Parts,INV-7,100.00,1234"], "vendors.csv": [VEN, v]})
    assert df.decision.tolist() == ["RELEASE"], df.reason.tolist()
    closed = "V1,Acme Parts,11-111,INACTIVE,****1234,,N/A,2024-01-01,YES,2026-08-10,500.00"
    df = gate.evaluate({"payment_run.csv": [RUN, "1,V1,Acme Parts,INV-7,100.00,1234"], "vendors.csv": [VEN, closed]})
    assert df.decision.tolist() == ["HOLD"]


def test_blank_tax_form_is_missing_when_the_file_records_tax_forms():
    # 4.4 flagged only an explicit NO; a blank among YES rows is a vendor whose form was never recorded
    rows = [VEN, "V1,Acme Parts,11-111,ACTIVE,****1234,,N/A,2024-01-01,YES,2026-08-10,500.00",
            "V2,Birch Metals,22-222,ACTIVE,****5678,,N/A,2024-01-01,,2026-08-11,700.00"]
    assert [(h.clause, h.line_number) for h in rules.vendors(rows)] == [("4.4", 3)]
    # a file that never fills the column does not track tax forms: nothing to read
    blank = [VEN, rows[1].replace("YES", ""), rows[2]]
    assert not rules.vendors(blank)


def test_mileage_and_allowances_are_not_asked_for_a_receipt():
    # 6.6 already treats these as claims that never carry a receipt; 6.2 asked for one on every claim over $25
    lines = [EXP, "E-1,2026-09-01,Ada Ng,MILEAGE,48.60,,Client visit 90 miles,",
             "E-2,2026-09-02,Ada Ng,PER DIEM,40.00,,Conference day,",
             "E-3,2026-09-03,Ada Ng,TAXI,40.00,,Client visit,"]
    assert [(h.clause, h.line_number) for h in rules.expenses(lines)] == [("6.2", 4)]


def test_an_assistant_to_a_director_is_not_a_director():
    lines = [APP, "A1,INVOICE,INV-1,2026-09-01,Acme Parts,15000.00,J Okoro,M Lind,Assistant to the Director,PO-1",
             "A2,INVOICE,INV-2,2026-09-01,Acme Parts,15000.00,J Okoro,M Lind,PA to CFO,PO-2",
             "A3,INVOICE,INV-3,2026-09-01,Acme Parts,15000.00,J Okoro,M Lind,Assistant Director,PO-3",
             "A4,INVOICE,INV-4,2026-09-01,Acme Parts,15000.00,J Okoro,M Lind,Finance Director,PO-4",
             "A5,INVOICE,INV-5,2026-09-01,Acme Parts,15000.00,J Okoro,M Lind,Director of Operations,PO-5"]
    assert [h.line_number for h in rules.approvals(lines) if h.clause == "1.3"] == [2, 3, 4]


def test_director_check_is_one_finding_per_purchase_not_per_document():
    # a PO over the limit and the invoice billed against it are one purchase: the PO is where approval is given
    po = "A1,PO,PO-1,2026-09-01,Acme Parts,15000.00,J Okoro,M Lind,Manager,PO-1"
    inv = "A2,INVOICE,INV-1,2026-09-05,Acme Parts,15000.00,J Okoro,M Lind,Manager,PO-1"
    (h,) = [h for h in rules.approvals([APP, po, inv]) if h.clause == "1.3"]
    assert (h.line_number, h.related) == (2, [("approvals.csv", 3)])
    # a director approved the order: the invoice against it needs no second director
    signed = po.replace("Manager", "Finance Director")
    assert not [h for h in rules.approvals([APP, signed, inv]) if h.clause == "1.3"]
    # but an invoice over what the PO approved still does
    big = inv.replace("15000.00", "18000.00")
    assert [h.line_number for h in rules.approvals([APP, signed, big]) if h.clause == "1.3"] == [3]


def test_overpayment_is_not_the_whole_payment_when_the_invoice_amount_is_blank():
    lines = [PAY, "P1,2026-09-03,2026-09-01,V1,Acme Parts,INV-1,,500.00"]
    assert not [h for h in rules.payments(lines) if h.clause == "5.1"]
    # the invoice amount is known from another line of the same invoice
    lines = [PAY, "P1,2026-09-03,2026-09-01,V1,Acme Parts,INV-1,400.00,0.00",
             "P2,2026-09-04,2026-09-01,V1,Acme Parts,INV-1,,500.00"]
    assert [(h.clause, h.amount) for h in rules.payments(lines) if h.clause == "5.1"] == [("5.1", 100.0)]


def test_a_duplicate_is_found_when_only_one_payment_carries_the_vendor_id():
    lines = [PAY, "P1,2026-09-03,2026-09-01,V1,Acme Parts,INV-1,500.00,500.00",
             "P2,2026-09-10,2026-09-01,,Acme Parts Ltd,INV-1,500.00,500.00"]
    assert [h.clause for h in rules.payments(lines) if h.clause == "5.2"] == ["5.2"]
    # a name used by two vendor ids stays unresolved: two suppliers, no duplicate
    lines.insert(2, "P0,2026-09-02,2026-09-01,V2,Acme Parts,INV-9,50.00,50.00")
    assert not [h for h in rules.payments(lines) if h.clause == "5.2"]


def test_weekend_trip_matches_the_employee_however_the_name_is_written():
    lines = [EXP, "E-1,2026-09-04,\"Lowe, Kate\",HOTEL,180.00,R-1,Client site visit,",
             "E-2,2026-09-05,Kate Lowe,TAXI,22.00,R-2,,"]
    assert not [h for h in rules.expenses(lines) if h.clause == "6.4"]


def test_invoice_suppression_is_for_the_supplier_not_one_line_of_text():
    lines = ["[PDF A.pdf]", "Arden Print", "From: Arden Print", "Invoice No: ARD-1", "Total due: $1,000.00",
             "[PDF B.pdf]", "Kestrel Plastics", "From: Kestrel Plastics", "Invoice No: KES-2", "Total due: $1,000.00"]
    assert learn.entity("invoices.txt", lines[4], lines=lines, line=5) == "Arden Print"
    assert learn.entity("invoices.txt", lines[9], lines=lines, line=10) == "Kestrel Plastics"


def test_po_after_invoice_uses_the_po_of_the_same_vendor():
    # two suppliers' systems both issued PO-9; the invoice was checked against whichever came last in the file
    lines = [APP, "A1,PO,PO-9,2026-08-25,Acme Parts,1200.00,J Okoro,M Lind,Manager,PO-9",
             "A2,INVOICE,INV-1,2026-09-01,Acme Parts,1200.00,J Okoro,M Lind,Manager,PO-9",
             "A3,PO,PO-9,2026-09-20,Birch Metals,800.00,J Okoro,M Lind,Manager,PO-9"]
    assert not [h for h in rules.approvals(lines) if h.clause == "1.1"]


def test_split_check_does_not_merge_two_people_through_an_initial():
    # "K. Lowe" could be Kate or Kim; joining both to it made Kate's and Kim's bills one split
    base = "INVOICE,INV-{n},2026-09-0{d},Acme Parts,6000.00,{who},M Lind,Manager,"
    lines = [APP] + [f"A{n}," + base.format(n=n, d=d, who=w) for n, d, w in
                     [(1, 1, "K. Lowe"), (2, 2, "Kate Lowe"), (3, 2, "Kim Lowe")]]
    assert not [h for h in rules.approvals(lines) if h.clause == "1.4"]
    # Kate's two bills (one under her initial) are still one split
    lines = [APP] + [f"A{n}," + base.format(n=n, d=d, who=w) for n, d, w in [(1, 1, "K. Lowe"), (2, 2, "Kate Lowe")]]
    assert [h.clause for h in rules.approvals(lines) if h.clause == "1.4"] == ["1.4"]
