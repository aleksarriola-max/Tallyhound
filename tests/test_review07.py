"""Regression tests from the 0.7 review: each one pins a defect found before release."""
import io
import time
import zipfile

import openpyxl

from tallyhound import challenge, importer, rules, score, uploads

PAY = "payment_id,pay_date,invoice_date,vendor_id,supplier,invoice_no,invoice_amount,paid_amount"
APPR = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no\n" \
       "A-1,INVOICE,X-1,2026-09-02,Arden,100.00,A. Patel,K. Lowe,Manager,PO-1\n"


def _read(*files):
    return uploads.read_uploads([(n, t.encode() if isinstance(t, str) else t) for n, t in files])


# ---- recognising files
def test_a_generic_export_is_not_taken_for_a_bank_statement():
    files, notes = _read(("approvals.csv", APPR), ("Export.csv", "Date,Name,Num,Amount,Memo\n2026-09-01,Ada,1,10.00,x\n"))
    assert set(files) == {"approvals.csv"} and any("do not clearly match" in n for n in notes)


def test_a_properly_named_file_keeps_its_place():
    bank = "date,description,amount\n2026-09-01,PAYMENT ASHBY,-10.00\n"
    files, notes = _read(("Payroll bank export.csv", bank), ("bank_statement.csv", bank.replace("ASHBY", "BEXLEY")))
    assert "BEXLEY" in files["bank_statement.csv"][1]


def test_the_columns_decide_not_the_name():
    exp = "claim_id,date,employee,category,amount,receipt_ref,notes,people\nE-1,2026-09-01,Ada,TAXI,10.00,RC-1,x,\n"
    files, _ = _read(("Card transactions.csv", exp))
    assert set(files) == {"expenses.csv"}
    bills = "Bill Date,Vendor,Bill No,Amount,Approved By,PO Number\n2026-09-01,Arden,B-1,10.00,K. Lowe,PO-1\n"
    files, _ = _read(("Vendor Bills.csv", bills))
    assert "payment_run.csv" not in files


# ---- tidying
def _qb(*rows, head=",Date,Num,Invoice Date,Invoice No,Original Amount,Amount Paid,Memo"):
    return "\n".join(["Ashgrove Ltd", "Bill Payment List", "", head, *rows, "", "Printed 2026-10-08"])


def test_xero_style_total_lines_and_a_supplier_called_total():
    text = _qb("Ashby Components,,,,,,,", ",09/01/2026,P-1,09/01/2026,A-1,100.00,100.00,",
               "Total Ashby Components,,,,,,100.00,", "TOTAL,,,,,,,", ",09/02/2026,P-2,09/02/2026,T-1,500.00,500.00,",
               "Total for TOTAL,,,,,,500.00,")
    lines, _ = importer.tidy(text.splitlines())
    rows = lines[1:]
    assert rows == ["Ashby Components,09/01/2026,P-1,09/01/2026,A-1,100.00,100.00,",
                    "TOTAL,09/02/2026,P-2,09/02/2026,T-1,500.00,500.00,"]


def test_rows_after_a_total_line_do_not_inherit_the_vendor():
    text = _qb("Ashby,,,,,,,", ",09/01/2026,P-1,09/01/2026,A-1,100.00,100.00,", "Total for Ashby,,,,,,100.00,",
               ",09/03/2026,P-9,09/03/2026,Z-1,50.00,50.00,")
    lines, _ = importer.tidy(text.splitlines())
    assert lines[-1].startswith(",09/03/2026")


def test_headings_are_not_written_over_a_real_name_column():
    text = "\n".join(["Check Detail", "", ",Date,Num,Name,Invoice No,Amount Paid,Original Amount", "Checking,,,,,,",
                      ",09/01/2026,1001,Ashby,A-1,100.00,100.00", ",09/02/2026,1002,Bexley,B-1,100.00,100.00"])
    lines, notes = importer.tidy(text.splitlines())
    assert lines[0].startswith(",Date") and "Checking" not in "\n".join(lines)
    assert any("own name column" in n for n in notes)


