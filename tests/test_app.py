"""Smoke tests: every page renders, and the review and export flow works."""
import io

import openpyxl
import pytest
from streamlit.testing.v1 import AppTest

from tallyhound import common as C

APP = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app.py")


@pytest.mark.parametrize("page", C.NAV)
def test_every_page_renders(page):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value(page).run()
    assert not at.exception, [e.value for e in at.exception]


def test_review_decisions_and_download_step():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state.nav = "Review"
    at.run()
    at.button(key="appr_F-01").click().run()
    assert at.session_state.decisions["F-01"]["status"] == "Approved"
    at.session_state.nav = "Review"
    at.run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Approved"] == "1" and metrics["Pending"] == "25"


def test_reject_needs_a_reason():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state.nav = "Review"
    at.run()
    at.session_state["open_F-02"] = True                 # open the case's details
    at.run()
    at.button(key="rejbtn_F-02").click().run()                       # no reason yet: asks for one, decides nothing
    assert "F-02" not in at.session_state.decisions
    assert any("Write a reason first" in w.value for w in at.warning)
    at.text_input(key="rej_F-02").set_value("Not enough evidence").run()
    at.button(key="rejbtn_F-02").click().run()
    assert at.session_state.decisions["F-02"] == dict(status="Rejected", reason="Not enough evidence")


def _exports_app():
    import streamlit as st

    from tallyhound import common as C
    from tallyhound import exports
    C.init_state()
    C.decide("F-01", "Approved")
    C.decide("F-03", "Rejected", "one job")
    st.session_state.wb = exports.build_workbook(False)
    st.session_state.draft = exports.build_workbook(True)
    st.session_state.pdf = exports.build_memo()


def test_exports_build():
    at = AppTest.from_function(_exports_app, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    wb = openpyxl.load_workbook(io.BytesIO(at.session_state.wb))
    assert wb.sheetnames == ["Summary", "Monthly audit", "Payment gate", "Supplier recovery", "Subscriptions", "Audit trail"]
    summary = {r[0].value: r[1].value for r in wb["Summary"].iter_rows(min_row=2)}
    assert summary["Approved"] == 1 and summary["Rejected"] == 1 and summary["Pending"] == 24
    draft = openpyxl.load_workbook(io.BytesIO(at.session_state.draft))
    assert "DRAFT" in draft["Summary"]["B2"].value
    assert at.session_state.pdf.startswith(b"%PDF")


def test_decisions_survive_a_reload():
    at = AppTest.from_file(APP, default_timeout=60).run()
    sid = at.query_params["s"][0] if isinstance(at.query_params["s"], list) else at.query_params["s"]
    at.session_state.nav = "Review"
    at.run()
    at.button(key="appr_F-01").click().run()
    # a second browser session opening the same address gets the same work back
    at2 = AppTest.from_file(APP, default_timeout=60)
    at2.query_params["s"] = sid
    at2.run()
    assert at2.session_state.decisions["F-01"]["status"] == "Approved"
    assert any(r["finding"] == "F-01" for r in at2.session_state.audit_log)


def test_reset_clears_saved_work():
    at = AppTest.from_file(APP, default_timeout=60).run()
    sid = at.query_params["s"][0] if isinstance(at.query_params["s"], list) else at.query_params["s"]
    at.session_state.nav = "Review"
    at.run()
    at.button(key="appr_F-01").click().run()
    at.button(key="reset_demo").click().run()
    assert at.session_state.decisions == {}
    at3 = AppTest.from_file(APP, default_timeout=60)
    at3.query_params["s"] = sid
    at3.run()
    assert at3.session_state.decisions == {}


def test_guided_tour_walks_the_flow():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert "step 1 of 6" in " ".join(m.value for m in at.markdown)
    at.button(key="tour_start").click().run()
    assert at.session_state.sim is not None
    assert not at.exception
    # fast-forward: the run has failed once, been retried and finished; one approve and one reject; a download
    for it in at.session_state.sim["queue"]:
        it["status"] = "Done"
    at.session_state.sim["running"] = False
    at.session_state.fail_pending = False
    at.session_state.decisions = {"F-01": {"status": "Approved", "reason": ""},
                                  "F-02": {"status": "Rejected", "reason": "duplicate"}}
    at.session_state.tour_downloaded = True
    at.run()
    assert "Guided tour complete" in " ".join(m.value for m in at.markdown)
