"""The fictional data must keep the promises the app makes."""
from tallyhound import common as C


def test_findings_counts():
    f = C.findings()
    assert len(f) == 26
    assert f.severity.value_counts().to_dict() == {"Medium": 12, "High": 7, "Low": 7}
    assert f.area.value_counts().to_dict() == {"Payments": 6, "Approvals": 6, "Vendors": 4, "Contracts": 5, "Expenses": 5}


def test_every_quote_is_found_in_its_source_file():
    ok, hidden = C.load_findings_checked()
    assert hidden.empty, f"unverified findings: {list(hidden.id)}"
    assert C.quote_pct() == 100
    for r in ok.itertuples():
        lines = C.read_source(r.source_file)
        assert lines[r.line_number - 1] == r.evidence


def test_unverifiable_quote_is_hidden(monkeypatch):
    real = C.read_source
    monkeypatch.setattr(C, "read_source", lambda name: [ln.replace("Marlowe", "Xxxx") for ln in real(name)])
    C.load_findings_checked.clear()
    try:
        ok, hidden = C.load_findings_checked()
        assert "F-01" in set(hidden.id) and "F-01" not in set(ok.id)
    finally:
        C.load_findings_checked.clear()


def test_payment_gate_totals():
    t = C.gate_totals(C.gate("payment_run_2026-10-01.csv"))
    assert (t["lines"], t["hold_n"], t["rel_n"], t["vendors"]) == (14, 8, 6, 8)
    assert round(t["hold_amt"], 2) == 64165.30
    assert round(t["total"], 2) == 121405.75


def test_recovery_and_subscriptions_totals():
    assert round(C.recovery().claim.sum(), 2) == 6864.61
    s = C.subscriptions()
    assert len(s) == 15 and int(s.licences.sum()) == 911 and int(s.saving.sum()) == 25850


def test_policy_clause_text_for_required_clauses():
    P = C.policy()
    assert P["4.3|Vendors"] == "No payment may be released to a changed bank account until verification is recorded."
    assert P["5.2|Payments"] == "A single invoice may be paid once only."
