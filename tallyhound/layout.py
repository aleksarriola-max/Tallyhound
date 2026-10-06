"""The app's five sections - Home, Review, Reports, Settings and Trust ("How we know it's right") - organised around
what people come to do.

Home: what needs you today, and one button to check new files.
Review: one queue of cases (and the payment run), decided once, then download.
Reports: summaries and trends for people who read rather than review.
Settings: policy, rules, data and users. Trust: how we know the results are right.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import auth, custom, guide, pages, pages_extra, review, run_analysis, sim, triage
from . import common as C

SAMPLE = "Sample company (demo)"


# ============================================================== Home
def _demo_selection() -> dict:
    return {"audit": ["2026-09"], "gate": ["Payment run 2026-10-01"]}


def _start(choice: str) -> None:
    """Queue a run for the chosen data. The sample uses the simulated run; uploads use the chosen engine."""
    S = st.session_state
    if choice == SAMPLE:
        custom.activate(None)                  # the sample run's results are the sample's, so review the sample
        sel = _demo_selection()
    else:
        sel = {"audit": [choice]}
    skip = frozenset(a for a in run_analysis.AGENT_KEYS if not S.get(f"adv_{a}", True))
    sim.start(sim.build_queue(sel, skip))


@st.dialog("Check new files", width="large")
def run_dialog() -> None:
    S = st.session_state
    up = st.file_uploader("Upload a zip of your files", type="zip", disabled=not auth.can("run"),
                          help=None if auth.can("run") else "Only a preparer or admin can add files.")
    st.caption("Any of payments.csv, approvals.csv, vendors.csv, contracts.txt, expenses.csv, bank_statement.csv, "
               "payment_run.csv and invoice PDFs. Read in memory, never written to disk.")
    label = run_analysis.add_zip(up) if up is not None else None
    options = [SAMPLE, *S.get("uploads", {})]
    if label and S.get("_last_upload") != up.file_id:     # a new upload: select it
        S["_last_upload"] = up.file_id
        S["run_choice"] = label
    if S.get("run_choice") not in options:
        S["run_choice"] = options[-1]
    choice = st.selectbox("Data to check", options, key="run_choice")
    if choice != SAMPLE:
        with st.expander("Engine and agents", expanded=False):
            run_analysis.engine_settings(False)
            c = st.columns(3)
            for i, name in enumerate(run_analysis.AGENT_KEYS):
                c[i % 3].checkbox(name, value=True, key=f"adv_{name}")
    else:
        st.caption("The demo runs a simulated set of agents on the fictional sample company, about 20 seconds per "
                   "item. One agent fails once on purpose so you can see what a retry looks like.")
    blocked = not auth.can("run")
    if st.button("Run", type="primary", disabled=blocked, key="run_go",
                 on_click=lambda: _start(st.session_state.get("run_choice", SAMPLE)), **C.bw()):
        st.rerun()                                    # close the dialog; the run shows on Home
    if blocked:
        st.caption("Your role cannot start runs.")


APPROVE_HELP = ("Approve = this is a real problem to follow up; it goes into the workbook and memo. It does not pay or "
                "approve any invoice. If it is not a problem, open Details and reject it with a reason.")


def queue_findings():
    """The findings the review queue shows: everything except clauses running in shadow mode."""
    f = C.findings()
    return f[~f.clause.astype(str).isin(triage.shadow_clauses())]


def _queue_counts() -> tuple[int, int]:
    f = queue_findings()
    groups = triage.cases(f)
    pending = [g for g in groups if not all(r.id in st.session_state.decisions for r in g)]
    return len(pending), len(f)


def _holds() -> tuple[int, float]:
    name, (gdf, clearable) = next(iter(pages.gate_runs().items()))
    if gdf.empty:
        return 0, 0.0
    cleared = st.session_state.cleared if clearable else {}
    held = gdf[(gdf.decision == "HOLD") & ~gdf.line.astype(str).isin(cleared)]
    return len(held), float(held.amount.sum())


def home() -> None:
    S = st.session_state
    guide.tour()
    run = S.get("sim")
    running = bool(run and run["running"])
    head, btn = st.columns([4, 1.4], vertical_alignment="center")
    head.markdown("### What needs you today")
    if btn.button("Check new files", type="primary", disabled=running, key="open_run", **C.bw()):
        run_dialog()

    if run and (running or any(i["status"] == "Failed" for i in run["queue"])):
        with st.container(border=True):
            st.markdown("**Run in progress**" if running else "**Run stopped - an agent failed**")
            st.fragment(run_every=1 if running else None)(_run_status)()

    cases, n = _queue_counts()
    holds, held_amt = _holds()
    label = custom.active_label()
    warns = [x for x in custom.profile(label) if x["level"] == "warn"] if label else []
    m = st.columns(3)
    m[0].metric("Cases to review", cases, help=f"Undecided cases, minor items included. {n} findings in all; "
                "findings that point at the same line form one case and are decided together.")
    if not label and not run:
        st.caption("These are the sample company's results from its last run. Press Check new files to watch a run "
                   "happen, or to upload your own files.")
    m[1].metric("Payments on hold", holds, help=f"{C.money(held_amt)} held in the payment run")
    m[2].metric("Data to confirm", len(warns), help="Things the data check could not decide on its own")
    if cases:
        st.markdown("**Top of the queue**")
        todo = [g for g in triage.cases(queue_findings()) if not all(x.id in S.decisions for x in g)]
        for g in todo[:5]:                       # the next five undecided cases, so the list refills as you decide
            r = g[0]
            st.markdown(f"{C.sev_badge(r.severity)} &nbsp; {C.esc(r.title)} &nbsp; "
                        f"<span class='th-muted'>{C.money(r.amount)}</span>", unsafe_allow_html=True)
        st.button("Open the review queue", on_click=C.goto, args=("Review",), key="home_review")
    elif n:
        st.success("Every case is decided. Download the results from Review.")
    for w in warns:
        st.warning(f"`{w['file']}` - {C.esc(w['message'])}")
    if label and warns:
        st.caption("Confirm these under Settings > Data.")
    with st.expander("Recent runs", expanded=False):
        run_analysis.recent_runs()


def _run_status() -> None:
    S = st.session_state
    if sim.tick():
        st.rerun()
    sm = S.sim
    if not sm:
        return
    for it in sm["queue"]:
        agents = [a for a in it["agents"] if a["status"] != "Skipped"]
        done = sum(a["status"] == "Done" for a in agents)
        c1, c2 = st.columns([3, 2], vertical_alignment="center")
        c1.markdown(f"**{C.esc(it['label'])}** &nbsp; <span class='th-muted'>{C.esc(it['status'])}</span>", unsafe_allow_html=True)
        c2.progress(done / max(len(agents), 1))
        bad = next((a for a in it["agents"] if a["status"] == "Failed"), None)
        if bad:
            st.error(f"The {bad['name']} agent failed.")
            st.button("Retry", key=f"home_retry_{it['label']}", on_click=sim.retry, type="primary")
    if not sm["running"] and all(i["status"] == "Done" for i in sm["queue"]):
        st.success("Run finished.")
        st.button("Review the results", on_click=C.goto, args=("Review",), key="home_done_review", type="primary")
    with st.expander("Agents and log", expanded=False):
        run_analysis.agents_body()


# ============================================================== Review
def _toggle(key: str) -> None:
    S = st.session_state
    S[key] = not S.get(key, False)


def case_row(g: list) -> None:
    """One line per case; details open underneath."""
    S = st.session_state
    r, others = g[0], g[1:]
    ids = [r.id] + [o.id for o in others]
    statuses = [S.decisions.get(i, {}).get("status") for i in ids]
    left = statuses.count(None)
    d = S.decisions.get(r.id) if left == 0 else None
    blocked = auth.review_block_reason()
    c1, c2, c3, c4 = st.columns([6.2, 1.4, 1.3, 1.1], vertical_alignment="center")
    extra = f" <span class='th-muted'>+{len(others)} related</span>" if others else ""
    c1.markdown(f"{C.sev_badge(r.severity)} &nbsp; {C.esc(r.title)}{extra}", unsafe_allow_html=True)
    c2.markdown(f"<div style='text-align:right;font-weight:600'>{C.money(r.amount)}</div>", unsafe_allow_html=True)
    if left == 0 and len(set(statuses)) == 1:
        c3.markdown(C.badge(statuses[0], C.RELEASE if statuses[0] == "Approved" else C.HOLD), unsafe_allow_html=True)
    elif left == 0:
        c3.markdown(C.badge("Mixed", C.MUTED), unsafe_allow_html=True)
    else:
        if left < len(ids):
            extra2 = f" <span class='th-muted'>({len(ids) - left} of {len(ids)} decided)</span>"
            c1.markdown(extra2, unsafe_allow_html=True)
        c3.button("Approve" if left == len(ids) else f"Approve {left}", key=f"appr_{r.id}", type="primary", on_click=review._decide_all, args=(ids, "Approved"),
                  disabled=bool(blocked), help=blocked or APPROVE_HELP, **C.bw())
    open_key = f"open_{r.id}"
    c4.button("Close" if S.get(open_key) else "Details", key=f"btn_{open_key}", on_click=_toggle, args=(open_key,),
              type="tertiary")
    if S.get(open_key):
        with st.container(border=True):
            case_details(r, others, ids, d, blocked)


def case_details(r, others, ids, d, blocked) -> None:
    S = st.session_state
    st.caption(f"{r.id} · {r.area} · {r.source_file} line {r.line_number} · priority {triage.priority(r)} "
               "(severity, amount and the Skeptic's confidence)")
    clause = C.policy().get(f"{r.clause}|{r.area}")
    if clause:
        st.markdown(f"**Policy clause {C.esc(r.clause)}:** {C.esc(clause)}")
    lines = C.source_lines(r.source_file)
    head = lines[0] if lines and r.source_file.endswith(".csv") and int(r.line_number) != 1 else ""
    review._code((head + "\n" if head else "") + str(r.evidence))      # the column names make a CSV line readable
    rel = [int(x) for x in list(r.matched_lines) if int(x) != int(r.line_number) and 0 < int(x) <= len(lines)][:5]
    if rel:
        st.caption("Related " + ("line" if len(rel) == 1 else "lines") + " " + ", ".join(map(str, rel)) + ":")
        review._code("\n".join(lines[x - 1] for x in rel))
    if lines:
        with st.expander("Show it in the file"):
            n = int(r.line_number)
            lo, hi = max(1, n - 3), min(len(lines), n + 3)
            st.markdown(C.source_html(lines[lo - 1:hi], list(r.matched_lines), n, start=lo), unsafe_allow_html=True)
            st.caption(f"Lines {lo}-{hi} of {len(lines)}.")
    st.markdown(f"**Skeptic:** {r.skeptic_verdict} - {C.esc(r.skeptic_reason)}")
    st.markdown(f"**Proposed fix:** {C.esc(r.proposed_fix)}")
    clears = triage.CLEARS.get(str(r.clause))
    if clears:
        st.caption(f"Clears if {clears}.")
    if others:
        st.markdown("**Decided together with:** " + "; ".join(f"{o.id} {C.esc(o.title)} (clause {o.clause})" for o in others))
    note = S.notes.get(r.id, {})
    a, b = st.columns([1, 2])
    a.text_input("Owner", value=note.get("owner", ""), key=f"own_{r.id}", placeholder="Who follows this up?")
    b.text_input("Note", value=note.get("note", ""), key=f"note_{r.id}")
    st.button("Save owner and note", key=f"savenote_{r.id}", on_click=C.save_note, args=(r.id,))
    if d:
        st.caption(f"{d['status']}" + (f": {C.esc(d['reason'])}" if d.get("reason") else ""))
        x1, x2, _ = st.columns([1, 1.3, 3])
        x1.button("Undo", key=f"undo_{r.id}", on_click=review._undo_all, args=(ids,), disabled=bool(blocked))
        if d["status"] == "Rejected":
            x2.download_button("Save as test case", review.test_case_json(r, ids), file_name=f"case_{r.id}_{r.clause}.json",
                               mime="application/json", key=f"case_{r.id}",
                               help="A regression test: put it in tests/cases/ so this false alarm can never come back.")
    else:
        done = [i for i in ids if i in S.decisions]
        if done:
            st.caption(f"{len(done)} of {len(ids)} in this case already decided: "
                       + ", ".join(f"{i} {S.decisions[i]['status']}" for i in done) + ". The buttons act on the rest.")
            st.button(f"Undo those {len(done)}", key=f"undo_{r.id}", on_click=review._undo_all, args=(done,),
                      disabled=bool(blocked), type="tertiary")
            if len(done) == len(ids):
                return
        st.text_input("Reason to reject (required)", key=f"rej_{r.id}",
                      placeholder="Why is this not a problem? e.g. approved by phone, credit note received")
        who = review._entity(r)
        st.checkbox(f"Don't flag clause {r.clause} again for {who}", key=f"supp_{r.id}")
        if S.get(f"_rej_missing_{r.id}"):
            st.warning("Write a reason first: every rejection needs one for the audit trail.")
        st.button("Reject", key=f"rejbtn_{r.id}", disabled=bool(blocked),
                  on_click=review.reject_clicked, args=(r.id, str(r.clause), r.source_file, who, ids))


def findings_tab() -> None:
    S = st.session_state
    review.suppressed_notice()
    why = auth.review_block_reason()
    if why:
        st.warning(why)
    f = C.findings()
    triage.refresh_health()
    L = C.limits()
    shadow = triage.shadow_clauses()
    in_shadow = f[f.clause.astype(str).isin(shadow)]
    f = f[~f.clause.astype(str).isin(shadow)]
    t1, t2 = st.columns([3, 2], vertical_alignment="center")
    sev = t1.segmented_control("Severity", ["All", "High", "Medium", "Low"], default="All", key="rev_sev",
                               label_visibility="collapsed")
    # cases are built from every finding, then filtered, so a filter never splits a case
    all_groups = triage.cases(f)
    groups = all_groups if sev in (None, "All") else [g for g in all_groups if any(x.severity == sev for x in g)]
    # bulk approval takes whole cases only: every undecided member Confirmed by the Skeptic
    conf_ids = [x.id for g in groups
                if (todo := [x for x in g if x.id not in S.decisions]) and all(x.skeptic_verdict == "Confirmed" for x in todo)
                for x in todo]
    if S.get("_bulk_ask") and conf_ids:          # two steps: approving many findings at once is a deliberate act
        y, n = t2.columns(2)
        y.button(f"Yes, approve {len(conf_ids)}", type="primary", key="bulk_yes", disabled=bool(why), **C.bw(),
                 on_click=lambda ids=tuple(conf_ids): (review.bulk_approve(list(ids)), S.pop("_bulk_ask", None)))
        n.button("Cancel", key="bulk_no", on_click=lambda: S.pop("_bulk_ask", None), **C.bw())
        st.caption(f"This approves the {len(conf_ids)} undecided findings that the Skeptic confirmed, each as one "
                   "decision in the audit trail with the reason \"Bulk: confirmed by the Skeptic\".")
    else:
        t2.button(f"Approve all confirmed ({len(conf_ids)})", on_click=lambda: S.update(_bulk_ask=True),
                  disabled=not conf_ids or bool(why), key="bulk_all", help=APPROVE_HELP, **C.bw())

    def minor(r) -> bool:
        return (float(r.amount) < L["materiality"] and r.severity != "High") or triage.demoted(str(r.clause))
    main = [g for g in groups if not all(minor(r) for r in g)]
    small = [g for g in groups if all(minor(r) for r in g)]
    done = sum(all(x.id in S.decisions for x in g) for g in main)
    st.caption(f"{len(main)} cases ({done} decided), most important first: severity, amount and the Skeptic's "
               "confidence together, so a large Medium can come before a small High.")
    limit = len(main) if S.get("rev_all") else 20
    for g in main[:limit]:
        case_row(g)
    if len(main) > 20:
        st.toggle(f"Show all {len(main)} cases", key="rev_all")
    if small:
        with st.expander(f"Minor items ({sum(len(g) for g in small)}): under ${L['materiality']:,.0f}, or from a rule "
                         "reviewers usually reject"):
            for g in small:
                case_row(g)
    if not in_shadow.empty:
        with st.expander(f"Shadow rules would have raised {len(in_shadow)} more"):
            st.caption("Mark a few. Once enough are real, the rule can be promoted under Settings > Rules.")
            for r in in_shadow.itertuples():
                k = f"{r.clause}|{S.get('dataset') or 'sample'}|{r.source_file}:{r.line_number}"   # stable across runs and datasets
                c1, c2, c3 = st.columns([6, 1.3, 1.6], vertical_alignment="center")
                mark = S.get("shadow_marks", {}).get(k)
                c1.markdown(f"clause {r.clause}: {C.esc(r.title)}" + ("" if mark is None else f" - {'real' if mark else 'not a problem'}"))
                c2.button("Real", key=f"shy_{r.id}", on_click=review._mark_shadow, args=(k, 1), disabled=bool(why))
                c3.button("Not a problem", key=f"shn_{r.id}", on_click=review._mark_shadow, args=(k, 0), disabled=bool(why))


def review_page() -> None:
    tabs = st.tabs(["Findings", "Payment run", "Download"])
    with tabs[0]:
        findings_tab()
    with tabs[1]:
        pages.payment_gate()
    with tabs[2]:
        review.step4()


# ============================================================== Reports, Settings, Trust
def reports() -> None:
    tabs = st.tabs(["Summary", "Trends and risk", "Recovery", "Subscriptions"])
    with tabs[0]:
        pages.overview()
    with tabs[1]:
        pages_extra.trends_page()
    with tabs[2]:
        pages.recovery_page()
    with tabs[3]:
        pages.subscriptions_page()


def _remove_upload(label: str) -> None:
    S = st.session_state
    S.uploads.pop(label, None)
    S.get("custom", {}).pop(label, None)
    S.extra_opts.get("audit", {}).pop(label, None)
    if S.get("dataset") == label:
        custom.activate(None)
    from . import store
    store.save_uploads()


def data_settings() -> None:
    S = st.session_state
    ups = S.get("uploads", {})
    if not ups:
        st.caption("No uploaded data yet. Use Check new files on Home.")
        return
    for label, files in ups.items():
        with st.container(border=True):
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            c1.markdown(f"**{C.esc(label)}** &nbsp; <span class='th-muted'>{C.esc(', '.join(sorted(files)))}</span>",
                        unsafe_allow_html=True)
            c2.button("Remove", key=f"rm_{label}", on_click=_remove_upload, args=(label,), type="tertiary")
            run_analysis.column_matching(label)
            run_analysis.data_check(label)
    st.caption("Scheduled checks run outside the app: python scripts/watch.py FOLDER --alert (see the README).")


def users_settings() -> None:
    if not auth.enabled():
        st.info("Sign-in is off, so everyone can do everything (demo mode). Turn it on by creating users on the "
                "server: python scripts/add_user.py NAME ROLE (roles: preparer, reviewer, admin).")
    else:
        users = auth.load_users()
        st.dataframe(pd.DataFrame([dict(Name=n, Role=u["role"]) for n, u in users.items()]), hide_index=True, **C.dfw())
        st.caption("Whoever started a run cannot approve its findings. Manage users with scripts/add_user.py.")


def settings() -> None:
    tabs = st.tabs(["Policy", "Rules", "Data", "Users"])
    with tabs[0]:
        pages_extra.policy_limits()
    with tabs[1]:
        pages_extra.rule_health_section()
        pages_extra.learning_section()
    with tabs[2]:
        data_settings()
    with tabs[3]:
        users_settings()


def trust() -> None:
    st.caption("How we know the results are right: graded against planted problems and traps, every quote checked "
               "against the file, and a tamper-evident record of every decision.")
    tabs = st.tabs(["Scorecard", "Guardrails", "Audit trail"])
    with tabs[0]:
        pages_extra.scorecard_page()
    with tabs[1]:
        pages.guardrails()
    with tabs[2]:
        pages.live_activity()