def test_plain_files_named_the_tallyhound_way_are_untouched():
    lines = ["supplier,date,amount,x", "Total,2026-09-01,10.00,y", "Ashby,2026-09-02,5.00,", "z,,,"]
    assert importer.tidy(lines) == (lines, [])


def test_a_title_line_with_three_cells_is_not_the_header():
    text = "\n".join(["Report period:,From 2026-09-01,To 2026-09-30", "",
                      "payment_id,pay_date,invoice_date,supplier,invoice_no,invoice_amount,paid_amount",
                      "P-1,2026-09-01,2026-09-01,Ashby,A-1,10.00,10.00"])
    lines, _ = importer.tidy(text.splitlines())
    assert lines[0].startswith("payment_id") and len(lines) == 2


def test_a_value_over_two_lines_stays_in_its_row():
    text = PAY + '\nP-1,2026-09-01,2026-09-01,V-1,Ashby,A-1,100.00,100.00\nP-2,2026-09-02,2026-09-02,V-1,"Ashby\nLtd",B-1,5.00,5.00\n'
    files, notes = _read(("payments.csv", text))
    assert files["payments.csv"][2] == 'P-2,2026-09-02,2026-09-02,V-1,"Ashby Ltd",B-1,5.00,5.00'
    assert any("joined" in n for n in notes)


def test_contracts_with_a_tab_are_not_turned_into_a_table():
    text = "[CONTRACT C-1 | Arden | Clause 2.1]\tPrices fixed.\n[INVOICE A-1 | Arden | 2026-09-01] 1,200.00, freight\n"
    files, _ = _read(("contracts.txt", text))
    assert files["contracts.txt"][1].startswith("[INVOICE A-1")


# ---- Excel
def _book(rows, dims=None, hidden_first=False) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    if hidden_first:
        lookup = ws
        lookup.title = "Lookup"
        for i in range(50):
            lookup.append([f"code{i}", i, i, i])
        lookup.sheet_state = "hidden"
        ws = wb.create_sheet("Data")
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    raw = buf.getvalue()
    if dims:                                   # a stale stored size, as some exporters write
        zin = zipfile.ZipFile(io.BytesIO(raw))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for i in zin.infolist():
                data = zin.read(i)
                if i.filename.startswith("xl/worksheets/"):
                    data = data.replace(b'<dimension ref="A1:C2"/>', f'<dimension ref="{dims}"/>'.encode())
                z.writestr(i, data)
        raw = out.getvalue()
    return raw


def test_a_stale_sheet_size_does_not_drop_columns():
    lines, _ = importer.xlsx_lines(_book([["a", "b", "c"], [1, 2, 3]], dims="A1"))
    assert lines == ["a,b,c", "1,2,3"]


def test_hidden_sheets_are_skipped_and_line_breaks_flattened():
    lines, notes = importer.xlsx_lines(_book([["a", "b", "c"], ["x y", "p\rq", 3]], hidden_first=True))
    assert lines == ["a,b,c", "x y,p q,3"] and any("hidden" in n for n in notes)


def test_formulas_without_a_saved_value_are_reported():
    lines, notes = importer.xlsx_lines(_book([["a", "b", "c"], [1, 2, "=A2+B2"]]))
    assert lines[1] == "1,2," and any("formula" in n for n in notes)


def test_a_workbook_with_too_many_cells_is_cut_with_a_note():
    lines, notes = importer.xlsx_lines(_book([["a", "b", "c"]] + [[i, i, i] for i in range(100)]), max_cells=30)
    assert lines is not None and len(lines) < 20 and any("too large" in n for n in notes)


# ---- bundling
def test_a_broken_zip_among_loose_files_is_reported():
    _, notes = uploads.bundle([("q3.zip", b"PK\x03\x04junk"), ("approvals.csv", APPR.encode())])
    assert any("q3.zip" in n and "not a valid zip" in n for n in notes)


# ---- near-duplicates
def _pay(*rows):
    return [PAY] + [",".join(r) for r in rows]


def test_instalments_under_suffixed_numbers_are_not_near_duplicates():
    lines = _pay(["P-1", "2026-09-02", "2026-09-01", "V-1", "A", "INV-1001", "1000.00", "500.00"],
                 ["P-2", "2026-09-20", "2026-09-01", "V-1", "A", "INV-1001-2", "1000.00", "500.00"])
    assert not [h for h in rules.payments(lines) if h.clause == "5.6"]


