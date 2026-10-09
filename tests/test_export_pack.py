"""The export pack: fictional files in the layouts of QuickBooks, Xero, Sage, NetSuite, Expensify and bank downloads
(tests/exports/pack, written by scripts/make_export_pack.py). Each one must be read as the right audit file, or
refused with a reason - never misread."""
import csv
import io
from pathlib import Path

import pytest

from tallyhound import columns, rules, uploads

PACK = Path(__file__).resolve().parent / "exports" / "pack"

# file -> (audit file it must become, data rows, words that must appear in its note) or (None, 0, reason words)
EXPECT = {
    "qbo_bill_payment_list.csv": ("payments.csv", 3, ["4 title line", "2 vendor heading", "3 total line", "footer"]),
    "qbd_check_detail.csv": (None, 0, ["closest to payments.csv", "invoice no"]),
    "Bills export.csv": ("payments.csv", 3, ["2 line-item row"]),
    "Contacts.csv": ("vendors.csv", 2, ["recognised from its columns"]),
    "Account Transactions.csv": ("bank_statement.csv", 3, ["section heading", "total line"]),
    "Purchase Day Book.csv": (None, 0, ["Ignored"]),
    "VendorPaymentsSearchResults.csv": ("payments.csv", 2, ["recognised from its columns"]),
    "Chase1234_Activity_20260930.CSV": ("bank_statement.csv", 3, ["recognised from its columns"]),
    "data.csv": (None, 0, ["bank_statement.csv"]),
    "Expensify_Export.csv": ("expenses.csv", 2, ["recognised from its columns"]),
    "A_P Payments.xlsx": ("payments.csv", 2, ['sheet "Sheet1"', "2 vendor heading", "2 total line"]),
    "expenses_september.xlsx": ("expenses.csv", 3, ['sheet "Claims"', "hidden sheet"]),
    "zahlungen_payments.csv": ("payments.csv", 2, ["semicolon"]),
    "approvals.tsv": ("approvals.csv", 1, []),
    "vendors.txt": ("vendors.csv", 1, []),
}


def test_the_pack_is_complete():
    assert sorted(p.name for p in PACK.iterdir()) == sorted(EXPECT)


def _accept_suggestions(name: str, lines: list[str]) -> list[str]:
    """The file as the checks see it once the pre-filled column matching is saved, with "(not in file)" for the rest."""
    head = next(csv.reader([lines[0]]))
    missing = rules.missing_columns(name, lines)
    m = columns.suggest(missing, head)
    back = {src: dst for dst, src in m.items()}
    new = [back.get(h, h) for h in head] + [c for c in missing if c not in m]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(new)
    return [buf.getvalue()] + lines[1:]


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_each_export_is_read_right_or_refused(name):
    kind, n, words = EXPECT[name]
    files, notes = uploads.read_uploads([(name, (PACK / name).read_bytes())])
    text = " ".join(notes)
    if kind is None:
        assert files == {}, files.keys()
    else:
        assert list(files) == [kind] and len(files[kind]) - 1 == n
        lines = _accept_suggestions(kind, files[kind])
        assert rules.missing_columns(kind, lines) == []
        assert isinstance(rules.analyze({kind: lines}), list)             # the checks run on it without error
    for w in words:
        assert w in text, (w, notes)


def _hits(name: str) -> list:
    files, _ = uploads.read_uploads([(name, (PACK / name).read_bytes())])
    kind = next(iter(files))
    return rules.analyze({kind: _accept_suggestions(kind, files[kind])})


def test_line_items_are_not_taken_for_bills_paid_twice():
    assert not [h for h in _hits("Bills export.csv") if h.clause in ("5.2", "5.6")]


def test_a_european_file_still_finds_its_duplicate():
    dup = [h for h in _hits("zahlungen_payments.csv") if h.clause == "5.2"]
    assert len(dup) == 1 and dup[0].amount == 1960.17          # 1.960,17 read as one thousand nine hundred ...


def test_month_name_dates_and_debit_columns_are_read():
    files, _ = uploads.read_uploads([("Account Transactions.csv", (PACK / "Account Transactions.csv").read_bytes())])
    lines = _accept_suggestions("bank_statement.csv", files["bank_statement.csv"])
    R = rules.rows(lines)
    assert [r.d("date").isoformat() for _, r in R] == ["2026-09-02", "2026-09-05", "2026-09-10"]
    assert [rules._f(r["amount"]) for _, r in R] == [1960.17, 9366.95, 0.0]     # money in has no debit


def test_accents_survive_utf16_and_windows_encodings():
    files, _ = uploads.read_uploads([("vendors.txt", (PACK / "vendors.txt").read_bytes())])
    assert "Café Müller GmbH" in files["vendors.csv"][1]
