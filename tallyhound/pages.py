"""All pages except Run analysis."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from . import common as C
from . import sim

GATE_FILE = "payment_run_2026-10-01.csv"
EVENT_CFG = {"time": st.column_config.Column(width=70), "agent": st.column_config.Column(width=110),
             "message": st.column_config.Column(width=420)}
CHECKS = ["Vendor active", "Invoice open and approved", "Vendor matches invoice", "Not already paid",
          "Not duplicated in run", "Amount within invoice", "Bank change verified", "Bank matches master"]


def sev_counts(df: pd.DataFrame) -> dict:
    return {s: int((df.severity == s).sum()) for s in ("High", "Medium", "Low")}


# --------------------------------------------------------------------------
def overview() -> None:
    C.sample_only_notice()
    f = C.findings()
    gt = C.gate_totals(C.gate(GATE_FILE))
    rec, subs = C.recovery(), C.subscriptions()
    m = st.columns(4)
    m[0].metric("Held in payment run", C.money(gt["hold_amt"]))
    m[1].metric("Recoverable", C.money(rec.claim.sum()))
    m[2].metric("Annual savings", f"${subs.saving.sum():,.0f}")
    m[3].metric("Open findings", len(f))

    st.subheader("Findings by area")
    areas = ["Payments", "Approvals", "Vendors", "Contracts", "Expenses"]
    cols = st.columns(5)
    for col, a in zip(cols, areas):
        sub = f[f.area == a]
        sc = sev_counts(sub)
        with col, st.container(border=True):
            st.markdown(f"**{a}**")
            st.markdown(f"<span style='font-size:1.8rem;font-weight:700;color:{C.TEAL_DARK}'>{len(sub)}</span>", unsafe_allow_html=True)
            st.markdown(" ".join(C.badge(f"{k[0]} {v}", C.SEV_COLOR[k]) for k, v in sc.items()), unsafe_allow_html=True)

    left, right = st.columns([2, 3])
    with left:
        st.subheader("Findings by severity")
        sc = sev_counts(f)
        cdf = pd.DataFrame({"Severity": list(sc), "Findings": list(sc.values())})
        chart = alt.Chart(cdf).mark_bar().encode(
            x=alt.X("Severity:N", sort=list(sc), axis=alt.Axis(labelAngle=0, title=None)),
            y=alt.Y("Findings:Q", axis=alt.Axis(title=None, tickMinStep=1)),
            color=alt.Color("Severity:N", legend=None,
                            scale=alt.Scale(domain=list(sc), range=[C.SEV_COLOR[k] for k in sc])),
            tooltip=["Severity", "Findings"]).properties(height=280)
        st.altair_chart(chart, **C.dfw())
    with right:
        st.subheader("Latest events")
        ev = pd.concat([pd.DataFrame(st.session_state.sim["log"] if st.session_state.sim else []),
                        C.read_csv("events.csv")], ignore_index=True)
        st.dataframe(ev.head(8), hide_index=True, column_config=EVENT_CFG, **C.dfw())


# --------------------------------------------------------------------------
def findings_page() -> None:
    f = C.findings()
    sc = sev_counts(f)
    left, right = st.columns([5, 4])
    with left:
        c1, c2 = st.columns([3, 2], vertical_alignment="center")
        labels = {"All": f"All {len(f)}", **{k: f"{k} {v}" for k, v in sc.items()}}
        sev = c1.segmented_control("Severity", list(labels), default="All", format_func=labels.get,
                                   key="find_sev", label_visibility="collapsed")
        area = c2.selectbox("Area", ["All areas"] + sorted(f.area.unique()), label_visibility="collapsed", key="find_area")
        view = f if sev in (None, "All") else f[f.severity == sev]
        view = view if area == "All areas" else view[view.area == area]
        view = view.reset_index(drop=True)
        table = view[["id", "severity", "area", "title", "amount"]].rename(
            columns={"id": "ID", "severity": "Severity", "area": "Area", "title": "Title", "amount": "Amount"})
        ev = st.dataframe(table, hide_index=True, on_select="rerun", selection_mode="single-row", key="find_tbl",
                          column_config={"Amount": st.column_config.NumberColumn(format="$%.2f")}, height=520, **C.dfw())
    rows = ev.selection.rows if ev and ev.selection else []
    with right:
        if not rows or rows[0] >= len(view):
            st.info("Select a finding on the left to see the detail.")
            return
        detail(view.iloc[rows[0]])


def detail(r) -> None:
    with st.container(border=True):
        st.markdown(f"{C.sev_badge(r.severity)} &nbsp; **{r.id}** · {r.area} · {C.money(r.amount)}", unsafe_allow_html=True)
        st.markdown(f"**{C.esc(r.title)}**")
        st.markdown(f"**Policy clause {r.clause}**")
        st.markdown(f"> {C.esc(C.clause_text(r.area, r.clause))}")
        st.markdown("**Evidence**")
        st.code(r.evidence, language=None)
        st.caption(f"{r.source_file}, line {r.line_number}")
        st.markdown("**Innocent explanations considered**")
        st.markdown(C.esc(r.innocent_explanations))
        st.markdown(f"**Skeptic verdict: {r.skeptic_verdict}**")
        st.markdown(C.esc(r.skeptic_reason))
        st.markdown("**Proposed fix**")
        st.markdown(C.esc(r.proposed_fix))
        st.markdown("**Audit trail**")
        trail = C.full_trail(C.findings())
        st.dataframe(trail[trail.finding == r.id][["time", "actor", "action", "detail"]], hide_index=True, **C.dfw())


# --------------------------------------------------------------------------
def style_gate(df: pd.DataFrame):
    def row(r):
        bg = "#f6dede" if r["Decision"].startswith("HOLD") else "#e0f0e7"
        return [f"background-color:{bg};color:#07161f"] * len(r)

    def chk(v):
        return f"color:{C.RELEASE};font-weight:700" if v == "✓" else f"color:{C.HOLD};font-weight:700"

    ccols = [str(i) for i in range(1, 9)]
    return df.style.apply(row, axis=1).map(chk, subset=ccols)


def payment_gate() -> None:
    C.sample_only_notice()
    S = st.session_state
    runs = {"Payment run 2026-10-01": GATE_FILE, "Payment run 2026-10-08": "payment_run_2026-10-08.csv"}
    run = st.segmented_control("Run", list(runs), default="Payment run 2026-10-01", key="gate_run",
                               label_visibility="collapsed") or "Payment run 2026-10-01"
    gdf = C.gate(runs[run]).copy()
    cleared = S.cleared if run == "Payment run 2026-10-01" else {}
    gdf["final"] = [("RELEASE (cleared)" if str(l) in cleared else d) for l, d in zip(gdf.line, gdf.decision)]
    hold = gdf[gdf.final == "HOLD"]
    rel = gdf[gdf.final != "HOLD"]
    h1, h2, h3 = st.columns([5, 2, 2], vertical_alignment="center")
    h1.markdown(f"<span style='font-size:1.4rem;font-weight:700'>"
                f"<span style='color:{C.HOLD}'>{len(hold)} HOLD {C.money(hold.amount.sum())}</span> · "
                f"<span style='color:{C.RELEASE}'>{len(rel)} RELEASE</span></span>", unsafe_allow_html=True)
    h3.download_button("Export release file", data=rel[["line", "supplier", "invoice", "amount"]].to_csv(index=False),
                       file_name=f"release_{run.split()[-1]}.csv", mime="text/csv", **C.bw())

    show = pd.DataFrame({"Line": gdf.line.astype(int), "Decision": gdf.final, "Supplier": gdf.supplier,
                         "Invoice": gdf.invoice, "Amount": gdf.amount.map(C.money), "Reason": gdf.reason})
    for i in range(1, 9):
        show[str(i)] = ["✓" if v == "1" else "✗" for v in gdf[f"c{i}"]]
    widths = {"Line": 40, "Decision": 72, "Supplier": 118, "Invoice": 76, "Amount": 82, "Reason": 200}
    cfg = {k: st.column_config.Column(width=v) for k, v in widths.items()}
    cfg.update({str(i): st.column_config.Column(width=32) for i in range(1, 9)})
    ev = st.dataframe(style_gate(show), hide_index=True, on_select="rerun", selection_mode="multi-row",
                      key=f"gate_tbl_{run}", height=560, column_config=cfg, **C.dfw())
    st.caption("Checks: " + " · ".join(f"{i + 1} {c}" for i, c in enumerate(CHECKS)))

    rows = ev.selection.rows if ev and ev.selection else []
    sel_lines = [int(show.iloc[r].Line) for r in rows]
    holdable = [l for l in sel_lines if gdf[gdf.line == str(l)].final.iloc[0] == "HOLD"]
    c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
    reason = c1.text_input("Reason for clearing the hold (required)", key="clear_reason")
    if c2.button("Clear hold", disabled=not (holdable and reason.strip()) or run != "Payment run 2026-10-01"):
        for l in holdable:
            S.cleared[str(l)] = reason.strip()
            C.log_action("Reviewer", "Hold cleared", f"Line {l}", reason.strip())
        st.rerun()
    st.caption("Select HOLD lines in the table, give a reason, then clear the hold. Agents never release a payment." +
               ("" if run == "Payment run 2026-10-01" else " Clearing is only enabled for run 2026-10-01 in this demo."))


# --------------------------------------------------------------------------
def recovery_page() -> None:
    C.sample_only_notice()
    rec = C.recovery()
    m = st.columns(4)
    for col, (k, v) in zip(m, [("Invoices", 12), ("Item lines", 27), ("Suppliers", 4), ("Credit notes", 3)]):
        col.metric(k, v)
    st.caption("Analysis date 2026-10-20")
    show = rec.rename(columns={"supplier": "Supplier", "invoice": "Invoice", "source": "Source", "reason": "Reason",
                               "claim": "Claim", "status": "Status"})
    st.dataframe(show, hide_index=True, height=35 * (len(show) + 1) + 3,
                 column_config={"Claim": st.column_config.NumberColumn(format="dollar")}, **C.dfw())
    st.markdown(f"**Total recoverable: {C.money(rec.claim.sum())}**")
    st.caption("Agents propose claims. A person approves them on the Review step.")


def subscriptions_page() -> None:
    C.sample_only_notice()
    s = C.subscriptions()
    m = st.columns(4)
    m[0].metric("Tools", len(s))
    m[1].metric("User logins", int(s.licences.sum()))
    m[2].metric("Idle licences", int((s.licences - s.active_90d).sum()))
    m[3].metric("Annual saving", f"${s.saving.sum():,.0f}")
    st.caption("Analysis date 2026-10-31")
    show = pd.DataFrame({"Tool": s.tool, "Licences": s.licences,
                         "Active (90 days)": (100 * s.active_90d / s.licences).round(0),
                         "Annual cost": s.annual_cost.map("${:,}".format), "Proposed action": s.proposed_action,
                         "Saving": s.saving.map("${:,}".format)})
    st.dataframe(show, hide_index=True, height=35 * (len(show) + 1) + 3, column_config={
        "Active (90 days)": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%")}, **C.dfw())


# --------------------------------------------------------------------------
def live_feed_body() -> None:
    S = st.session_state
    live = pd.DataFrame(S.sim["log"][::-1] if S.sim else [], columns=["time", "agent", "message"])
    ev = pd.concat([live, C.read_csv("events.csv")], ignore_index=True)
    st.dataframe(ev, hide_index=True, height=520, column_config=EVENT_CFG, **C.dfw())
    st.caption("Refreshes every 3 seconds.")


def live_activity() -> None:
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Event feed")
        st.fragment(run_every=3)(live_feed_body)()
    with right:
        with st.container(border=True):
            st.markdown("**Always-on monitor**")
            for r in C.read_csv("monitor.csv").itertuples():
                st.markdown(f"{r.item}: <b>{r.value}</b>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("**Agents**")
            item = sim.current(st.session_state.sim)
            status = {a["name"]: a["status"] for a in item["agents"]} if item else {}
            colors = {"Waiting": C.MUTED, "Running": C.TEAL, "Done": C.RELEASE, "Failed": C.HOLD, "Skipped": C.MUTED}
            for n in sim.agent_names():
                s = status.get(n, "Idle")
                st.markdown(f"{n} &nbsp; {C.chip(s, colors.get(s, C.MUTED))}", unsafe_allow_html=True)


# --------------------------------------------------------------------------
def evidence_viewer() -> None:
    f = C.findings()
    labels = {r.id: f"{r.id} · {r.area} · {r.title[:60]}" for r in f.itertuples()}
    fid = st.selectbox("Finding", list(labels), format_func=labels.get)
    r = f[f.id == fid].iloc[0]
    lines = C.source_lines(r.source_file)
    main_ok = lines[r.line_number - 1] == r.evidence if 0 < r.line_number <= len(lines) else False
    if not main_ok:  # defensive: never show an unverified claim
        st.error("Quote not found in the source file. This claim is hidden.")
        return
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown(f"{C.sev_badge(r.severity)} &nbsp; **{r.id}** · {r.area} · clause {r.clause}", unsafe_allow_html=True)
        st.markdown("**Agent claim**")
        st.markdown(C.esc(r.title))
        st.markdown(f"**Clause {r.clause}**")
        st.markdown(f"> {C.esc(C.clause_text(r.area, r.clause))}")
        st.markdown(f'<div style="background:#e0f0e7;border:1px solid {C.RELEASE};color:#07161f;border-radius:6px;padding:.5rem .7rem">'
                    f'<b>Quote check passed:</b> {r.matched_n} line(s) matched in {r.source_file}</div>', unsafe_allow_html=True)
        st.caption(f"Main evidence on line {r.line_number}. Related lines are shown in light blue.")
    with right:
        st.markdown(f"**{r.source_file}**")
        with st.container(height=470):
            st.markdown(C.source_html(lines, list(r.matched_lines), main=int(r.line_number)), unsafe_allow_html=True)


# --------------------------------------------------------------------------
def guardrails() -> None:
    f = C.findings()
    fmt = dict(quote_pct=C.quote_pct(), n_findings=len(f))
    g = list(C.read_csv("guardrails.csv").itertuples())
    for r in range(2):
        cols = st.columns(3)
        for col, item in zip(cols, g[r * 3:r * 3 + 3]):
            with col, st.container(border=True, height=210):
                st.markdown(f"**{item.name}** &nbsp; {C.badge('Active', C.RELEASE)}", unsafe_allow_html=True)
                st.markdown(C.esc(item.description))
                st.markdown(f"<span style='font-size:1.15rem;font-weight:700;color:{C.TEAL_DARK}'>{item.metric.format(**fmt)}</span>",
                            unsafe_allow_html=True)
    tamper_demo(f)
    st.subheader("Blocked attempts")
    st.caption("None this session.")
    st.dataframe(pd.DataFrame(columns=["Time", "Agent", "Attempt", "Blocked by"]), hide_index=True, **C.dfw())


def tamper_demo(f: pd.DataFrame) -> None:
    """Let a visitor try to sneak a made-up quote past the quote check."""
    st.subheader("Try to fool the quote check")
    st.caption("Pick a finding, change its quoted line - even one character - and check it. A finding is only shown "
               "when its quote is an exact line of the source file at the stated line number.")
    if f.empty:
        st.caption("No findings to try it on.")
        return
    pick = st.selectbox("Finding", list(f.id), format_func=lambda i: f"{i} - {f.set_index('id').loc[i, 'title']}",
                        key="tamper_pick")
    r = f.set_index("id").loc[pick]
    text = st.text_area("Quoted line (edit it)", value=r.evidence, key=f"tamper_text_{pick}", height=90)
    one = pd.DataFrame([dict(id=pick, severity=r.severity, area=r.area, clause=r.clause, amount=r.amount, title=r.title,
                             skeptic_verdict=r.skeptic_verdict, evidence=text, related_evidence=r.related_evidence,
                             source_file=r.source_file, line_number=r.line_number,
                             innocent_explanations="", skeptic_reason="", proposed_fix="")])
    ok, _ = C.check_findings(one, C.source_lines)
    if not ok.empty:
        st.success(f"Verified: this is exactly line {r.line_number} of {r.source_file}. The finding would be shown.")
    else:
        lines = C.source_lines(r.source_file)
        actual = lines[int(r.line_number) - 1] if 0 < int(r.line_number) <= len(lines) else ""
        st.error(f"Blocked: line {r.line_number} of {r.source_file} does not say that. The finding would be hidden.")
        st.caption("The file actually says:")
        st.code(actual or "(no such line)", language=None)
