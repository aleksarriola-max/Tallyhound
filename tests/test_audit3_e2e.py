"""Third audit, done by using the app in a real browser (October 2026). Each test is the smallest repro of one bug
seen there, and failed before its fix."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tallyhound import auth, store
from tallyhound import common as C

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def team(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TALLYHOUND_USERS", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "ITER", 1000)
    auth._fails.clear()
    for name, role in (("rita", "reviewer"), ("ada", "admin")):
        auth.add_user(name, role, f"pw-{name}-1")
    return tmp_path


def _signed_in(name: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input[0].input(name)
    at.text_input[1].input(f"pw-{name}-1")
    at.button[0].click()
    at.run()
    at.session_state.nav = "Review"
    at.run()
    return at


def _note(at: AppTest) -> str:
    return " ".join(w.value for w in at.warning)


# ---- the downloaded audit trail showed "14:2x" as the time of every agent proposal on the sample company
def test_the_sample_trail_has_real_times_from_the_recorded_run():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    trail = C.base_trail(C.findings())
    assert "14:2x" not in set(trail.time)
    times = dict(zip(trail.actor, trail.time))
    assert times["Payments agent"] == "14:28:21" and times["Expenses agent"] == "14:31:42"
    assert times["Skeptic"] == "14:32:10"              # September's run, not the older July one below it


# ---- an admin's reset was announced to an open tab as "a teammate saved at the same moment"
def test_a_teammates_reset_is_called_a_reset_in_the_open_tab(team):
    rita, ada = _signed_in("rita"), _signed_in("ada")
    rita.button(key="appr_F-11").click().run()
    ada.run()
    ada.button(key="reset_demo").click().run()
    assert json.loads(store._read(store.TEAM))["reset_id"]               # the emptied work carries the reset's id
    rita.button(key="appr_F-16").click().run()                           # rita's tab still holds the old work
    note = _note(rita)
    assert "reset the saved work" in note and "saved at the same moment" not in note
    saved = json.loads(store._read(store.TEAM))
    assert set(saved["decisions"]) == {"F-16"}                           # the reset stands; her new click is kept
    assert C.decisions_mismatch(saved["audit_log"], saved["decisions"]) == []


def test_an_ordinary_teammate_save_is_still_not_called_a_reset(team):
    rita, ada = _signed_in("rita"), _signed_in("ada")
    ada.button(key="appr_F-11").click().run()
    rita.button(key="appr_F-16").click().run()
    note = _note(rita)
    assert "saved at the same moment" in note and "reset" not in note


# ---- a one-off note above the page (e.g. after a save conflict) moved the tabs when it went away, so the next click
# threw the person back to the first tab (Review > Download > Prepare the downloads showed Findings again)
def _tabs_path(at: AppTest) -> list[int] | None:
    def walk(node, path):
        for i, child in getattr(node, "children", {}).items():
            if getattr(child, "type", None) == "tab_container":
                return path + [i]
            found = walk(child, path + [i])
            if found:
                return found
        return None
    return walk(at.main, [])


def test_a_one_off_note_does_not_shift_the_tabs():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state.nav = "Review"
    at.run()
    without = _tabs_path(at)
    at.session_state["_restore_note"] = "A teammate saved at the same moment."
    at.run()
    assert "A teammate saved" in _note(at)
    assert _tabs_path(at) == without
    at.run()                                                              # the note is gone: nothing moves either
    assert _tabs_path(at) == without


# ---- a change that is not a decision (shadow mode, a policy limit, a cleared hold) made in a tab a teammate had saved
# over since was dropped in the save conflict without a word
def test_a_shadow_mode_change_lost_in_a_save_conflict_is_reported(team):
    rita, ada = _signed_in("rita"), _signed_in("ada")
    rita.button(key="appr_F-11").click().run()                           # ada's tab is now behind
    ada.session_state.nav = "Settings"
    ada.run()
    box = next(m for m in ada.multiselect if m.label == "Clauses in shadow mode")
    box.set_value(["5.2"]).run()
    note = _note(ada)
    assert "shadow mode changed" in note and "Not kept" in note
    assert "F-11" in json.loads(store._read(store.TEAM))["decisions"]    # rita's decision stands


# ---- with sign-in off, the browser's Back button took the address back to the page without ?s=..., so the next
# reload (or a bookmark of that address) opened a new, empty session and the work seemed gone
def test_the_address_gets_its_session_id_back_after_the_back_button(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("TALLYHOUND_USERS", raising=False)
    at = AppTest.from_file(APP, default_timeout=60).run()
    sid = at.session_state.sid
    assert at.query_params["s"] in (sid, [sid])                          # a list in older Streamlit test runners
    at.query_params.pop("s")                                              # what Back leaves in the address
    at.run()
    assert at.query_params["s"] in (sid, [sid])                          # a list in older Streamlit test runners
