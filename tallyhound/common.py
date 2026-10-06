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

NAV = ["Home", "Review", "Reports", "Settings", "Trust"]
STEPS = ["1  Choose data (Folder or zip)", "2  Run (Eight agents)",
         "3  Review (Approve / reject)", "4  Download (Excel and memo)"]


# ---- widget helpers that work on old and new Streamlit ----
def _stretch() -> dict:
    return {"width": "stretch"} if "width" in inspect.signature(st.button).parameters else {"use_container_width": True}


def bw() -> dict:
    """Keyword args that make a button/download button fill its column."""
    return _stretch()


def dfw() -> dict:
    return {"width": "stretch"} if "width" in inspect.signature(st.dataframe).parameters else {"use_container_width": True}


_MD = str.maketrans({c: "\\" + c for c in "\\`*_[]$~|#"})


def esc(text) -> str:
    """Make text from files or models safe inside st.markdown: HTML is escaped (no tags or scripts from an upload),
    and markdown characters are backslash-escaped so '****1234' stays literal and '[x](url)' is not a link."""
    out = html.escape(str(text), quote=False).translate(_MD)
    return out.replace("://", ":\\/\\/").replace("www.", "www\\.")     # no clickable links from bare addresses


def money(x: float) -> str:
    return f"${x:,.2f}"


# ---- data ----
@st.cache_data
def read_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA / name, dtype=str, keep_default_na=False)


@st.cache_data
def read_source(name: str) -> list[str]:
    p = SRC / name
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


@st.cache_data
def _policy_base() -> dict:
    df = read_csv("policy.csv")
    return {r.clause + "|" + r.area: r.text for r in df.itertuples()}


def limits() -> dict:
    """The policy limits in force: the defaults, changed under Settings > Policy."""
    from . import rules
    S = st.session_state
    return {**rules.LIMITS, **S.get("limits", {}),
            "po_exempt_words": S.get("po_exempt_words", rules.PO_EXEMPT_WORDS),
            "po_exempt_vendors": S.get("po_exempt_vendors", [])}


def policy() -> dict:
    """Clause texts, with the amounts shown as currently set under Settings > Policy."""
    L = limits()
    swap = {"$2,500": f"${L['po_limit']:,.0f}", "$10,000": f"${L['director_limit']:,.0f}",
            "$75.00": f"${L['meal_limit']:,.2f}", "$25.00": f"${L['receipt_limit']:,.2f}"}
    out = {}
    for k, text in _policy_base().items():
        for a, b in swap.items():
            text = text.replace(a, b)
        out[k] = text
    return out


def clause_text(area: str, clause: str) -> str:
    return policy().get(f"{clause}|{area}", "")


def check_findings(df: pd.DataFrame, getter) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split findings into (verified, hidden). A finding is verified only when its quote is an exact line of its
    source file at the stated line number, and every related quote is an exact line too."""
    df = df.copy()
    df["amount"] = df["amount"].astype(float)
    df["line_number"] = df["line_number"].astype(int)
    keep, matched_lines, matched_n = [], [], []
    for r in df.itertuples():
        lines = getter(r.source_file)
        main = [i + 1 for i, ln in enumerate(lines) if ln == r.evidence]
        rel_txt = [x for x in str(r.related_evidence).split(" || ") if x]
        rel = [i + 1 for t in rel_txt for i, ln in enumerate(lines) if ln == t]
        ok = r.line_number in main and all(any(ln == t for ln in lines) for t in rel_txt)
        keep.append(ok)
        matched_lines.append(sorted(set(main + rel)))
        matched_n.append(len(main) + len(rel))
    df["verified"], df["matched_lines"], df["matched_n"] = keep, matched_lines, matched_n
    mask = df.verified.astype(bool)              # .loc with a boolean mask keeps the columns even when nothing is found
    return df.loc[mask].reset_index(drop=True), df.loc[~mask].reset_index(drop=True)


@st.cache_data
def load_findings_checked() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sample findings, with every quote checked against the real files."""
    return check_findings(read_csv("findings.csv"), read_source)


