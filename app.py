"""Tallyhound - finance audit console (fictional test data). Run: streamlit run app.py"""
import streamlit as st

st.set_page_config(layout="wide", page_title="Tallyhound", page_icon=None)

from tallyhound import common as C  # noqa: E402
from tallyhound import custom, pages, pages_extra, run_analysis, sim, store  # noqa: E402

C.init_state()
C.inject_css()


def ticker() -> None:
    """Advances the simulated run once a second, whichever page is open."""
    if sim.tick():
        st.rerun()
    s = st.session_state.sim
    if s and s["running"]:
        item = next((i for i in s["queue"] if i["status"] == "Running"), None)
        st.caption(f"Run in progress{': ' + item['label'] if item else ''}")


running = bool(st.session_state.sim and st.session_state.sim["running"])

with st.sidebar:
    st.markdown("### TALLYHOUND")
    st.radio("Navigation", C.NAV, key="nav", label_visibility="collapsed")
    st.fragment(run_every=1 if running else None)(ticker)()
    if st.session_state.get("custom"):
        labels = ["Sample company", *st.session_state.custom]
        cur = st.session_state.get("dataset")
        st.selectbox("Data in review", labels, index=labels.index(cur) if cur in labels else 0, key="ds_pick",
                     on_change=lambda: custom.activate(None if st.session_state.ds_pick == "Sample company"
                                                       else st.session_state.ds_pick))
    st.caption("Your decisions are saved automatically, so a reload keeps them.")
    st.button("Reset demo", key="reset_demo", on_click=store.reset, type="tertiary")
    st.markdown(
        '<div class="th-guard"><b>GUARDRAILS</b><br>Blocked internet attempts: 0<br>'
        f"Quotes verified: {C.quote_pct()}%</div>",
        unsafe_allow_html=True,
    )

C.banner_and_header()
if st.session_state.pop("_restored", False):
    st.toast("Restored your earlier work.")

PAGES = {
    "Run analysis": run_analysis.run_page,
    "Overview": pages.overview,
    "Findings": pages.findings_page,
    "Payment gate": pages.payment_gate,
    "Recovery": pages.recovery_page,
    "Subscriptions": pages.subscriptions_page,
    "Live activity": pages.live_activity,
    "Evidence viewer": pages.evidence_viewer,
    "Scorecard": pages_extra.scorecard_page,
    "Policy": pages_extra.policy_page,
    "Guardrails": pages.guardrails,
}
PAGES[st.session_state.nav]()

store.save_if_changed()
