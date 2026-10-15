"""Second audit, I/O and UI suspicions: each test is the smallest repro that failed before its fix."""
import json
import subprocess
import sys
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from tallyhound import common as C
from tallyhound import headless, pages, review, store

ROOT = Path(__file__).resolve().parent.parent


def test_two_first_pdf_uploads_at_once_share_one_reading_slot():
    # a fresh server; making the semaphore is slowed down so the two uploads meet in the gap
    code = """
import threading, time
from tallyhound import invoices
real = threading.BoundedSemaphore
def slow(n):
    time.sleep(0.2)
    return real(n)
invoices.threading.BoundedSemaphore = slow
now, most, lock = [0], [0], threading.Lock()
def fake(items):
    with lock:
        now[0] += 1
        most[0] = max(most[0], now[0])
    time.sleep(0.3)
    with lock:
        now[0] -= 1
    return {}
invoices._read_pdfs = fake
ts = [threading.Thread(target=invoices.read_pdfs, args=([],)) for _ in range(2)]
[t.start() for t in ts]
[t.join() for t in ts]
print(most[0])
"""
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "1"


def test_watch_alert_without_a_channel_fails_and_keeps_the_findings_new(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    for p in (ROOT / "data" / "source").iterdir():
        (folder / p.name).write_bytes(p.read_bytes())
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith(("TALLYHOUND_SLACK", "TALLYHOUND_SMTP",
                                                                                     "TALLYHOUND_MAIL"))}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "watch.py"), str(folder), "--alert"], capture_output=True,
                       text=True, env=env)
    assert r.returncode == 1 and "No alert channel" in r.stdout
    assert not (folder / headless.REPORT_DIR / "last.json").exists()     # so a channel set up later alerts on them


def test_anonymiser_keeps_brackets_in_the_reviewers_reason(tmp_path):
    case = {"description": "Rejected by a reviewer: paid by cheque (see note 4) (Duplicate payment)",
            "files": {}, "must_not_flag": []}
    src = tmp_path / "case.json"
    src.write_text(json.dumps(case), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "anonymise.py"), str(src), "--out", str(tmp_path / "o")],
                   check=True, capture_output=True)
    desc = json.loads((tmp_path / "o" / "case.json").read_text(encoding="utf-8"))["description"]
    assert "see note 4" in desc and "Duplicate payment" not in desc


# ---- a forged click on the disabled Clear hold button
def _forged_clear_on_a_view_only_run():
    import streamlit as st

    from tallyhound import common as C
    from tallyhound import pages
    S = st.session_state
    C.init_state()
    S["gate_run"] = "Payment run 2026-10-08"            # the sample's second run: view only
    g = C.gate("payment_run_2026-10-08.csv")
    pos = list(g.decision).index("HOLD")
    from streamlit.delta_generator import DeltaGenerator
    real_df, real_btn = st.dataframe, DeltaGenerator.button

    class Sel:
        rows = [pos]

    class Ev:
        selection = Sel()
    st.dataframe = lambda *a, **k: Ev()
    # Streamlit before 1.61 runs a click a modified browser sends for a disabled button
    DeltaGenerator.button = lambda self, label, *a, **k: ((label == "Clear hold" and not S.get("_forged"))
                                                          or real_btn(self, label, *a, **k))
    S["clear_reason"] = "forged"
    try:
        pages.payment_gate()
    finally:
        S["_forged"] = True
        st.dataframe, DeltaGenerator.button = real_df, real_btn
    S["result"] = dict(S.cleared)


def test_a_forged_clear_hold_on_a_view_only_run_is_refused():
    at = AppTest.from_function(_forged_clear_on_a_view_only_run, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["result"] == {}         # before: line N of the view-only run cleared line N of 2026-10-01


def _overview_with_shadow():
    import streamlit as st

    from tallyhound import common as C
    from tallyhound import pages
    C.init_state()
    f = C.findings()
    clause = str(f.clause.iloc[0])
    st.session_state["shadow"] = [clause]
    st.session_state["want"] = int((f.clause.astype(str) != clause).sum())
    pages.overview()


def test_reports_tiles_leave_out_shadow_mode_findings_as_review_does():
    at = AppTest.from_function(_overview_with_shadow, default_timeout=60).run()
    assert not at.exception
    tiles = [int(m.value.split(">")[1].split("<")[0]) for m in at.markdown if "font-size:1.8rem" in m.value]
    assert sum(tiles) == at.session_state["want"]


def test_a_replayed_rejection_keeps_its_dont_flag_again(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    S = st.session_state
    S.clear()
    C.init_state()
    S["supp_F-01"] = True
    review._reject("F-01", "known contract", "5.1", "payments.csv", "Ashby Components")
    ops = S.pop("_pending_ops")
    S.update(decisions={}, suppressions=[])               # what the reload brings: the teammate's newer save
    S["_replay"] = ops
    store._replay(S)
    assert S.decisions["F-01"]["status"] == "Rejected"
    assert [x["entity"] for x in S.suppressions] == ["Ashby Components"]
    S.clear()


def test_run_finished_box_stays_dismissed_after_a_reload():
    S = st.session_state
    S.clear()
    S["_run_seen"] = "run-7"
    saved = json.loads(json.dumps(store.snapshot()))      # what is written, and what a reload reads back
    good, bad = store.clean(saved)
    assert bad == [] and good.get("_run_seen") == "run-7"
    S.clear()


def test_the_release_file_of_an_uploaded_run_has_a_plain_name():
    assert pages.release_name("Payment run 2026-10-01") == "release_2026-10-01.csv"
    assert pages.release_name("Uploaded run (sept export)") == "release_sept_export.csv"
    assert pages.release_name("Uploaded run (sept (2))") == "release_sept_2.csv"


def test_a_click_after_a_teammates_reset_is_not_dropped_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("TALLYHOUND_USERS", raising=False)
    monkeypatch.setattr(store, "session_id", lambda: "ab" * 16)
    S = st.session_state
    S.clear()
    store.load_into_session()
    C.init_state()
    C.decide("F-01", "Approved")
    store.save_if_changed()
    store._delete("ab" * 16)                              # a teammate presses Reset
    C.decide("F-02", "Approved")
    try:
        store.save_if_changed()                           # refused: the row is gone; reloads
    except Exception:                                     # noqa: BLE001  st.rerun outside a script run
        pass
    store.load_into_session()
    assert "reset" in S.get("_restore_note", "")
    S.clear()
