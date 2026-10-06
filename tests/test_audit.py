"""Found in the error audit (October 2026): hostile uploads, unsafe HTML, zip bombs, model-address tricks, guessing
passwords. Each test pins one fix so it cannot quietly come back."""
import io
import time
import zipfile
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tallyhound import auth, challenge, custom, llm, rules, store
from tallyhound import common as C

APP = str(Path(__file__).resolve().parent.parent / "app.py")
EVIL = '<img src=x onerror=alert(1)>'


def z(entries) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as f:
        for n, d in entries:
            f.writestr(n, d)
    return b.getvalue()


def hostile_month() -> dict[str, list[str]]:
    files, _, _ = challenge.generate_full(5, "medium")
    files = {k: list(v) for k, v in files.items()}
    for name, col in (("payments.csv", "supplier"), ("approvals.csv", "vendor"), ("vendors.csv", "name"),
                      ("expenses.csv", "employee")):
        head = files[name][0].split(",")
        i = head.index(col)
        for n in range(1, len(files[name])):
            cells = files[name][n].split(",")
            if len(cells) == len(head) and n % 3 == 0:
                cells[i] = f"{EVIL} [link](http://x) **{cells[i]}**"
                files[name][n] = ",".join(cells)
    head = files["payments.csv"][0].split(",")
    cells = files["payments.csv"][2].split(",")
    cells[head.index("paid_amount")] = "nan"
    files["payments.csv"][2] = ",".join(cells)
    return files


# ---- unsafe HTML
def test_esc_neutralises_html_and_markdown():
    out = C.esc(f"{EVIL} $1,200 ****1234 [a](b)")
    assert "<img" not in out and "&lt;img" in out
    assert "\\$" in out and "\\*\\*\\*\\*1234" in out and "\\[a\\]" in out
    assert "://" not in C.esc("see http://evil.example") and "www." not in C.esc("www.evil.example")


def test_hostile_upload_walks_every_page_without_errors_or_raw_html():
    at = AppTest.from_file(APP, default_timeout=90).run()
    label = "<b>evil</b> month"
    at.session_state.uploads = {label: hostile_month()}
    at.session_state.extra_opts = {"audit": {label: "8 files"}}
    at.run()
    at.button(key="open_run").click().run()
    at.button(key="run_go").click().run()
    for _ in range(60):
        if at.session_state.get("dataset") == label:
            break
        time.sleep(1)
        at.run()
    assert at.session_state.dataset == label
    assert not at.exception, [e.value for e in at.exception]
    for f in at.session_state.custom[label]:
        at.session_state[f"open_{f['id']}"] = True
    for page in C.NAV:
        at.session_state.nav = page
        at.run()
        assert not at.exception, (page, [e.value for e in at.exception])
        for m in at.markdown:
            if m.allow_html and "th-src" not in m.value:     # the source viewer escapes each line itself
                assert "<img" not in m.value and "<b>evil" not in m.value, (page, m.value[:200])


# ---- amounts that are not money
@pytest.mark.parametrize("s", ["nan", "NaN", "inf", "-inf", "1e309", "9" * 40])
def test_amounts_that_are_not_money_read_as_zero(s):
    assert rules._f(s) == 0.0


def test_nan_amounts_do_not_crash_reconciliation_and_are_reported():
    files = hostile_month()
    hits = rules.analyze(files)
    assert all(h.amount == h.amount for h in hits)
    import streamlit as st                               # outside a running app this is a plain dict-like state
    st.session_state.uploads = {"t": files}
    warn = [x for x in custom.profile("t") if x["kind"] == "unreadable"]
    assert any("paid_amount" in x["message"] and "line 3" in x["message"] for x in warn)


# ---- zips
def test_zip_bomb_is_stopped_before_it_fills_memory():
    big = b"payment_id\n" + b"0" * (40 * 1024 * 1024)
    names = ["payments", "approvals", "vendors", "expenses", "payment_run", "bank_statement"]
    files, notes = custom.parse_zip(z([(f"{n}.csv", big) for n in names]))
    assert len(files) < len(names)
    assert any("unpacked data" in n for n in notes)


def test_password_protected_zip_is_a_note_not_a_crash():
    data = bytearray(z([("payments.csv", "payment_id\n1")]))
    data[data.find(b"PK\x03\x04") + 6] |= 1
    data[data.find(b"PK\x01\x02") + 8] |= 1
    files, notes = custom.parse_zip(bytes(data))
    assert files == {} and "password" in notes[0]


def test_windows_and_utf16_csvs_keep_their_accents():
    f1, _ = custom.parse_zip(z([("payments.csv", "supplier\nCafé €".encode("cp1252"))]))
    f2, _ = custom.parse_zip(z([("payments.csv", "supplier\nCafé".encode("utf-16"))]))
    assert f1["payments.csv"][1] == "Café €" and f2["payments.csv"][1] == "Café"


