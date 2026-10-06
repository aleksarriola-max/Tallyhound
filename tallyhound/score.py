"""Scorecard: grade proposed findings against an answer key of planted problems.

A proposed finding counts as a hit when it points at the same file and at least one of the same lines (its main
line or a related line) as a planted problem. Recall is the share of planted problems found; precision is the share
of proposed findings that point at a planted problem.
"""
from __future__ import annotations

import csv
import io


def key_from_csv(text: str) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            main = int(r["line_number"])
        except (KeyError, ValueError):
            continue
        rel = [int(x) for x in str(r.get("related_lines", "")).replace(",", ";").split(";") if x.strip().isdigit()]
        out.append(dict(id=r.get("id", ""), clause=r.get("clause", ""), area=r.get("area", ""),
                        source_file=r["source_file"].strip(), lines=sorted({main, *rel}), description=r.get("description", "")))
    return out


def key_from_findings(df, lines_of) -> list[dict]:
    """The sample company's 26 findings as an answer key."""
    out = []
    for r in df.itertuples():
        lines = lines_of(r.source_file)
        rel = [i + 1 for t in str(r.related_evidence).split(" || ") if t and t != "nan" for i, ln in enumerate(lines) if ln == t]
        out.append(dict(id=r.id, clause=str(r.clause), area=r.area, source_file=r.source_file,
                        lines=sorted({int(r.line_number), *rel}), description=r.title))
    return out


def _lines(p: dict) -> set[int]:
    return {int(p["line_number"]), *[int(x) for x in p.get("related_lines", [])]}


def matches(p: dict, k: dict) -> bool:
    return p["source_file"] == k["source_file"] and bool(_lines(p) & set(k["lines"]))


def score(proposed: list[dict], key: list[dict]) -> dict:
    """proposed: dicts with source_file, line_number, related_lines and optionally verdict."""
    found = [any(matches(p, k) for p in proposed) for k in key]
    true = [any(matches(p, k) for k in key) for p in proposed]
    out = dict(planted=len(key), proposed=len(proposed), found=sum(found), true_pos=sum(true),
               false_alarms=len(proposed) - sum(true),
               recall=sum(found) / len(key) if key else 0.0,
               precision=sum(true) / len(proposed) if proposed else 0.0,
               found_ids=[k["id"] for k, f in zip(key, found) if f])
    judged = [(p, t) for p, t in zip(proposed, true) if p.get("verdict") in ("Confirmed", "Doubtful")]
    if judged and any("rule check" not in str(p.get("reason", "")).lower() for p, _ in judged):   # a real Skeptic ran
        conf = [(p, t) for p, t in judged if p["verdict"] == "Confirmed"]
        out["skeptic"] = dict(
            confirmed=len(conf),
            precision_confirmed=sum(t for _, t in conf) / len(conf) if conf else 0.0,
            recall_confirmed=sum(any(matches(p, k) for p, _ in conf) for k in key) / len(key) if key else 0.0,
            real_doubted=sum(1 for p, t in judged if t and p["verdict"] == "Doubtful"),
            false_alarms_caught=sum(1 for p, t in judged if not t and p["verdict"] == "Doubtful"))
    return out


def per_issue(key: list[dict], runs: dict[str, list[dict]]) -> list[dict]:
    """One row per planted problem with Found / Missed for each run."""
    rows = []
    for k in key:
        row = {"Planted problem": k["description"], "Clause": k["clause"], "File": k["source_file"],
               "Line": k["lines"][0] if k["lines"] else ""}
        for name, proposed in runs.items():
            hit = next((p for p in proposed if matches(p, k)), None)
            row[name] = "Missed" if hit is None else ("Found (doubted)" if hit.get("verdict") == "Doubtful" else "Found")
        rows.append(row)
    return rows
