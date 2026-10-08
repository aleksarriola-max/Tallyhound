"""Checks added in 0.7: near-duplicate invoice numbers, big early payments to new vendors, claims bunched under the
receipt limit, approvals bunched under the director limit, and the Patterns tab."""
import random
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tallyhound import challenge, patterns, rules, score

ROOT = Path(__file__).resolve().parent.parent
PAY = "payment_id,pay_date,invoice_date,vendor_id,supplier,invoice_no,invoice_amount,paid_amount"


def _pay(*rows):
    return [PAY] + [",".join(r) for r in rows]


def _near(lines):
    return [h for h in rules.payments(lines) if h.clause == "5.6"]


# ---- near-duplicate invoice numbers
def test_similar_invoice_numbers():
    yes = [("ASH-6100", "ASH-6100A"), ("6100", "ASH-6100"), ("ASH-6100", "ASH-6010"), ("INV-1O01", "INV-1001")]
    no = [("INV-1001", "INV-1002"), ("A-1", "A-1"), ("LIC-7730", "LIC-7731"), ("AB", "ABC")]
    assert all(rules.similar_invoice(a, b) for a, b in yes)
    assert not any(rules.similar_invoice(a, b) for a, b in no)


def test_a_rekeyed_invoice_number_is_flagged_once():
    lines = _pay(["P-1", "2026-09-02", "2026-09-01", "V-1", "Ashby", "ASH-6100", "1960.17", "1960.17"],
                 ["P-2", "2026-09-05", "2026-09-01", "V-1", "Ashby", "ASH-6100A", "1960.17", "1960.17"],
                 ["P-3", "2026-09-06", "2026-09-01", "V-1", "Ashby", "ASH6100", "1960.17", "1960.17"])
    hits = _near(lines)
    assert len(hits) == 1 and hits[0].line_number == 3 and hits[0].related == [("payments.csv", 2)]
    assert "statement" in rules.FIXES["5.6"] and rules.INNOCENT["5.6"]


def test_a_weekly_series_and_two_site_licences_are_left_alone():
    lines = _pay(["P-1", "2026-09-02", "2026-09-01", "V-1", "Clean Co", "WK-4100", "450.00", "450.00"],
                 ["P-2", "2026-09-09", "2026-09-08", "V-1", "Clean Co", "WK-4101", "450.00", "450.00"],
                 ["P-3", "2026-09-10", "2026-09-10", "V-2", "Lic Co", "LIC-7730", "1250.00", "1250.00"],
                 ["P-4", "2026-09-10", "2026-09-10", "V-2", "Lic Co", "LIC-7731", "1250.00", "1250.00"])
    assert _near(lines) == []


def test_different_amounts_far_apart_or_reversed_are_not_near_duplicates():
    lines = _pay(["P-1", "2026-09-02", "2026-09-01", "V-1", "A", "ASH-6100", "100.00", "100.00"],
                 ["P-2", "2026-09-03", "2026-09-01", "V-1", "A", "ASH-6100A", "100.50", "100.50"],      # other amount
                 ["P-3", "2026-09-02", "2026-09-01", "V-2", "B", "B-7000", "300.00", "300.00"],
                 ["P-4", "2026-09-29", "2026-09-28", "V-2", "B", "B-7000A", "300.00", "300.00"],        # weeks apart
                 ["P-5", "2026-09-02", "2026-09-01", "V-3", "C", "C-5000", "400.00", "400.00"],
                 ["P-6", "2026-09-03", "2026-09-01", "V-3", "C", "C-5000A", "400.00", "400.00"],
                 ["P-7", "2026-09-04", "2026-09-01", "V-3", "C", "C-5000A", "400.00", "-400.00"])       # voided
    assert _near(lines) == []


# ---- new vendor paid a lot, fast
VEN = "vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount"


def test_a_new_vendor_paid_big_soon_is_flagged():
    lines = [VEN, "V-1,Nova,11-1,ACTIVE,****1,,N/A,2026-09-02,YES,2026-09-09,14500.00",
             "V-2,Small,11-2,ACTIVE,****2,,N/A,2026-09-02,YES,2026-09-09,1200.00",       # small first payment
             "V-3,Old,11-3,ACTIVE,****3,,N/A,2024-01-02,YES,2026-09-09,14500.00",         # set up long ago
             "V-4,Late,11-4,ACTIVE,****4,,N/A,2026-07-01,YES,2026-09-09,14500.00"]        # 70 days later
    hits = [h for h in rules.vendors(lines) if h.clause == "4.5"]
    assert [h.line_number for h in hits] == [2] and "7 days after" in hits[0].title
    assert [h.line_number for h in rules.vendors(lines, {**rules.LIMITS, "new_vendor_days": 80}) if h.clause == "4.5"] == [2, 5]


# ---- bunched under a limit
EXP = "claim_id,date,employee,category,amount,receipt_ref,notes,people"


