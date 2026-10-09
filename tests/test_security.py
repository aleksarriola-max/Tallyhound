"""Security round (October 2026): roles checked on the server, the sign-in lockout under parallel tries, links and
images from uploaded text, formulas in the release file, Slack alerts and zips with very many entries. Each test
pins one fix."""
import io
import json
import re
import threading
import time
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tallyhound import auth, headless, uploads
from tallyhound import common as C

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
SRC = ROOT / "data" / "source"
EVIL = "![t](https://evil.example/t.png) [pay here](https://evil.example/a) www.evil.example"


@pytest.fixture()
def team(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TALLYHOUND_USERS", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "ITER", 1000)
    auth._fails.clear()
    auth.add_user("pete", "preparer", "pw-pete-1")
    auth.add_user("ada", "admin", "pw-ada-1")
    return tmp_path


# ---- roles are checked where the change is made, not only by disabling the button
def _preparer_forces_everything():
    import streamlit as st

    from tallyhound import common as C
    from tallyhound import layout, learn, pages_extra, review, store
    S = st.session_state
    S.user, S.role = "pete", "preparer"
    C.init_state()
    fid = C.findings().id.iloc[0]
    review._decide_all([fid], "Approved")
    review._reject(fid, "not a problem", "5.1", "payments.csv", "Ashby")
    S.suppressions = [dict(clause="5.1", source_file="payments.csv", entity="Ashby", reason="r")]
    learn.unsuppress(0)
    pages_extra._set_override("5.1", "demoted")
    pages_extra._apply_hint("po_limit", 1.0)
    store.reset()
    S.result = dict(decisions=dict(S.decisions), suppressions=len(S.suppressions), override=dict(S.get("rule_override", {})),
                    limits=dict(S.get("limits", {})))
    S.role = "reviewer"                    # a reviewer cannot start a run
    layout._start(layout.SAMPLE)
    S.result["sim"] = S.get("sim")


def test_a_preparer_cannot_decide_or_change_policy_even_by_calling_the_actions(team):
    """Streamlit before 1.61 runs a click on a disabled button if a modified browser sends one; the actions check
    the role themselves."""
    at = AppTest.from_function(_preparer_forces_everything, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    r = at.session_state.result
    assert r == dict(decisions={}, suppressions=1, override={}, limits={}, sim=None)


def test_a_forged_click_on_a_disabled_approve_button_is_refused(team):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input[0].input("pete")
    at.text_input[1].input("pw-pete-1")
    at.button[0].click().run()
    at.session_state.nav = "Review"
    at.run()
    b = at.button(key="appr_F-01")
    assert b.disabled
    try:
        b.click().run()                    # Streamlit 1.55 sends it, as a modified browser could
    except Exception:                      # noqa: BLE001  newer Streamlit refuses it in the test client itself
        pass
    assert dict(at.session_state.decisions) == {}


def test_a_non_admin_cannot_put_rules_into_shadow_mode(team):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input[0].input("pete")
    at.text_input[1].input("pw-pete-1")
    at.button[0].click().run()
    at.session_state.nav = "Settings"
    at.run()
    box = next(m for m in at.multiselect if m.label == "Clauses in shadow mode")
    assert box.disabled and box.key != "shadow"
    try:
        box.set_value(["5.1"]).run()
    except Exception:                      # noqa: BLE001  newer Streamlit refuses it in the test client itself
        pass
    assert "5.1" not in (at.session_state["shadow"] if "shadow" in at.session_state else [])


# ---- sign-in lockout
def test_tries_sent_at_once_cannot_get_past_the_lockout(team, monkeypatch):
    tried = []
    real = auth._check

    def slow(n, p):                        # a real hash takes a moment, so tries from many sessions overlap
        tried.append(p)
        time.sleep(0.05)
        return real(n, p)
    monkeypatch.setattr(auth, "_check", slow)
    ts = [threading.Thread(target=auth.check, args=("ada", f"guess {i}")) for i in range(40)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(tried) == auth.MAX_FAILS and auth.locked_for("ada") > 0
    auth._fails.clear()
    assert auth.check("ada", "pw-ada-1") == "admin" and "ada" not in auth._fails


# ---- links and images from uploaded or model text
def test_escaped_text_leaves_no_address_the_page_would_link():
    shown = re.sub(r"\\(.)", r"\1", C.esc(EVIL + " http://evil.example/c"))     # what is left once escapes are read
    assert "://" not in shown and "www." not in shown


def test_the_dont_flag_again_label_shows_the_vendor_name_as_text(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))

    def app(src, evil):
        from pathlib import Path

        import streamlit as st

        from tallyhound import common as C
        from tallyhound import layout
        S = st.session_state
        C.init_state()
        lines = Path(src, "payments.csv").read_text(encoding="utf-8").splitlines()
        cells = lines[1].split(",")
        cells[4] = evil                    # the supplier on line 2
        lines[1] = ",".join(cells)
        S.uploads = {"m": {"payments.csv": lines}}
        S.custom = {"m": [dict(id="F-01", area="Payments", clause="5.1", severity="High", amount=1.0, title="t",
                               source_file="payments.csv", line_number=2, related_lines=[], verdict="Confirmed",
                               reason="r", innocent="", fix="")]}
        S.dataset, S["open_F-01"] = "m", True
        layout.review_page()
    at = AppTest.from_function(app, args=(str(SRC), EVIL), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    label = at.checkbox(key="supp_F-01").label
    assert "evil" in label and "![t](" not in label and "[pay here](" not in label and "https://" not in label


# ---- spreadsheet formulas in the release file
def test_release_file_keeps_formulas_as_text_and_numbers_as_numbers():
    from tallyhound import pages
    rel = pd.DataFrame(dict(line=["1", "2", "3", "4", "5"],
                            supplier=['=HYPERLINK("http://x","click")', "+cmd|' /C calc'!A0", "@SUM(1)", "-2+3", "Arden"],
                            invoice=["INV-1", "\t=1+1", "-500.00", "-5", "A-2"], amount=[1.0, -2.5, 3.0, 4.0, 5.0]))
    rows = list(pd.read_csv(io.StringIO(pages.release_csv(rel)), dtype=str).itertuples(index=False))
    assert [r.supplier for r in rows] == ["'=HYPERLINK(\"http://x\",\"click\")", "'+cmd|' /C calc'!A0", "'@SUM(1)", "'-2+3",
                                          "Arden"]
    assert [r.invoice for r in rows] == ["INV-1", "'\t=1+1", "-500.00", "-5", "A-2"]   # plain numbers stay numbers
    assert [r.amount for r in rows] == ["1.0", "-2.5", "3.0", "4.0", "5.0"]


# ---- alerts
def test_slack_alert_cannot_ping_everyone_or_hide_a_link(monkeypatch):
    sent = []

    class Reply:
        def read(self):
            return b"ok"
    monkeypatch.setenv("TALLYHOUND_SLACK_WEBHOOK", "http://127.0.0.1:1/hook")
    monkeypatch.setattr(headless.urllib.request, "urlopen", lambda req, timeout: sent.append(req.data) or Reply())
    assert headless.send_alert("Tallyhound: 1 new\n- [High] Payment to <!channel> <http://evil.example|Pay now>") == ["Slack"]
    text = json.loads(sent[0])["text"]
    assert "<" not in text and ">" not in text and "&lt;!channel&gt;" in text


# ---- zips
def test_a_zip_with_very_many_entries_is_cut_short():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("payments.csv", (SRC / "payments.csv").read_text(encoding="utf-8"))
        for i in range(3 * uploads.MAX_ENTRIES):
            z.writestr(f"x{i}", "")
    files, notes = uploads.parse_zip(buf.getvalue())
    assert "payments.csv" in files                                  # named audit files are read first
    assert len(notes) <= uploads.MAX_ENTRIES + 5 and any(n.startswith(f"Stopped at {uploads.MAX_ENTRIES} files") for n in notes)
    many, more = uploads.read_uploads([("a.zip", buf.getvalue()), ("vendors.csv", b"vendor_id,name\n")])
    assert any("Stopped at" in n for n in more)
