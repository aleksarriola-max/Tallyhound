"""Page: Run analysis (4 steps)."""
from __future__ import annotations

import zipfile

import pandas as pd
import streamlit as st

from . import common as C
from . import guide, review, sim

CARD_HEIGHT = 300
AGENT_KEYS = ["Payments", "Approvals", "Vendors", "Contracts", "Expenses", "Invoices"]


def run_page() -> None:
    S = st.session_state
    guide.tour()
    cols = st.columns(4)
    for i, (col, label) in enumerate(zip(cols, C.STEPS), start=1):
        col.button(label, key=f"step_btn_{i}", type="primary" if S.step == i else "secondary",
                   on_click=lambda n=i: S.update(step=n), **C.bw())
    st.write("")
    {1: step1, 2: step2, 3: review.step3, 4: review.step4}[S.step]()


# --------------------------------------------------------------------------
# Step 1
# --------------------------------------------------------------------------
def picker_options(card) -> list[str]:
    base = list(C.options().query("workflow == @card.id")["option"])
    return base + list(st.session_state.extra_opts.get(card.id, {}))


def workflow_card(card, running: bool) -> None:
    S = st.session_state
    with st.container(border=True, height=CARD_HEIGHT):
        st.checkbox(f"**{card.name}**", key=f"inc_{card.id}", disabled=running)
        st.caption(card.purpose)
        opts = picker_options(card)
        if card.picker == "multi":
            S.setdefault(f"sel_{card.id}", [card.default])
            picked = st.multiselect("Months", opts, key=f"sel_{card.id}", disabled=running,
                                    label_visibility="collapsed")
        else:
            S.setdefault(f"sel_{card.id}", card.default)
            picked = st.selectbox("Data", opts, key=f"sel_{card.id}", disabled=running,
                                  label_visibility="collapsed")
            picked = [picked] if picked else []
        if picked:
            if card.picker == "multi" and len(picked) > 1:
                body = "".join(f"<div>{o}: {C.option_preview(card.id, o)[1]}</div>" for o in picked)
                head = f"Preview · {len(picked)} months"
            else:
                head, body = C.option_preview(card.id, picked[0])
            st.markdown(f'<div class="th-preview"><b>{head}</b>{body}</div>', unsafe_allow_html=True)
            est = "Est. ~8 min per month" if card.id == "audit" else f"Est. ~{int(C.opt_row(card.id, picked[0])['est_min'])} min"
            last = C.opt_row(card.id, picked[0])["last_run"]
        else:
            st.markdown('<div class="th-preview"><b>Preview</b>Pick something to run</div>', unsafe_allow_html=True)
            est, last = "Est. -", "Never run"
        color = C.RELEASE if last == "Done" else C.MUTED
        st.markdown(f'<span class="th-muted">{est}</span> &nbsp; {C.chip("Last run: " + last, color)}',
                    unsafe_allow_html=True)


def ticked_selection(use_all: bool = False) -> dict:
    S = st.session_state
    out = {}
    for card in C.read_csv("workflow_cards.csv").itertuples():
        if use_all or S.get(f"inc_{card.id}"):
            sel = S.get(f"sel_{card.id}", [card.default] if card.picker == "multi" else card.default)
            sel = sel if isinstance(sel, list) else [sel]
            sel = [s for s in sel if s]
            if sel:
                out[card.id] = sel
    return out


def start_run(use_all: bool) -> None:
    S = st.session_state
    selection = ticked_selection(use_all)
    if not selection:
        return
    skip = frozenset(a for a in AGENT_KEYS if not S.get(f"adv_{a}", True))
    sim.start(sim.build_queue(selection, skip), fresh=bool(S.get("adv_fresh")))


def step1() -> None:
    S = st.session_state
    running = bool(S.sim and S.sim["running"])
    left, right = st.columns([7, 3])
    cards = list(C.read_csv("workflow_cards.csv").itertuples())
    with left:
        for r in range(2):
            cols = st.columns(2)
            for col, card in zip(cols, cards[r * 2:r * 2 + 2]):
                with col:
                    workflow_card(card, running)

        sel = ticked_selection()
        n = len(sel)
        b1, b2, _ = st.columns([2, 1.3, 4])
        if running:
            b1.button("Stop after current", on_click=sim.request_stop, **C.bw())
        else:
            from . import auth
            b1.button(f"Run selected ({n})", type="primary", disabled=n == 0 or not auth.can("run"),
                      on_click=start_run, args=(False,), **C.bw())
            b2.button("Run all", on_click=start_run, args=(True,), disabled=not auth.can("run"), **C.bw())
        if n == 0 and not running:
            st.caption("Tick at least one workflow")
        else:
            st.caption(f"Runs one at a time on the local GPU - total est. {sim.total_est(sel)} min")

        with st.expander("Add new data", expanded=False):
            up = st.file_uploader("Zip file", type="zip", label_visibility="collapsed", disabled=running)
            st.caption("Upload a zip with any of: payments.csv, approvals.csv, vendors.csv, contracts.txt, expenses.csv. "
                       "The columns must match the sample files in data/source/. The zip is read in memory and never written to disk.")
            if up is not None:
                add_zip(up)
        with st.expander("Advanced", expanded=False):
            c = st.columns(3)
            for i, name in enumerate(AGENT_KEYS):
                c[i % 3].checkbox(name, value=True, key=f"adv_{name}", disabled=running)
            c[2].checkbox("Skeptic review (always on, locked)", value=True, disabled=True)
            st.toggle("Start fresh (archive previous results)", key="adv_fresh", disabled=running)
            engine_settings(running)

    with right:
        queue_panel(sel, running)

    st.subheader("Recent runs")
    recent_runs()


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
        have = llm.models(S.get("adv_url") or llm.DEFAULT_URL)
        if not have:
            st.warning("Ollama is not reachable from here. The hosted demo cannot see your computer; run the app "
                       "locally (streamlit run app.py) with Ollama running.")
        elif (S.get("adv_model") or llm.DEFAULT_MODEL) not in have:
            st.warning("That model is not installed. Installed: " + ", ".join(have))
        else:
            st.success(f"Ollama is running with {len(have)} model(s).")


