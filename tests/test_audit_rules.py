"""Audit of the rule checks, the payment gate and the docs: each test is one bug that was found and fixed."""
from pathlib import Path

import pandas as pd

from tallyhound import challenge, gate, learn, patterns, rules

ROOT = Path(__file__).resolve().parent.parent
APP = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no"
VEN = "vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount"
RUN = "line,vendor_id,supplier,invoice,amount,bank_last4"


def test_po_raised_after_a_bill_is_flagged_like_one_after_an_invoice():
    # QuickBooks calls an invoice a "Bill"; every other approvals check already treats it as an invoice
    lines = [APP, "A1,BILL,INV-1,2026-09-01,Acme Parts,1200.00,J Okoro,M Lind,Manager,PO-9",
             "A2,PO,PO-9,2026-09-15,Acme Parts,1200.00,J Okoro,M Lind,Manager,PO-9"]
    assert [h.title for h in rules.approvals(lines)] == ["PO-9 raised 14 days after invoice INV-1"]


def test_month_first_dates_with_dashes_are_read():
    # date_order() calls 09-19-2026 month-first, so the parser must read it, not drop it as blank
    assert rules._d("09-19-2026") == rules._d("09/19/2026") is not None
    pay = ["payment_id,pay_date,invoice_date,supplier,invoice_no,invoice_amount,paid_amount",
           "P1,09-19-2026,09-10-2026,Acme,INV-1,100.00,100.00"]
    assert [h.clause for h in rules.payments(pay)] == ["5.4"]


def test_approvals_at_exactly_the_director_limit_count_as_just_under_it():
    # 1.3 needs a director only above the limit, so $10,000.00 is the most a requester can steer to
    lines = [APP, "A1,INVOICE,D-1,2026-09-01,Acme Parts,10000.00,J Okoro,M Lind,Manager,PO-1",
             "A2,INVOICE,D-2,2026-09-02,Birch Metals,10000.00,J Okoro,M Lind,Manager,PO-2"]
    assert [h.clause for h in rules.approvals(lines)] == ["1.6"]


def test_bunching_counts_amounts_at_the_limit_as_under_it():
    # a claim of exactly the receipt limit needs no receipt: it is under the limit, not above it
    r = patterns.just_under([24.5, 24.6, 24.7, 25.0, 25.0, 25.0], 25.0)
    assert (r["below"], r["above"], r["flag"]) == (6, 0, True)
    assert patterns.just_under([25.01, 25.5], 25.0)["above"] == 2


def test_weekend_taxi_on_the_same_day_as_a_trip_claim_is_left_alone():
    lines = ["claim_id,date,employee,category,amount,receipt_ref,notes,people",
             "E1,2026-09-19,R. Chen,HOTEL,180.00,RC-1,Hotel for client conference,",
             "E2,2026-09-19,R. Chen,TAXI,30.00,RC-2,,",
             "E3,2026-09-19,R. Chen,TRAVEL,40.00,RC-3,,"]
    assert [(h.clause, h.line_number) for h in rules.expenses(lines)] == []
    alone = [lines[0], "E9,2026-09-19,R. Chen,TRAVEL,40.00,RC-3,,"]
    assert [h.clause for h in rules.expenses(alone)] == ["6.4"]


def test_new_vendor_with_a_blank_status_is_checked_like_an_active_one():
    # a blank status reads as active in 4.1 and the cross-file check; 4.5 skipped it
    lines = [VEN, "V1,Acme,11-111,,****1234,,N/A,2026-09-01,YES,2026-09-10,25000.00"]
    assert [h.clause for h in rules.vendors(lines)] == ["4.5"]


def _gate(run, vendors=None, approvals=None, payments=None):
    f = {"payment_run.csv": [RUN] + run}
    if vendors:
        f["vendors.csv"] = [VEN] + vendors
    if approvals:
        f["approvals.csv"] = [APP] + approvals
    if payments:
        f["payments.csv"] = ["payment_id,pay_date,invoice_date,supplier,invoice_no,invoice_amount,paid_amount"] + payments
    return gate.evaluate(f)


V1 = "V1,Acme Parts,11-111,ACTIVE,****1234,,N/A,2024-01-01,YES,2026-08-10,500.00"
V2 = "V2,Birch Metals,22-222,ACTIVE,****5678,,N/A,2024-01-01,YES,2026-08-10,500.00"


