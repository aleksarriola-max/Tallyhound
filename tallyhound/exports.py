"""Excel workbook and PDF memo builders."""
from __future__ import annotations

import html
import io
from datetime import datetime

import pandas as pd
import streamlit as st
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import common as C

MEMO_ROWS = 200          # per section; the workbook lists everything


def decisions_frame(draft: bool = False) -> pd.DataFrame:
    f = C.findings()
    d = st.session_state.decisions
    out = f[["id", "severity", "area", "clause", "amount", "title", "skeptic_verdict"]].copy()
    if draft:
        out["decision"], out["reviewer_reason"] = "Not reviewed (draft)", ""
    else:
        from . import triage
        shadow = set(triage.shadow_clauses())
        out["decision"] = ["Shadow (not in review)" if str(c) in shadow else d.get(i, {}).get("status", "Pending")
                           for i, c in zip(f.id, f.clause)]
        out["reviewer_reason"] = [d.get(i, {}).get("reason", "") for i in f.id]
    notes = st.session_state.get("notes", {})
    out["owner"] = [notes.get(i, {}).get("owner", "") for i in f.id]
    out["note"] = [notes.get(i, {}).get("note", "") for i in f.id]
    return out


def gate_frame() -> pd.DataFrame:
    from . import pages
    df = next(iter(pages.gate_runs().values()))[0].copy()
    if df.empty:
        return df
    cleared = st.session_state.cleared
    df["final_decision"] = [("RELEASE (hold cleared)" if str(l) in cleared else d) for l, d in zip(df.line, df.decision)]
    df["clear_reason"] = [cleared.get(str(l), "") for l in df.line]
    return df[["line", "supplier", "invoice", "amount", "decision", "final_decision", "reason", "failed_checks", "clear_reason"]]


def _style(ws) -> None:
    head = PatternFill("solid", start_color="0A3A4F")
    for cell in ws[1]:
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), head
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for i, col in enumerate(ws.columns, start=1):
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)
        name = str(ws.cell(1, i).value or "").lower()
        if any(w in name for w in ("amount", "value", "claim", "cost", "saving")):    # money: two decimals everywhere
            for c in col[1:]:
                if isinstance(c.value, (int, float)) and not isinstance(c.value, bool):
                    c.number_format = "#,##0.00"
    ws.freeze_panes = "A2"


def build_workbook(draft: bool = False) -> bytes:
    f = decisions_frame(draft)
    dc = C.decision_counts(C.findings())
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = pd.DataFrame([
        ["Status", "DRAFT - not reviewed" if draft else
         ("Reviewed by a person" if dc["pending"] == 0 else
          f"PARTLY REVIEWED - {dc['pending']} of {len(f) - dc['shadow']} findings still pending")],
        ["Company", f"Uploaded files: {C.custom_label()}" if C.custom_label() else "Bramblecourt Instruments Ltd (fictional)"],
        ["Notice", "Findings proposed by agents or rules and decided by a person" if C.custom_label()
         else "FICTIONAL TEST DATA - not a real company"],
        ["Generated", now],
        ["Findings", len(f)],
        ["Approved", "-" if draft else dc["approved"]],
        ["Rejected", "-" if draft else dc["rejected"]],
        ["Pending", len(f) if draft else dc["pending"]],
        ["Approved value", "-" if draft else round(dc["value"], 2)],
        ["Audit trail", _trail_word()],
    ], columns=["Item", "Value"])
    trail = C.full_trail(C.findings())
    f, trail = _printable(f), _printable(trail)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        f.to_excel(xw, sheet_name="Monthly audit", index=False)
        g = _printable(gate_frame())
        if not g.empty and (not C.custom_label() or "payment_run.csv" in st.session_state.uploads.get(C.custom_label(), {})):
            g.to_excel(xw, sheet_name="Payment gate", index=False)
        if not C.custom_label():
            C.recovery().to_excel(xw, sheet_name="Supplier recovery", index=False)
            C.subscriptions().to_excel(xw, sheet_name="Subscriptions", index=False)
        trail.to_excel(xw, sheet_name="Audit trail", index=False)
        for ws in xw.book.worksheets:
            defuse_formulas(ws)
            _style(ws)
    return buf.getvalue()


