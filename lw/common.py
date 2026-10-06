"""Shared helpers: data loading, quote verification, styling, session state."""
from __future__ import annotations

import html
import inspect
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SRC = DATA / "source"

TEAL_DARK, TEAL, INK, SKY, PAPER = "#0a3a4f", "#12a1b2", "#07161f", "#b3e0f7", "#f2f9ff"
SEV_COLOR = {"High": "#9f2a2f", "Medium": "#a8680f", "Low": "#12a1b2"}
HOLD, RELEASE = "#9f2a2f", "#2b7a55"
MUTED = "#5b6b73"

NAV = ["Run analysis", "Overview", "Findings", "Payment gate", "Recovery",
       "Subscriptions", "Live activity", "Evidence viewer", "Guardrails"]
STEPS = ["1  Choose data (Folder or zip)", "2  Run (Seven agents)",
         "3  Review (Approve / reject)", "4  Download (Excel and memo)"]


# ---- widget helpers that work on old and new Streamlit ----
def _stretch() -> dict:
    return {"width": "stretch"} if "width" in inspect.signature(st.button).parameters else {"use_container_width": True}


def bw() -> dict:
    """Keyword args that make a button/download button fill its column."""
    return _stretch()


def dfw() -> dict:
    return {"width": "stretch"} if "width" in inspect.signature(st.dataframe).parameters else {"use_container_width": True}


def esc(text: str) -> str:
    """Escape $ so Streamlit markdown does not treat amounts as LaTeX."""
    return str(text).replace("$", "\\$")


def money(x: float) -> str:
    return f"${x:,.2f}"


# ---- data ----
@st.cache_data
def read_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA / name, dtype=str, keep_default_na=False)


@st.cache_data
def read_source(name: str) -> list[str]:
    return (SRC / name).read_text(encoding="utf-8").splitlines()


@st.cache_data
def policy() -> dict:
    df = read_csv("policy.csv")
    return {r.clause + "|" + r.area: r.text for r in df.itertuples()}


def clause_text(area: str, clause: str) -> str:
    return policy().get(f"{clause}|{area}", "")