def test_a_courier_series_on_one_day_is_not_a_near_duplicate():
    lines = _pay(["P-1", "2026-09-02", "2026-09-01", "V-1", "C", "SC-4101", "150.00", "150.00"],
                 ["P-2", "2026-09-02", "2026-09-01", "V-1", "C", "SC-4110", "150.00", "150.00"])
    assert not [h for h in rules.payments(lines) if h.clause == "5.6"]


def test_near_duplicates_stay_fast_with_thousands_of_equal_amounts():
    lines = _pay(*[[f"P-{i}", "2026-09-02", f"2026-09-{1 + i % 28:02d}", "V-1", "A", f"INV-{10000 + i}", "99.00", "99.00"]
                   for i in range(4000)])
    t = time.time()
    rules.payments(lines)
    assert time.time() - t < 10


# ---- pattern checks
EXP = "claim_id,date,employee,category,amount,receipt_ref,notes,people"


def test_different_people_with_one_surname_are_not_merged():
    rows = [f"E-{i},2026-09-0{i},{who},PARKING,24.00,,x," for i, who in enumerate(["K. Lowe", "Kate Lowe", "Kim Lowe"], 1)]
    assert not [h for h in rules.expenses([EXP] + rows) if h.clause == "6.6"]


def test_mileage_and_spread_out_claims_are_not_a_pattern():
    mileage = [f"E-{i},2026-09-0{i},Ada,MILEAGE,24.00,,x," for i in range(1, 5)]
    spread = [f"E-{i},2026-{m}-01,Ada,PARKING,24.00,,x," for i, m in enumerate(["06", "08", "10"], 1)]
    assert not [h for h in rules.expenses([EXP] + mileage) if h.clause == "6.6"]
    assert not [h for h in rules.expenses([EXP] + spread) if h.clause == "6.6"]


def test_director_approved_orders_are_not_steering():
    head = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no"
    rows = ["A-1,INVOICE,X-1,2026-09-02,Arden,9800.00,Ada,Bo,Finance Director,PO-1",
            "A-2,INVOICE,Y-1,2026-09-12,Bexley,9950.00,Ada,Bo,Finance Director,PO-2"]
    assert not [h for h in rules.approvals([head] + rows) if h.clause == "1.6"]


def test_a_vendor_list_import_date_is_not_a_set_up_date():
    head = "vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount"
    rows = [f"V-{i},Vendor {i},11-{i},ACTIVE,****1,,N/A,2026-09-01,YES,2026-09-15,{'15000.00' if i < 3 else '100.00'}"
            for i in range(10)]
    assert not [h for h in rules.vendors([head] + rows) if h.clause == "4.5"]


def test_pattern_findings_are_doubted_not_confirmed_by_the_rules_engine():
    import streamlit as st

    from tallyhound import common as C
    from tallyhound import custom
    st.session_state.clear()
    C.init_state()
    files, _ = challenge.generate(3, "hard")
    job = custom.Job("m", files, "rules", "", "", C.policy(), [])
    job.thread.join(60)
    pattern = [r for r in job.records if r["clause"] in rules.PATTERN_CLAUSES]
    assert pattern and all(r["verdict"] == "Doubtful" for r in pattern)
    assert all(r["verdict"] == "Confirmed" for r in job.records if r["clause"] not in rules.PATTERN_CLAUSES)
    st.session_state.clear()


# ---- the generator
def test_every_planted_unrecorded_payment_can_be_found():
    for seed in range(1, 41):
        files, key = challenge.generate(seed, "medium")
        k = score.key_from_csv(challenge.key_csv(key))
        hits = rules.analyze(files)
        sc = score.score([dict(source_file=h.source_file, line_number=h.line_number,
                               related_lines=[ln for _, ln in h.related]) for h in hits], k)
        missed = [x for x in k if x["expect"] == "problem" and x["clause"] == "5.5" and x["id"] not in sc["found_ids"]]
        assert not missed, (seed, missed)


