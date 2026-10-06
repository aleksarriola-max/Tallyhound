"""Tallyhound - finance audit console (fictional test data). Run: streamlit run app.py"""
import streamlit as st

st.set_page_config(layout="wide", page_title="Tallyhound", page_icon=None)

from tallyhound import common as C  # noqa: E402
from tallyhound import auth, custom, layout, sim, store  # noqa: E402

C.init_state()
C.inject_css()
if not auth.gate():
    st.stop()


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
    st.radio("Navigation", C.NAV, key="nav", label_visibility="collapsed",
             format_func=lambda p: {"Trust": "How we know it's right"}.get(p, p))
    st.fragment(run_every=1 if running else None)(ticker)()
    if st.session_state.get("custom"):
        labels = ["Sample company", *st.session_state.custom]
        cur = st.session_state.get("dataset")
        want = cur if cur in labels else "Sample company"
        if st.session_state.get("ds_pick") != want:
            st.session_state.ds_pick = want          # a run can switch the data in review; the box must follow
        st.selectbox("Data in review", labels, key="ds_pick",
                     on_change=lambda: custom.activate(None if st.session_state.ds_pick == "Sample company"
                                                       else st.session_state.ds_pick))
    if auth.current_user():
        st.caption(f"Signed in as {auth.current_user()} ({auth.role()})")
        st.button("Sign out", key="sign_out", on_click=auth.sign_out, type="tertiary")
    st.markdown(f"<div class='th-foot'>Quotes verified {C.quote_pct()}% · offline · work saved automatically</div>",
                unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.button("Reset demo", key="reset_demo", on_click=store.reset, type="tertiary", disabled=not auth.can("policy"),
              help=None if auth.can("policy") else "Only an admin can reset: it erases the audit trail.")
    if st.session_state.get("tour_off"):
        c2.button("Tour", key="tour_show", on_click=lambda: st.session_state.update(tour_off=False), type="tertiary")

C.banner_and_header()
if st.session_state.pop("_restored", False):
    st.toast("Restored your earlier work.")
if st.session_state.get("_restore_note"):
    st.warning(st.session_state.pop("_restore_note"))

PAGES = {
    "Home": layout.home,
    "Review": layout.review_page,
    "Reports": layout.reports,
    "Settings": layout.settings,
    "Trust": layout.trust,
}
try:
    PAGES.get(st.session_state.nav, layout.home)()
except Exception as e:                    # noqa: BLE001  - a broken page must still leave a way out
    if type(e).__module__.startswith("streamlit"):
        raise                             # st.rerun / st.stop and Streamlit's own errors work as usual
    st.error("This page hit a problem and could not be shown. Your saved work is untouched. Try another page, "
             "pick other data in the sidebar, or reset if it keeps happening.")
    with st.expander("Technical details"):
        st.exception(e)
    st.button("Reset saved work", key="reset_after_error", on_click=store.reset, disabled=not auth.can("policy"))

store.save_if_changed()