@st.cache_data
def load_findings_checked() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (verified findings, hidden findings). Quotes are checked against the real files."""
    df = read_csv("findings.csv").copy()
    df["amount"] = df["amount"].astype(float)
    df["line_number"] = df["line_number"].astype(int)
    keep, matched_lines, matched_n = [], [], []
    for r in df.itertuples():
        lines = read_source(r.source_file)
        main = [i + 1 for i, ln in enumerate(lines) if ln == r.evidence]
        rel_txt = [x for x in r.related_evidence.split(" || ") if x]
        rel = [i + 1 for t in rel_txt for i, ln in enumerate(lines) if ln == t]
        ok = r.line_number in main and len(rel) == len(rel_txt)
        keep.append(ok)
        matched_lines.append(sorted(set(main + rel)))
        matched_n.append(len(main) + len(rel))
    df["verified"], df["matched_lines"], df["matched_n"] = keep, matched_lines, matched_n
    return df[df.verified].reset_index(drop=True), df[~df.verified].reset_index(drop=True)


def findings() -> pd.DataFrame:
    return load_findings_checked()[0]


def quote_pct() -> int:
    ok, bad = load_findings_checked()
    total = len(ok) + len(bad)
    return round(100 * len(ok) / total) if total else 100


@st.cache_data
def gate(file: str) -> pd.DataFrame:
    df = read_csv(file).copy()
    df["amount"] = df["amount"].astype(float)
    return df


def gate_totals(df: pd.DataFrame) -> dict:
    h = df[df.decision == "HOLD"]
    r = df[df.decision == "RELEASE"]
    return dict(lines=len(df), total=df.amount.sum(), vendors=df.supplier.nunique(),
                hold_n=len(h), hold_amt=h.amount.sum(), rel_n=len(r), rel_amt=r.amount.sum())


@st.cache_data
def options() -> pd.DataFrame:
    df = read_csv("workflow_options.csv").copy()
    df["est_min"] = df["est_min"].astype(int)
    df["result_findings"] = df["result_findings"].astype(int)
    return df


def opt_row(workflow: str, option: str) -> pd.Series:
    """Option row, with a fallback for options added by uploading a zip."""
    df = options()
    hit = df[(df.workflow == workflow) & (df.option == option)]
    if len(hit):
        return hit.iloc[0]
    base = df[df.workflow == workflow].iloc[0].copy()
    extra = st.session_state.get("extra_opts", {}).get(workflow, {}).get(option, "uploaded files")
    base["option"], base["preview_title"] = option, f"Preview · {option}"
    base["preview"], base["file"], base["last_run"] = extra, "", "Never run"
    return base


def option_preview(workflow: str, option: str) -> tuple[str, str]:
    row = opt_row(workflow, option)
    if row["file"]:
        t = gate_totals(gate(row["file"]))
        return row["preview_title"], f"{t['lines']} lines · {money(t['total'])} · {t['vendors']} vendors"
    return row["preview_title"], row["preview"]


@st.cache_data
def recovery() -> pd.DataFrame:
    df = read_csv("recovery.csv").copy()
    df["claim"] = df["claim"].astype(float)
    return df


@st.cache_data
def subscriptions() -> pd.DataFrame:
    df = read_csv("subscriptions.csv").copy()
    for c in ("licences", "active_90d", "annual_cost", "saving"):
        df[c] = df[c].astype(int)
    return df


# ---- audit trail ----
def base_trail(fdf: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in fdf.itertuples():
        rows.append(dict(time="14:2x", actor=f"{r.area} agent", action="Proposed", finding=r.id, detail=r.title))
        rows.append(dict(time="14:32", actor="Skeptic", action=f"Verdict: {r.skeptic_verdict}", finding=r.id,
                         detail=r.skeptic_reason))
    return pd.DataFrame(rows)


def log_action(actor: str, action: str, finding: str, detail: str = "") -> None:
    st.session_state.audit_log.append(dict(time=datetime.now().strftime("%H:%M:%S"), actor=actor,
                                           action=action, finding=finding, detail=detail))


def full_trail(fdf: pd.DataFrame) -> pd.DataFrame:
    human = pd.DataFrame(st.session_state.audit_log, columns=["time", "actor", "action", "finding", "detail"])
    return pd.concat([base_trail(fdf), human], ignore_index=True)


# ---- decisions ----
def decision_counts(fdf: pd.DataFrame) -> dict:
    d = st.session_state.decisions
    appr = [i for i in fdf.id if d.get(i, {}).get("status") == "Approved"]
    rej = [i for i in fdf.id if d.get(i, {}).get("status") == "Rejected"]
    val = fdf[fdf.id.isin(appr)].amount.sum()
    return dict(approved=len(appr), rejected=len(rej), pending=len(fdf) - len(appr) - len(rej), value=val)


def decide(fid: str, status: str, reason: str = "") -> None:
    st.session_state.decisions[fid] = dict(status=status, reason=reason)
    log_action("Reviewer", status, fid, reason or "Approved by reviewer")


def undo(fid: str) -> None:
    st.session_state.decisions.pop(fid, None)
    log_action("Reviewer", "Reset to pending", fid)


def goto(page: str) -> None:
    st.session_state.nav = page


# ---- session ----
def init_state() -> None:
    S = st.session_state
    S.setdefault("nav", "Run analysis")
    S.setdefault("step", 1)
    S.setdefault("decisions", {})
    S.setdefault("audit_log", [])
    S.setdefault("cleared", {})
    S.setdefault("sim", None)
    S.setdefault("recent_extra", [])
    S.setdefault("events_extra", [])
    S.setdefault("fail_pending", True)
    S.setdefault("last_tick", 0.0)
    S.setdefault("extra_opts", {})


# ---- look and feel ----
CSS = f"""
<style>
.block-container {{ padding-top: 3.6rem; padding-bottom: 3rem; max-width: 1500px; }}
.lw-banner {{ background:{SKY}; color:{INK}; font-weight:700; letter-spacing:.22em; text-align:center;
  padding:.45rem .5rem; border-radius:4px; font-size:.82rem; margin-bottom:.6rem; }}
.lw-header {{ display:flex; align-items:baseline; gap:.9rem; flex-wrap:wrap; }}
.lw-title {{ font-family:'Arial Narrow','Roboto Condensed','Helvetica Neue',Arial,sans-serif; font-stretch:condensed;
  font-weight:800; font-size:2rem; letter-spacing:.06em; color:{TEAL_DARK}; }}