def add_zip(up) -> None:
    from . import custom
    S = st.session_state
    files, notes = custom.parse_zip(up.getvalue())
    for n in notes:
        st.warning(n)
    if not files:
        st.error("No usable audit files found. See the file layout above.")
        return
    label = up.name.rsplit(".", 1)[0][:40] or "uploaded"
    if label in C.read_csv("workflow_options.csv").query("workflow == 'audit'")["option"].values:
        label += " (upload)"
    if label not in S.get("uploads", {}):
        custom.add_upload(label, files)
    st.success(f"Added \"{label}\" to the Monthly audit picker: {len(files)} of 5 files. "
               "Pick it there and press Run selected.")
    st.dataframe(pd.DataFrame([dict(file=n, lines=len(v)) for n, v in sorted(files.items())]), hide_index=True, **C.dfw())
    column_matching(label)
    data_check(label)


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
            (st.warning if x["level"] == "warn" else st.caption)(f"`{x['file']}` - {x['message']}")
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


def queue_panel(selection: dict, running: bool) -> None:
    S = st.session_state
    with st.container(border=True):
        st.markdown("**Run queue**")
        frag = st.fragment(run_every=1 if running else None)(queue_body)
        frag(selection)


def queue_body(selection: dict) -> None:
    S = st.session_state
    sm = S.sim
    if sm and sm["queue"]:
        for it in sm["queue"]:
            st.markdown(f"**{it['label']}**")
            if it["status"] == "Running":
                done = sum(a["status"] in ("Done", "Skipped") for a in it["agents"])
                agent = next((a for a in it["agents"] if a["status"] not in ("Done", "Skipped")), it["agents"][-1])
                st.progress((sum(a["pct"] for a in it["agents"] if a["status"] != "Skipped") /
                             max(1, 100 * sum(a["status"] != "Skipped" for a in it["agents"]))))
                st.caption(f"Running: {agent['name']} agent ({min(done + 1, len(it['agents']))} of {len(it['agents'])})")
            elif it["status"] == "Done":
                st.markdown(C.chip(f"Done ({it['findings']} findings)", C.RELEASE), unsafe_allow_html=True)
            elif it["status"] == "Failed":
                bad = next(a for a in it["agents"] if a["status"] == "Failed")
                st.markdown(C.chip("Failed", C.HOLD) + f" &nbsp;<span class='th-muted'>{bad['name']} agent</span>",
                            unsafe_allow_html=True)
                if st.button("Retry", key=f"retry_{it['label']}"):
                    sim.retry()
                    st.rerun()
            else:
                st.markdown(C.chip("Queued", C.MUTED), unsafe_allow_html=True)
        if sm["running"] and sm["stop"]:
            st.caption("Will stop after the current item.")
    elif selection:
        for wf, opts in selection.items():
            for o in opts:
                st.markdown(f"**{o if wf != 'audit' else o + ' audit'}**")
                st.markdown(C.chip("Queued", C.MUTED), unsafe_allow_html=True)
    else:
        st.caption("Nothing queued. Tick a workflow to add it.")


def recent_runs() -> None:
    S = st.session_state
    base = C.read_csv("recent_runs.csv")
    extra = pd.DataFrame(S.recent_extra, columns=base.columns) if S.recent_extra else base.iloc[0:0]
    df = pd.concat([extra, base], ignore_index=True).astype(str)
    ev = st.dataframe(df, hide_index=True, on_select="rerun", selection_mode="single-row", key="recent_tbl", **C.dfw())
    rows = ev.selection.rows if ev and ev.selection else []
    pick = df.iloc[rows[0]] if rows else None
    target = "Findings"
    if pick is not None:
        run = str(pick["Run"])
        target = ("Findings" if "audit" in run else "Payment gate" if "Payment run" in run
                  else "Recovery" if "recovery" in run.lower() else "Subscriptions")
    st.button("Open results", disabled=pick is None, on_click=C.goto, args=(target,))
    if pick is None:
        st.caption("Select a row, then open its results.")


# --------------------------------------------------------------------------
# Step 2
# --------------------------------------------------------------------------
def step2() -> None:
    S = st.session_state
    running = bool(S.sim and S.sim["running"])
    frag = st.fragment(run_every=1 if running else None)(agents_body)
    frag()


def agents_body() -> None:
    S = st.session_state
    sm = S.sim
    item = sim.current(sm)
    roles = dict(zip(C.read_csv("agents.csv")["agent"], C.read_csv("agents.csv")["role"]))
    if item is None:
        st.info("Nothing is running yet. Go to step 1, tick a workflow and press Run selected.")
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
            if c3.button("Rerun this agent", key=f"rerun_{a['name']}"):
                sim.retry()
                st.rerun()
    all_done = bool(item) and all(a["status"] in ("Done", "Skipped") for a in agents)
    if st.button("Go to Review", type="primary", disabled=not all_done, key="go_review"):
        st.session_state.step = 3
        st.rerun()
    if sm:
        with st.expander("Run log", expanded=False):
            st.dataframe(pd.DataFrame(sm["log"][::-1]), hide_index=True, **C.dfw())