# ---- second review round
def test_inch_marks_never_join_rows():
    text = PAY + '\nP-1,2026-09-01,2026-09-01,V-1,Ashby,5" pipe,10.00,10.00\nP-2,2026-09-02,2026-09-02,V-1,Ashby,elbows,5.00,5.00' \
                 '\nP-3,2026-09-03,2026-09-03,V-1,Ashby,3" valve,7.00,7.00\n'
    files, notes = _read(("payments.csv", text))
    assert len(files["payments.csv"]) == 4 and not any("joined" in n for n in notes)


def test_a_last_column_over_two_lines_is_joined():
    head = "claim_id,date,employee,category,amount,receipt_ref,notes"
    text = head + '\nE-1,2026-09-01,Ada,TAXI,10.00,RC-1,"to the airport\nand back"\nE-2,2026-09-02,Ada,TAXI,5.00,RC-2,x\n'
    files, _ = _read(("expenses.csv", text))
    assert files["expenses.csv"][1].endswith('"to the airport and back"') and len(files["expenses.csv"]) == 3


def test_an_unpaid_bills_report_is_not_a_bank_statement():
    text = "Date,Transaction Type,Num,Vendor,Due Date,Amount,Open Balance,Approved By\n" \
           "2026-09-01,Bill,B-1,Arden,2026-09-30,100.00,100.00,K. Lowe\n"
    files, _ = _read(("Unpaid Bills.csv", text))
    assert "bank_statement.csv" not in files


def test_near_duplicates_scale_to_large_files():
    lines = _pay(*[[f"P-{i}", "2026-09-02", f"2026-09-{1 + i % 28:02d}", "V-1", "A", f"INV-{100000 + i}", "99.00", "99.00"]
                   for i in range(20000)])
    t = time.time()
    rules.payments(lines)
    assert time.time() - t < 10           # about 1.5 s; generous for slow CI machines and coverage runs


def test_a_total_line_without_an_amount_is_not_a_heading():
    text = _qb("Ashby,,,,,,,", ",09/01/2026,P-1,09/01/2026,A-1,100.00,100.00,", "Total for Ashby,,,,,,,",
               ",09/03/2026,P-9,09/03/2026,Z-1,50.00,50.00,")
    lines, _ = importer.tidy(text.splitlines())
    assert lines[-1].startswith(",09/03/2026") and not any(ln.startswith("Total for Ashby") for ln in lines)


# ---- third round: leftovers from the second review
def test_a_title_line_with_dates_is_still_a_title():
    text = "\n".join(["Bill Payment List", "From,09/01/2026,To,09/30/2026", "",
                      ",Date,Num,Invoice Date,Invoice No,Original Amount,Amount Paid", "Ashby,,,,,,",
                      ",09/02/2026,1001,09/01/2026,A-1,10.00,10.00"])
    lines, notes = importer.tidy(text.splitlines())
    assert lines == ["Vendor,Date,Num,Invoice Date,Invoice No,Original Amount,Amount Paid",
                     "Ashby,09/02/2026,1001,09/01/2026,A-1,10.00,10.00"]


def test_headings_in_a_named_first_column_are_filled_in():
    text = "\n".join(["Supplier ledger", "", "Supplier,Date,Ref,Amount", "Ashby Components,,,",
                      ",2026-09-02,A-1,100.00", "Total Ashby Components,,,100.00"])
    lines, notes = importer.tidy(text.splitlines())
    assert lines == ["Supplier,Date,Ref,Amount", "Ashby Components,2026-09-02,A-1,100.00"]


def test_a_pandas_index_file_is_left_alone():
    lines = [",payment_id,amount,note", "0,P-1,10.00,x", "1,P-2,5.00,", "2,,,"]
    assert importer.tidy(lines) == (lines, [])


# ---- audit of the 0.7 features
def test_long_runs_of_spaces_never_make_reading_slow():
    t = time.time()
    uploads.read_uploads([("payments.csv", ("vendor,amount,1" + " " * 40000 + "x\n").encode())])
    importer.NUMERIC.match("1" + " " * 40000 + "x")
    importer.TOTAL.match("total" + " " * 40000 + "x")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", "<f " * 133333)
    uploads.read_uploads([("x.xlsx", buf.getvalue())])
    assert time.time() - t < 10


