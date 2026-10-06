"""Open-source release audit (October 2026): denial of service through uploads, session fixation, a fail-closed
users file, private state files, escaping of dataset names, job clean-up, honest exports and the review flow.
Each test pins one fix."""
import importlib
import io
import json
import os
import re
import signal
import stat
import sys
import time
import zipfile
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tallyhound import auth, challenge, custom, headless, store
from tallyhound import common as C

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")


def _pdf(fan: int, depth: int, lie: bool = False) -> bytes:
    """A tiny PDF whose page tree fans out to fan**depth pages (optionally claiming /Count 1 everywhere)."""
    objs = {1: "<< /Type /Catalog /Pages 2 0 R >>"}
    leaf = 2 + depth
    for d in range(depth):
        nid, kid = 2 + d, (2 + d + 1 if d < depth - 1 else leaf)
        objs[nid] = f"<< /Type /Pages /Kids [{' '.join([f'{kid} 0 R'] * fan)}] /Count {1 if lie else fan ** (depth - d)} >>"
    objs[leaf] = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] >>"
    out, offs = io.BytesIO(), {}
    out.write(b"%PDF-1.4\n")
    for k in sorted(objs):
        offs[k] = out.tell()
        out.write(f"{k} 0 obj\n{objs[k]}\nendobj\n".encode())
    x, n = out.tell(), max(objs) + 1
    out.write(f"xref\n0 {n}\n0000000000 65535 f \n".encode())
    for k in range(1, n):
        out.write(f"{offs[k]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode())
    return out.getvalue()


# ---- denial of service
def test_pdf_bombs_are_refused_quickly_and_real_invoices_still_read():
    f, k, p = challenge.generate_full(2, "medium")
    src = zipfile.ZipFile(io.BytesIO(challenge.to_zip(f, k, p)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in src.namelist():
            z.writestr(n, src.read(n))
        z.writestr("invoices/bomb.pdf", _pdf(20, 5))
        z.writestr("invoices/liar.pdf", _pdf(20, 5, lie=True))
    t = time.time()
    files, notes = custom.parse_zip(buf.getvalue())
    assert time.time() - t < 60
    assert sum(ln.startswith("[PDF") for ln in files["invoices.txt"]) == sum(n.endswith(".pdf") for n in src.namelist())
    for bomb in ("bomb.pdf", "liar.pdf"):            # refused, timed out, or (newer pypdf) read quickly as empty
        assert any(bomb in n and ("could not be read safely" in n or "no readable text" in n) for n in notes), bomb


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="needs a POSIX timer")
def test_no_pattern_backtracks_badly_on_hostile_input():
    """Every compiled pattern in the code meets long runs of the characters that cause catastrophic backtracking."""
    sys.path.insert(0, str(ROOT / "scripts"))

    class Slow(Exception):
        pass

    def alarm(*_):
        raise Slow

    old = signal.signal(signal.SIGALRM, alarm)
    n = 20_000
    hostile = ["1" * n, " " * n + "x", "1 " * (n // 2) + "x", "Aa " * (n // 3), "1." * (n // 2), "1," * (n // 2) + "x",
               "Total" + " " * n + "x", "account " + "1 " * (n // 2) + "x", "a@" * (n // 2), "AB12" * (n // 4), "(" * n]
    slow = []
    try:
        for mod in ["tallyhound.rules", "tallyhound.invoices", "tallyhound.custom", "tallyhound.columns",
                    "tallyhound.agents", "tallyhound.agents_tools", "tallyhound.headless", "anonymise"]:
            for name, rx in vars(importlib.import_module(mod)).items():
                if isinstance(rx, re.Pattern):
                    for s in hostile:
                        signal.setitimer(signal.ITIMER_REAL, 1.0)
                        try:
                            rx.search(s)
                            rx.findall(s)
                        except Slow:
                            slow.append(f"{mod}.{name}")
                        finally:
                            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, old)
    assert not slow, sorted(set(slow))


def test_overlong_lines_are_cut_with_a_note():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("payments.csv", "payment_id,supplier\n1," + "x" * 10_000)
    files, notes = custom.parse_zip(buf.getvalue())
    assert max(map(len, files["payments.csv"])) == custom.MAX_LINE_CHARS and any("were cut" in n for n in notes)


def test_finished_and_abandoned_jobs_are_let_go():
    class Done:
        def is_alive(self):
            return False

    class J:
        thread, touched = Done(), 0.0
    custom.JOBS.update({"a|x": J(), "b|y": J()})
    custom.JOBS["b|y"].touched = time.time()
    custom.prune_jobs()
    assert "a|x" not in custom.JOBS and "b|y" in custom.JOBS
    custom.JOBS.pop("b|y", None)


# ---- sign-in, sessions, files
@pytest.fixture()
def signin(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TALLYHOUND_USERS", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "ITER", 1000)
    auth._fails.clear()
    auth.add_user("rita", "reviewer", "pw-rita-1")
    auth.add_user("pete", "preparer", "pw-pete-1")
    return tmp_path


def _sign_in(at, name, pw):
    at.text_input[0].input(name)
    at.text_input[1].input(pw)
    at.button[0].click()
    at.run()


def test_a_planted_session_link_cannot_capture_work_when_sign_in_is_on(signin):
    attacker_sid = "ab" * 16
    store._write(attacker_sid, json.dumps({"notes": {"F-01": {"owner": "x", "note": "attacker's"}}}))
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["s"] = attacker_sid                       # the victim opens the attacker's link ...
    at.run()
    _sign_in(at, "rita", "pw-rita-1")
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state.sid == store.TEAM                 # ... but works in the team workspace, not that id
    assert "s" not in at.query_params
    assert at.session_state.notes.get("F-01", {}).get("note") != "attacker's"


def test_signing_out_clears_the_tab(signin):
    at = AppTest.from_file(APP, default_timeout=60).run()
    _sign_in(at, "pete", "pw-pete-1")
    at.session_state.notes = {"F-02": {"owner": "pete", "note": "private"}}
    at.run()
    at.button(key="sign_out").click().run()
    assert "notes" not in at.session_state or not at.session_state.notes
    assert "user" not in at.session_state


def test_a_damaged_users_file_lets_nobody_in(signin):
    (signin / "users.json").write_text("{not json")
    assert auth.enabled() and not auth.can("policy")
    assert auth.check("rita", "pw-rita-1") is None


def test_state_files_are_private(signin):
    C._trail_key()
    store._write("cd" * 16, "{}")
    for name in ("users.json", "trail.key", "tallyhound.db"):
        mode = stat.S_IMODE(os.stat(signin / name).st_mode)
        assert mode & 0o077 == 0, (name, oct(mode))


def test_a_teammates_newer_save_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    v1 = store._write("ef" * 16, '{"a": 1}')
    v2 = store._write("ef" * 16, '{"a": 2}', expect=v1)       # a teammate saves
    assert v2 is not None
    assert store._write("ef" * 16, '{"a": 3}', expect=v1) is None   # this tab still holds v1: refused
    assert store._read("ef" * 16) == '{"a": 2}'


# ---- what people see
def test_dataset_names_from_uploads_cannot_carry_markup():
    from tallyhound import run_analysis
    label = run_analysis.dataset_label("[Session expired](https://evil.example) <b>x</b>.zip")
    assert not re.search(r"[\[\]()<>:/]", label)
    assert run_analysis.dataset_label("September 2026.zip") == "September 2026"
    assert run_analysis.dataset_label("...zip") == "uploaded"


def test_folder_check_csv_keeps_formulas_as_text():
    assert headless.safe_cell("=HYPERLINK(\"http://x\")").startswith("'") and headless.safe_cell("Arden") == "Arden"


def test_workbook_says_partly_reviewed_while_findings_are_pending():
    import openpyxl

    def app():
        import streamlit as st

        from tallyhound import common as C
        from tallyhound import exports
        C.init_state()
        C.decide(C.findings().id.iloc[0], "Approved")
        st.session_state.book = exports.build_workbook()
    at = AppTest.from_function(app, default_timeout=60).run()
    ws = openpyxl.load_workbook(io.BytesIO(at.session_state.book))["Summary"]
    status = {r[0].value: r[1].value for r in ws.iter_rows(min_row=2)}["Status"]
    assert status.startswith("PARTLY REVIEWED")


def test_bulk_approval_asks_first():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state.nav = "Review"
    at.run()
    at.button(key="bulk_all").click().run()
    assert not at.session_state.decisions                   # nothing decided yet
    at.button(key="bulk_yes").click().run()
    assert at.session_state.decisions and C.decisions_mismatch(at.session_state.audit_log, at.session_state.decisions) == []


def test_a_reload_during_a_run_keeps_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    at = AppTest.from_file(APP, default_timeout=60).run()
    sid = at.query_params["s"]
    sid = sid[0] if isinstance(sid, list) else sid
    at.button(key="open_run").click().run()
    at.button(key="run_go").click().run()
    time.sleep(1)
    at.run()
    again = AppTest.from_file(APP, default_timeout=60)
    again.query_params["s"] = sid
    again.run()
    assert again.session_state.sim and again.session_state.sim["running"]
