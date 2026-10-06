"""Uploading and checking files, engine settings and run status (used by the Check new files dialog)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import common as C
from . import sim

AGENT_KEYS = ["Payments", "Approvals", "Vendors", "Contracts", "Expenses", "Invoices"]



# --------------------------------------------------------------------------

def engine_settings(running: bool) -> None:
    """How uploaded files are analysed. The sample company always uses the simulated run."""
    from . import llm
    S = st.session_state
    st.markdown("**Engine for uploaded files**")
    st.selectbox("Engine", ["Built-in rules", "Built-in rules + Ollama Skeptic", "Ollama agents", "Ollama agents with tools"], key="adv_engine", disabled=running,
                 label_visibility="collapsed")
    st.caption("Built-in rules are fixed tests: fast and repeatable. Ollama agents use a model on this computer "
               "to read the files, with a Skeptic that challenges each finding. Rules + Skeptic finds candidates with the fixed "
               "rules and lets the model only challenge them: better coverage than the model alone. Agents with tools query "
               "the files (filter rows, find duplicates) instead of reading them whole: for large files.")
    if S.get("adv_engine") in ("Ollama agents", "Built-in rules + Ollama Skeptic", "Ollama agents with tools"):
        c1, c2 = st.columns(2)
        c1.text_input("Model", value=llm.DEFAULT_MODEL, key="adv_model", disabled=running)
        c2.text_input("Model server address", value=llm.DEFAULT_URL, key="adv_url", disabled=running,
                      help="Ollama: http://localhost:11434. LM Studio, vLLM or llama.cpp: their address ending in /v1, "
                           "for example http://localhost:1234/v1")
        refused = llm.check_url(S.get("adv_url") or llm.DEFAULT_URL)
        have = [] if refused else llm.models(S.get("adv_url") or llm.DEFAULT_URL)
        if refused:
            st.warning(refused)
        elif not have:
            st.warning("Ollama is not reachable from here. The hosted demo cannot see your computer; run the app "
                       "locally (streamlit run app.py) with Ollama running.")
        elif (S.get("adv_model") or llm.DEFAULT_MODEL) not in have:
            st.warning("That model is not installed. Installed: " + ", ".join(have))
        else:
            st.success(f"Ollama is running with {len(have)} model(s).")


def dataset_label(filename: str) -> str:
    """The name a dataset gets from its zip's file name: letters, digits, spaces and .-_ only, so it can never carry
    markup or a link into the page."""
    import re
    return re.sub(r"[^\w .-]+", "_", filename.rsplit(".", 1)[0]).strip(" ._")[:40] or "uploaded"


def add_zip(up) -> str | None:
    """Read an uploaded zip, register it, and show what was found. Returns the dataset name."""
    from . import custom
    S = st.session_state
    files, notes = custom.parse_zip(up.getvalue())
    if not files:
        for n in notes:
            st.warning(n)
        st.error("No usable audit files found.")
        return None
    label = dataset_label(up.name)
    if label in C.read_csv("workflow_options.csv").query("workflow == 'audit'")["option"].values:
        label += " (upload)"
    if label not in S.get("uploads", {}):
        custom.add_upload(label, files)
    st.success(f"Read \"{C.esc(label)}\": {len(files)} file(s).")
    with st.expander(f"Files ({len(files)})" + (f" and {len(notes)} note(s)" if notes else "")):
        for n in notes:
            st.caption(n)
        st.dataframe(pd.DataFrame([dict(file=n, lines=len(v)) for n, v in sorted(files.items())]), hide_index=True, **C.dfw())
    column_matching(label)
    data_check(label)
    return label


def _set_date_order(label: str, name: str) -> None:
    S = st.session_state
    S.setdefault("date_order", {}).setdefault(label, {})[name] = S[f"dord_{label}_{name}"]


def data_check(label: str) -> None:
    """Show what Tallyhound believes about the files before it analyses them."""
    from . import custom
    items = custom.profile(label)
    if not items:
        return
    warn = [x for x in items if x["level"] == "warn"]
    with st.expander(f"Data check: {len(warn)} thing(s) to confirm" if warn else "Data check: looks right", expanded=bool(warn)):
        for x in items:
            (st.warning if x["level"] == "warn" else st.caption)(f"`{x['file']}` - {C.esc(x['message'])}")
            if x.get("kind") == "dates":
                st.radio("Dates in " + x["file"], ["day-first", "month-first"], key=f"dord_{label}_{x['file']}",
                         horizontal=True, index=None, on_change=_set_date_order, args=(label, x["file"]))


def _save_matching(label: str, name: str, missing: list[str]) -> None:
    import csv

    from . import columns, custom
    S = st.session_state
    m = custom.mapping(label).setdefault(name, {})
    for col in missing:
        v = S.get(f"map_{label}_{name}_{col}", "(choose)")
        if v == "(not in file)":
            m[col] = ""
        elif v != "(choose)":
            m[col] = v
    lines = S_lines(label, name)
    if lines and S.get(f"mapremember_{label}_{name}", True):
        S.setdefault("presets", {})[columns.signature(name, next(csv.reader([lines[0]])))] = dict(m)


def column_matching(label: str) -> None:
    """Let a person say which of their columns holds each field the checks need."""
    import csv

    from . import custom
    todo = custom.unmatched(label)
    if not todo:
        return
    st.markdown("**Match your columns**")
    st.caption("Some files use different column names. Pick which of your columns holds each field. Choose "
               "\"(not in file)\" when you do not have it; checks that need it then find nothing for that field.")
    from . import columns
    for name, missing in todo.items():
        head = next(csv.reader([S_lines(label, name)[0]])) if S_lines(label, name) else []
        guess = columns.suggest(missing, head)
        with st.container(border=True):
            st.markdown(f"`{name}`" + (" - suggestions pre-filled, please check them" if guess else ""))
            cols = st.columns(min(4, len(missing)))
            opts = ["(choose)", "(not in file)", *head]
            for i, col in enumerate(missing):
                cols[i % len(cols)].selectbox(col, opts, index=opts.index(guess[col]) if col in guess else 0,
                                              key=f"map_{label}_{name}_{col}")
            st.checkbox("Remember this matching for files with the same columns", value=True,
                        key=f"mapremember_{label}_{name}")
            st.button("Save column matching", key=f"mapsave_{label}_{name}", on_click=_save_matching,
                      args=(label, name, missing))


def S_lines(label: str, name: str) -> list[str]:
    return st.session_state.uploads[label].get(name, [])



def recent_runs() -> None:
    S = st.session_state
    base = C.read_csv("recent_runs.csv")
    extra = pd.DataFrame(S.recent_extra, columns=base.columns) if S.recent_extra else base.iloc[0:0]
    df = pd.concat([extra, base], ignore_index=True).astype(str)
    ev = st.dataframe(df, hide_index=True, on_select="rerun", selection_mode="single-row", key="recent_tbl", **C.dfw())
    rows = ev.selection.rows if ev and ev.selection else []
    pick = df.iloc[rows[0]] if rows else None
    target = "Review"
    if pick is not None:
        run = str(pick["Run"])
        target = "Review" if ("audit" in run or "Payment run" in run) else "Reports"
    st.button("Open results", disabled=pick is None, on_click=C.goto, args=(target,), key="recent_open")
    if pick is None:
        st.caption("Select a row, then open its results.")


# --------------------------------------------------------------------------

def agents_body() -> None:
    S = st.session_state
    sm = S.sim
    item = sim.current(sm)
    roles = dict(zip(C.read_csv("agents.csv")["agent"], C.read_csv("agents.csv")["role"]))
    if item is None:
        st.info("Nothing is running yet. Use Check new files on Home to start a run.")
        agents = [dict(name=n, status="Waiting", pct=0, secs=0) for n in sim.agent_names()]
        title = "No run yet"
    else:
        agents, title = item["agents"], f"{item['label']} - {item['status']}"
    st.markdown(f"**{title}**")
    colors = {"Waiting": C.MUTED, "Running": C.TEAL, "Done": C.RELEASE, "Failed": C.HOLD, "Skipped": C.MUTED}
    for a in agents:
        c1, c2, c3, c4 = st.columns([3, 1.2, 4, 0.8], vertical_alignment="center")
        c1.markdown(f"**{a['name']}**<br><span class='th-muted'>{roles.get(a['name'], '')}</span>", unsafe_allow_html=True)
        c2.markdown(C.chip(a["status"], colors[a["status"]]), unsafe_allow_html=True)
        c3.progress(a["pct"] / 100 if a["status"] != "Skipped" else 0.0)
        c4.markdown(f"{a['secs'] // 60}:{a['secs'] % 60:02d}")
        if a["status"] == "Failed":
            if c3.button("Retry", key=f"rerun_{a['name']}"):
                sim.retry()
                st.rerun()
    all_done = bool(item) and all(a["status"] in ("Done", "Skipped") for a in agents)
    st.button("Go to Review", type="primary", disabled=not all_done, key="go_review", on_click=C.goto, args=("Review",))
    if sm:
        with st.expander("Run log", expanded=False):
            st.dataframe(pd.DataFrame(sm["log"][::-1]), hide_index=True, **C.dfw())
