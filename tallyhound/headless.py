"""Run the built-in checks without the app - for a scheduled job that watches a folder.

    python scripts/watch.py "C:/Finance/AP inbox"            # report only
    python scripts/watch.py "C:/Finance/AP inbox" --alert    # also send an alert for new problems

Writes a dated report (Markdown + CSV) into <folder>/tallyhound-reports/. With --alert it posts to Slack and/or
sends an email when there are findings that were not in the previous report, or payment-run lines on HOLD.
Nothing is ever approved or released here: the report is a to-do list for a person.

Alert settings come from environment variables:
    TALLYHOUND_SLACK_WEBHOOK   a Slack incoming-webhook URL
    TALLYHOUND_SMTP_HOST, TALLYHOUND_SMTP_PORT (587), TALLYHOUND_SMTP_USER, TALLYHOUND_SMTP_PASSWORD,
    TALLYHOUND_MAIL_FROM, TALLYHOUND_MAIL_TO (comma-separated)
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import smtplib
import urllib.request
import zipfile
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from . import gate, rules

REPORT_DIR = "tallyhound-reports"


def load_folder(folder: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Read the audit files (and invoice PDFs) from a folder, or from the newest zip in it."""
    from . import custom
    zips = sorted(folder.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in folder.rglob("*"):
            if p.is_file() and REPORT_DIR not in p.parts and p.suffix.lower() in (".csv", ".txt", ".pdf"):
                z.write(p, p.relative_to(folder).as_posix())
    files, notes = custom.parse_zip(buf.getvalue())
    if not any(n in files for n in rules.FILES.values()) and zips:
        files, notes = custom.parse_zip(zips[-1].read_bytes())
        notes.insert(0, f"Read {zips[-1].name}")
    files.pop("answer_key.csv", None)
    return files, notes


def fingerprint(h) -> str:
    return hashlib.sha256(f"{h.clause}|{h.source_file}|{h.title}".encode()).hexdigest()[:16]


def run(folder: Path, limits: dict | None = None) -> dict:
    files, notes = load_folder(folder)
    hits = rules.analyze(files, limits)
    g = gate.evaluate(files)
    return dict(files=files, notes=notes, hits=hits, gate=g)


def write_report(folder: Path, res: dict, remember: bool = True) -> tuple[Path, list]:
    out = folder / REPORT_DIR
    out.mkdir(exist_ok=True)
    state_file = out / "last.json"
    before = set(json.loads(state_file.read_text())["fingerprints"]) if state_file.exists() else set()
    hits = res["hits"]
    new = [h for h in hits if fingerprint(h) not in before]
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    with open(out / f"findings_{stamp}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["new", "severity", "area", "clause", "amount", "title", "file", "line", "evidence"])
        for h in hits:
            lines = res["files"].get(h.source_file, [])
            w.writerow(["yes" if h in new else "", h.severity, h.area, h.clause, f"{h.amount:.2f}", h.title, h.source_file,
                        h.line_number, lines[h.line_number - 1] if 0 < h.line_number <= len(lines) else ""])
    g = res["gate"]
    held = g[g.decision == "HOLD"] if not g.empty else g
    md = [f"# Tallyhound report {stamp.replace('_', ' ')}", "",
          f"{len(hits)} findings ({len(new)} new). " + (f"Payment run: {len(held)} of {len(g)} lines on HOLD." if not g.empty else ""),
          "", "Nothing here has been approved or released. A person reviews every item.", ""]
    problems = data_problems(res)
    if problems:
        md += ["## Data that was NOT checked", "", "Fix these first: findings below do not cover them.", ""] + \
              [f"- {n}" for n in problems] + [""]
    for sev in ("High", "Medium", "Low"):
        part = [h for h in hits if h.severity == sev]
        if part:
            md += [f"## {sev} ({len(part)})", ""] + [f"- {'NEW ' if h in new else ''}[{h.clause}] {h.title} "
                                                    f"({h.source_file} line {h.line_number})" for h in part] + [""]
    if not g.empty and len(held):
        md += ["## Payment run lines on HOLD", ""] + [f"- Line {r.line}: {r.supplier} {r.invoice} ${r.amount:,.2f} - {r.reason}"
                                                     for r in held.itertuples()] + [""]
    if res["notes"]:
        md += ["## Notes", ""] + [f"- {n}" for n in res["notes"]]
    path = out / f"report_{stamp}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    if remember:
        remember_hits(folder, hits)
    return path, new


def remember_hits(folder: Path, hits: list) -> None:
    """Record these findings as already reported, so the next run alerts only on new ones. Call it only once the alert
    went out: if it failed, the next run must alert again."""
    (folder / REPORT_DIR / "last.json").write_text(json.dumps(
        {"fingerprints": [fingerprint(h) for h in hits], "time": datetime.now().strftime("%Y-%m-%d_%H%M")}))


PROBLEM_NOTES = ("missing columns", "Skipped", "Stopped at", "could not", "no readable text", "Two files", "not a valid zip")


def data_problems(res: dict) -> list[str]:
    """Notes that mean some data was not checked. A scheduled job must not treat these as an all-clear."""
    out = [n for n in res["notes"] if any(k in n for k in PROBLEM_NOTES)]
    if res["files"].get("payment_run.csv") and res["gate"].empty:
        out.append("payment_run.csv could not be checked (columns line, supplier, invoice and amount are needed)")
    if not any(k.endswith((".csv", ".txt")) and k != "answer_key.csv" for k in res["files"]):
        out.append("No audit files were found in the folder")
    return out


def alert_text(new: list, held: int, report: Path, problems: list[str] | None = None) -> str:
    high = sum(h.severity == "High" for h in new)
    head = (f"Tallyhound: {len(new)} new finding(s), {high} High" + (f"; {held} payment line(s) on HOLD" if held else "")
            + (f"; {len(problems)} data problem(s) - some files were NOT checked" if problems else ""))
    lines = [f"- [{h.severity}] {h.title}" for h in new[:10]] + [f"- NOT CHECKED: {p}" for p in (problems or [])[:5]]
    return "\n".join([head, *lines, f"Report: {report}", "Nothing was approved or released - please review."])


def send_alert(text: str, errors: list[str] | None = None) -> list[str]:
    """Send to every channel that is configured. Returns the channels that worked; a channel that fails does not stop
    the others, and its error is added to errors."""
    done = []
    errors = errors if errors is not None else []
    hook = os.environ.get("TALLYHOUND_SLACK_WEBHOOK")
    if hook:
        try:
            req = urllib.request.Request(hook, data=json.dumps({"text": text}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read()  # noqa: S310  (the operator's own webhook)
            done.append("Slack")
        except (OSError, ValueError) as e:
            errors.append(f"Slack: {type(e).__name__}: {e}")
    host, to = os.environ.get("TALLYHOUND_SMTP_HOST"), os.environ.get("TALLYHOUND_MAIL_TO")
    if host and to:
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = text.splitlines()[0], os.environ.get("TALLYHOUND_MAIL_FROM", "tallyhound@localhost"), to
            msg.set_content(text)
            with smtplib.SMTP(host, int(os.environ.get("TALLYHOUND_SMTP_PORT", "587")), timeout=20) as s:
                s.starttls()
                if os.environ.get("TALLYHOUND_SMTP_USER"):
                    s.login(os.environ["TALLYHOUND_SMTP_USER"], os.environ.get("TALLYHOUND_SMTP_PASSWORD", ""))
                s.send_message(msg)
            done.append("email")
        except (OSError, ValueError, smtplib.SMTPException) as e:
            errors.append(f"email: {type(e).__name__}: {e}")
    return done
