"""Scorecard, challenge generator, column matching, policy limits, owner/notes and the tamper demo."""
import io
from pathlib import Path

import openpyxl
from streamlit.testing.v1 import AppTest

from tallyhound import challenge, rules, score

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
SRC = ROOT / "data" / "source"


def sample_files():
    return {p.name: p.read_text(encoding="utf-8").splitlines() for p in SRC.iterdir()}


def proposed(hits):
    return [dict(source_file=h.source_file, line_number=h.line_number, related_lines=[ln for _, ln in h.related]) for h in hits]


# ---- challenge generator
def test_challenge_is_repeatable_and_rules_find_every_easy_and_medium_problem():
    assert challenge.generate(7, "medium") == challenge.generate(7, "medium")
    for diff in ("easy", "medium"):
        for seed in (1, 2, 3):
            files, key = challenge.generate(seed, diff)
            s = score.score(proposed(rules.analyze(files)), score.key_from_csv(challenge.key_csv(key)))
            assert s["recall"] == 1.0 and s["false_alarms"] == 0, (diff, seed, s)


def test_hard_challenge_hides_three_problems_from_the_fixed_rules():
    files, key = challenge.generate(3, "hard")
    k = score.key_from_csv(challenge.key_csv(key))
    s = score.score(proposed(rules.analyze(files)), k)
    assert s["planted"] - s["found"] == 3 and s["false_alarms"] == 0


def test_challenge_zip_round_trips_with_its_answer_key():
    from tallyhound import custom
    files, key = challenge.generate(2, "easy")
    got, notes = custom.parse_zip(challenge.to_zip(files, key))
    assert "answer_key.csv" in got and any("answer_key" in n for n in notes)
    assert {n: got[n] for n in files} == files


# ---- scoring
def test_score_edges():
    key = [dict(id="K-1", clause="5.2", area="Payments", source_file="payments.csv", lines=[5, 6], description="dup")]
    assert score.score([], key)["recall"] == 0
    hit = dict(source_file="payments.csv", line_number=6, related_lines=[])
    miss = dict(source_file="payments.csv", line_number=9, related_lines=[])
    s = score.score([hit, miss], key)
    assert s["recall"] == 1 and s["precision"] == 0.5 and s["false_alarms"] == 1


def test_sample_rules_score():
    from tallyhound import common as C
    key = score.key_from_findings(C.load_findings_checked()[0], C.read_source)
    s = score.score(proposed(rules.analyze(sample_files())), key)
    assert s["found"] >= 25 and s["false_alarms"] == 0


# ---- new checks and limits
def test_split_orders_and_late_po_on_sample():
    hits = rules.analyze(sample_files())
    assert any(h.clause == "1.4" and h.line_number == 9 for h in hits)
    assert any(h.clause == "1.1" and h.line_number == 28 and "17 days" in h.title for h in hits)


def test_limits_change_what_the_rules_flag():
    files = sample_files()
    base = [h for h in rules.analyze(files) if h.clause == "6.1"]
    strict = [h for h in rules.analyze(files, {"meal_limit": 20.0}) if h.clause == "6.1"]
    assert len(strict) > len(base)


def test_flexible_dates_and_amounts():
    assert str(rules._d("09/21/2026")) == "2026-09-21" and str(rules._d("21.09.2026")) == "2026-09-21"
    assert rules._f("$1,234.50") == 1234.5 and rules._f("(12.00)") == -12.0


# ---- column matching
def _mapping_app():
    import streamlit as st
    from tallyhound import common as C
    from tallyhound import custom, rules
    C.init_state()
    lines = ["Pay ID,Paid On,Inv Date,Supplier Name,Inv No,Inv Amt,Paid Amt",
             "X1,2026-09-01,2026-09-01,Acme,INV-1,100.00,100.00",
             "X2,2026-09-02,2026-09-01,Acme,INV-1,100.00,100.00"]
    custom.add_upload("theirs", {"payments.csv": lines})
    st.session_state.before = dict(custom.unmatched("theirs"))
    custom.mapping("theirs")["payments.csv"] = {
        "payment_id": "Pay ID", "pay_date": "Paid On", "invoice_date": "Inv Date", "supplier": "Supplier Name",
        "invoice_no": "Inv No", "invoice_amount": "Inv Amt", "paid_amount": "Paid Amt"}
    st.session_state.after = dict(custom.unmatched("theirs"))
    v = custom.view("theirs")
    st.session_state.hits = [(h.clause, h.line_number) for h in rules.run_area("Payments", v)]
    st.session_state.same_data = v["payments.csv"][1:] == lines[1:]


def test_column_matching_keeps_original_lines_as_evidence():
    at = AppTest.from_function(_mapping_app, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert "payments.csv" in at.session_state.before and at.session_state.after == {}
    assert ("5.2", 3) in at.session_state.hits and at.session_state.same_data


# ---- app pages
def test_scorecard_makes_and_adds_a_challenge():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value("Scorecard").run()
    assert not at.exception, [e.value for e in at.exception]
    at.button[[b.label for b in at.button].index("Make challenge")].click().run()
    at.button[[b.label for b in at.button].index("Add to the Monthly audit picker")].click().run()
    label = at.session_state.challenge["label"]
    assert label in at.session_state.uploads and at.session_state.answer_keys[label]
    at.selectbox(key="sc_pick").select(label).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Built-in rules (now)" in str(d.value) for d in at.dataframe)


def test_policy_page_saves_limits():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value("Policy").run()
    at.number_input(key="lim_meal_limit").set_value(50.0).run()
    at.button[[b.label for b in at.button].index("Save limits")].click().run()
    assert at.session_state.limits["meal_limit"] == 50.0
    assert not at.exception


def test_owner_and_note_are_saved_and_exported():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state.step = 3
    at.run()
    at.text_input(key="own_F-01").set_value("Treasury - K. Lowe").run()
    at.text_area(key="note_F-01").set_value("Call-back booked").run()
    at.button(key="savenote_F-01").click().run()
    assert at.session_state.notes["F-01"] == {"owner": "Treasury - K. Lowe", "note": "Call-back booked"}


def test_tamper_demo_blocks_an_edited_quote():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value("Guardrails").run()
    assert any("Verified" in s.value for s in at.success)
    key = next(t.key for t in at.text_area if t.key.startswith("tamper_text_"))
    at.text_area(key=key).set_value(at.text_area(key=key).value.replace("NO", "YES", 1) + " ").run()
    assert any("Blocked" in e.value for e in at.error)
