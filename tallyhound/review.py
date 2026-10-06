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


def _decide_all(ids: list[str], status: str, reason: str = "") -> None:
    for i in ids:
        C.decide(i, status, reason)


def _undo_all(ids: list[str]) -> None:
    for i in ids:
        if i in st.session_state.decisions:
            C.undo(i)


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


def finding_card(r, others: list | None = None) -> None:
    from . import triage
    S = st.session_state
    others = others or []
    ids = [r.id] + [o.id for o in others]
    d = S.decisions.get(r.id)
    blocked = auth.review_block_reason()
    with st.container(border=True):
        a, b = st.columns([5, 2], vertical_alignment="center")
        a.markdown(f"{C.sev_badge(r.severity)} &nbsp; **{C.esc(r.id)}** &nbsp;·&nbsp; {C.esc(r.area)} &nbsp;·&nbsp; clause {C.esc(r.clause)}"
                   + (f" &nbsp;·&nbsp; <span class='th-muted'>case of {len(ids)}</span>" if others else ""),
                   unsafe_allow_html=True)
        b.markdown(f"<div style='text-align:right;font-weight:700'>{C.money(r.amount)}</div>", unsafe_allow_html=True)
        st.markdown(f"**{C.esc(r.title)}**")
        _code(r.evidence)
        st.markdown(f"Skeptic verdict: **{r.skeptic_verdict}** - {C.esc(r.skeptic_reason)}")
        st.markdown(f"Proposed fix: {C.esc(r.proposed_fix)}")
        clears = triage.CLEARS.get(str(r.clause))
        if clears:
            st.caption(f"Clears if {clears}. Priority {triage.priority(r)}.")
        if others:
            st.markdown("Also in this case (decided together): " + "; ".join(
                f"**{o.id}** {C.esc(o.title)} (clause {o.clause})" for o in others))
        note = S.notes.get(r.id, {})
        label = "Owner and notes" + (f" - {note['owner']}" if note.get("owner") else "") + (" (note)" if note.get("note") else "")
        with st.expander(label):
            st.text_input("Owner", value=note.get("owner", ""), key=f"own_{r.id}", placeholder="Who follows this up?")
            st.text_area("Note", value=note.get("note", ""), key=f"note_{r.id}", height=80)
            st.button("Save", key=f"savenote_{r.id}", on_click=C.save_note, args=(r.id,))
        if d:
            color = C.RELEASE if d["status"] == "Approved" else C.HOLD
            c1, c2, c3 = st.columns([4, 1.4, 1], vertical_alignment="center")
            c1.markdown(C.badge(d["status"], color) + (f" &nbsp; <span class='th-muted'>{C.esc(d['reason'])}</span>" if d["reason"] else ""),
                        unsafe_allow_html=True)
            if d["status"] == "Rejected":
                c2.download_button("Save as test case", test_case_json(r, ids), file_name=f"case_{r.id}_{r.clause}.json",
                                   mime="application/json", key=f"case_{r.id}", type="tertiary",
                                   help="A regression test: put it in tests/cases/ so this false alarm can never come back.")
            c3.button("Undo", key=f"undo_{r.id}", on_click=_undo_all, args=(ids,), disabled=bool(blocked))
        else:
            c1, c2 = st.columns([1, 4])
            c1.button("Approve", key=f"appr_{r.id}", type="primary", on_click=_decide_all, args=(ids, "Approved"),
                      disabled=bool(blocked), help=blocked)
            with c2.expander("Reject..."):
                reason = st.text_input("Reason (required)", key=f"rej_{r.id}")
                who = _entity(r)
                st.checkbox(f"Don't flag clause {r.clause} again for {who}", key=f"supp_{r.id}")
                st.button("Reject", key=f"rejbtn_{r.id}", disabled=not reason.strip() or bool(blocked),
                          on_click=_reject, args=(r.id, reason.strip(), str(r.clause), r.source_file, who, ids))


def _entity(r) -> str:
    from . import learn
    lines = C.source_lines(r.source_file)
    return learn.entity(r.source_file, r.evidence, lines[0] if r.source_file.endswith(".csv") and lines else "")


def _reject(fid: str, reason: str, clause: str, source_file: str, who: str, ids: list[str] | None = None) -> None:
    from . import learn
    for i in ids or [fid]:
        C.decide(i, "Rejected", reason)
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


def _mark_shadow(key: str, value: int) -> None:
    st.session_state.setdefault("shadow_marks", {})[key] = value


def step3() -> None:
    from . import triage
    suppressed_notice()
    why = auth.review_block_reason()
    if why:
        st.warning(why)
    S = st.session_state
    f = C.findings()
    triage.refresh_health()
    L = C.limits()
    shadow = triage.shadow_clauses()
    in_shadow = f[f.clause.astype(str).isin(shadow)]
    f = f[~f.clause.astype(str).isin(shadow)]
    left, right = st.columns([7, 3])
    with left:
        sev = st.segmented_control("Severity", ["All", "High", "Medium", "Low"], default="All",
                                   key="rev_sev", label_visibility="collapsed")
        shown = f if sev in (None, "All") else f[f.severity == sev]

        def minor(r) -> bool:
            return (float(r.amount) < L["materiality"] and r.severity != "High") or triage.demoted(str(r.clause))
        groups = triage.cases(shown)
        main = [g for g in groups if not all(minor(r) for r in g)]
        small = [g for g in groups if all(minor(r) for r in g)]
        n_items = sum(len(g) for g in main)
        if len(main) > 20 and not S.get("rev_all"):
            st.caption(f"Showing the 20 most important of {len(main)} cases ({n_items} findings), ranked by severity, "
                       "money at stake and the Skeptic's confidence.")
            todo = main[:20]
        else:
            todo = main
        if len(main) > 20:
            st.toggle(f"Show all {len(main)} cases", key="rev_all")
        for g in todo:
            finding_card(g[0], g[1:])
        if small:
            with st.expander(f"Minor items ({sum(len(g) for g in small)}): under ${L['materiality']:,.0f} or from a rule "
                             "reviewers usually reject"):
                for g in small:
                    finding_card(g[0], g[1:])
        if not in_shadow.empty:
            with st.expander(f"Shadow rules: {len(in_shadow)} finding(s) these rules would have raised"):
                st.caption("Shadow rules run without adding to the queue. Mark a few: once enough are real problems, "
                           "the rule can be promoted on the Policy page.")
                for r in in_shadow.itertuples():
                    k = f"{r.clause}|{r.id}"
                    c1, c2, c3 = st.columns([6, 1.3, 1.3], vertical_alignment="center")
                    mark = S.get("shadow_marks", {}).get(k)
                    c1.markdown(f"**{r.id}** clause {r.clause}: {C.esc(r.title)}" +
                                ("" if mark is None else f" - marked {'real' if mark else 'not a problem'}"))
                    c2.button("Real", key=f"shy_{r.id}", on_click=_mark_shadow, args=(k, 1))
                    c3.button("Not a problem", key=f"shn_{r.id}", on_click=_mark_shadow, args=(k, 0))
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
