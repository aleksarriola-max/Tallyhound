"""Fifth audit (October 2026): two people in one team workspace, big uploads in the interface, numbers that agree
everywhere, accessible colours and Windows-safe files. Each test pins one fix."""
import json
import random
import time
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tallyhound import auth, challenge, custom, gate, store
from tallyhound import common as C

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")


@pytest.fixture()
def team(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TALLYHOUND_USERS", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "ITER", 1000)
    auth._fails.clear()
    for name, role in (("rita", "reviewer"), ("ravi", "reviewer"), ("ada", "admin")):
        auth.add_user(name, role, f"pw-{name}-1")
    return tmp_path


def _signed_in(name: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input[0].input(name)
    at.text_input[1].input(f"pw-{name}-1")
    at.button[0].click()
    at.run()
    at.session_state.nav = "Review"
    at.run()
    return at


def _saved() -> dict:
    return json.loads(store._read(store.TEAM) or "{}")


# ---- two people, one workspace
def test_two_reviewers_clicking_in_turn_lose_nothing(team):
    a, b = _signed_in("rita"), _signed_in("ravi")
    a.button(key="appr_F-11").click().run()
    b.button(key="appr_F-16").click().run()              # b's copy is older: refused, reloaded, re-applied
    b.run()
    saved = _saved()
    assert {"F-11", "F-16"} <= set(saved["decisions"])
    assert C.decisions_mismatch(saved["audit_log"], saved["decisions"]) == []


def test_a_reset_is_not_undone_by_another_open_tab(team):
    a, admin = _signed_in("rita"), _signed_in("ada")
    a.button(key="appr_F-11").click().run()
    admin.run()
    admin.button(key="reset_demo").click().run()
    assert store._read(store.TEAM) is None
    a.button(key="appr_F-16").click().run()              # rita's tab still holds the old work
    a.run()
    saved = _saved()
    assert "F-11" not in saved.get("decisions", {})        # the reset stands; only her new click is there


def test_saves_refuse_a_changed_or_deleted_row(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    v = store._write("ab" * 16, "{}")
    assert store._write("ab" * 16, '{"x": 1}') is None    # "I saw no row" but one exists: refused
    store._delete("ab" * 16)
    assert store._write("ab" * 16, '{"x": 2}', expect=v) is None   # deleted since loaded (a reset): refused


def test_two_uploads_at_once_keep_both(tmp_path, monkeypatch):
    import streamlit as st
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    st.session_state.clear()
    st.session_state.sid = "cd" * 16
    st.session_state.uploads = {"Oct": {"payments.csv": ["a"]}}
    store.save_uploads()
    st.session_state.uploads = {"Nov": {"payments.csv": ["b"]}}   # another tab that never saw Oct
    store.save_uploads()
    assert set(json.loads(store._read("cd" * 16 + ":uploads"))) == {"Oct", "Nov"}
    store.save_uploads(removed=("Oct",))
    assert set(json.loads(store._read("cd" * 16 + ":uploads"))) == {"Nov"}
    st.session_state.clear()


def test_the_run_remembers_who_started_it():
    import streamlit as st
    st.session_state.clear()
    C.init_state()
    files, _ = challenge.generate(2, "easy")
    st.session_state.uploads = {"m": files}
    job = custom.Job("m", files, "rules", "", "", C.policy(), [], started_by="alice")
    job.thread.join(30)
    st.session_state.user = "adam"                         # a different person's tab finishes it
    custom.finalize("m", job)
    assert st.session_state.run_history[-1]["user"] == "alice"
    st.session_state.clear()


def test_two_pdfs_with_the_same_name_are_both_read():
    import io
    import zipfile
    f, k, p = challenge.generate_full(2, "medium")
    pdfs = [n for n in zipfile.ZipFile(io.BytesIO(challenge.to_zip(f, k, p))).namelist() if n.endswith(".pdf")]
    src = zipfile.ZipFile(io.BytesIO(challenge.to_zip(f, k, p)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("north/invoice.pdf", src.read(pdfs[0]))
        z.writestr("south/invoice.pdf", src.read(pdfs[1]))
    files, _ = custom.parse_zip(buf.getvalue())
    heads = [ln for ln in files["invoices.txt"] if ln.startswith("[PDF")]
    assert heads == ["[PDF invoice (2).pdf]", "[PDF invoice.pdf]"] or len(heads) == 2


def test_restore_rejects_findings_with_odd_types():
    bad = {"custom": {"m": [dict(id="F-01", source_file="a.csv", line_number="2", area="Payments", clause="5.1",
                                 severity="High", title="x", amount=1.0)]}}
    assert "custom" in store.clean(bad)[1]


# ---- numbers that agree
def _frame(rows):
    return pd.DataFrame(rows, columns=["id", "clause", "amount", "source_file", "line_number", "matched_lines",
                                       "severity", "skeptic_verdict"])


def test_a_case_is_worth_its_largest_finding_not_the_sum():
    import streamlit as st
    st.session_state.clear()
    st.session_state.decisions = {"F-1": {"status": "Approved"}, "F-2": {"status": "Approved"}}
    f = _frame([["F-1", "1.4", 9000.0, "approvals.csv", 5, [3, 4, 5], "Medium", "Confirmed"],
                ["F-2", "1.1", 4500.0, "approvals.csv", 4, [4], "Medium", "Confirmed"]])
    assert C.decision_counts(f)["value"] == 9000.0                 # the split order already includes its part
    st.session_state.clear()


def test_shadow_findings_are_not_pending():
    import streamlit as st
    st.session_state.clear()
    st.session_state.decisions, st.session_state.shadow = {}, ["6.4"]
    f = _frame([["F-1", "6.4", 10.0, "expenses.csv", 3, [3], "Low", "Confirmed"],
                ["F-2", "5.2", 10.0, "payments.csv", 4, [4], "High", "Confirmed"]])
    dc = C.decision_counts(f)
    assert (dc["pending"], dc["shadow"]) == (1, 1)
    st.session_state.clear()


def test_money_and_held_totals():
    assert C.money(-1234.5) == "-$1,234.50" and C.money(float("nan")) == "-" and C.money(12) == "$12.00"
    df = pd.DataFrame(dict(line=["1", "2"], decision=["HOLD", "HOLD"], amount=[100.0, -40.0], supplier=["a", "b"]))
    assert C.gate_totals(df)["hold_amt"] == 100.0                  # a negative line never lowers the held total


def test_payment_run_line_without_a_usable_amount_is_held():
    run = ["line,supplier,invoice,amount", "1,Arden,INV-1,nan", "2,Arden,INV-2,-50.00"]
    g = gate.evaluate({"payment_run.csv": run})
    assert list(g.decision) == ["HOLD", "HOLD"] and all("missing, zero or negative" in r for r in g.reason)


# ---- scale
def test_a_big_upload_keeps_pages_fast(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    files = challenge.generate_full(3, "medium")[0]
    rnd = random.Random(1)
    P = files["payments.csv"]
    h = P[0].split(",")
    rows = [P[0]]
    for i in range(4000):
        c = P[1 + i % (len(P) - 1)].split(",")
        c[h.index("payment_id")], c[h.index("invoice_no")] = f"PA{i}", f"IN{i}"
        c[h.index("paid_amount")] = c[h.index("invoice_amount")] = f"{rnd.uniform(50, 9000):.2f}"
        rows.append(",".join(c))
    files["payments.csv"] = rows
    at = AppTest.from_file(APP, default_timeout=300).run()
    at.session_state.uploads = {"big": files}
    at.session_state.extra_opts = {"audit": {"big": "x"}}
    at.run()
    at.button(key="open_run").click().run()
    at.button(key="run_go").click().run()
    for _ in range(120):
        if "dataset" in at.session_state and at.session_state["dataset"] == "big":
            break
        time.sleep(0.5)
        at.run()
    for page in C.NAV:
        at.session_state.nav = page
        t = time.time()
        at.run()
        assert not at.exception and time.time() - t < 15, page


# ---- accessibility and files
def _contrast(a: str, b: str) -> float:
    def lum(h):
        c = [int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_colours_meet_wcag_aa():
    for sev, col in C.SEV_COLOR.items():
        assert _contrast("#ffffff", col) >= 4.5, sev                # white text on the severity badges
    for col in (C.RELEASE, C.HOLD, C.TEAL_DARK, C.MUTED, C.INK):
        assert _contrast(col, C.PAPER) >= 4.5, col                 # coloured text on the page
    for bg in ("#f6dede", "#e0f0e7"):                              # payment-run table rows
        assert _contrast(C.RELEASE, bg) >= 4.5 and _contrast(C.HOLD, bg) >= 4.5


def test_sample_data_line_endings_are_pinned():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "data/** -text" in attrs
