"""Pages: Scorecard (how good is each engine?) and Policy (the limits the rules use)."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from . import challenge, custom, rules, score
from . import common as C

ENGINE_NAMES = {"rules": "Built-in rules", "rules+skeptic": "Rules + Ollama Skeptic", "ollama": "Ollama agents",
                "ollama-tools": "Ollama agents with tools"}
SAMPLE = "Sample company"


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _sample_key() -> list[dict]:
    return score.key_from_findings(C.load_findings_checked()[0], C.read_source)


def _dataset(pick: str) -> tuple[list[dict], dict[str, list[str]]]:
    S = st.session_state
    if pick == SAMPLE:
        return _sample_key(), {n: C.read_source(n) for n in rules.FILES.values()}
    k = S.answer_keys[pick]
    return (_sample_key() if k == "sample" else k), custom.view(pick)


def _history(pick: str) -> list[dict]:
    S = st.session_state
    keys = S.get("answer_keys", {})
    return [h for h in S.get("run_history", [])
            if h["label"] == pick or (pick == SAMPLE and keys.get(h["label"]) == "sample")]


def scorecard_page() -> None:
    S = st.session_state
    st.caption("Grades each engine against an answer key of planted problems. Recall: how many planted problems it "
               "found. Precision: how many of its findings were real. A finding counts when it points at the same "
               "file and line as a planted problem.")
    with_key = [SAMPLE] + [lbl for lbl in S.get("uploads", {}) if S.get("answer_keys", {}).get(lbl)]
    cur = S.get("dataset")
    if S.get("sc_pick") not in with_key:
        S["sc_pick"] = cur if cur in with_key else SAMPLE
    pick = st.selectbox("Data with an answer key", with_key, key="sc_pick")
    key, files = _dataset(pick)

    hits = rules.analyze(files, C.limits())
    runs = {"Built-in rules (now)": [dict(source_file=h.source_file, line_number=h.line_number,
                                         related_lines=[ln for _, ln in h.related]) for h in hits]}
    for h in _history(pick):
        name = f"{ENGINE_NAMES.get(h['engine'], h['engine'])}{' (' + h['model'] + ')' if h['model'] else ''} · {h['time']}"
        runs[name] = h["proposed"]

    rows = []
    for name, prop in runs.items():
        s = score.score(prop, key)
        sk = s.get("skeptic")
        rows.append({"Run": name, "Proposed": s["proposed"], "Found": f"{s['found']} of {s['planted']}",
                     "False alarms": s["false_alarms"],
                     "Traps flagged": f"{s['traps_flagged']} of {s['traps']}" if s["traps"] else "-",
                     "Recall": _pct(s["recall"]), "Precision": _pct(s["precision"]),
                     "Precision of Confirmed": _pct(sk["precision_confirmed"]) if sk else "-",
                     "Real problems doubted": sk["real_doubted"] if sk else "-",
                     "False alarms caught": sk["false_alarms_caught"] if sk else "-",
                     "_r": s["recall"], "_p": s["precision"]})
    df = pd.DataFrame(rows)
    st.dataframe(df.drop(columns=["_r", "_p"]), hide_index=True, **C.dfw())

    long = pd.concat([df[["Run"]].assign(Measure="Recall", Value=df["_r"]),
                      df[["Run"]].assign(Measure="Precision", Value=df["_p"])])
    long["Bar"] = long["Run"] + " - " + long["Measure"]
    order = [f"{r} - {m}" for r in df["Run"] for m in ("Recall", "Precision")]
    chart = (alt.Chart(long).mark_bar(size=16).encode(
        y=alt.Y("Bar:N", title=None, sort=order, axis=alt.Axis(labelLimit=420)),
        x=alt.X("Value:Q", title=None, axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
        color=alt.Color("Measure:N", scale=alt.Scale(domain=["Recall", "Precision"], range=[C.TEAL_DARK, C.TEAL]),
                        legend=None),
        tooltip=["Run", "Measure", alt.Tooltip("Value:Q", format=".0%")])
        .properties(height=30 * len(long) + 20))
    st.altair_chart(chart, **C.dfw())

    if len(runs) == 1:
        st.info("Only the built-in rules are scored so far. To score the AI: on Home press Check new files, pick "
                "this data, choose an Ollama engine under Engine and agents, and the finished run appears here.")
    n_traps = sum(k.get("expect") == "trap" for k in key)
    with st.expander(f"Every planted problem ({len(key) - n_traps})" + (f" and trap ({n_traps})" if n_traps else ""),
                     expanded=False):
        st.dataframe(pd.DataFrame(score.per_issue(key, runs)), hide_index=True, **C.dfw())

    st.subheader("Make a challenge")
    st.caption("A new fictional month with problems planted in it, plus traps: legitimate things that look suspicious "
               "(batch transfers, rent without a PO, discounts, instalments, voids, day-first dates, name variants...). "
               "A finding on a trap is a false alarm. Hard mode makes some problems subtle and words a few so fixed "
               "rules miss them - a fair test of whether the AI adds value.")
    c1, c2, c3 = st.columns([2, 1, 2], vertical_alignment="bottom")
    diff = c1.segmented_control("Difficulty", ["easy", "medium", "hard"], default="medium", key="ch_diff") or "medium"
    seed = int(c2.number_input("Seed", min_value=1, max_value=9999, value=1, step=1, key="ch_seed"))
    if c3.button("Make challenge", type="primary", **C.bw()):
        files_c, key_c, pdfs_c = challenge.generate_full(seed, diff)
        S["challenge"] = dict(label=f"challenge-{diff}-{seed}", files=files_c, key=key_c, pdfs=pdfs_c)
    ch = S.get("challenge")
    if ch:
        n = len(ch["key"])
        st.success(f"{ch['label']}: {sum(len(v) - 1 for k, v in ch['files'].items() if k.endswith('.csv'))} rows "
                   f"with {n} planted problems.")
        d1, d2, _ = st.columns([2, 2, 3])
        d1.download_button("Download zip (with answer key)", challenge.to_zip(ch["files"], ch["key"], ch.get("pdfs")),
                           file_name=f"{ch['label']}.zip", mime="application/zip", **C.bw())
        d2.button("Add to the Monthly audit picker", on_click=_add_challenge, **C.bw())
        if ch["label"] in S.get("uploads", {}):
            st.caption(f"Added. Pick \"{ch['label']}\" in the Monthly audit card on Run analysis and run it; "
                       "it is then scored here.")


def _add_challenge() -> None:
    ch = st.session_state["challenge"]
    custom.add_upload(ch["label"], ch["files"], key=score.key_from_csv(challenge.key_csv(ch["key"])))


def _save_limits() -> None:
    S = st.session_state
    new = {k: float(S[f"lim_{k}"]) if k not in ("split_days", "bank_days") else int(S[f"lim_{k}"]) for k in rules.LIMIT_LABELS}
    S["po_exempt_words"] = [w.strip() for w in S.get("lim_exempt_words", "").split(",") if w.strip()]
    S["po_exempt_vendors"] = [w.strip() for w in S.get("lim_exempt_vendors", "").splitlines() if w.strip()]
    changed = {k: v for k, v in new.items() if v != C.limits()[k]}
    S["limits"] = new
    if changed:
        C.log_action("Reviewer", "Policy limits changed", "", ", ".join(f"{k}={v}" for k, v in changed.items()))


def _reset_limits() -> None:
    S = st.session_state
    S["limits"] = {}
    for k, v in rules.LIMITS.items():
        S[f"lim_{k}"] = v
    S.pop("po_exempt_words", None)
    S.pop("po_exempt_vendors", None)
    S["lim_exempt_words"] = ", ".join(rules.PO_EXEMPT_WORDS)
    S["lim_exempt_vendors"] = ""
    C.log_action("Reviewer", "Policy limits reset", "", "defaults")


def policy_page() -> None:
    policy_limits()
    rule_health_section()
    learning_section()


def policy_limits() -> None:
    S = st.session_state
    st.caption("The limits the built-in rules use. Changes apply to the next run on uploaded files. The sample "
               "company's findings are pre-written and do not change.")
    L = C.limits()
    with st.container(border=True):
        items = list(rules.LIMIT_LABELS.items())
        for i in range(0, len(items), 4):
            cols = st.columns(4)
            for col, (k, label) in zip(cols, items[i:i + 4]):
                S.setdefault(f"lim_{k}", L[k])
                if k in ("split_days", "bank_days"):
                    col.number_input(label, min_value=0, max_value=30, step=1, key=f"lim_{k}")
                else:
                    col.number_input(label, min_value=0.0, step=50.0 if L[k] >= 100 else 1.0, key=f"lim_{k}")
        S.setdefault("lim_exempt_words", ", ".join(L["po_exempt_words"]))
        S.setdefault("lim_exempt_vendors", "\n".join(L["po_exempt_vendors"]))
        e1, e2 = st.columns(2)
        e1.text_area("Bills that need no PO: words in the vendor name (comma-separated)", key="lim_exempt_words", height=90)
        e2.text_area("Vendors that need no PO (one per line)", key="lim_exempt_vendors", height=90)
        b1, b2, _ = st.columns([1, 1, 4])
        from . import auth
        locked = not auth.can("policy")
        b1.button("Save limits", type="primary", on_click=_save_limits, disabled=locked, **C.bw())
        b2.button("Reset to defaults", on_click=_reset_limits, disabled=locked, **C.bw())
        if locked:
            st.caption("Only an admin can change the policy.")
    pol = C.policy()
    rows = [dict(Area=k.split("|")[1], Clause=k.split("|")[0], Text=v) for k, v in pol.items()]
    with st.expander(f"The policy clauses ({len(rows)})"):
        st.dataframe(pd.DataFrame(rows), hide_index=True, **C.dfw())


def _set_override(clause: str, value: str | None) -> None:
    S = st.session_state
    o = S.setdefault("rule_override", {})
    if value:
        o[clause] = value
    else:
        o.pop(clause, None)
    C.log_action("Reviewer", "Rule status changed", "", f"clause {clause}: {value or 'automatic'}")


def _promote(clause: str) -> None:
    S = st.session_state
    S["shadow"] = [c for c in S.get("shadow", []) if c != clause]
    C.log_action("Reviewer", "Shadow rule promoted", "", f"clause {clause}")


def rule_health_section() -> None:
    from . import auth, triage
    S = st.session_state
    st.subheader("How each rule is doing")
    st.caption(f"From your reviewers' decisions on uploaded data. A rule rejected {triage.DEMOTE_REJECT_RATE:.0%} or more of "
               f"the time (after {triage.DEMOTE_MIN_DECISIONS} decisions) is demoted to Minor items automatically - "
               "never deleted. You can restore it.")
    df = triage.rule_health()
    if df.empty:
        st.caption("No decisions on uploaded data yet.")
    else:
        show = df.copy()
        show["Reject rate"] = show["Reject rate"].map(lambda x: f"{100 * x:.0f}%")
        st.dataframe(show, hide_index=True, **C.dfw())
        for r in df[df.Status == "Demoted"].itertuples():
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            c1.warning(f"Clause {r.Clause} is demoted: reviewers rejected {r.Rejected} of {r.Decisions} of its findings. "
                       "Check its limits or exemptions above.")
            c2.button("Restore", key=f"restore_{r.Clause}", on_click=_set_override, args=(r.Clause, "normal"),
                      disabled=not auth.can("policy"))
    st.subheader("Shadow rules")
    st.caption("A rule in shadow mode still runs, but its findings stay out of the review queue until reviewers have "
               f"marked at least {triage.PROMOTE_MIN_MARKS} of them and {triage.PROMOTE_PRECISION:.0%} were real problems. "
               "Use it when you add or change a rule.")
    all_clauses = sorted({k.split("|")[0] for k in C.policy()}, key=lambda c: [int(x) for x in c.split(".")])
    st.multiselect("Clauses in shadow mode", all_clauses, key="shadow", disabled=not auth.can("policy"))
    for c in S.get("shadow", []):
        n, p = triage.shadow_precision(c)
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        c1.markdown(f"Clause **{c}**: {n} marked, {100 * p:.0f}% real" + (" - ready to promote" if triage.ready_to_promote(c) else ""))
        c2.button("Promote", key=f"promote_{c}", on_click=_promote, args=(c,),
                  disabled=not triage.ready_to_promote(c) or not auth.can("policy"))


def learning_section() -> None:
    from . import learn
    S = st.session_state
    st.subheader("Learned from reviewers")
    hints = learn.limit_hints(C.findings(), S.decisions, C.limits())
    for h in hints:
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        c1.info(f"{h['n']} findings under clause {h['clause']} were rejected and all were just over "
                f"${h['current']:,.0f}. Raise the limit to ${h['suggested']:,.0f}?")
        c2.button("Apply", key=f"hint_{h['clause']}", on_click=_apply_hint, args=(h["key"], h["suggested"]))
    sup = S.get("suppressions", [])
    if not sup and not hints:
        st.caption("Nothing yet. When a reviewer rejects a finding and ticks \"Don't flag this again\", it shows here.")
    for i, x in enumerate(sup):
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        c1.markdown(f"Clause **{x['clause']}** in `{x['source_file']}` for **{C.esc(x['entity'])}** - {C.esc(x['reason'])}")
        c2.button("Remove", key=f"unsupp_{i}", on_click=learn.unsuppress, args=(i,))


def _apply_hint(key: str, value: float) -> None:
    S = st.session_state
    S["limits"] = {**C.limits(), key: value}
    S[f"lim_{key}"] = value
    C.log_action("Reviewer", "Policy limit changed from a hint", "", f"{key}={value}")


# --------------------------------------------------------------------------
SEV_W = {"High": 3, "Medium": 2, "Low": 1}


def vendor_risk(f: pd.DataFrame) -> pd.DataFrame:
    """One row per supplier, employee or vendor named in the findings, scored by severity and money at stake."""
    from . import learn
    rows = []
    for r in f.itertuples():
        lines = C.source_lines(r.source_file)
        who = learn.entity(r.source_file, r.evidence, lines[0] if r.source_file.endswith(".csv") and lines else "")
        rows.append(dict(who=who, sev=r.severity, amount=float(r.amount), clause=str(r.clause)))
    if not rows:
        return pd.DataFrame(columns=["Who", "Findings", "High", "Amount flagged", "Clauses", "Risk score"])
    d = pd.DataFrame(rows)
    g = d.groupby("who").agg(Findings=("sev", "size"), High=("sev", lambda x: (x == "High").sum()),
                             amount=("amount", "sum"), Clauses=("clause", lambda x: ", ".join(sorted(set(x)))),
                             w=("sev", lambda x: sum(SEV_W[v] for v in x))).reset_index()
    g["Risk score"] = (g.w * 10 + (g.amount / 1000).clip(upper=30)).round(0).astype(int)
    g["Amount flagged"] = g.amount.map(C.money)
    return g.rename(columns={"who": "Who"}).sort_values("Risk score", ascending=False)[
        ["Who", "Findings", "High", "Amount flagged", "Clauses", "Risk score"]]


def trends_page() -> None:
    S = st.session_state
    st.subheader("Trends")
    hist = S.get("run_history", [])
    if not hist:
        st.info("No finished runs on uploaded data yet. Each run on your own files (or a challenge) adds a point here, "
                "so month-on-month changes show up.")
    else:
        rows = [dict(Run=f"{h['label']} · {h['time']}", Area=p.get("area", "?"), Severity=p.get("severity", "?"))
                for h in hist for p in h["proposed"]]
        if rows:
            d = pd.DataFrame(rows)
            chart = (alt.Chart(d).mark_bar().encode(
                x=alt.X("Run:N", sort=None, title=None, axis=alt.Axis(labelAngle=-20, labelLimit=220)),
                y=alt.Y("count():Q", title="Findings"),
                color=alt.Color("Area:N", legend=alt.Legend(orient="top", title=None)),
                tooltip=["Run", "Area", "count()"]).properties(height=280))
            st.altair_chart(chart, **C.dfw())
        st.dataframe(pd.DataFrame([dict(Run=h["label"], When=h["time"], Engine=ENGINE_NAMES.get(h["engine"], h["engine"]),
                                        Findings=len(h["proposed"]),
                                        High=sum(p.get("severity") == "High" for p in h["proposed"]),
                                        Flagged=C.money(sum(p.get("amount", 0) for p in h["proposed"])))
                                   for h in hist][::-1]), hide_index=True, **C.dfw())
    st.subheader("Who carries the most risk")
    st.caption("For the data in review. Score: 10 per finding weighted by severity (High 3, Medium 2, Low 1), plus up "
               "to 30 for the money at stake. A way to choose where to look first, not a verdict on anyone.")
    st.dataframe(vendor_risk(C.findings()).head(15), hide_index=True, **C.dfw())
