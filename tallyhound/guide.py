"""Guided tour: a short checklist that walks a first-time visitor through the whole flow."""
from __future__ import annotations

import streamlit as st

from . import common as C
from . import sim


def _progress() -> list[tuple[str, str, bool]]:
    """(title, hint, done) for each tour step, worked out from what the visitor has actually done."""
    S = st.session_state
    run = S.get("sim")
    queue = run["queue"] if run else []
    own = any(i.get("custom") for i in queue)   # a run on uploaded files has no planted failure
    failed_now = any(i["status"] == "Failed" for i in queue)
    dc = C.decision_counts(C.findings())
    rejected = [v for v in S.decisions.values() if v.get("status") == "Rejected"]
    return [
        ("Start a run", "Press Start demo run. It audits the sample company's September and checks its payment run.",
         run is not None),
        ("Watch an agent fail", "The Expenses agent is set to fail once, on purpose. Wait about 20 seconds for it.",
         run is not None and (own or failed_now or not S.fail_pending)),
        ("Retry it", "Press Retry on the failed run. The agent restarts and the queue carries on.",
         run is not None and (own or not S.fail_pending)),
        ("Let the run finish", "Wait until both items say Done. Progress shows on this page.",
         bool(queue) and all(i["status"] == "Done" for i in queue)),
        ("Review the findings", "In Review, approve one case and reject another (open Details to reject; it needs a reason).",
         dc["approved"] >= 1 and len(rejected) >= 1),
        ("Download the results", "In Review > Download, download the Excel workbook or the PDF memo.",
         bool(S.get("tour_downloaded"))),
    ]


def demo_start() -> None:
    """Start the demo run on the sample company."""
    from . import layout
    st.session_state["_tour_started"] = True
    layout._start(layout.SAMPLE)


def mark_downloaded() -> None:
    st.session_state["tour_downloaded"] = True


def _hide() -> None:
    st.session_state["tour_off"] = True


def _show() -> None:
    st.session_state["tour_off"] = False


def tour() -> None:
    S = st.session_state
    if S.get("tour_off"):
        return
    steps = _progress()
    nxt = next((i for i, s in enumerate(steps) if not s[2]), None)
    with st.container(border=True, key="tour_box"):
        head, hide = st.columns([6, 1])
        if nxt is None:
            head.markdown("**Guided tour complete.** You ran the agents, recovered from a failure, reviewed "
                          "the findings and downloaded the results. Try the other pages from the sidebar.")
        else:
            head.markdown(f"**Guided tour - step {nxt + 1} of {len(steps)}: {steps[nxt][0]}**")
            head.caption(steps[nxt][1])
        hide.button("Hide", key="tour_hide", on_click=_hide, type="tertiary")
        line = "  \n".join(
            f"{'Done' if done else ('Next' if i == nxt else 'To do')} - {title}"
            for i, (title, _, done) in enumerate(steps))
        with st.expander("All steps", expanded=False):
            st.markdown(line)
        if nxt == 0:
            st.button("Start demo run", key="tour_start", type="primary", on_click=demo_start)
        elif nxt in (1, 2) and S.sim and any(i["status"] == "Failed" for i in S.sim["queue"]):
            st.button("Retry the failed agent", key="tour_retry", type="primary", on_click=sim.retry)
        elif nxt == 4:
            st.button("Go to review", key="tour_review", type="primary", on_click=lambda: S.update(nav="Review"))
        elif nxt == 5:
            st.button("Go to review", key="tour_dl", type="primary", on_click=lambda: S.update(nav="Review"))