def test_gate_holds_an_invoice_already_paid_under_another_spelling_of_its_number():
    # the rules call INV-0042 and INV42 one invoice (5.2); the gate released it
    df = _gate(["1,V1,Acme Parts,INV-0042,300.00,1234"], [V1],
               ["A3,INVOICE,INV-0042,2026-09-02,Acme Parts,300.00,J Okoro,M Lind,Manager,PO-3"],
               ["P1,2026-09-03,2026-09-02,Acme Parts,INV42,300.00,300.00"])
    assert df.decision.tolist() == ["HOLD"] and df.reason[0].startswith("Invoice already paid")


def test_gate_keeps_two_suppliers_invoice_numbers_apart():
    # two suppliers can both send invoice 1001: neither belongs to the other, neither is a repeat
    df = _gate(["1,V1,Acme Parts,1001,100.00,1234", "2,V2,Birch Metals,1001,200.00,5678"], [V1, V2],
               ["A1,INVOICE,1001,2026-09-01,Acme Parts,100.00,J Okoro,M Lind,Manager,PO-1",
                "A2,INVOICE,1001,2026-09-02,Birch Metals,200.00,J Okoro,M Lind,Manager,PO-2"],
               ["P1,2026-09-03,2026-09-02,Cobble Ltd,1001,50.00,50.00"])
    assert df.decision.tolist() == ["RELEASE", "RELEASE"], df.reason.tolist()
    again = _gate(["1,V1,Acme Parts,1001,100.00,1234", "2,V1,Acme Parts,1001,100.00,1234"], [V1])
    assert again.decision.tolist() == ["RELEASE", "HOLD"] and "earlier in this run" in again.reason[1]


def test_gate_reads_vendor_status_and_bank_verification_as_the_rules_do():
    v = "v1,Acme Parts,11-111,Active - preferred,****1234,2026-08-01,Y,2024-01-01,YES,2026-08-10,500.00"
    assert not rules.vendors([VEN, v])
    df = _gate(["1,V1,Acme Parts,INV-7,100.00,1234"], [v])
    assert df.decision.tolist() == ["RELEASE"], df.reason.tolist()


def test_gate_does_not_call_a_missing_master_account_a_different_one():
    df = _gate(["1,V1,Acme Parts,INV-7,100.00,1234"], ["V1,Acme Parts,11-111,ACTIVE,,,N/A,2024-01-01,YES,,"])
    assert df.decision.tolist() == ["RELEASE"]
    assert "differs" not in df.reason[0] and "no bank account to compare" in df.reason[0]


def test_gate_still_holds_the_challenge_run_lines():
    files, _ = challenge.generate(3, "medium")
    assert (gate.evaluate(files).decision == "HOLD").sum() == 4


def test_cross_file_vendor_findings_get_their_clause_text():
    # cross_file() reports Vendors clause 4.1 under Payments; the reason and the Skeptic got no policy text
    df = pd.read_csv(ROOT / "data" / "policy.csv", dtype=str)
    policy = {f"{r.clause}|{r.area}": r.text for r in df.itertuples()}
    vend = ["vendor_id,name,tax_id,status,bank_changed_on,bank_verified,w9_on_file,last_paid_on,last_paid_amount",
            "V1,Acme Parts,11-111,ACTIVE,,,YES,2026-08-10,500.00"]
    pay = ["payment_id,pay_date,invoice_date,vendor_id,supplier,invoice_no,invoice_amount,paid_amount",
           "P1,2026-09-03,2026-09-02,V9,Ghost Ltd,X1,10.00,10.00"]
    (h,) = rules.cross_file({"payments.csv": pay, "vendors.csv": vend})
    assert (h.clause, h.area) == ("4.1", "Payments")
    assert rules.clause_text(policy, h.clause, h.area) == "Payments may only go to active vendors."
    assert rules.clause_text(policy, "5.2", "Payments") == "A single invoice may be paid once only."


def test_po_after_invoice_rejections_do_not_suggest_a_new_po_limit():
    f = pd.DataFrame(dict(id=["F-1", "F-2"], clause=["1.1", "1.1"], amount=[2600.0, 2700.0],
                          title=["PO-9 raised 14 days after invoice INV-1", "PO-12 raised 9 days after invoice INV-5"]))
    assert learn.limit_hints(f, {"F-1": {"status": "Rejected"}, "F-2": {"status": "Rejected"}}, rules.LIMITS) == []


def test_readme_trap_count_matches_the_generator():
    _, key = challenge.generate(1, "easy")
    n = sum(k["expect"] == "trap" for k in key)
    assert f"plants {n} of them" in (ROOT / "README.md").read_text(encoding="utf-8")
