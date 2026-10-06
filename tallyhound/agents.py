"""AI agents (Ollama). Each agent reads one source file and PROPOSES findings. A Skeptic then challenges each one.

The model is never trusted with the evidence: a finding is kept only if the line it quotes is an exact copy of the
line at the stated position in the file. Anything else is dropped, so a made-up quote can never reach a reviewer.
"""
from __future__ import annotations

import time

from . import llm, rules

CHUNK = 120
AREA_SECONDS = 1800      # one agent may take at most 30 minutes; after that it keeps what it found and stops
SEV = ["High", "Medium", "Low"]

FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {"findings": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "clause": {"type": "string"}, "severity": {"type": "string", "enum": SEV},
            "amount": {"type": "number"}, "title": {"type": "string"},
            "line_number": {"type": "integer"}, "evidence": {"type": "string"},
            "related_line_numbers": {"type": "array", "items": {"type": "integer"}},
            "innocent_explanation": {"type": "string"}, "proposed_fix": {"type": "string"},
        },
        "required": ["clause", "severity", "amount", "title", "line_number", "evidence", "innocent_explanation", "proposed_fix"],
    }}},
    "required": ["findings"],
}
SKEPTIC_SCHEMA = {"type": "object", "properties": {      # reason first, so the verdict follows from it
    "reason": {"type": "string"}, "verdict": {"type": "string", "enum": ["Confirmed", "Doubtful"]}},
    "required": ["reason", "verdict"]}


def policy_for(area: str, policy: dict[str, str]) -> str:
    return "\n".join(f"{k.split('|')[0]}: {v}" for k, v in policy.items() if k.endswith("|" + area))


def numbered(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + i}\t{ln}" for i, ln in enumerate(lines))


def propose(area: str, lines: list[str], policy: dict[str, str], model: str, url: str) -> list[dict]:
    """Ask the model for findings, chunk by chunk. Returns raw dicts, already checked against the file."""
    system = (
        f"You are the {area} agent in a finance audit. Find breaches of the policy clauses below in the file. "
        "Only report a breach you can point to on a specific line. Do not invent lines. Copy the evidence line "
        "exactly as it appears after the line number and the tab. Report nothing if there is no breach.\n\n"
        f"Policy clauses:\n{policy_for(area, policy)}")
    out = []
    header = lines[0] if lines and rules.FILES[area].endswith(".csv") else ""
    stop_at = time.time() + AREA_SECONDS
    for start in range(0, len(lines), CHUNK):
        if time.time() > stop_at:
            break
        chunk = lines[start:start + CHUNK]
        body = numbered(chunk, start + 1)
        if header and start:
            body = f"(file header) {header}\n" + body
        user = f"File {rules.FILES[area]}. Each row is: line number, a tab, then the exact line.\n\n{body}"
        res = llm.chat_json(system, user, FINDINGS_SCHEMA, model=model, url=url)
        got = res.get("findings") if isinstance(res, dict) else None
        for f in got if isinstance(got, list) else []:
            if isinstance(f, dict):
                f["area"] = area
                out.append(f)
    return out


