"""Metamorphic and property tests of the rules, the payment gate and the challenge generator.

Each test changes the input in a way that must not change the answer (row order, column order and name spelling,
blank lines, number and date formats, consistent renames, unrelated far-away records) or must change it in a known
way (amounts and limits scaled together, two months together = each month on its own), and compares findings by
clause and by the records they point at - never by line number or wording.

Population-based checks are left out where the change legitimately moves them: 4.5 (a set-up date shared by many
vendors is a data migration), 1.6 (approvals just under the director limit, counted over the whole file) and the
Patterns tab (first digits, round amounts), which describes all the amounts together.
"""
import csv
import io
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tallyhound import challenge, columns, gate, headless, patterns, rules, score, uploads
from tallyhound import common as C
from tests.test_round5 import _contrast

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "source"
QUICK = settings(max_examples=8, derandomize=True, deadline=None, database=None,
                 suppress_health_check=list(HealthCheck))
CSVS = ["payments.csv", "approvals.csv", "vendors.csv", "expenses.csv", "bank_statement.csv", "payment_run.csv"]
MONEY = {"payments.csv": ["invoice_amount", "paid_amount"], "approvals.csv": ["amount"], "vendors.csv": ["last_paid_amount"],
         "expenses.csv": ["amount"], "bank_statement.csv": ["amount"], "payment_run.csv": ["amount"]}
DATES = {"payments.csv": ["pay_date", "invoice_date"], "approvals.csv": ["date"], "expenses.csv": ["date"],
         "vendors.csv": ["bank_changed_on", "created_on", "last_paid_on", "status_changed_on"], "bank_statement.csv": ["date"]}
TEXT_AMOUNTS = ("contracts.txt", "invoices.txt")       # amounts inside free text: not rewritten by the tests below


@lru_cache(maxsize=None)
def _month(i: int) -> tuple:
    """Fresh months (seeds the CI quality gate does not use) and the sample company, as immutable tuples."""
    if i == 0:
        files = {p.name: p.read_text(encoding="utf-8").splitlines() for p in SAMPLE.iterdir()}
        files["payment_run.csv"] = (ROOT / "data" / "payment_run_2026-10-01.csv").read_text(encoding="utf-8").splitlines()
    else:
        files, _ = challenge.generate(7300 + i, ("easy", "medium", "hard")[i % 3])
    return tuple((k, tuple(v)) for k, v in files.items())


def month(i: int) -> dict[str, list[str]]:
    return {k: list(v) for k, v in _month(i)}


MONTHS = st.integers(0, 6)


# ---------------------------------------------------------------- comparing findings by what they point at
def _record(files, name, ln):
    lines = files[name]
    if not name.endswith(".csv"):
        return lines[ln - 1]
    head = next(csv.reader([lines[0]]))
    vals = next(csv.reader([lines[ln - 1]]))
    return tuple(sorted((columns.norm_header(h), v) for h, v in zip(head, vals) if h != "extra_col"))


def sig(files, hits, drop=lambda col: False, skip=()):
    """Counter of (clause, severity, amount, file, the set of records the finding points at)."""
    out = Counter()
    for h in hits:
        if h.clause in skip:
            continue
        recs = [_record(files, h.source_file, h.line_number)] + [_record(files, f, ln) for f, ln in h.related]
        recs = [tuple(x for x in r if not drop(x[0])) if isinstance(r, tuple) else r for r in recs]
        out[(h.clause, h.severity, round(h.amount, 2), h.source_file, frozenset(recs))] += 1
    return out


def _rows(lines):
    return list(csv.reader(lines))


def _lines(rows_):
    out = []
    for r in rows_:
        buf = io.StringIO()
        csv.writer(buf, lineterminator="").writerow(r)
        out.append(buf.getvalue())
    return out


def rewrite(files, spec, fn):
    """files with fn(value) written into the named columns (header line kept as it is)."""
    out = dict(files)
    for name, cols in spec.items():
        if name not in files:
            continue
        R = _rows(files[name])
        idx = [R[0].index(c) for c in cols if c in R[0]]
        for r in R[1:]:
            for i in idx:
                if i < len(r) and r[i].strip():
                    r[i] = fn(r[i], name)
        out[name] = _lines(R)
    return out