def test_three_claims_just_under_the_receipt_limit():
    rows = [f"E-{i},2026-09-0{i},R. Mehta,PARKING,24.{50 + i},,Client visit," for i in range(1, 4)]
    hits = [h for h in rules.expenses([EXP] + rows) if h.clause == "6.6"]
    assert len(hits) == 1 and hits[0].severity == "Low" and len(hits[0].related) == 2
    assert not [h for h in rules.expenses([EXP] + rows[:2]) if h.clause == "6.6"]           # two is not a pattern
    with_receipts = [r.replace(",,Client", ",RC-1,Client") for r in rows]
    assert not [h for h in rules.expenses([EXP] + with_receipts) if h.clause == "6.6"]


APP_H = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no"


def test_approvals_just_under_the_director_limit_to_different_suppliers():
    rows = ['A-1,INVOICE,X-1,2026-09-02,Arden,9800.00,"Lowe, Kate",D. Reyes,Manager,PO-1',
            "A-2,INVOICE,Y-1,2026-09-12,Bexley,9950.00,Kate Lowe,D. Reyes,Manager,PO-2"]
    hits = [h for h in rules.approvals([APP_H] + rows) if "just under" in h.title]
    assert len(hits) == 1 and hits[0].clause == "1.6" and hits[0].line_number == 3
    same = [rows[0], rows[1].replace("Bexley", "Arden")]
    assert not [h for h in rules.approvals([APP_H] + same) if "just under" in h.title]


# ---- the generator plants them and the rules find them, without touching the traps
def test_generated_months_find_the_new_problems_and_spare_the_new_traps():
    kinds = {"pay_neardup": 0, "app_underlimit": 0, "ven_newfast": 0, "exp_underlimit": 0}
    for diff in ("easy", "medium", "hard"):
        for seed in range(1, 16):
            files, key = challenge.generate(seed, diff)
            k = score.key_from_csv(challenge.key_csv(key))
            hits = rules.analyze(files)
            sc = score.score([dict(source_file=h.source_file, line_number=h.line_number,
                                   related_lines=[ln for _, ln in h.related]) for h in hits], k)
            assert sc["traps_flagged"] == 0, (diff, seed, sc["trap_kinds"])
            assert sc["false_alarms"] == 0, (diff, seed)
            for raw, row in zip(key, k):
                for kind in kinds:
                    if row["expect"] == "problem" and raw["description"] and _kind(raw) == kind:
                        assert row["id"] in sc["found_ids"], (diff, seed, kind)
                        kinds[kind] += 1
    assert all(kinds.values()), kinds


def _kind(raw: dict) -> str:
    d = raw["description"]
    return ("pay_neardup" if " paid again as " in d else "app_underlimit" if "just under the director" in d else
            "ven_newfast" if "soon after set-up" in d else "exp_underlimit" if "just under the receipt" in d else "")


def test_old_problems_stay_where_they_were(monkeypatch):
    """The 0.7 additions use their own random stream: a seed still plants the same older problems and traps."""
    def older(key):
        return [k["description"] for k in key if not _kind(k)]
    with_extras = [older(challenge.generate(s, d)[1]) for s in (1, 2, 3) for d in ("easy", "hard")]
    monkeypatch.setattr(challenge, "EXTRA_COUNTS", {"easy": 0, "medium": 0, "hard": 0})
    monkeypatch.setattr(challenge._Gen, "extra_traps", lambda self: None)
    without = [older(challenge.generate(s, d)[1]) for s in (1, 2, 3) for d in ("easy", "hard")]
    trap_free = [[x for x in k if not x.startswith("TRAP (series") and not x.startswith("TRAP (two_sites")
                  and not x.startswith("TRAP (new_small") and not x.startswith("TRAP (parking")] for k in with_extras]
    assert trap_free == without


# ---- patterns
def test_benford_needs_enough_data_and_spots_steering():
    rnd = random.Random(1)
    natural = [10 ** rnd.uniform(1, 4) for _ in range(3000)]
    b = patterns.benford(natural)
    assert b["usable"] and b["verdict"] == "close conformity"
    assert not patterns.benford(natural[:100])["usable"]
    steered = natural[:2000] + [rnd.uniform(9000, 9999) for _ in range(600)]
    assert patterns.benford(steered)["verdict"].startswith("nonconformity")


def test_round_amounts_and_bunching():
    assert patterns.round_share([500.0, 1200.0, 1234.56, 80.0]) == dict(n=3, round=2, share=2 / 3)
    j = patterns.just_under([9700, 9800, 9900, 9990, 10100], 10000)
    assert (j["below"], j["above"], j["flag"]) == (4, 1, True)
    assert patterns.first_digit(0.042) == 4 and patterns.first_digit(0) is None


def test_the_patterns_tab_renders():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    at.session_state.nav = "Reports"
    at.run()
    assert not at.exception
    assert any("Patterns worth a look" in s.value for s in at.subheader)


def test_policy_shows_the_new_vendor_window():
    import streamlit as st

    from tallyhound import common as C
    st.session_state.clear()
    st.session_state.limits = {"new_vendor_days": 45}
    assert "within 45 days" in C.policy()["4.5|Vendors"]
    st.session_state.clear()
