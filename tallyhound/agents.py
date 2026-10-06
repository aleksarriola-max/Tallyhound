"""AI agents (Ollama). Each agent reads one source file and PROPOSES findings. A Skeptic then challenges each one.

The model is never trusted with the evidence: a finding is kept only if the line it quotes is an exact copy of the
line at the stated position in the file. Anything else is dropped, so a made-up quote can never reach a reviewer.
"""
from __future__ import annotations

from . import llm, rules

CHUNK = 120
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
SKEPTIC_SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["Confirmed", "Doubtful"]}, "reason": {"type": "string"}},
    "required": ["verdict", "reason"]}


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
    for start in range(0, len(lines), CHUNK):
        chunk = lines[start:start + CHUNK]
        body = numbered(chunk, start + 1)
        if header and start:
            body = f"(file header) {header}\n" + body
        user = f"File {rules.FILES[area]}. Each row is: line number, a tab, then the exact line.\n\n{body}"
        res = llm.chat_json(system, user, FINDINGS_SCHEMA, model=model, url=url)
        for f in res.get("findings", []):
            f["area"] = area
            out.append(f)
    return out


def verified(f: dict, lines: list[str], area: str) -> dict | None:
    """Keep a proposed finding only if its quote is the exact line at its line number."""
    try:
        n = int(f["line_number"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (1 <= n <= len(lines)) or lines[n - 1] != f.get("evidence"):
        return None
    clause = str(f.get("clause", "")).strip()
    rel = [x for x in f.get("related_line_numbers", []) if isinstance(x, int) and 1 <= x <= len(lines) and x != n]
    return dict(area=area, clause=clause, severity=f.get("severity") if f.get("severity") in SEV else "Medium",
                amount=float(f.get("amount") or 0), title=str(f.get("title", "")).strip() or "Untitled finding",
                source_file=rules.FILES[area], line_number=n, evidence=lines[n - 1],
                related_lines=sorted(set(rel)), innocent=str(f.get("innocent_explanation", "")),
                fix=str(f.get("proposed_fix", "")))


def labelled(line: str, header: str) -> str:
    """Show a CSV row as column=value pairs so the model cannot mix up the columns."""
    import csv
    if not header:
        return line
    cols, vals = next(csv.reader([header])), next(csv.reader([line]))
    if len(cols) != len(vals):
        return line
    return ", ".join(f"{c}={v if v != '' else '(empty)'}" for c, v in zip(cols, vals))


def skeptic(f: dict, clause_text: str, model: str, url: str, header: str = "", related: list[str] | None = None) -> tuple[str, str]:
    system = ("You are the Skeptic in a finance audit. Another agent proposed a finding. Try to disprove it using only "
              "the evidence line and the policy clause. Read each field by its column name; do not guess what a field means. Answer Confirmed only if the evidence line clearly breaches the "
              "clause. Answer Doubtful if the breach is not clear from that line, or there is an obvious innocent reason.")
    extra = "".join(f"\nRelated line: {labelled(x, header)}" for x in (related or []))
    user = (f"Policy clause {f['clause']}: {clause_text or '(clause not found)'}\n"
            f"Proposed finding: {f['title']}\nEvidence line: {labelled(f['evidence'], header)}{extra}\n"
            f"Possible innocent explanation: {f['innocent']}")
    r = llm.chat_json(system, user, SKEPTIC_SCHEMA, model=model, url=url)
    verdict = "Confirmed" if r.get("verdict") == "Confirmed" else "Doubtful"
    return verdict, str(r.get("reason", "")).strip() or "No reason given."