def iso(files):
    """Every date as YYYY-MM-DD (generated expenses can be day-first), so other date formats can be written."""
    return rewrite(files, DATES, lambda v, n: (d.isoformat() if (d := rules._d(v, rules.date_order(files[n]) == "day-first"))
                                                else v))


def gate_view(files):
    df = gate.evaluate(files)
    return [] if df.empty else list(zip(df.invoice, df.amount, df.decision, df.failed_checks))


# ---------------------------------------------------------------- minimal examples of the bugs these tests found
PAY_H = "payment_id,pay_date,invoice_date,vendor_id,supplier,invoice_no,invoice_amount,paid_amount"
APPR_H = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no"
EXP_H = "claim_id,date,employee,category,amount,receipt_ref,notes,people"


def test_newest_first_payments_do_not_turn_instalments_into_a_duplicate():
    first = "P1,2026-09-01,2026-08-20,V-1,Ashby,INV-1,1000.00,500.00"       # invoice of 1,000 paid in two halves;
    second = "P2,2026-09-15,2026-08-20,V-1,Ashby,INV-1,500.00,500.00"      # the later line shows the open balance
    for order in ([first, second], [second, first]):                        # many exports list the newest first
        assert not rules.payments([PAY_H, *order])


def test_an_overpayment_is_judged_on_the_earliest_payment_in_any_row_order():
    first = "P1,2026-09-01,2026-08-20,V-1,Ashby,INV-1,500.00,500.00"
    again = "P2,2026-09-02,2026-08-20,V-1,Ashby,INV-1,250.00,500.00"
    for order in ([first, again], [again, first]):
        got = rules.payments([PAY_H, *order])
        assert sorted(h.clause for h in got) == ["5.2"], order             # paid twice; not also "overpaid"


def test_bank_matching_pairs_every_line_it_can():
    """Two equal payments (1st, 10th) and two bank lines (6th, 12th), 5-day window: nearest-first gave the 6th the
    payment of the 10th, left the 12th 'unrecorded' and raised a false High."""
    pays = [PAY_H, "P1,2026-09-01,2026-08-20,V-1,Ashby,INV-1,700.00,700.00",
            "P2,2026-09-10,2026-08-25,V-1,Ashby,INV-2,700.00,700.00"]
    one, two = "2026-09-06,ASHBY,-700.00,", "2026-09-12,ASHBY,-700.00,"
    for bank in ([one, two], [two, one]):
        assert rules.reconcile({"payments.csv": pays, "bank_statement.csv": ["date,description,amount,reference", *bank]}) == []


def test_a_later_split_by_the_same_requester_is_its_own_finding():
    rows = [f"A{i},INVOICE,X-{i},{d},Arden Metals,6000.00,K. Lowe,M. Ray,Manager," for i, d in
            enumerate(["2026-03-02", "2026-03-03", "2026-09-01", "2026-09-02"])]
    got = sorted((h.line_number, [ln for _, ln in h.related]) for h in rules.approvals([APPR_H, *rows]) if h.clause == "1.4")
    assert got == [(3, [2]), (5, [4])]


def test_a_later_run_of_claims_under_the_receipt_limit_is_its_own_finding():
    rows = [f"E{i},{d},R. Mehta,SUPPLIES,24.50,,Business purpose recorded," for i, d in
            enumerate(["2026-03-02", "2026-03-03", "2026-03-04", "2026-09-01", "2026-09-02", "2026-09-03"])]
    got = [h.line_number for h in rules.expenses([EXP_H, *rows]) if h.clause == "6.6"]
    assert got == [4, 7]


