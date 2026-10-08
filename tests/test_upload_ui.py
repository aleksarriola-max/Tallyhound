"""The upload flow as a person sees it: picking files, the notes, column matching and the data check."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

QB = Path(__file__).resolve().parent / "exports" / "Transaction List by Vendor.csv"


def _page(paths):
    """A page that runs the real upload helper on files given by path (AppTest cannot drive a file picker)."""
    from pathlib import Path

    import streamlit as st

    from tallyhound import common as C
    from tallyhound import run_analysis

    class Up:
        def __init__(self, p):
            self.name, self._raw, self.file_id = Path(p).name, Path(p).read_bytes(), p

        def getvalue(self):
            return self._raw
    C.init_state()
    st.session_state["_label"] = run_analysis.add_zip([Up(p) for p in paths])


def _app(paths):
    return AppTest.from_function(_page, args=(paths,), default_timeout=60)


def test_a_quickbooks_export_goes_through_column_matching(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    at = _app([str(QB)]).run()
    assert not at.exception
    label = at.session_state["_label"]
    assert label == "Transaction List by Vendor"
    notes = [c.value for c in at.caption]
    assert any("read as payments.csv, recognised from its columns" in n for n in notes)
    picks = {s.label: s.value for s in at.selectbox}
    assert picks["supplier"] == "Vendor" and picks["invoice_no"] == "Invoice No" and picks["paid_amount"] == "Amount Paid"
    at.button(key=f"mapsave_{label}_payments.csv").click().run()
    from tallyhound import rules
    m = at.session_state["mappings"][label]["payments.csv"]
    assert set(rules.REQUIRED["payments.csv"]) <= set(m) and m["supplier"] == "Vendor"
    assert not [b for b in at.button if b.key and b.key.startswith("mapsave_")]     # nothing left to match
    assert at.session_state["presets"]                 # remembered for next month's export with the same columns


def test_nothing_usable_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    junk = tmp_path / "holiday list.csv"
    junk.write_text("name,start,end\nAda,2026-01-01,2026-01-05\n", encoding="utf-8")
    at = _app([str(junk)]).run()
    assert not at.exception and at.session_state["_label"] is None
    assert any("No usable audit files" in e.value for e in at.error)
    assert any("do not clearly match one of the audit files" in w.value for w in at.warning)


def test_several_files_get_a_stable_name():
    from tallyhound import run_analysis
    assert run_analysis.upload_name(["vendors.xlsx", "payments.csv"]) == "payments and 1 more"
    assert run_analysis.upload_name(["march.zip"]) == "march.zip"
