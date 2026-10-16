"""Differential audit of 0.7.0 against 0.7.1: each test is a case 0.7.0 got right that a 0.7.1 fix broke."""
from tallyhound import gate, rules, uploads

APP = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no"
VEN = "vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount"
RUN = "line,vendor_id,supplier,invoice,amount,bank_last4"
V1 = "V1,Acme Parts,11-111,ACTIVE,****1234,,N/A,2024-01-01,YES,2026-08-10,500.00"
PAY_TABS = "\t".join(["payment_id", "pay_date", "invoice_date", "supplier", "invoice_no", "invoice_amount", "paid_amount"])


def _splits(names: list[str]) -> list[str]:
    base = "INVOICE,INV-{n},2026-09-0{n},Acme Parts,6000.00,{who},M Lind,Manager,"
    lines = [APP] + [f"A{n}," + base.format(n=n, who=w) for n, w in enumerate(names, 1)]
    return [h.clause for h in rules.approvals(lines) if h.clause == "1.4"]


# ---- 1.4: one requester under two spellings
def test_split_check_still_joins_a_user_name_to_its_initial():
    # "klowe" is "K. Lowe" (see same_person); with no full name in the file there is nothing to be unsure about
    assert _splits(["K. Lowe", "klowe"]) == ["1.4"]


def test_split_check_still_joins_a_name_with_and_without_a_middle_initial():
    assert _splits(["Kate Lowe", "Kate A. Lowe"]) == ["1.4"]
    # and "K. Lowe" fits only that one person, so it joins too
    assert _splits(["Kate Lowe", "K. Lowe", "Kate A. Lowe"]) == ["1.4"]


def test_split_check_still_keeps_two_people_apart():
    assert _splits(["K. Lowe", "Kate Lowe", "Kim Lowe"]) == []


# ---- 1.3: an invoice "within" a PO
def test_invoices_billed_beyond_a_director_approved_po_still_need_a_director():
    # the director approved $20,000; the second $15,000 invoice takes the PO to $30,000
    po = "A1,PO,PO-1,2026-09-01,Acme Parts,20000.00,T Brandt,D Reyes,Finance Director,"
    inv = "A{n},INVOICE,INV-{n},2026-09-0{n},Acme Parts,15000.00,T Brandt,K Lowe,Manager,PO-1"
    lines = [APP, po, inv.format(n=2), inv.format(n=3)]
    assert [h.line_number for h in rules.approvals(lines) if h.clause == "1.3"] == [4]


def test_an_invoice_quoting_another_suppliers_po_is_checked_on_its_own():
    po = "A1,PO,PO-1,2026-09-01,Ashby Components,20000.00,T Brandt,D Reyes,Finance Director,"
    inv = "A2,INVOICE,ZEP-1,2026-09-05,Zephyr Holdings,15000.00,T Brandt,K Lowe,Manager,PO-1"
    assert [h.line_number for h in rules.approvals([APP, po, inv]) if h.clause == "1.3"] == [3]


def test_a_director_whose_role_names_who_they_report_to_is_a_director():
    for role in ("Finance Director (reports to CEO)", "Director reporting to the CFO"):
        assert rules.is_director(role), role
    for role in ("PA to Director", "Assistant to the CFO", "Assistant Director", "Associate Director"):
        assert not rules.is_director(role), role


# ---- payment gate: an invoice listed twice in approvals
def _gate(run: list[str], approvals: list[str]):
    df = gate.evaluate({"payment_run.csv": [RUN] + run, "vendors.csv": [VEN, V1], "approvals.csv": [APP] + approvals})
    return list(zip(df.decision, df.reason))


def test_gate_releases_an_invoice_approved_on_a_later_row():
    # an approvals log lists the invoice when it was entered (no approver yet) and again once approved
    got = _gate(["1,V1,Acme Parts,INV-7,100.00,1234"],
                ["A1,INVOICE,INV-7,2026-09-01,Acme Parts,100.00,J Okoro,,Manager,PO-1",
                 "A2,INVOICE,INV-7,2026-09-03,Acme Parts,100.00,J Okoro,M Lind,Manager,PO-1"])
    assert got[0][0] == "RELEASE", got


def test_gate_compares_with_the_latest_approved_amount():
    rows = ["A1,INVOICE,INV-7,2026-09-01,Acme Parts,100.00,J Okoro,M Lind,Manager,PO-1",
            "A2,INVOICE,INV-7,2026-09-03,Acme Parts,120.00,J Okoro,M Lind,Manager,PO-1"]
    assert _gate(["1,V1,Acme Parts,INV-7,120.00,1234"], rows)[0][0] == "RELEASE"
    # still held when nothing approved covers it
    assert _gate(["1,V1,Acme Parts,INV-7,130.00,1234"], rows)[0][0] == "HOLD"
    assert _gate(["1,V1,Acme Parts,INV-7,100.00,1234"], [r.replace("M Lind", "") for r in rows])[0][0] == "HOLD"


# ---- tab and semicolon files: a legitimate quoted value
def _read(name: str, text: str) -> list[str]:
    files, _ = uploads.read_uploads([(name, text.encode())])
    return files["payments.csv"]


def test_a_multi_line_value_below_a_title_line_is_joined_not_split():
    row = ["P-2", "2026-09-02", "2026-09-01", '"Acme\nParts"', "A-2", "200.00", "200.00"]
    last = ["P-3", "2026-09-03", "2026-09-01", "Acme", "A-3", "300.00", "300.00"]
    text = "Payments report\n" + "\n".join([PAY_TABS, "\t".join(row), "\t".join(last)]) + "\n"
    got = _read("payments.tsv", text)
    assert [r["supplier"] for _, r in rules.rows(got)] == ["Acme Parts", "Acme"], got


def test_a_multi_line_value_on_a_short_row_is_joined_not_split():
    # the export leaves off the empty last column
    row = ["P-1", "2026-09-01", "2026-09-01", '"Acme\nParts"', "A-1", "100.00"]
    text = "\n".join([PAY_TABS, "\t".join(row)]) + "\n"
    got = _read("payments.tsv", text)
    assert len(got) == 2 and [r["supplier"] for _, r in rules.rows(got)] == ["Acme Parts"], got


def test_a_quoted_semicolon_on_a_short_row_stays_in_its_value():
    text = "\n".join([PAY_TABS.replace("\t", ";"), 'P-1;2026-09-01;2026-09-01;"Acme; Parts";A-1;100,00',
                      "P-2;2026-09-02;2026-09-01;Acme;A-2;200,00;200,00"]) + "\n"
    got = _read("payments.csv", text)
    assert [r["supplier"] for _, r in rules.rows(got)] == ["Acme; Parts", "Acme"], got
