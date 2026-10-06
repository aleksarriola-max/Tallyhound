"""Scorecard: grade proposed findings against an answer key of planted problems.

A proposed finding counts as a hit when it points at the same file and at least one of the same lines (its main
line or a related line) as a planted problem. Recall is the share of planted problems found; precision is the share
of proposed findings that point at a planted problem.
"""
from __future__ import annotations

import csv
import io


def key_from_csv(text: str) -> list[dict]:
    out, seen = [], set()
    for r in csv.DictReader(io.StringIO(text)):
        try:
            main = int(r["line_number"])
        except (KeyError, ValueError):
            continue
        rel = [int(x) for x in str(r.get("related_lines", "")).replace(",", ";").split(";") if x.strip().isdigit()]
        row = dict(id=r.get("id", ""), clause=r.get("clause", ""), area=r.get("area", ""),
                   source_file=(r.get("source_file") or "").strip(), lines=sorted({main, *rel}),
                   description=r.get("description", ""), expect=(r.get("expect") or "problem").strip().lower())
        sig = (row["source_file"], tuple(row["lines"]), row["expect"])
        if sig not in seen:                      # the same planted row twice would count twice
            seen.add(sig)
            out.append(row)
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


def _credit(proposed: list[dict], key: list[dict]) -> list[bool]:
    """Which planted problems were found. Each proposed finding is credited to at most one planted problem - the one
    on its own line if there is one - so a single finding with a long list of related lines cannot "find" them all."""
    found = [False] * len(key)
    for p in proposed:
        hits = [i for i, k in enumerate(key) if matches(p, k)]
        if not hits:
            continue
        main = [i for i in hits if p["source_file"] == key[i]["source_file"] and int(p["line_number"]) in key[i]["lines"]]
        pick = next((i for i in main + hits if not found[i]), None)
        if pick is not None:
            found[pick] = True
    return found


def score(proposed: list[dict], key: list[dict]) -> dict:
    """proposed: dicts with source_file, line_number, related_lines and optionally verdict.
    Key rows marked expect="trap" are legitimate look-alikes: a finding on one is a false alarm, counted separately."""
    traps = [k for k in key if str(k.get("expect", "")).lower() == "trap"]
    key = [k for k in key if str(k.get("expect", "")).lower() != "trap"]
    trap_hit = [k for k in traps if any(matches(p, k) for p in proposed)]
    found = _credit(proposed, key)
    true = [any(matches(p, k) for k in key) for p in proposed]
    out = dict(planted=len(key), proposed=len(proposed), found=sum(found), true_pos=sum(true),
               false_alarms=len(proposed) - sum(true),
               recall=sum(found) / len(key) if key else 0.0,
               precision=sum(true) / len(proposed) if proposed else 0.0,
               found_ids=[k["id"] for k, f in zip(key, found) if f],
               traps=len(traps), traps_flagged=len(trap_hit), trap_ids=[k["id"] for k in trap_hit],
               trap_kinds=sorted({k["description"].split(")")[0].replace("TRAP (", "") for k in trap_hit}))
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
    """One row per planted problem or trap, with what each run did with it."""
    rows = []
    for k in key:
        trap = str(k.get("expect", "")).lower() == "trap"
        row = {"Planted": k["description"], "Kind": "Trap (legitimate)" if trap else "Problem", "Clause": k["clause"],
               "File": k["source_file"], "Line": k["lines"][0] if k["lines"] else ""}
        for name, proposed in runs.items():
            hit = next((p for p in proposed if matches(p, k)), None)
            if trap:
                row[name] = "Left alone" if hit is None else "FALSE ALARM"
            else:
                row[name] = "Missed" if hit is None else ("Found (doubted)" if hit.get("verdict") == "Doubtful" else "Found")
        rows.append(row)
    return rows
