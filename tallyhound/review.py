"""Review decisions (approve, reject, undo, bulk approve, suppressions) and the Download tab."""
from __future__ import annotations

import streamlit as st

from . import common as C
from . import exports, guide


def _code(text: str) -> None:
    try:
        st.code(text, language=None, wrap_lines=True)
    except TypeError:
        st.code(text, language=None)


def _decide_all(ids: list[str], status: str, reason: str = "") -> None:
    """Decide the undecided findings of a case. An earlier decision is never overwritten; undo it first."""
    for i in ids:
        if i not in st.session_state.decisions:
            C.decide(i, status, reason)


def _undo_all(ids: list[str]) -> None:
    from . import learn
    S = st.session_state
    for i in ids:
        d = S.decisions.get(i)
        if d is None:
            continue
        sup = d.get("suppression")
        C.undo(i)
        if sup and learn.is_suppressed(*sup) and not any(x.get("suppression") == sup for x in S.decisions.values()):
            learn.remove(*sup)                 # the rejection that set it aside is undone, so the suppression goes too
            C.log_action("Reviewer", "Suppression removed", i, f"clause {sup[0]} for {sup[2]}: its rejection was undone")


def test_case_json(r, ids: list[str]) -> str:
    """A regression test for tests/cases/: the files, and the lines that must not be flagged again."""
    import json

    from . import custom
    label = custom.active_label()
    files = custom.view(label) if label else {n: C.read_source(n) for n in rules_files()}
    f = C.findings().set_index("id")
    must_not = [dict(source_file=f.loc[i, "source_file"], line=int(f.loc[i, "line_number"]), clause=str(f.loc[i, "clause"]))
                for i in ids if i in f.index]
    d = st.session_state.decisions.get(r.id, {})
    return json.dumps(dict(description=f"Rejected by a reviewer: {d.get('reason', '')} ({r.title})",
                           files={k: v for k, v in files.items() if v}, must_not_flag=must_not), indent=1)


def rules_files() -> list[str]:
    from . import rules
    return list(rules.FILES.values())



def _entity(r) -> str:
    from . import learn
    lines = C.source_lines(r.source_file)
    return learn.entity(r.source_file, r.evidence, lines[0] if r.source_file.endswith(".csv") and lines else "")


def _reject(fid: str, reason: str, clause: str, source_file: str, who: str, ids: list[str] | None = None) -> None:
    from . import learn
    _decide_all(ids or [fid], "Rejected", reason)
    if st.session_state.get(f"supp_{fid}"):
        learn.suppress(clause, source_file, who, reason)
        st.session_state.decisions.get(fid, {})["suppression"] = [clause, source_file, who]
        C.log_action("Reviewer", "Suppression added", fid, f"clause {clause} for {who}: {reason}")


def reject_clicked(fid: str, clause: str, source_file: str, who: str, ids: list[str] | None = None) -> None:
    """The Reject button: always clickable, so nobody has to know to press Enter in the reason box first. Without a
    reason it only asks for one."""
    S = st.session_state
    reason = str(S.get(f"rej_{fid}", "")).strip()
    if not reason:
        S[f"_rej_missing_{fid}"] = True
        return
    S.pop(f"_rej_missing_{fid}", None)
    _reject(fid, reason, clause, source_file, who, ids)


def bulk_approve(ids: list[str]) -> None:
    for i in ids:
        if i not in st.session_state.decisions:
            C.decide(i, "Approved", "Bulk: confirmed by the Skeptic")


def suppressed_notice() -> None:
    n = st.session_state.get("_n_suppressed", 0)
    if n or st.session_state.get("show_suppressed"):
        st.toggle(f"Show {n} finding(s) set aside by your suppressions" if n else "Showing suppressed findings",
                  key="show_suppressed")


def _mark_shadow(key: str, value: int) -> None:
    st.session_state.setdefault("shadow_marks", {})[key] = value



def step4() -> None:
    f = C.findings()
    dc = C.decision_counts(f)
    m = st.columns(4)
    m[0].metric("Approved", dc["approved"])
    m[1].metric("Rejected", dc["rejected"])
    m[2].metric("Approved value", C.money(dc["value"]))
    m[3].metric("Pending", dc["pending"])
    b1, b2, b3 = st.columns([2, 2, 2])
    b1.download_button("Download Excel workbook", data=exports.build_workbook(False), file_name="tallyhound_workbook.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", on_click=guide.mark_downloaded, **C.bw())
    b2.download_button("Download Memo (PDF)", data=exports.build_memo(), file_name="tallyhound_memo.pdf",
                       mime="application/pdf", on_click=guide.mark_downloaded, **C.bw())
    b3.download_button("Download draft (not reviewed)", data=exports.build_workbook(True),
                       file_name="tallyhound_draft_not_reviewed.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="tertiary", on_click=guide.mark_downloaded, **C.bw())
    st.caption("The workbook and memo record your decisions; while findings are pending they say \"partly reviewed\". "
               "The draft is the findings alone, before anyone has decided, for sharing early.")
    df = exports.decisions_frame()
    areas = [a for a in ["Payments", "Approvals", "Vendors", "Contracts", "Expenses", "Invoices"] if (df.area == a).any()
             or a != "Invoices"]
    tabs = st.tabs(["Summary", *areas, "Audit trail"])
    with tabs[0]:
        s = df.groupby("area").agg(findings=("id", "count"),
                                   approved=("decision", lambda x: (x == "Approved").sum()),
                                   rejected=("decision", lambda x: (x == "Rejected").sum()),
                                   pending=("decision", lambda x: (x == "Pending").sum())).reset_index()
        st.dataframe(s, hide_index=True, **C.dfw())
    for tab, area in zip(tabs[1:-1], areas):
        with tab:
            st.dataframe(df[df.area == area].drop(columns=["area"]), hide_index=True, **C.dfw())
    with tabs[-1]:
        st.dataframe(C.full_trail(f).iloc[::-1], hide_index=True, **C.dfw())
