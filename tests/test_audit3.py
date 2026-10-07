"""Third error audit (October 2026): rules on real-world shapes of data, sessions that could not be restored, a run
with nothing found, the anonymiser and the trail key. Each test pins one fix."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tallyhound import challenge, invoices, rules, store
from tallyhound import common as C

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
BANK_H = "date,description,amount,reference"
PAY_H = "payment_id,pay_date,invoice_date,vendor_id,supplier,invoice_no,invoice_amount,paid_amount"
VEND_H = "vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount"
APPR_H = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no"
EXP_H = "claim_id,date,employee,category,amount,receipt_ref,notes,people"


def clauses(hits):
    return sorted(h.clause for h in hits)


# ---- bank reconciliation
@pytest.mark.parametrize("desc", ["TRANSFER TO J SMITH", "Consulting fees - Smith Advisory", "Visa Logistics Ltd",
                                  "PAYEE J SMITH", "ONLINE PAYMENT TO NEW PAYEE", "Payroll Partners Ltd"])
def test_unrecorded_payments_are_not_hidden_by_routine_words(desc):
    files = {"bank_statement.csv": [BANK_H, f"2026-09-10,{desc},-9800.00,"],
             "payments.csv": [PAY_H, "P1,2026-09-01,2026-08-20,V-1,Arden Metals,INV-1,500.00,500.00"]}
    assert "5.5" in clauses([h for h in rules.reconcile(files) if h.severity == "High"])


@pytest.mark.parametrize("desc", ["BANK CHARGES SEP", "WIRE FEE", "STRIPE FEES", "HMRC PAYE", "IRS EFTPS FEDERAL TAX",
                                  "AMEX EPAYMENT", "INTEREST", "LOAN INSTALMENT", "TRANSFER TO SAVINGS ACCT 4471"])
def test_routine_bank_debits_are_still_left_out(desc):
    files = {"bank_statement.csv": [BANK_H, f"2026-09-10,{desc},-980.00,"],
             "payments.csv": [PAY_H, "P1,2026-09-10,2026-08-20,V-1,Arden Metals,INV-1,500.00,500.00",
                              "P2,2026-09-11,2026-08-20,V-1,Arden Metals,INV-2,480.00,480.00"]}
    assert not [h for h in rules.reconcile(files) if h.severity == "High"]


def test_dr_cr_bank_files_are_read_the_right_way_round():
    files = {"bank_statement.csv": [BANK_H, "2026-09-02,ARDEN METALS,500.00 DR,P1", "2026-09-03,CUSTOMER RECEIPT,250.00 CR,",
                                    "2026-09-04,J SMITH,9000.00 DR,"],
             "payments.csv": [PAY_H, "P1,2026-09-01,2026-08-20,V-1,Arden Metals,INV-1,500.00,500.00"]}
    hits = rules.reconcile(files)
    assert [(h.severity, h.line_number) for h in hits] == [("High", 4)]


def test_one_cent_difference_is_a_match_not_two_findings():
    files = {"bank_statement.csv": [BANK_H, "2026-09-02,ARDEN METALS,-500.01,"],
             "payments.csv": [PAY_H, "P1,2026-09-01,2026-08-20,V-1,Arden Metals,INV-1,500.00,500.00"]}
    assert rules.reconcile(files) == []


def test_unrelated_small_payments_cannot_explain_a_debit_to_a_person():
    pays = [PAY_H] + [f"P{i},2026-09-0{1 + i % 5},2026-08-20,V-{i},Vendor {i},INV-{i},{a:.2f},{a:.2f}"
                      for i, a in enumerate([2500, 1000, 900, 600, 700, 300])]
    files = {"bank_statement.csv": [BANK_H, "2026-09-06,J SMITH,-5000.00,"], "payments.csv": pays}
    assert any(h.severity == "High" for h in rules.reconcile(files))


def test_a_large_bacs_batch_is_matched():
    pays = [PAY_H] + [f"P{i},2026-09-05,2026-08-20,V-{i},Vendor {i},INV-{i},{100 + i:.2f},{100 + i:.2f}" for i in range(15)]
    total = sum(100 + i for i in range(15))
    files = {"bank_statement.csv": [BANK_H, f"2026-09-06,BACS BATCH 05SEP,-{total:.2f},BATCH-1"], "payments.csv": pays}
    assert not [h for h in rules.reconcile(files) if h.severity == "High"]


# ---- amounts and duplicates
@pytest.mark.parametrize("text, value", [("1234,56", 1234.56), ("1 234,56", 1234.56), ("120.00-", -120.0),
                                         ("120.00 CR", -120.0), ("USD 1,200.00", 1200.0), ("1'234.50", 1234.5),
                                         ("−50.00", -50.0)])
def test_amount_formats_from_real_exports(text, value):
    assert rules._f(text) == pytest.approx(value)


@pytest.mark.parametrize("second", ["INV-1", "INV 001", " inv001 "])
def test_duplicates_are_found_however_the_invoice_number_is_written(second):
    lines = [PAY_H, "P1,2026-09-01,2026-08-20,V-1,Ashby Components,INV-001,500.00,500.00",
             f"P2,2026-09-08,2026-08-20,V-1,Ashby Components UK,{second},500.00,500.00"]
    assert "5.2" in clauses(rules.payments(lines))


def test_instalments_with_an_open_balance_column_are_not_duplicates():
    lines = [PAY_H, "P1,2026-09-01,2026-08-20,V-1,Ashby,INV-1,1000.00,500.00",
             "P2,2026-09-15,2026-08-20,V-1,Ashby,INV-1,500.00,500.00"]
    assert "5.2" not in clauses(rules.payments(lines))


def test_a_reversal_later_in_the_file_cancels_the_reissue():
    lines = [PAY_H, "P1,2026-09-01,2026-08-20,V-1,Ashby,INV-1,500.00,500.00",
             "P2,2026-09-03,2026-08-20,V-1,Ashby,INV-1,500.00,500.00", "P1R,2026-09-04,2026-08-20,V-1,Ashby,INV-1,500.00,-500.00"]
    assert "5.2" not in clauses(rules.payments(lines))


# ---- people and approvals
@pytest.mark.parametrize("a, b, same", [("John Smith", "Jane Smith", False), ("Juan García", "José García", False),
                                        ("Smith, John", "John Smith", True), ("jsmith", "J. Smith", True),
                                        ("José García", "Jose Garcia", True), ("J. Smith Jr", "Jane Brown Jr", False)])
def test_self_approval_name_matching(a, b, same):
    assert rules.same_person(a, b) is same


def test_po_placeholders_bills_director_titles_and_exempt_phrases():
    lines = [APPR_H, "A1,BILL,X-1,2026-09-01,Arden Metals,4000.00,K. Lowe,M. Ray,Manager,N/A",
             "A2,INVOICE,X-2,2026-09-01,Power Tools Direct,4000.00,K. Lowe,M. Ray,Manager,",
             "A3,INVOICE,X-3,2026-09-01,City Power & Light,4000.00,K. Lowe,M. Ray,Manager,",
             "A4,INVOICE,X-4,2026-09-01,Arden Metals,12000.00,K. Lowe,M. Ray,Finance Director,PO-1"]
    got = {(h.clause, h.line_number) for h in rules.approvals(lines)}
    assert ("1.1", 2) in got and ("1.1", 3) in got and ("1.1", 4) not in got and ("1.3", 5) not in got


def test_split_orders_by_one_person_under_two_spellings():
    lines = [APPR_H, "A1,INVOICE,X-1,2026-09-01,Arden Metals,6000.00,K. Lowe,M. Ray,Manager,",
             "A2,INVOICE,X-2,2026-09-02,Arden Metals,6000.00,Kate Lowe,M. Ray,Manager,"]
    assert "1.4" in clauses(rules.approvals(lines))


# ---- vendors and expenses
@pytest.mark.parametrize("verified", ["", "N", "PENDING", "FALSE"])
def test_bank_change_is_unverified_unless_clearly_yes(verified):
    lines = [VEND_H, f"V-1,Arden,11-1,ACTIVE,****1,2026-09-01,{verified},2025-01-01,YES,2026-09-10,900.00"]
    assert [h.severity for h in rules.vendors(lines) if h.clause == "4.3"] == ["High"]


def test_vendor_placeholders_and_new_vendor_bank_are_not_findings():
    lines = [VEND_H, "V-1,Arden,N/A,Active - preferred,****1,2026-01-01,N/A,2026-01-01,YES,2026-09-10,900.00",
             "V-2,Bexley,n/a,ACTIVE,****2,,YES,2025-01-01,YES,2026-09-10,900.00",
             "V-3,Calder,000000000,ACTIVE,****3,,YES,2025-01-01,YES,,", "V-4,Dunmore,00-0000000,,****4,,YES,2025-01-01,YES,,"]
    assert rules.vendors(lines) == []


def test_expense_placeholders_categories_and_head_counts():
    lines = [EXP_H, "E1,2026-09-01,A. Patel,TAXI,40.00,N/A,Business purpose recorded,",
             "E2,2026-09-02,R. Chen,TAXI,40.00,N/A,Business purpose recorded,",
             "E3,2026-09-03,R. Chen,Meals,400.00,rc-9,Client dinner (2026),",
             "E4,2026-09-03,A. Patel,MEALS,90.00,RC-9,Team lunch,three",
             "E5,2026-09-04,A. Patel,SUPPLIES,30.00,RC-7,personal protective equipment,"]
    hits = rules.expenses(lines)
    got = clauses(hits)
    assert got.count("6.2") == 2 and "6.5" in got and "6.3" not in got
    # "(2026)" is not a head count, so the $400 dinner has none: flagged as such; "three" people at $90 is fine
    assert [h.line_number for h in hits if h.clause == "6.1"] == [4]
    assert "no head count" in next(h.title for h in hits if h.clause == "6.1")


# ---- invoice PDFs and contracts
V = [VEND_H, "V-1,Ashby Components,1,ACTIVE,****1111,,YES,2025-01-01,YES,,"]
A = [APPR_H, "A1,INVOICE,INV-77,2026-09-01,Ashby Components,1234.56,x,y,Manager,PO1",
     "A2,INVOICE,INV-78,2026-09-01,Ashby Components,500.00,x,y,Manager,PO1"]


def test_invoice_pdfs_iban_second_invoice_labels_eu_totals_and_customer_accounts():
    pdf = ["[PDF a.pdf]", "Ashby Components (UK) Ltd", "Inv No: INV-77", "Invoice: 01/09/2026", "Your account no: 55123",
           "Total due: 1.234,56 EUR", "IBAN GB29 NWBK 6016 1331 9268 19", "Invoice No. INV 78", "Total: 500.00",
           "Pay to account number: 4000 1234 9999"]
    hits = invoices.check({"invoices.txt": pdf, "vendors.csv": V, "approvals.csv": A})
    assert sorted((h.clause, h.title.split()[1]) for h in hits) == [("8.2", "INV"), ("8.2", "INV-77")]


def test_duplicate_pdf_is_found_when_one_copy_starts_with_tax_invoice():
    pdf = ["[PDF a.pdf]", "Ashby Components", "Invoice No: INV-77", "Total: 1,234.56",
           "[PDF b.pdf]", "TAX INVOICE", "Invoice No: INV-77", "Total: 1,234.56"]
    assert "8.3" in clauses(invoices.check({"invoices.txt": pdf, "vendors.csv": V, "approvals.csv": A}))


def test_total_with_a_qualifier_is_read():
    pdf = ["[PDF a.pdf]", "Ashby Components", "Invoice No: INV-78", "Total (incl. VAT): 1,500.00"]
    assert "8.1" in clauses(invoices.check({"invoices.txt": pdf, "approvals.csv": A}))


def test_contract_checks_survive_name_suffixes_and_dollar_rates():
    lines = ["[CONTRACT C-1 | Calder Logistics | Rate card] Driver rate is $92.00 per hour.",
             "[INVOICE CAL-1 | Calder Logistics Ltd | 2026-09-16] Line 1: Driver 10 hrs @ $120 per hour = 1200.00"]
    assert clauses(rules.contracts(lines)) == ["7.2"]


# ---- the app: nothing found, and sessions that could not be restored
def test_a_run_that_finds_nothing_shows_every_page(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    files = challenge.generate_full(3, "medium")[0]
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state.uploads = {"v": {"vendors.csv": files["vendors.csv"][:3]}}
    at.session_state.extra_opts = {"audit": {"v": "1 file"}}
    at.run()
    at.button(key="open_run").click().run()
    at.button(key="run_go").click().run()
    for _ in range(20):
        if ("dataset" in at.session_state and at.session_state["dataset"]) == "v":
            break
        time.sleep(1)
        at.run()
    for page in C.NAV:
        at.session_state.nav = page
        at.run()
        assert not at.exception, (page, [e.value for e in at.exception])


@pytest.mark.parametrize("data", [[1, 2], {"decisions": [1]}, {"decisions": {"F-01": "Approved"}}, {"custom": [1]},
                                  {"sim": "x"}, {"audit_log": ["a"]}, {"limits": "x"}, {"by_dataset": {"x": 1}},
                                  {"custom": {"m": [{"id": "F-01", "source_file": "payments.csv", "line_number": 2,
                                                     "area": "Payments", "clause": "5.1", "severity": "High",
                                                     "title": "x"}]}, "dataset": "m"}])
def test_damaged_saved_work_never_breaks_the_session(tmp_path, monkeypatch, data):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    sid = "ab" * 16
    store._write(sid, json.dumps(data))
    for _ in range(2):                                   # and not on the next reload either
        at = AppTest.from_file(APP, default_timeout=60)
        at.query_params["s"] = sid
        at.run()
        for page in C.NAV:
            at.session_state.nav = page
            at.run()
            assert not at.exception, (data, page, [e.value for e in at.exception])


def test_a_real_session_survives_the_shape_check_untouched():
    snap = {"decisions": {"F-01": {"status": "Approved", "reason": ""}}, "audit_log": [{"time": "t"}],
            "cleared": {"3": "phoned"}, "limits": {"po_limit": 3000.0, "po_exempt_words": ["rent"]},
            "answer_keys": {"m": "sample"}, "shadow": ["1.1"], "shadow_marks": {"1.1|m|a.csv:2": 1},
            "trail_seals": {"m": "x"}, "dataset": "m",
            "custom": {"m": [{"id": "F-01", "source_file": "a.csv", "line_number": 2, "area": "Payments", "clause": "5.1",
                              "severity": "High", "title": "x"}]}}
    good, bad = store.clean(snap)
    assert bad == [] and good == snap


def test_uploads_are_swept_with_their_session_not_on_their_own(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    store._write("cd" * 16, "{}")
    store._write("cd" * 16 + ":uploads", "{}")
    with store._db() as con:                             # the uploads row was written long ago, the work today
        con.execute("UPDATE state SET updated = 0 WHERE sid = ?", ("cd" * 16 + ":uploads",))
    store._sweep()
    assert store._read("cd" * 16 + ":uploads") == "{}"


# ---- anonymiser and trail key
def test_anonymiser_removes_personal_data_from_free_text_and_reasons(tmp_path):
    case = {"description": "Rejected by a reviewer: Approved verbally by Maria Gonzalez, see maria.g@acme-corp.com",
            "files": {"bank_statement.csv": [BANK_H, "2026-09-01,PAYMENT TO ACC 12345678 Mike Ross,-100.00,J. Pryce"],
                      "expenses.csv": [EXP_H, "E1,2026-09-01,Kate Lowe,MEAL,40.00,RC-1,Team dinner at 12 Baker Street "
                                              "NW1 6XE IBAN GB29 NWBK 6016 1331 9268 19 call 020 7946 0958,2"]},
            "must_not_flag": []}
    src = tmp_path / "case.json"
    src.write_text(json.dumps(case), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "anonymise.py"), str(src), "--out", str(tmp_path / "o")],
                   check=True, capture_output=True)
    text = (tmp_path / "o" / "case.json").read_text(encoding="utf-8")
    for secret in ["Maria", "Gonzalez", "acme-corp", "12345678", "Mike Ross", "Pryce", "Kate Lowe", "Baker", "NW1 6XE",
                   "6016 1331", "7946 0958"]:
        assert secret not in text, secret
    assert "2026-09-01" in text and "Team dinner" in text                 # dates and signals stay


def test_without_a_writable_disk_the_trail_key_is_secret_not_a_constant(monkeypatch, tmp_path):
    monkeypatch.delenv("TALLYHOUND_TRAIL_KEY", raising=False)
    (tmp_path / "a-file").write_text("x", encoding="utf-8")                       # a folder cannot be made inside a file
    monkeypatch.setattr(store, "state_dir", lambda: tmp_path / "a-file" / "x")
    monkeypatch.setattr(C, "_EPHEMERAL_KEY", None)
    k = C._trail_key()
    assert len(k) == 32 and b"tallyhound" not in k and C._trail_key() == k


# ---- the two judgment calls, decided
def test_three_decimal_amounts_follow_the_files_decimal_style():
    eu = ["id,amount", '1,"1.234"', '2,"99,50"']
    us = ["id,amount", "1,1.234", "2,99.50"]
    assert [rules._f(r["amount"]) for _, r in rules.rows(eu)] == [1234.0, 99.5]
    assert [rules._f(r["amount"]) for _, r in rules.rows(us)] == [1.234, 99.5]   # and the data check warns about it


def test_payment_without_vendor_id_to_an_unknown_payee_is_flagged():
    vend = [VEND_H, "V-1,Ashby Components,1,ACTIVE,****1,,YES,2025-01-01,YES,,"]
    pays = [PAY_H, "P1,2026-09-01,2026-08-20,,Ashby Components UK,INV-1,500.00,500.00",
            "P2,2026-09-01,2026-08-20,,J Smith Consulting,INV-2,900.00,900.00"]
    got = [(h.clause, h.line_number) for h in rules.cross_file({"payments.csv": pays, "vendors.csv": vend})]
    assert got == [("4.1", 3)]


@pytest.mark.parametrize("run", range(5))
def test_anonymised_month_gives_the_same_findings_every_run(tmp_path, run):
    files, _ = challenge.generate(2 + run, "medium")
    src = tmp_path / "src"
    src.mkdir()
    for n, v in files.items():
        (src / n).write_text("\n".join(v) + "\n", encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "anonymise.py"), str(src), "--out", str(tmp_path / "o")],
                   check=True, capture_output=True)
    out = {p.name: p.read_text(encoding="utf-8").splitlines() for p in (tmp_path / "o").iterdir()}
    key = lambda fs: sorted((h.clause, h.source_file, h.line_number) for h in rules.analyze(fs))  # noqa: E731
    assert key(files) == key(out)