def findings_checked() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(verified, hidden) for whichever data is in review: the sample company or an uploaded dataset."""
    from . import custom
    label = custom.active_label()
    return custom.frame(label) if label else load_findings_checked()


def source_lines(name: str) -> list[str]:
    from . import custom
    return custom.source_lines(name)


def findings() -> pd.DataFrame:
    return findings_checked()[0]


def custom_label() -> str | None:
    """Name of the uploaded dataset in review, or None for the sample company."""
    from . import custom
    return custom.active_label()


def sample_only_notice() -> None:
    d = custom_label()
    if d:
        st.info(f"This page shows the sample company. Your uploaded files (\"{d}\") drive Findings, Review and the "
                "downloads. Add a payment_run.csv to the zip to check a payment run; recovery and subscriptions are not "
                "analysed from uploads yet.")


def quote_pct() -> int | str:
    try:
        ok, bad = findings_checked()
    except Exception:                     # noqa: BLE001 - the sidebar must always draw, so Reset stays reachable
        return "?"
    total = len(ok) + len(bad)
    return round(100 * len(ok) / total) if total else 100


@st.cache_data
def gate(file: str) -> pd.DataFrame:
    df = read_csv(file).copy()
    df["amount"] = df["amount"].astype(float)
    return df


def gate_totals(df: pd.DataFrame, cleared: dict | None = None) -> dict:
    """Counts and money on HOLD and to RELEASE. With cleared, holds a person has cleared count as released, so every
    page and document reports the same final position."""
    cleared = cleared or {}
    held = (df.decision == "HOLD") & ~df.line.astype(str).isin(list(cleared))
    h, r = df[held], df[~held]
    return dict(lines=len(df), total=df.amount.sum(), vendors=df.supplier.nunique(),
                hold_n=len(h), hold_amt=h.amount.sum(), rel_n=len(r), rel_amt=r.amount.sum(),
                cleared_n=int(((df.decision == "HOLD") & ~held).sum()))


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
    base["est_min"] = 1
    base["result_findings"] = 0
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


CHAIN_FIELDS = ("time", "actor", "action", "finding", "detail")
GENESIS = "0" * 64
NEW_RUN = "New run"          # an entry with this action means: the findings were replaced, earlier decisions are void


def entry_hash(prev: str, e: dict) -> str:
    import hashlib
    import json
    body = json.dumps({k: "" if e.get(k) is None else str(e.get(k)) for k in CHAIN_FIELDS}, sort_keys=True)
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


_EPHEMERAL_KEY: bytes | None = None


def _trail_key() -> bytes:
    """A secret only the server knows (TALLYHOUND_TRAIL_KEY, or a random key kept next to the saved work). Without
    it nobody can produce a valid seal, so a trail cannot be edited, cut short or rebuilt from scratch unnoticed."""
    import os
    import secrets
    from . import store
    env = os.environ.get("TALLYHOUND_TRAIL_KEY")
    if env:
        return env.encode()
    p = store.state_dir() / "trail.key"
    try:
        if not p.exists():
            p.write_text(secrets.token_hex(32), encoding="utf-8")
            p.chmod(0o600)
        return p.read_text(encoding="utf-8").strip().encode()
    except OSError:
        # no writable disk: a key that lives only as long as this server process. Trails sealed with it cannot be
        # checked after a restart (they show as broken), but nobody can forge one - unlike a fixed fallback key.
        global _EPHEMERAL_KEY
        if _EPHEMERAL_KEY is None:
            _EPHEMERAL_KEY = secrets.token_bytes(32)
            import logging
            logging.getLogger("tallyhound").warning("No writable state folder: audit trails are sealed with a temporary "
                                                    "key. Set TALLYHOUND_TRAIL_KEY or TALLYHOUND_STATE_DIR.")
        return _EPHEMERAL_KEY


def seal_of(log: list[dict]) -> str:
    import hashlib
    import hmac
    head = log[-1].get("hash", "") if log else GENESIS
    return hmac.new(_trail_key(), f"{len(log)}|{head}".encode(), hashlib.sha256).hexdigest()


def _ds_key() -> str:
    return st.session_state.get("dataset") or "__sample__"


def log_action(actor: str, action: str, finding: str, detail: str = "") -> None:
    """Append to the audit trail. Each entry carries the fingerprint (SHA-256) of itself plus the entry before, and
    the whole trail carries a seal made with the server's secret key, so changing, removing, adding or reordering any
    entry - including cutting entries off the end - shows."""
    from . import auth
    S = st.session_state
    log = S.audit_log
    who = auth.current_user()
    if who and actor == "Reviewer":
        actor = f"Reviewer ({who})"
    e = dict(time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor=actor, action=action, finding=finding, detail=detail)
    e["prev"] = log[-1].get("hash", GENESIS) if log else GENESIS
    e["hash"] = entry_hash(e["prev"], e)
    log.append(e)
    S.setdefault("trail_seals", {})[_ds_key()] = seal_of(log)


_CURRENT = object()


def verify_trail(log: list[dict], seal=_CURRENT) -> tuple[bool, int | None]:
    """(intact, index of the first broken entry). index == len(log) means the trail as a whole is not the one the
    server sealed: entries were cut off the end, or the trail was replaced. An empty trail with no seal is intact."""
    if seal is _CURRENT:
        seal = st.session_state.get("trail_seals", {}).get(_ds_key())
    prev = GENESIS
    for i, e in enumerate(log):
        if not isinstance(e, dict) or "hash" not in e or e.get("prev") != prev or entry_hash(prev, e) != e["hash"]:
            return False, i
        prev = e["hash"]
    if not log and not seal:
        return True, None
    import hmac
    if not seal or not hmac.compare_digest(str(seal), seal_of(log)):
        return False, len(log)
    return True, None


def replay_decisions(log: list[dict]) -> dict[str, str]:
    """The decisions the trail says were made (finding id -> Approved / Rejected)."""
    out: dict[str, str] = {}
    for e in log:
        a = e.get("action")
        if a in ("Approved", "Rejected"):
            out[str(e.get("finding"))] = a
        elif a == "Reset to pending":
            out.pop(str(e.get("finding")), None)
        elif a == NEW_RUN:
            out = {}
    return out


def decisions_mismatch(log: list[dict], decisions: dict) -> list[str]:
    """Finding ids whose recorded decision differs from what the trail says. Empty when they agree."""
    want = replay_decisions(log)
    have = {k: v.get("status") for k, v in decisions.items() if isinstance(v, dict)}
    return sorted(k for k in set(want) | set(have) if want.get(k) != have.get(k))


def full_trail(fdf: pd.DataFrame) -> pd.DataFrame:
    human = pd.DataFrame(st.session_state.audit_log, columns=["time", "actor", "action", "finding", "detail", "prev", "hash"])
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


def save_note(fid: str) -> None:
    S = st.session_state
    owner, note = S.get(f"own_{fid}", "").strip(), S.get(f"note_{fid}", "").strip()
    S.notes[fid] = dict(owner=owner, note=note)
    log_action("Reviewer", "Owner and note saved", fid, f"{owner or 'no owner'}: {note[:80]}")


def undo(fid: str) -> None:
    st.session_state.decisions.pop(fid, None)
    log_action("Reviewer", "Reset to pending", fid)


def goto(page: str) -> None:
    st.session_state.nav = page


# ---- session ----
def init_state() -> None:
    from . import store
    store.load_into_session()
    S = st.session_state
    S.setdefault("nav", "Home")
    S.setdefault("step", 1)
    S.setdefault("decisions", {})
    S.setdefault("audit_log", [])
    S.setdefault("cleared", {})
    S.setdefault("uploads", {})
    S.setdefault("sim", None)
    S.setdefault("recent_extra", [])
    S.setdefault("events_extra", [])
    S.setdefault("fail_pending", True)
    S.setdefault("last_tick", 0.0)
    S.setdefault("extra_opts", {})
    S.setdefault("tour_off", False)
    S.setdefault("notes", {})
    S.setdefault("tour_downloaded", False)


# ---- look and feel ----
CSS = f"""
<style>
.block-container {{ padding-top: 2.6rem; padding-bottom: 3rem; max-width: 1500px; }}
.th-banner {{ background:{SKY}; color:{INK}; font-weight:700; letter-spacing:.22em; text-align:center;
  padding:.45rem .5rem; border-radius:4px; font-size:.82rem; margin-bottom:.6rem; }}
