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
    st.subheader("Scorecard")
    st.caption("Grades each engine against an answer key of planted problems. Recall: how many planted problems it "
               "found. Precision: how many of its findings were real. A finding counts when it points at the same "
               "file and line as a planted problem.")
    with_key = [SAMPLE] + [lbl for lbl in S.get("uploads", {}) if S.get("answer_keys", {}).get(lbl)]
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
                     "False alarms": s["false_alarms"], "Recall": _pct(s["recall"]), "Precision": _pct(s["precision"]),
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
        st.info("Only the built-in rules are scored so far. To score the AI: upload this data as a zip on Run "
                "analysis, run it with an Ollama engine, and the finished run appears here.")
    with st.expander(f"Every planted problem ({len(key)})", expanded=False):
        st.dataframe(pd.DataFrame(score.per_issue(key, runs)), hide_index=True, **C.dfw())

    st.subheader("Make a challenge")
    st.caption("A new fictional month with problems planted in it, and its answer key. Hard mode makes some problems "
               "subtle on purpose, and words a few so the fixed rules miss them - a good test of whether the AI adds value.")
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
    new = {k: float(S[f"lim_{k}"]) if k != "split_days" else int(S[f"lim_{k}"]) for k in rules.LIMITS}
    changed = {k: v for k, v in new.items() if v != C.limits()[k]}
    S["limits"] = new
    if changed:
        C.log_action("Reviewer", "Policy limits changed", "", ", ".join(f"{k}={v}" for k, v in changed.items()))


def _reset_limits() -> None:
    S = st.session_state
    S["limits"] = {}
    for k, v in rules.LIMITS.items():
        S[f"lim_{k}"] = v
    C.log_action("Reviewer", "Policy limits reset", "", "defaults")


def policy_page() -> None:
    S = st.session_state
    st.subheader("Policy")
    st.caption("The limits the built-in rules use. Changes apply to the next run on uploaded files. The sample "
               "company's findings are pre-written and do not change.")
    L = C.limits()
    with st.container(border=True):
        cols = st.columns(len(rules.LIMITS))
        for col, (k, label) in zip(cols, rules.LIMIT_LABELS.items()):
            S.setdefault(f"lim_{k}", L[k])
            if k == "split_days":
                col.number_input(label, min_value=1, max_value=30, step=1, key=f"lim_{k}")
            else:
                col.number_input(label, min_value=0.0, step=50.0 if L[k] >= 100 else 5.0, key=f"lim_{k}")
        b1, b2, _ = st.columns([1, 1, 4])
        from . import auth
        locked = not auth.can("policy")
        b1.button("Save limits", type="primary", on_click=_save_limits, disabled=locked, **C.bw())
        b2.button("Reset to defaults", on_click=_reset_limits, disabled=locked, **C.bw())
        if locked:
            st.caption("Only an admin can change the policy.")
    pol = C.policy()
    rows = [dict(Area=k.split("|")[1], Clause=k.split("|")[0], Text=v) for k, v in pol.items()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, **C.dfw())
    learning_section()


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