def test_optional_columns_are_read_whatever_their_case_or_spacing():
    # "Type" read as blank made a purchase order look like an invoice with no PO
    po = "A1,PO,PO-1,2026-09-01,Arden Metals,4000.00,K. Lowe,M. Ray,Manager,"
    assert not rules.approvals([APPR_H.replace("type", "Type"), po])
    # "Created On" read as blank lost the big-payment-to-a-new-vendor check
    vh = "vendor_id,name,tax_id,status,bank_changed_on,bank_verified,w9_on_file,last_paid_on,last_paid_amount,{}"
    v = "V-1,Arden Metals,11-111,ACTIVE,,YES,YES,2026-09-10,25000.00,2026-09-01"
    assert [h.clause for h in rules.vendors([vh.format("Created On"), v])] == ["4.5"]
    # " Bank_Last4 " read as blank skipped the gate's bank-account check and released the line
    files = {"payment_run.csv": ["line,supplier,invoice,amount, Bank_Last4 ", "1,Arden Metals,X-1,100.00,9999"],
             "vendors.csv": ["vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,w9_on_file,last_paid_on,"
                             "last_paid_amount", "V-1,Arden Metals,11-111,ACTIVE,****1234,,YES,YES,,"]}
    assert gate.evaluate(files).decision.tolist() == ["HOLD"]
    assert rules.missing_columns("payments.csv", [PAY_H.upper()])      # required columns still go through matching


# ---------------------------------------------------------------- relations that must hold on whole months
@QUICK
@given(MONTHS, st.randoms(use_true_random=False))
def test_row_order_does_not_change_findings_or_gate(i, rnd):
    f = month(i)
    g = dict(f)
    for n in CSVS:
        if n in f:
            body = f[n][1:]
            rnd.shuffle(body)
            g[n] = [f[n][0]] + body
    assert sig(f, rules.analyze(f)) == sig(g, rules.analyze(g))
    gate_lines = gate_view(f)
    if len(set(x[0] for x in gate_lines)) == len(gate_lines):       # a repeated invoice: the later copy is held
        assert sorted(gate_lines) == sorted(gate_view(g))


@QUICK
@given(MONTHS, st.randoms(use_true_random=False))
def test_column_order_an_extra_column_and_blank_lines_change_nothing(i, rnd):
    f = month(i)
    g = dict(f)
    for n in CSVS:
        if n not in f:
            continue
        R = _rows(f[n])
        idx = list(range(len(R[0])))
        rnd.shuffle(idx)
        pos = rnd.randrange(len(R[0]) + 1)
        R = [[r[j] if j < len(r) else "" for j in idx] for r in R]
        R = [r[:pos] + (["extra_col"] if k == 0 else [f"x{rnd.randrange(99)}"]) + r[pos:] for k, r in enumerate(R)]
        L = _lines(R)
        for _ in range(2):
            L.insert(rnd.randrange(1, len(L) + 1), "")
        g[n] = L
    assert sig(f, rules.analyze(f)) == sig(g, rules.analyze(g))
    assert gate_view(f) == gate_view(g)


@QUICK
@given(MONTHS, st.sampled_from([str.upper, str.title, lambda h: f" {h} ", lambda h: h.replace("_", " ").title()]))
def test_column_name_case_and_spacing_change_nothing_after_upload(i, spell):
    """Through the upload path and the automatic matching of a scheduled run, as a person's export arrives."""
    f = month(i)
    g = dict(f)
    for n in CSVS:
        if n in f:
            R = _rows(f[n])
            R[0] = [spell(h) for h in R[0]]
            g[n] = _lines(R)

    def read(files):
        got, notes = uploads.parse_zip(challenge.to_zip(files, []))
        got.pop("answer_key.csv", None)
        return headless.match_columns(got, notes)
    a, b = read(f), read(g)
    assert sig(a, rules.analyze(a)) == sig(b, rules.analyze(b))
    assert gate_view(a) == gate_view(b)


def _rename(files, rnd):
    names = sorted({w for v in files.values() for ln in v for w in re.findall(r"\b[A-Z][a-z]{3,}\b", ln)}
                   & set(challenge.NAMES))
    new = {}
    while len(new) < len(names):
        w = "".join(rnd.choice("bdfgklmnprstvz") + rnd.choice("aeiou") for _ in range(3)).capitalize() + "x"
        if w not in new.values():
            new[names[len(new)]] = w
    if not new:
        return files, new
    pat = re.compile(r"\b(" + "|".join(new) + r")\b", re.I)

    def sub(m):
        w = new[m.group(1).capitalize()]
        return w.upper() if m.group(1).isupper() else w
    return {n: [pat.sub(sub, ln) for ln in v] for n, v in files.items()}, new