def test_upload_notes_cannot_carry_links_or_formatting(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    odd = tmp_path / "odd **bold** [mail me](mailto:someone@example.com).csv"
    odd.write_text("name,start,end\nAda,2026-01-01,2026-01-05\n", encoding="utf-8")
    from tests.test_upload_ui import _page
    at = AppTest.from_function(_page, args=([str(odd)],), default_timeout=60).run()
    shown = " ".join(w.value for w in at.warning)
    assert "\\*\\*bold\\*\\*" in shown and "\\[mail me\\]" in shown


def test_the_receipt_pattern_check_stays_fast():
    rows = [f"E-{i},{date_str(i * 16)},Ada,PARKING,23.75,,x," for i in range(8000)]
    t = time.time()
    rules.expenses([EXP] + rows)
    assert time.time() - t < 10


def date_str(days: int) -> str:
    from datetime import date, timedelta
    return (date(2000, 1, 1) + timedelta(days=days)).isoformat()


def test_a_big_workbook_is_read_up_to_the_cell_cap_not_refused():
    rows = [["a", "b", "c", "d"]] + [[i, i, i, i] for i in range(3000)]
    raw = _book(rows)
    lines, notes = importer.xlsx_lines(raw, max_unpacked_mb=0.2, max_cells=4000)   # the sheet alone is 0.45 MB
    assert lines is not None and 900 <= len(lines) <= 1001 and any("too large" in n for n in notes)


def test_patterns_chart_colours_meet_wcag():
    from tallyhound import common as C
    from tallyhound import pages_extra as P
    from tests.test_round5 import _contrast
    assert _contrast(P.BAR, C.PAPER) >= 3 and _contrast(C.INK, P.BAR) >= 3        # bars, and ticks over bars
    assert _contrast(P.AXIS["labelColor"], C.PAPER) >= 4.5


def test_a_batch_within_cents_of_one_payment_does_not_take_it():
    """Easy month 42: a BACS batch of $7,902.74 and one supplier's own payment of $7,902.77. Exact amounts are matched
    first everywhere, so the supplier's bank line keeps its payment and the batch is matched to the three it covers."""
    files, key = challenge.generate(42, "easy")
    k = score.key_from_csv(challenge.key_csv(key))
    hits = rules.analyze(files)
    sc = score.score([dict(source_file=h.source_file, line_number=h.line_number,
                           related_lines=[ln for _, ln in h.related]) for h in hits], k)
    assert sc["traps_flagged"] == 0 and sc["false_alarms"] == 0


# ---- end-to-end browser test findings
def test_a_newer_run_started_here_is_not_replaced_by_a_finished_saved_one(monkeypatch):
    import streamlit as st

    from tallyhound import sim, store
    st.session_state.clear()
    st.session_state["_tab"], st.session_state["sid"] = "mine", "ab" * 16
    st.session_state.sim = dict(queue=[], running=True, log=[], tab="mine", beat=time.time(), created=200.0)
    old = dict(queue=[], running=False, log=[], tab="other", beat=1.0, created=100.0)
    monkeypatch.setattr(store, "read_sim", lambda sid: old)
    assert sim._follow() is None and st.session_state.sim["created"] == 200.0      # this tab drives its own run
    st.session_state.sim = dict(queue=[], running=True, log=[], tab="mine", beat=time.time(), created=50.0)
    assert sim._follow() is not None and st.session_state.sim["tab"] == "other"     # an older copy follows the saved
    st.session_state.clear()


def test_the_trail_names_who_ran_it_with_the_run_time():
    import streamlit as st

    from tallyhound import common as C
    from tallyhound import custom
    st.session_state.clear()
    C.init_state()
    st.session_state.user = "prep"
    files, _ = challenge.generate(2, "easy")
    st.session_state.uploads = {"m": files}
    job = custom.Job("m", files, "rules", "", "", C.policy(), [], started_by="prep")
    job.thread.join(30)
    custom.finalize("m", job)
    trail = C.full_trail(C.findings())
    assert "Person (prep)" in set(trail.actor) and "14:2x" not in set(trail.time)
    st.session_state.clear()
