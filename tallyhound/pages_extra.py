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
        files_c, key_c = challenge.generate(seed, diff)
        S["challenge"] = dict(label=f"challenge-{diff}-{seed}", files=files_c, key=key_c)
    ch = S.get("challenge")
    if ch:
        n = len(ch["key"])
        st.success(f"{ch['label']}: {sum(len(v) - 1 for k, v in ch['files'].items() if k.endswith('.csv'))} rows "
                   f"with {n} planted problems.")
        d1, d2, _ = st.columns([2, 2, 3])
        d1.download_button("Download zip (with answer key)", challenge.to_zip(ch["files"], ch["key"]),
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
        b1.button("Save limits", type="primary", on_click=_save_limits, **C.bw())
        b2.button("Reset to defaults", on_click=_reset_limits, **C.bw())
    pol = C.policy()
    rows = [dict(Area=k.split("|")[1], Clause=k.split("|")[0], Text=v) for k, v in pol.items()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, **C.dfw())