@QUICK
@given(st.integers(1, 6), st.randoms(use_true_random=False))
def test_renaming_every_vendor_consistently_changes_only_the_names(i, rnd):
    f = month(i)
    g, new = _rename(f, rnd)
    pat = re.compile(r"\b(" + "|".join(new) + r")\b", re.I) if new else None

    def view(hits, rename):
        return sorted((h.clause, h.source_file, h.line_number, h.severity, round(h.amount, 2), tuple(h.related),
                       (pat.sub(lambda m: new[m.group(1).capitalize()], h.title) if rename and pat else h.title).lower())
                      for h in hits)
    assert view(rules.analyze(f), True) == view(rules.analyze(g), False)
    assert [x[2:] for x in gate_view(f)] == [x[2:] for x in gate_view(g)]


MONEY_LIMITS = ("po_limit", "director_limit", "meal_limit", "receipt_limit", "tolerance", "materiality")
AMOUNT_COLS = {"amount", "paid amount", "invoice amount", "last paid amount"}
LIMIT_CLAUSES = ("1.1", "1.3", "6.1", "6.2", "4.5")         # an amount above a limit: scaling up only adds these


@QUICK
@given(MONTHS)
def test_scaling_amounts_and_limits_together_changes_nothing(i):
    f = month(i)
    g = rewrite(f, MONEY, lambda v, n: f"{rules._f(v) * 100:.2f}")
    L = {k: rules.LIMITS[k] * 100 for k in MONEY_LIMITS}

    def view(files, limits=None):
        s = sig(files, rules.analyze(files, limits), drop=lambda c: c in AMOUNT_COLS)
        return Counter({k[:2] + k[3:]: v for k, v in s.items() if k[3] not in TEXT_AMOUNTS})
    assert view(f) == view(g, L)
    before, after = view(f), view(g)                         # same limits, amounts 100 times bigger
    for clause in LIMIT_CLAUSES:
        assert {k for k in before if k[0] == clause} <= {k for k in after if k[0] == clause}, clause


@QUICK
@given(MONTHS, st.sampled_from(["comma", "plain", "dollar", "paren", "usd"]))
def test_number_formatting_changes_nothing(i, style):
    f = month(i)

    def fmt(v, n):
        if v.strip().upper().endswith(("CR", "DR")):
            return v
        x = rules._f(v)
        return {"comma": f"{x:,.2f}", "plain": f"{x:.2f}".rstrip("0").rstrip("."),
                "dollar": f"${x:,.2f}" if x >= 0 else f"-${-x:,.2f}", "paren": f"({-x:,.2f})" if x < 0 else f"{x:,.2f}",
                "usd": f"USD {x:.2f}"}[style]
    g = rewrite(f, MONEY, fmt)
    drop = lambda c: c in AMOUNT_COLS                        # noqa: E731
    assert sig(f, rules.analyze(f), drop) == sig(g, rules.analyze(g), drop)
    assert [x[0::2] for x in gate_view(f)] == [x[0::2] for x in gate_view(g)]
    pf, pg = patterns.summary(f, rules.LIMITS), patterns.summary(g, rules.LIMITS)
    assert pf == pg


@QUICK
@given(MONTHS, st.sampled_from(["%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d %b %Y", "%b %d, %Y"]))
def test_date_format_changes_nothing_when_the_order_is_clear(i, fmt):
    f = iso(month(i))
    g = rewrite(f, DATES, lambda v, n: d.strftime(fmt) if (d := rules._d(v)) else v)
    drop = lambda c: "date" in c or c.endswith(" on")       # noqa: E731
    assert sig(f, rules.analyze(f), drop) == sig(g, rules.analyze(g), drop)
    assert gate_view(f) == gate_view(g)


def _add(files, name, row):
    if name in files:
        R = _rows(files[name])
        R.append([row.get(c, "") for c in R[0]])
        files[name] = _lines(R)


