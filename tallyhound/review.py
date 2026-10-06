"""Run analysis steps 3 (Review) and 4 (Download)."""
from __future__ import annotations

import streamlit as st

from . import common as C
from . import auth, exports, guide


def _code(text: str) -> None:
    try:
        st.code(text, language=None, wrap_lines=True)
    except TypeError:
        st.code(text, language=None)


def finding_card(r) -> None:
    S = st.session_state
    d = S.decisions.get(r.id)
    blocked = auth.review_block_reason()
    with st.container(border=True):
        a, b = st.columns([5, 2], vertical_alignment="center")
        a.markdown(f"{C.sev_badge(r.severity)} &nbsp; **{r.id}** &nbsp;·&nbsp; {r.area} &nbsp;·&nbsp; clause {r.clause}",
                   unsafe_allow_html=True)
        b.markdown(f"<div style='text-align:right;font-weight:700'>{C.money(r.amount)}</div>", unsafe_allow_html=True)
        st.markdown(f"**{C.esc(r.title)}**")
        _code(r.evidence)
        st.markdown(f"Skeptic verdict: **{r.skeptic_verdict}** - {C.esc(r.skeptic_reason)}")
        st.markdown(f"Proposed fix: {C.esc(r.proposed_fix)}")
        note = S.notes.get(r.id, {})
        label = "Owner and notes" + (f" - {note['owner']}" if note.get("owner") else "") + (" (note)" if note.get("note") else "")
        with st.expander(label):
            st.text_input("Owner", value=note.get("owner", ""), key=f"own_{r.id}", placeholder="Who follows this up?")
            st.text_area("Note", value=note.get("note", ""), key=f"note_{r.id}", height=80)
            st.button("Save", key=f"savenote_{r.id}", on_click=C.save_note, args=(r.id,))
        if d:
            color = C.RELEASE if d["status"] == "Approved" else C.HOLD
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            c1.markdown(C.badge(d["status"], color) + (f" &nbsp; <span class='th-muted'>{d['reason']}</span>" if d["reason"] else ""),
                        unsafe_allow_html=True)
            c2.button("Undo", key=f"undo_{r.id}", on_click=C.undo, args=(r.id,), disabled=bool(blocked))
        else:
            c1, c2 = st.columns([1, 4])
            c1.button("Approve", key=f"appr_{r.id}", type="primary", on_click=C.decide, args=(r.id, "Approved"),
                      disabled=bool(blocked), help=blocked)
            with c2.expander("Reject..."):
                reason = st.text_input("Reason (required)", key=f"rej_{r.id}")
                who = _entity(r)
                st.checkbox(f"Don't flag clause {r.clause} again for {who}", key=f"supp_{r.id}")
                st.button("Reject", key=f"rejbtn_{r.id}", disabled=not reason.strip() or bool(blocked),
                          on_click=_reject, args=(r.id, reason.strip(), str(r.clause), r.source_file, who))


def _entity(r) -> str:
    from . import learn
    lines = C.source_lines(r.source_file)
    return learn.entity(r.source_file, r.evidence, lines[0] if r.source_file.endswith(".csv") and lines else "")


def _reject(fid: str, reason: str, clause: str, source_file: str, who: str) -> None:
    from . import learn
    C.decide(fid, "Rejected", reason)
    if st.session_state.get(f"supp_{fid}"):
        learn.suppress(clause, source_file, who, reason)
        C.log_action("Reviewer", "Suppression added", fid, f"clause {clause} for {who}: {reason}")


def bulk_approve(ids: list[str]) -> None:
    for i in ids:
        if i not in st.session_state.decisions:
            C.decide(i, "Approved", "Bulk: confirmed by the Skeptic")


def suppressed_notice() -> None:
    n = st.session_state.get("_n_suppressed", 0)
    if n or st.session_state.get("show_suppressed"):
        st.toggle(f"Show {n} finding(s) set aside by your suppressions" if n else "Showing suppressed findings",
                  key="show_suppressed")


def step3() -> None:
    suppressed_notice()
    why = auth.review_block_reason()
    if why:
        st.warning(why)
    S = st.session_state
    f = C.findings()
    left, right = st.columns([7, 3])
    with left:
        sev = st.segmented_control("Severity", ["All", "High", "Medium", "Low"], default="All",
                                   key="rev_sev", label_visibility="collapsed")
        shown = f if sev in (None, "All") else f[f.severity == sev]
        for r in shown.itertuples():
            finding_card(r)
    with right:
        with st.container(border=True):
            st.markdown("**Bulk action**")
            conf = shown[shown.skeptic_verdict == "Confirmed"]
            with st.container(height=300, border=False):
                for r in conf.itertuples():
                    S.setdefault(f"bulk_{r.id}", True)
                    st.checkbox(f"{r.id} · {r.severity} · {r.area}", key=f"bulk_{r.id}", disabled=r.id in S.decisions)
            ids = [r.id for r in conf.itertuples() if S.get(f"bulk_{r.id}")]
            st.button("Approve all confirmed by the Skeptic", type="primary", on_click=bulk_approve, args=(ids,),
                      disabled=not ids or bool(auth.review_block_reason()), **C.bw())
            approved = sum(S.decisions.get(i, {}).get("status") == "Approved" for i in shown.id)
            st.caption(f"Approved {approved} of {len(shown)} shown")
            st.button("Continue to Download", on_click=lambda: S.update(step=4), **C.bw())


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
    if dc["pending"]:
        st.caption(f"{dc['pending']} finding(s) are still pending. The final files list them as Pending.")
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
