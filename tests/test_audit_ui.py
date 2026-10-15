"""Bugs found in the v0.7.0 UI audit, each pinned by the smallest repro that failed before its fix."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _app():
    return AppTest.from_file(APP, default_timeout=60).run()


def test_shadow_mode_survives_leaving_the_rules_tab():
    at = _app()
    at.radio(key="nav").set_value("Settings").run()
    box = next(m for m in at.multiselect if m.label == "Clauses in shadow mode")
    box.set_value(["6.4"]).run()
    assert at.session_state["shadow"] == ["6.4"]
    at.radio(key="nav").set_value("Review").run()
    at.radio(key="nav").set_value("Home").run()           # a run without the Rules tab drawn
    assert not at.exception
    assert "shadow" in at.session_state and at.session_state["shadow"] == ["6.4"]
    at.radio(key="nav").set_value("Settings").run()
    box = next(m for m in at.multiselect if m.label == "Clauses in shadow mode")
    assert box.value == ["6.4"]
    assert any(e["action"] == "Shadow mode changed" for e in at.session_state["audit_log"])


def test_a_click_is_not_replayed_onto_a_rerun_with_new_findings(tmp_path, monkeypatch):
    """Two tabs: one approves F-01 while a teammate re-ran the checks on the same data. After the save conflict the
    click must not land on the new run's F-01, which is a different finding."""
    import streamlit as st

    from tallyhound import common as C
    from tallyhound import store
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    S = st.session_state
    S.clear()
    C.init_state()
    S.update(dataset="m", custom={"m": []}, run_history=[dict(label="m", run_id="r1", proposed=[])])
    C.decide("F-01", "Approved")                       # this tab's click, on run r1's F-01
    ops = S.pop("_pending_ops")
    # what the reload brings: the teammate's newer save, where the checks ran again (run r2, new findings)
    S.update(decisions={}, audit_log=[], run_history=S.run_history + [dict(label="m", run_id="r2", proposed=[])])
    C.log_action("Person", C.NEW_RUN, "-", "Ran the checks again")
    S["_replay"] = ops
    store._replay(S)
    assert "F-01" not in S.decisions
    assert "ran the checks again" in S["_restore_note"]
    S.clear()


def _gate_page():
    import streamlit as st

    from tallyhound import common as C
    from tallyhound import pages
    C.init_state()
    run = ["line,supplier,invoice,amount", "1,Ashby Components,ASH-1,1000.00", "2,Ashby Components,ASH-2,-400.00"]
    st.session_state.update(uploads={"m": {"payment_run.csv": run}}, custom={"m": []}, dataset="m")
    pages.overview()
    pages.payment_gate()


def test_the_payment_run_tab_shows_the_same_held_amount_as_reports():
    at = AppTest.from_function(_gate_page, default_timeout=60).run()
    assert not at.exception
    held = next(m.value for m in at.metric if m.label == "Held in payment run")
    head = next(m.value for m in at.markdown if " HOLD " in m.value)
    assert held == "$0.00" and "1 HOLD $0.00" in head, head        # a negative line never lowers a total


def test_changing_the_po_exemptions_is_in_the_audit_trail():
    at = _app()
    at.radio(key="nav").set_value("Settings").run()
    at.text_area(key="lim_exempt_vendors").input("Ashby Components").run()
    next(b for b in at.button if b.label == "Save limits").click().run()
    assert at.session_state["po_exempt_vendors"] == ["Ashby Components"]
    assert any(e["action"] == "Policy limits changed" and "Ashby Components" in e["detail"]
               for e in at.session_state["audit_log"])


def test_prepared_downloads_follow_the_show_suppressed_toggle():
    from tallyhound import common as C
    from tallyhound import learn
    r = next(C.load_findings_checked()[0].itertuples())
    head = C.read_source(r.source_file)[0] if r.source_file.endswith(".csv") else ""
    at = _app()
    at.session_state["suppressions"] = [dict(clause=str(r.clause), source_file=r.source_file, reason="test",
                                             entity=learn.entity(r.source_file, r.evidence, head))]
    at.radio(key="nav").set_value("Review").run()
    pending = next(m.value for m in at.metric if m.label == "Pending")
    at.button(key="dl_prepare").click().run()
    assert not [b for b in at.button if b.key == "dl_prepare"]
    at.toggle(key="show_suppressed").set_value(True).run()        # the set-aside finding is back in review
    assert next(m.value for m in at.metric if m.label == "Pending") == str(int(pending) + 1)
    # the files prepared before do not have it: they must be prepared again, not offered as they are
    assert [b for b in at.button if b.key == "dl_prepare"]


def test_shadow_mode_text_says_a_rule_must_be_promoted():
    at = _app()
    at.radio(key="nav").set_value("Settings").run()
    text = next(c.value for c in at.caption if c.value.startswith("A rule in shadow mode"))
    assert "until an admin promotes it" in text            # nothing enters the queue on its own: Promote does it
    readme = (Path(APP).parent / "README.md").read_text(encoding="utf-8")
    assert "an admin presses *Promote*" in readme