@QUICK
@given(MONTHS)
def test_an_unrelated_vendor_years_away_changes_no_finding(i):
    f = iso(month(i))
    g = dict(f)
    _add(g, "vendors.csv", dict(vendor_id="V-9999", name="Zebulon Quartz Works", tax_id="99-9999999", status="ACTIVE",
                                bank_acct="****1111", bank_verified="N/A", created_on="2019-02-01", w9_on_file="YES",
                                last_paid_on="2019-06-12", last_paid_amount="420.00"))
    for k in range(3):
        _add(g, "payments.csv", dict(payment_id=f"P-ZZ{k}", pay_date=f"2019-06-1{k}", invoice_date=f"2019-06-0{k + 1}",
                                     vendor_id="V-9999", supplier="Zebulon Quartz Works", invoice_no=f"ZQW-{k + 1}",
                                     invoice_amount="420.00", paid_amount="420.00", bank_last4="1111", approved_by="X. Ample"))
        _add(g, "approvals.csv", dict(record_id=f"A-ZZ{k}", type="INVOICE", doc_no=f"ZQW-{k + 1}", date=f"2019-06-0{k + 1}",
                                      vendor="Zebulon Quartz Works", amount="420.00", requested_by="Q. Uill",
                                      approved_by="X. Ample", approver_role="Manager", po_no=f"PO-ZZ{k}"))
        _add(g, "expenses.csv", dict(claim_id=f"E-ZZ{k}", date=f"2019-06-1{k}", employee="Q. Uill", category="OFFICE",
                                     amount="12.00", receipt_ref=f"RC-ZZ{k}", notes="Stationery"))
    assert sig(f, rules.analyze(f), skip=("4.5",)) == sig(g, rules.analyze(g), skip=("4.5",))
    assert gate_view(f) == gate_view(g)


def test_the_new_vendor_check_is_population_based_on_purpose():
    """4.5 is skipped for a set-up date shared by many vendors (an import), so adding vendors can switch it off."""
    vh = "vendor_id,name,tax_id,status,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount"
    new = "V-1,Arden Metals,11-111,ACTIVE,,YES,2026-09-01,YES,2026-09-10,25000.00"
    assert [h.clause for h in rules.vendors([vh, new])] == ["4.5"]
    imported = [f"V-{k},Vendor {k},22-{k:03d},ACTIVE,,YES,2026-09-01,YES,,0" for k in range(2, 7)]
    assert not rules.vendors([vh, new, *imported])


@QUICK
@given(MONTHS)
def test_two_months_together_find_what_each_finds_alone(i):
    """A copy of the month a year earlier, with ids re-keyed (KES-7056 -> KESY-7056), appended to each file."""
    f = iso(month(i))
    keyed = re.compile(r"\b(?!V-)([A-Z]{1,4})-(\d)", re.I)
    early = {n: [f[n][0]] + [keyed.sub(r"\1Y-\2", ln) for ln in v[1:]] for n, v in
             rewrite({n: f[n] for n in ("payments.csv", "approvals.csv", "expenses.csv", "bank_statement.csv") if n in f},
                     DATES, lambda v, n: (rules._d(v) - timedelta(days=364)).isoformat()).items()}
    both = {**f, **{n: f[n] + v[1:] for n, v in early.items()}}
    alone = {**f, **early}
    pop = ("1.6",)
    want = sig(f, rules.analyze(f), skip=pop) + Counter(
        {k: v for k, v in sig(alone, rules.analyze(alone), skip=pop).items() if k[3] in early})
    assert sig(both, rules.analyze(both), skip=pop) == want


def test_findings_and_gate_do_not_depend_on_the_hash_seed():
    code = ("import sys; sys.path.insert(0, '.'); from tests.test_metamorphic import month; from tallyhound import rules, gate\n"
            "for i in (0, 3):\n"
            "    f = month(i); print([(h.clause, h.line_number, h.title, h.related, h.amount) for h in rules.analyze(f)])\n"
            "    print(gate.evaluate(f).to_csv())\n")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("1", "4242")}
    assert len(outs) == 1 and "5.2" in next(iter(outs))
    f = month(5)
    assert [vars(h) for h in rules.analyze(f)] == [vars(h) for h in rules.analyze(f)]