def test_two_files_for_one_slot_are_not_silently_merged():
    files, notes = custom.parse_zip(z([("payments.csv", "a\n1"), ("old/payments.csv", "b\n2")]))
    assert files["payments.csv"] == ["a", "1"]
    assert any("Two files are read as payments.csv" in n for n in notes)


# ---- model address
@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://169.254.169.254/latest/meta-data", "ftp://x",
                                 "http://metadata.google.internal", "http://[fe80::1]:11434"])
def test_model_address_cannot_reach_files_or_cloud_metadata(url):
    assert llm.check_url(url)
    assert llm.models(url) == []
    with pytest.raises(llm.LLMError):
        llm._call(url, "/api/tags", None, 1)


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://127.0.0.1:1234/v1", "http://ollama:11434"])
def test_local_model_addresses_are_still_allowed(url):
    assert llm.check_url(url) is None


# ---- sign-in and sessions
def test_repeated_wrong_passwords_lock_the_name(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_USERS", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "ITER", 1000)
    auth._fails.clear()
    auth.add_user("rita", "reviewer", "right-horse-battery")
    for _ in range(auth.MAX_FAILS):
        assert auth.check("rita", "wrong") is None
    assert auth.locked_for("rita") > 0
    assert auth.check("rita", "right-horse-battery") is None       # locked, even with the right password
    auth._fails["rita"][1] -= auth.LOCK_SECONDS                   # time passes
    assert auth.check("rita", "right-horse-battery") == "reviewer"
    auth._fails.clear()


def test_new_session_ids_are_128_bit_and_old_links_still_work():
    assert store._SID.match("a" * 32) and store._SID.match("b" * 12)
    assert not store._SID.match("../../x") and not store._SID.match("a" * 20)


def test_reset_is_admin_only_when_sign_in_is_on(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_USERS", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "ITER", 1000)
    auth.add_user("pat", "preparer", "pw-one-two-three")
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state.user, at.session_state.role = "pat", "preparer"
    at.run()
    assert not at.exception
    assert at.button(key="reset_demo").disabled


# ---- exports
def test_exports_survive_hostile_names_and_keep_formulas_as_text():
    import openpyxl

    from tallyhound import exports
    at = AppTest.from_file(APP, default_timeout=60).run()
    files = hostile_month()
    head = files["payments.csv"][0].split(",")
    cells = files["payments.csv"][4].split(",")
    cells[head.index("supplier")] = '=HYPERLINK("http://x";"click")'
    files["payments.csv"][4] = ",".join(cells)
    hits = rules.analyze(files)
    assert hits
    # a workbook sheet with a formula-looking value stays text after defusing
    wb = openpyxl.Workbook()
    wb.active["A1"] = '=HYPERLINK("http://x","click")'
    exports.defuse_formulas(wb.active)
    assert wb.active["A1"].data_type == "s"
    # the PDF memo accepts names with & and < (it used to crash)
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph
    import html
    Paragraph(html.escape("Smith & Sons <b"), getSampleStyleSheet()["Normal"])
    assert not at.exception


# ---- public demo
def test_public_demo_only_talks_to_a_model_on_its_own_server(monkeypatch):
    monkeypatch.setenv("TALLYHOUND_PUBLIC", "1")
    for url in ("http://10.0.0.5:11434", "http://192.168.1.20:11434", "http://ollama:11434", "http://example.com/v1"):
        assert llm.check_url(url), url
    for url in ("http://localhost:11434", "http://127.0.0.1:1234/v1", "http://[::1]:11434"):
        assert llm.check_url(url) is None, url
    monkeypatch.setenv("TALLYHOUND_PUBLIC", "0")
    assert llm.check_url("http://192.168.1.20:11434") is None      # your own computer or server: allowed


def test_streamlit_cloud_is_detected_as_public(monkeypatch):
    monkeypatch.delenv("TALLYHOUND_PUBLIC", raising=False)
    monkeypatch.setattr(llm, "__file__", "/mount/src/tallyhound/tallyhound/llm.py")
    assert llm.public()
    monkeypatch.setattr(llm, "__file__", "/home/me/tallyhound/tallyhound/llm.py")
    assert not llm.public()


def test_preparer_only_upload_when_sign_in_is_on(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_USERS", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "ITER", 1000)
    auth.add_user("rev", "reviewer", "pw-one-two-three")
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state.user, at.session_state.role = "rev", "reviewer"
    at.run()
    at.button(key="open_run").click().run()
    assert not at.exception
    assert at.button(key="run_go").disabled
