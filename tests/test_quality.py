"""Quality gates. These run in CI on every push and fail the build if the rules get noisier or miss more.

Challenges are fictional months with planted problems AND planted traps (legitimate look-alikes: batch transfers,
rent without a PO, discounts, instalments, voids, day-first dates, name variants...). A finding on a trap is a
false alarm. The thresholds below are the bar; raise them as the rules improve, never lower them quietly.
"""
import json
from collections import Counter
from pathlib import Path

import pytest

from tallyhound import challenge, gate, rules, score

CASES = Path(__file__).resolve().parent / "cases"
SEEDS = range(1, 11)
MIN_RECALL = {"easy": 0.99, "medium": 0.98, "hard": 0.90}
MIN_PRECISION = 0.95
MAX_FALSE_ALARMS_PER_100_ROWS = 0.5


def _run(seed, diff):
    files, key = challenge.generate(seed, diff)
    hits = rules.analyze(files)
    prop = [dict(source_file=h.source_file, line_number=h.line_number, related_lines=[ln for _, ln in h.related]) for h in hits]
    rows = sum(len(v) - 1 for k, v in files.items() if k.endswith(".csv"))
    return score.score(prop, score.key_from_csv(challenge.key_csv(key))), rows, hits


@pytest.mark.parametrize("diff", ["easy", "medium", "hard"])
def test_quality_gate(diff):
    recall, precision, fa, rows, trapped = [], [], 0, 0, Counter()
    for seed in SEEDS:
        s, n, _ = _run(seed, diff)
        recall.append(s["recall"])
        precision.append(s["precision"])
        fa += s["false_alarms"]
        rows += n
        trapped.update(s["trap_kinds"])
    assert not trapped, f"rules flagged legitimate look-alikes (traps): {dict(trapped)}"
    assert sum(recall) / len(recall) >= MIN_RECALL[diff], f"recall {sum(recall) / len(recall):.3f}"
    assert sum(precision) / len(precision) >= MIN_PRECISION, f"precision {sum(precision) / len(precision):.3f}"
    assert 100 * fa / rows <= MAX_FALSE_ALARMS_PER_100_ROWS, f"{100 * fa / rows:.2f} false alarms per 100 rows"


@pytest.mark.parametrize("seed", range(1, 6))
def test_payment_gate_holds_exactly_the_bad_lines(seed):
    files, _ = challenge.generate(seed, "medium")
    df = gate.evaluate(files)
    assert sorted(df[df.decision == "HOLD"].line.astype(int)) == [2, 3, 7, 8]   # line 9 is a name variant: release


def test_without_traps_nothing_changes_for_problems():
    for seed in (1, 2, 3):
        f, k = challenge.generate(seed, "medium", traps=False)
        hits = rules.analyze(f)
        prop = [dict(source_file=h.source_file, line_number=h.line_number, related_lines=[ln for _, ln in h.related]) for h in hits]
        s = score.score(prop, score.key_from_csv(challenge.key_csv(k)))
        assert s["recall"] == 1 and s["false_alarms"] == 0


def _cases():
    return sorted(CASES.glob("*.json"))


@pytest.mark.parametrize("path", _cases(), ids=lambda p: p.stem)
def test_regression_case(path):
    """Each file in tests/cases is a real false alarm (or miss) someone reported, saved from the Review page."""
    case = json.loads(path.read_text(encoding="utf-8"))
    hits = rules.analyze(case["files"], case.get("limits"))
    at = {(h.source_file, h.line_number, h.clause) for h in hits} | \
         {(f, ln, h.clause) for h in hits for f, ln in h.related}
    for x in case.get("must_not_flag", []):
        bad = [h for h in hits if h.source_file == x["source_file"] and h.line_number == x["line"]
               and (not x.get("clause") or str(h.clause) == str(x["clause"]))]
        assert not bad, f"{path.name}: {x} was flagged again: {[h.title for h in bad]} ({case.get('description', '')})"
    for x in case.get("must_flag", []):
        assert any((x["source_file"], x["line"], c) in at for c in [x.get("clause")] if c) or \
            any(h.source_file == x["source_file"] and h.line_number == x["line"] for h in hits), f"{path.name}: {x} was missed"


# ---- the review-queue guardrails
def test_cases_group_findings_that_share_lines():
    import pandas as pd
    from tallyhound import triage
    f = pd.DataFrame([dict(id="F-1", severity="Medium", amount=13000.0, skeptic_verdict="Confirmed", source_file="a.csv",
                           line_number=9, matched_lines=[7, 8, 9], clause="1.4", title="split"),
                      dict(id="F-2", severity="Medium", amount=4600.0, skeptic_verdict="Confirmed", source_file="a.csv",
                           line_number=7, matched_lines=[7], clause="1.1", title="no PO"),
                      dict(id="F-3", severity="High", amount=50.0, skeptic_verdict="Doubtful", source_file="b.csv",
                           line_number=7, matched_lines=[7], clause="5.2", title="other file")])
    groups = triage.cases(f)
    assert sorted(len(g) for g in groups) == [1, 2]
    big = next(g for g in groups if len(g) == 2)
    assert big[0].id == "F-1"                         # the strongest finding leads the case


def test_decimal_commas_and_chosen_date_order():
    assert rules._f("1.234,50") == 1234.5 and rules._f("€2.000,00") == 2000.0 and rules._f("1,234.50") == 1234.5
    lines = ["date,description,amount,tallyhound_dayfirst", "03/09/2026,X,1.00"]
    assert str(rules.rows(lines)[0][1].d("date")) == "2026-09-03"
    assert rules.date_order(["d", "03/09/2026", "05/10/2026"]) == "ambiguous"
    assert rules.date_order(["d", "25/09/2026"]) == "day-first"


def test_anonymiser_keeps_structure_and_hides_names(tmp_path):
    import subprocess
    import sys
    files, _ = challenge.generate(2, "medium")
    src = tmp_path / "src"
    src.mkdir()
    for n, v in files.items():
        (src / n).write_text("\n".join(v) + "\n")
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "anonymise.py"), str(src),
                    "--out", str(tmp_path / "out")], check=True, capture_output=True)
    out = {p.name: p.read_text().splitlines() for p in (tmp_path / "out").iterdir()}
    some_vendor = rules.rows(files["vendors.csv"])[0][1]["name"]
    assert some_vendor not in "\n".join(out["vendors.csv"])
    before = [(h.clause, h.source_file, h.line_number) for h in rules.analyze(files)]
    after = [(h.clause, h.source_file, h.line_number) for h in rules.analyze(out)]
    assert sorted(before) == sorted(after)            # same findings on the same lines