@QUICK
@given(MONTHS)
def test_each_payment_run_line_is_decided_on_its_own(i):
    """HOLD or RELEASE for one line does not depend on the other lines, except the duplicate-in-run check (5)."""
    f = month(i)
    run = f["payment_run.csv"]
    whole = gate.evaluate(f)
    for k in range(1, len(run)):
        alone = gate.evaluate({**f, "payment_run.csv": [run[0], run[k]]}).iloc[0]
        fails = set(whole.iloc[k - 1].failed_checks.split(",")) - {"-", "5"}
        assert set(alone.failed_checks.split(",")) - {"-"} == fails, (i, k)


def test_fresh_challenge_seeds_plant_what_the_key_says_and_spare_every_trap():
    for seed in (6101, 6102, 6103, 6104):
        for diff in ("easy", "medium", "hard"):
            files, key = challenge.generate(seed, diff)
            k = score.key_from_csv(challenge.key_csv(key))
            assert len(k) == len(key), (seed, diff)                      # no two planted rows on the same lines
            for row in k:
                if row["source_file"].endswith(".csv"):
                    assert all(2 <= ln <= len(files[row["source_file"]]) for ln in row["lines"]), (seed, diff, row)
            hits = rules.analyze(files)
            s = score.score([dict(source_file=h.source_file, line_number=h.line_number,
                                  related_lines=[ln for _, ln in h.related]) for h in hits], k)
            assert s["traps_flagged"] == 0 and s["false_alarms"] == 0, (seed, diff, s["trap_kinds"])
            if diff != "hard":
                assert s["recall"] == 1.0, (seed, diff)


@QUICK
@given(MONTHS, st.randoms(use_true_random=False))
def test_patterns_describe_the_amounts_whatever_the_layout(i, rnd):
    f = month(i)
    g = dict(f)
    for n in patterns.SOURCES:
        if n in f:
            body = f[n][1:]
            rnd.shuffle(body)
            R = _rows([f[n][0]] + body)
            g[n] = _lines([r + (["extra_col"] if k == 0 else ["7"]) for k, r in enumerate(R)])
    assert patterns.summary(f, rules.LIMITS) == patterns.summary(g, rules.LIMITS)


# ---------------------------------------------------------------- accessibility of the app's own colours
def _rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{\{?([^}]*)\}", C.CSS)
    assert m, selector
    return m.group(1)


def test_the_source_viewer_sets_its_own_text_colour():
    """Its background is fixed (white, yellow, blue) while the text colour came from the theme: with a dark theme
    configured, the quoted lines were near-white on white (1.04:1)."""
    text = re.search(r"(?<![-\w])color:\s*(#[0-9a-fA-F]{6})", _rule(".th-src"))
    assert text, "the source viewer must set a text colour to go with its background"
    for sel in (".th-src", ".th-row.hit", ".th-row.rel"):
        bg = re.search(r"background:\s*#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", _rule(sel)).group(1)
        bg = "#" + (bg if len(bg) == 6 else "".join(c * 2 for c in bg))
        assert _contrast(text.group(1), bg) >= 4.5 and _contrast(C.MUTED, bg) >= 4.5, sel     # text and line numbers


def test_keyboard_focus_in_the_page_is_clearly_visible():
    """Streamlit's own focus ring is the primary colour at half strength: 2.8:1 on the page, under the 3:1 a focus
    indicator needs, and none at all on the focusable main area. The app draws its own, as it does in the sidebar."""
    m = re.search(r'section\[data-testid="stMain"\][^{]*:focus-visible[^{]*\{\{?([^}]*)\}', C.CSS)
    assert m, "no focus style for the main area"
    colour = re.search(r"outline:\s*\d+px solid\s*(#[0-9a-fA-F]{6})", m.group(1))
    assert colour and _contrast(colour.group(1), C.PAPER) >= 3 and _contrast(colour.group(1), "#ffffff") >= 3


def test_custom_html_has_no_image_without_alt_text():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tallyhound").glob("*.py")) + (ROOT / "app.py").read_text()
    assert all("alt=" in tag for tag in re.findall(r"<img\b[^>]*>", src))