def _printable(df: pd.DataFrame) -> pd.DataFrame:
    """Control characters from an uploaded file (a stray \x01) cannot be stored in a workbook; they are dropped."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
            out[col] = [ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in out[col]]
    return out


def _trail_word() -> str:
    S = st.session_state
    ok, _ = C.verify_trail(S.audit_log)
    if not ok:
        return "BROKEN - entries changed, removed or replaced"
    if C.decisions_mismatch(S.audit_log, S.decisions):
        return "BROKEN - decisions do not match the trail"
    return (f"Intact: {len(S.audit_log)} reviewer entries for this data, chained and sealed, decisions match. "
            "Agent rows above them are the run's own record and are not sealed.")


def defuse_formulas(ws) -> None:
    """Text from uploaded files that starts with '=' must stay text. Otherwise a supplier named
    '=HYPERLINK("http://...")' becomes a live formula when the reviewer opens the workbook."""
    for row in ws.iter_rows():
        for c in row:
            if c.data_type == "f":
                c.data_type = "s"


def build_memo() -> bytes:
    f = decisions_frame()
    dc = C.decision_counts(C.findings())
    ss = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8.5, leading=11)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Tallyhound audit memo")
    story = [
        Paragraph(f"Uploaded files: {html.escape(ds_label)}" if (ds_label := C.custom_label()) else "FICTIONAL TEST DATA - not a real company", ParagraphStyle("b", parent=ss["Normal"], textColor=colors.HexColor("#0a3a4f"), fontSize=9)),
        Spacer(1, 4),
        Paragraph("Tallyhound audit memo", ss["Title"]),
        Paragraph(("Uploaded data - " if C.custom_label() else "Bramblecourt Instruments Ltd (fictional) - ") + datetime.now().strftime("%Y-%m-%d"), ss["Normal"]),
        Spacer(1, 8),
        Paragraph((f"Findings: {len(f)}, all decided by a person. " if dc["pending"] == 0 else
                   f"Findings: {len(f)}. NOT FINISHED: {dc['pending']} are still pending review. ") + f"Approved: {dc['approved']} ({C.money(dc['value'])}). "
                  f"Rejected: {dc['rejected']}. Pending: {dc['pending']}. "
                  "Agents only proposed these findings; every decision recorded here was made by a person.", ss["Normal"]),
        Spacer(1, 8),
    ]
    from . import pages
    run_name, (gdf, clearable) = next(iter(pages.gate_runs().items()))   # the same run the Payment run tab shows
    if not gdf.empty and (not C.custom_label() or run_name.startswith("Uploaded")):
        gt = C.gate_totals(gdf, st.session_state.cleared if clearable else None)
        story += [Paragraph(f"Payment gate ({html.escape(run_name)}): {gt['hold_n']} lines on HOLD "
                            f"({C.money(gt['hold_amt'])}), "
                            f"{gt['rel_n']} lines to RELEASE ({C.money(gt['rel_amt'])})"
                            + (f", including {gt['cleared_n']} hold(s) cleared by a reviewer." if gt["cleared_n"] else "."),
                            ss["Normal"]), Spacer(1, 8)]
    for label in ("Approved", "Rejected", "Pending"):
        part = f[f.decision == label]
        story.append(Paragraph(f"{label} ({len(part)})", ss["Heading3"]))
        if part.empty:
            story += [Paragraph("None.", small), Spacer(1, 4)]
            continue
        rows = [["ID", "Severity", "Area", "Amount", "Finding"]]
        shown = part.sort_values("amount", ascending=False).head(MEMO_ROWS)
        for r in shown.itertuples():
            rows.append([r.id, r.severity, r.area, C.money(r.amount), Paragraph(html.escape(str(r.title)), small)])
        t = Table(rows, colWidths=[14 * mm, 18 * mm, 22 * mm, 24 * mm, 98 * mm], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a3a4f")),
                               ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b3e0f7"))]))
        story += [t, Spacer(1, 6)]
        if len(part) > MEMO_ROWS:
            story += [Paragraph(f"... and {len(part) - MEMO_ROWS} more (the {MEMO_ROWS} largest are listed; the workbook "
                                "has every finding).", small), Spacer(1, 6)]
    story += [Spacer(1, 14), Paragraph("Reviewed by: ______________________   Date: ______________", ss["Normal"])]
    doc.build(story)
    return buf.getvalue()
