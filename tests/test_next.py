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


def test_hard_challenge_hides_problems_from_the_fixed_rules():
    misses = 0
    for seed in range(1, 6):
        files, key = challenge.generate(seed, "hard")
        s = score.score(proposed(rules.analyze(files)), score.key_from_csv(challenge.key_csv(key)))
        misses += s["planted"] - s["found"]
        assert s["false_alarms"] == 0
    assert misses >= 1          # hard mode keeps at least some problems out of reach of fixed rules


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
    at.radio(key="nav").set_value("Trust").run()
    assert not at.exception, [e.value for e in at.exception]
    at.button[[b.label for b in at.button].index("Make challenge")].click().run()
    at.button[[b.label for b in at.button].index("Add to my data")].click().run()
    label = at.session_state.challenge["label"]
    assert label in at.session_state.uploads and at.session_state.answer_keys[label]
    at.selectbox(key="sc_pick").select(label).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Built-in rules (now)" in str(d.value) for d in at.dataframe)


def test_policy_page_saves_limits():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value("Settings").run()
    at.number_input(key="lim_meal_limit").set_value(50.0).run()
    at.button[[b.label for b in at.button].index("Save limits")].click().run()
    assert at.session_state.limits["meal_limit"] == 50.0
    assert not at.exception


def test_owner_and_note_are_saved_and_exported():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state.nav = "Review"
    at.run()
    at.session_state["open_F-01"] = True
    at.run()
    at.text_input(key="own_F-01").set_value("Treasury - K. Lowe").run()
    at.text_input(key="note_F-01").set_value("Call-back booked").run()
    at.button(key="savenote_F-01").click().run()
    assert at.session_state.notes["F-01"] == {"owner": "Treasury - K. Lowe", "note": "Call-back booked"}


def test_tamper_demo_blocks_an_edited_quote():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value("Trust").run()
    assert any("Verified" in s.value for s in at.success)
    key = next(t.key for t in at.text_area if t.key.startswith("tamper_text_"))
    at.text_area(key=key).set_value(at.text_area(key=key).value.replace("NO", "YES", 1) + " ").run()
    assert any("Blocked" in e.value for e in at.error)


# ---- payment gate on uploads
def test_gate_on_a_challenge_run_holds_the_four_bad_lines():
    from tallyhound import gate
    files, _ = challenge.generate(5, "medium")
    df = gate.evaluate(files)
    assert len(df) == 9                                     # line 9: a supplier-name variant that must NOT be held
    assert sorted(df[df.decision == "HOLD"].line.astype(int)) == [2, 3, 7, 8]
    assert set(df.loc[df.line == "7", "failed_checks"].iloc[0].split(",")) == {"5"}


def test_gate_says_which_checks_could_not_run():
    from tallyhound import gate
    files, _ = challenge.generate(5, "easy")
    df = gate.evaluate({"payment_run.csv": files["payment_run.csv"]})
    assert (df.decision == "RELEASE").sum() == 8 and "not checked" in df.reason.iloc[0]


# ---- Skeptic contradiction guard
def test_skeptic_asks_again_when_its_verdict_contradicts_its_reason(monkeypatch):
    from tallyhound import agents, llm
    answers = iter([{"reason": "People=1, so $156 per person clearly breaches the $75 limit.", "verdict": "Doubtful"},
                    {"reason": "People=1, so $156 per person clearly breaches the $75 limit.", "verdict": "Confirmed"}])
    calls = []
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: calls.append(a) or next(answers))
    f = dict(clause="6.1", title="Meal", evidence="E-1,2026-09-10,A,MEAL,156.00,RC-1,Client dinner,1", innocent="guests")
    assert agents.skeptic(f, "Meals are limited to $75.00 per person.", "m", "u")[0] == "Confirmed"
    assert len(calls) == 2


def test_skeptic_flags_a_verdict_that_stays_contradictory(monkeypatch):
    from tallyhound import agents, llm
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"reason": "This clearly breaches clause 6.1.", "verdict": "Doubtful"})
    v, why = agents.skeptic(dict(clause="6.1", title="t", evidence="x", innocent="y"), "c", "m", "u")
    assert v == "Doubtful" and why.startswith("Unclear")


def test_contradiction_detector():
    from tallyhound import agents
    assert agents.contradicts("Doubtful", "It clearly breaches the limit.")
    assert not agents.contradicts("Doubtful", "It does not clearly breach the clause.")
    assert agents.contradicts("Confirmed", "The line does not breach the clause.")
    assert not agents.contradicts("Confirmed", "The bank_verified flag is NO, so this breaches 4.3.")


# ---- tool-using agents (scripted pretend model)
def test_tool_agent_finds_a_duplicate_and_its_made_up_quote_is_dropped(monkeypatch):
    from tallyhound import agents, agents_tools, llm
    lines = sample_files()["payments.csv"]
    state = {"step": 0}

    def fake(messages, tools, **k):
        state["step"] += 1
        s = state["step"]
        call = lambda name, **args: {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}
        if s == 1:
            return call("describe")
        if s == 2:
            return call("duplicates", columns=["supplier", "invoice_no"])
        if s == 3:
            first = messages[-1]["content"].splitlines()[1]          # second line of the first duplicate group
            n, text = first.split("\t", 1)
            return {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "report_finding", "arguments": dict(clause="5.2", severity="High", title="Paid twice",
                                                                         amount=6150, line_number=int(n), evidence=text)}},
                {"function": {"name": "report_finding", "arguments": dict(clause="5.1", severity="Low", title="Invented",
                                                                         line_number=3, evidence="not a real line")}}]}
        return call("done")

    monkeypatch.setattr(llm, "chat_tools", fake)
    raw = agents_tools.investigate("Payments", lines, {"5.2|Payments": "paid once"}, "m", "u")
    kept = [v for f in raw if (v := agents.verified(f, lines, "Payments"))]
    assert [k["title"] for k in kept] == ["Paid twice"] and state["step"] == 4


def test_file_tools_answer_queries():
    from tallyhound import agents_tools
    t = agents_tools.FileTools("expenses.csv", sample_files()["expenses.csv"])
    assert "E-1042" in t.call("find_rows", {"column": "amount", "op": "gt", "value": "150"})
    assert "TX-88213" in t.call("duplicates", {"columns": ["receipt_ref"]})
    assert "No column" in t.call("find_rows", {"column": "nope", "op": "eq", "value": "x"})
    c = agents_tools.FileTools("contracts.txt", sample_files()["contracts.txt"])
    assert "surcharge" in c.call("find_text", {"text": "surcharge"}).lower()