.lw-co {{ color:{INK}; font-size:1rem; }}
.lw-strip {{ display:flex; gap:1.2rem; flex-wrap:wrap; font-size:.82rem; color:{INK}; margin:.15rem 0 1rem; }}
.lw-dot {{ display:inline-block; width:.55rem; height:.55rem; border-radius:50%; background:{RELEASE}; margin-right:.35rem; }}
.lw-badge {{ display:inline-block; padding:.08rem .55rem; border-radius:999px; color:#fff; font-size:.74rem;
  font-weight:700; letter-spacing:.03em; white-space:nowrap; }}
.lw-chip {{ display:inline-block; padding:.05rem .5rem; border-radius:4px; font-size:.74rem; font-weight:700;
  border:1px solid; white-space:nowrap; }}
.lw-preview {{ background:{PAPER}; border:1px solid {SKY}; border-radius:6px; padding:.5rem .7rem; font-size:.85rem; }}
.lw-preview b {{ display:block; font-size:.76rem; color:{TEAL_DARK}; letter-spacing:.03em; margin-bottom:.15rem; }}
.lw-muted {{ color:{MUTED}; font-size:.82rem; }}
.lw-src {{ background:#fff; border:1px solid {SKY}; border-radius:6px; padding:.4rem 0; font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:.78rem; line-height:1.5; overflow-x:auto; }}
.lw-row {{ white-space:pre; padding:0 .6rem 0 0; }}
.lw-ln {{ display:inline-block; width:3rem; text-align:right; padding-right:.8rem; color:{MUTED}; user-select:none; }}
.lw-row.hit {{ background:#ffe9a8; }}
.lw-row.rel {{ background:#e1f1fb; }}
/* sidebar */
section[data-testid="stSidebar"] {{ background:{TEAL_DARK}; }}
section[data-testid="stSidebar"] * {{ color:{PAPER}; }}
section[data-testid="stSidebar"] hr {{ border-color: rgba(179,224,247,.3); }}
.lw-guard {{ border:1px solid rgba(179,224,247,.4); border-radius:6px; padding:.6rem .7rem; font-size:.82rem; margin-top:1.5rem; }}
.lw-guard b {{ letter-spacing:.06em; }}
[class*="st-key-step_btn_"] button {{ padding:.35rem .4rem; }}
[class*="st-key-step_btn_"] button p {{ font-size:.82rem; white-space:nowrap; }}
div[data-testid="stMetricValue"] {{ color:{TEAL_DARK}; }}
button[data-testid="stBaseButton-primary"] {{ background:{TEAL_DARK}; border-color:{TEAL_DARK}; color:#fff; }}
button[data-testid="stBaseButton-primary"]:hover {{ background:{TEAL}; border-color:{TEAL}; color:#fff; }}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def badge(text: str, color: str) -> str:
    return f'<span class="lw-badge" style="background:{color}">{html.escape(text)}</span>'


def sev_badge(sev: str) -> str:
    return badge(sev, SEV_COLOR.get(sev, MUTED))


def chip(text: str, color: str) -> str:
    return f'<span class="lw-chip" style="color:{color};border-color:{color}">{html.escape(text)}</span>'


def banner_and_header(last_run: str = "14:32") -> None:
    st.markdown('<div class="lw-banner">FICTIONAL TEST DATA - not a real company</div>', unsafe_allow_html=True)
    st.markdown('<div class="lw-header"><span class="lw-title">TALLYHOUND</span>'
                '<span class="lw-co">Bramblecourt Instruments Ltd (fictional)</span></div>', unsafe_allow_html=True)
    items = ["Model loaded", "Offline", "Sandbox on", f"Last run {last_run}"]
    st.markdown('<div class="lw-strip">' + "".join(f'<span><span class="lw-dot"></span>{i}</span>' for i in items) + "</div>",
                unsafe_allow_html=True)


def source_html(lines: list[str], hits: list[int], main: int | None = None) -> str:
    out = []
    for i, ln in enumerate(lines, start=1):
        cls = "hit" if i == main else ("rel" if i in hits else "")
        out.append(f'<div class="lw-row {cls}"><span class="lw-ln">{i}</span>{html.escape(ln)}</div>')
    return '<div class="lw-src">' + "".join(out) + "</div>"