.th-header {{ display:flex; align-items:center; gap:.9rem; flex-wrap:wrap; margin-bottom:.8rem; }}
.th-header .th-dot {{ margin-left:auto; }}
.th-title {{ font-family:'Arial Narrow','Roboto Condensed','Helvetica Neue',Arial,sans-serif; font-stretch:condensed;
  font-weight:800; font-size:1.6rem; letter-spacing:.06em; color:{TEAL_DARK}; }}
.th-co {{ color:{INK}; font-size:1rem; }}
.th-strip {{ display:flex; gap:1.2rem; flex-wrap:wrap; font-size:.82rem; color:{INK}; margin:.15rem 0 1rem; }}
.th-dot {{ display:inline-block; width:.55rem; height:.55rem; border-radius:50%; background:{RELEASE}; margin-right:.35rem; }}
.th-badge {{ display:inline-block; padding:.08rem .55rem; border-radius:999px; color:#fff; font-size:.74rem;
  font-weight:700; letter-spacing:.03em; white-space:nowrap; }}
.th-chip {{ display:inline-block; padding:.05rem .5rem; border-radius:4px; font-size:.74rem; font-weight:700;
  border:1px solid; white-space:nowrap; }}
.th-preview {{ background:{PAPER}; border:1px solid {SKY}; border-radius:6px; padding:.5rem .7rem; font-size:.85rem; }}
.th-preview b {{ display:block; font-size:.76rem; color:{TEAL_DARK}; letter-spacing:.03em; margin-bottom:.15rem; }}
.th-muted {{ color:{MUTED}; font-size:.82rem; }}
.th-src {{ background:#fff; border:1px solid {SKY}; border-radius:6px; padding:.4rem 0; font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:.78rem; line-height:1.5; overflow-x:auto; }}
.th-row {{ white-space:pre; padding:0 .6rem 0 0; }}
.th-ln {{ display:inline-block; width:3rem; text-align:right; padding-right:.8rem; color:{MUTED}; user-select:none; }}
.th-row.hit {{ background:#ffe9a8; }}
.th-row.rel {{ background:#e1f1fb; }}
/* sidebar */
section[data-testid="stSidebar"] {{ background:{TEAL_DARK}; }}
section[data-testid="stSidebar"] * {{ color:{PAPER}; }}
section[data-testid="stSidebar"] hr {{ border-color: rgba(179,224,247,.3); }}
section[data-testid="stSidebar"] [data-testid="stSelectbox"] * {{
  color:{INK} !important; -webkit-text-fill-color:{INK} !important; }}
section[data-testid="stSidebar"] [data-testid="stSelectbox"] label, section[data-testid="stSidebar"] [data-testid="stSelectbox"] label * {{
  color:{PAPER} !important; -webkit-text-fill-color:{PAPER} !important; }}
.th-foot {{ font-size:.74rem; opacity:.75; margin-top:1.4rem; }}
.th-guard {{ border:1px solid rgba(179,224,247,.4); border-radius:6px; padding:.6rem .7rem; font-size:.82rem; margin-top:1.5rem; }}
.th-guard b {{ letter-spacing:.06em; }}
[class*="st-key-step_btn_"] button {{ padding:.35rem .4rem; }}
[class*="st-key-step_btn_"] button p {{ font-size:.82rem; white-space:nowrap; }}
div[data-testid="stMetricValue"] {{ color:{TEAL_DARK}; }}
button[data-testid="stBaseButton-primary"] {{ background:{TEAL_DARK}; border-color:{TEAL_DARK}; color:#fff; }}
button[data-testid="stBaseButton-primary"]:hover {{ background:{TEAL}; border-color:{TEAL}; color:#fff; }}
@media (max-width: 1150px) {{
  section[data-testid="stMain"] [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; row-gap: .6rem; }}
  section[data-testid="stMain"] [data-testid="stColumn"] {{ min-width: 260px; }}
  [class*="st-key-step_btn_"] button p {{ white-space: normal; }}
}}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def badge(text: str, color: str) -> str:
    return f'<span class="th-badge" style="background:{color}">{html.escape(text)}</span>'


def sev_badge(sev: str) -> str:
    return badge(sev, SEV_COLOR.get(sev, MUTED))


def chip(text: str, color: str) -> str:
    return f'<span class="th-chip" style="color:{color};border-color:{color}">{html.escape(text)}</span>'


def banner_and_header(last_run: str = "14:32") -> None:
    """A slim header: the name, one chip saying whose data this is, and nothing else."""
    mine = custom_label()
    chip = (f'<span class="th-chip" style="color:{TEAL_DARK};border-color:{TEAL}">Your files: {html.escape(mine)}</span>'
            if mine else f'<span class="th-chip" style="color:{MUTED};border-color:{MUTED}">Demo data: Bramblecourt '
                         'Instruments Ltd (fictional)</span>')
    st.markdown(f'<div class="th-header"><span class="th-title">TALLYHOUND</span>{chip}'
                f'<span class="th-dot" title="Local model, offline, sandboxed"></span></div>', unsafe_allow_html=True)
    S = st.session_state
    if S.get("custom") and not mine:
        st.warning("You are looking at the SAMPLE company. Your uploaded results are ready: choose "
                   f"\"{next(iter(S.custom))}\" in the **Data in review** box in the sidebar.")


def source_html(lines: list[str], hits: list[int], main: int | None = None, start: int = 1) -> str:
    out = []
    for i, ln in enumerate(lines, start=start):
        cls = "hit" if i == main else ("rel" if i in hits else "")
        out.append(f'<div class="th-row {cls}"><span class="th-ln">{i}</span>{html.escape(ln)}</div>')
    return '<div class="th-src">' + "".join(out) + "</div>"