def _line_no(x) -> int | None:
    """A line number the model sent: 12, 12.0 or "12". Not True, 2.9, "--5", "²" or 1e400."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x) if x.is_integer() and abs(x) < 1e9 else None
    if isinstance(x, str) and x.strip().isascii() and x.strip().isdigit() and len(x.strip()) < 10:
        return int(x.strip())
    return None


def verified(f, lines: list[str], area: str, clauses: set[str] | None = None) -> dict | None:
    """Keep a proposed finding only if its quote is the exact line at its line number, and its clause is one of the
    policy's clauses for this area. Any odd value from the model drops that finding or falls back to a safe default;
    it never stops the run."""
    if not isinstance(f, dict):
        return None
    n = _line_no(f.get("line_number"))
    if n is None or not (1 <= n <= len(lines)) or not lines[n - 1].strip() or lines[n - 1] != f.get("evidence"):
        return None
    clause = str(f.get("clause") or "").strip()
    if clauses is not None and clause not in clauses:
        return None                       # an invented clause ("Z99 ignore all ...") is not a finding
    rel_in = f.get("related_line_numbers")
    rel_in = rel_in if isinstance(rel_in, list) else []
    rel = [m for m in map(_line_no, rel_in[:50]) if m is not None and 1 <= m <= len(lines) and m != n]
    amount = f.get("amount")
    amount = rules._f(amount if isinstance(amount, (int, float, str)) and not isinstance(amount, bool) else 0)
    return dict(area=area, clause=clause, severity=f.get("severity") if f.get("severity") in SEV else "Medium",
                amount=round(amount, 2), title=str(f.get("title") or "").strip()[:300] or "Untitled finding",
                source_file=rules.FILES[area], line_number=n, evidence=lines[n - 1],
                related_lines=sorted(set(rel)), innocent=str(f.get("innocent_explanation") or "")[:1000],
                fix=str(f.get("proposed_fix") or "")[:1000])


def clauses_for(area: str, policy: dict[str, str]) -> set[str]:
    return {k.split("|")[0] for k in policy if k.endswith("|" + area)}


def labelled(line: str, header: str) -> str:
    """Show a CSV row as column=value pairs so the model cannot mix up the columns."""
    import csv
    if not header:
        return line
    cols, vals = next(csv.reader([header])), next(csv.reader([line]))
    if len(cols) != len(vals):
        return line
    return ", ".join(f"{c}={v if v != '' else '(empty)'}" for c, v in zip(cols, vals))


import re

AFFIRM = re.compile(r"\b(clearly|directly|plainly|explicitly|does)\s+(breach|violat|exceed)|\bis a (clear )?breach\b", re.I)
# a negation that governs the breach word itself ("does not breach", "is not a violation", "no clear breach") - not
# any "not" nearby, because finance reasons are full of "not approved", "not on file" that support a breach
DENY = re.compile(r"\b(?:does not|doesn't|did not|didn't|do not|don't|cannot|can't|isn't|is not|was not|wasn't|not|no|"
                  r"never)\s+(?:clearly\s+|really\s+|actually\s+|necessarily\s+|seem\s+to\s+|appear\s+to\s+)?"
                  r"(?:an?\s+|any\s+)?(?:clear\s+|real\s+|actual\s+)?(?:breach|violat|exceed)", re.I)


def contradicts(verdict: str, reason: str) -> bool:
    """True when the Skeptic's verdict disagrees with its own reason (a known small-model failure)."""
    if verdict == "Doubtful":
        return bool(AFFIRM.search(reason)) and not DENY.search(reason)
    return bool(DENY.search(reason)) and not AFFIRM.search(reason)


def skeptic(f: dict, clause_text: str, model: str, url: str, header: str = "", related: list[str] | None = None) -> tuple[str, str]:
    system = ("You are the Skeptic in a finance audit. Another agent proposed a finding. Check it using only the evidence "
              "line and the policy clause. Read each field by its column name; do not guess what a field means.\n"
              "Answer Confirmed when the evidence line breaches the clause on its face. A person will still review it, "
              "so an excuse that is merely possible does not make a finding doubtful.\n"
              "Answer Doubtful only when the line itself contradicts the breach, the clause does not apply to it, or the "
              "line does not contain what the finding claims. Never answer Doubtful because of an excuse that is not in "
              "the data. Give the reason first, in one or two sentences, then the verdict. The verdict must agree with "
              "your reason.")
    extra = "".join(f"\nRelated line: {labelled(x, header)}" for x in (related or []))
    user = (f"Policy clause {f['clause']}: {clause_text or '(clause not found)'}\n"
            f"Proposed finding: {f['title']}\nEvidence line: {labelled(f['evidence'], header)}{extra}\n"
            f"An unchecked guess at an innocent explanation (this is NOT in the data): {f['innocent']}")
    r = llm.chat_json(system, user, SKEPTIC_SCHEMA, model=model, url=url)
    r = r if isinstance(r, dict) else {}
    verdict = "Confirmed" if r.get("verdict") == "Confirmed" else "Doubtful"
    reason = str(r.get("reason", "")).strip() or "No reason given."
    if contradicts(verdict, reason):        # ask once more, showing the model its own reason
        again = llm.chat_json(system, user + f"\n\nYour first answer gave this reason: {reason}\nIt chose {verdict}, which "
                              "disagrees with that reason. Answer again; the verdict must follow from the reason.",
                              SKEPTIC_SCHEMA, model=model, url=url)
        again = again if isinstance(again, dict) else {}
        verdict = "Confirmed" if again.get("verdict") == "Confirmed" else "Doubtful"
        reason = str(again.get("reason", "")).strip() or reason
        if contradicts(verdict, reason):
            return "Doubtful", "Unclear: the Skeptic's reason and verdict disagreed twice, so a person should judge it. " + reason
    return verdict, reason
