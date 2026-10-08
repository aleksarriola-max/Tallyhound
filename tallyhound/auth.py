"""Optional sign-in with roles, and segregation of duties.

Off by default, so the public demo stays open. Turn it on by creating users with
    python scripts/add_user.py <name> <role>
which writes .tallyhound_state/users.json (passwords are stored only as salted PBKDF2 hashes).

Roles: preparer (upload and run), reviewer (approve, reject, clear holds), admin (everything, plus Policy).
Segregation of duties: nobody may approve or reject findings from a run they started themselves.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

import streamlit as st

MAX_FAILS = 5            # wrong passwords in a row for one name ...
LOCK_SECONDS = 60        # ... lock that name for this long
_fails: dict[str, list[float]] = {}
_lock = threading.Lock()

ROLES = {"preparer": {"run"}, "reviewer": {"review"}, "admin": {"run", "review", "policy"}}
ITER = 200_000


def users_file() -> Path:
    from . import store
    return Path(os.environ.get("TALLYHOUND_USERS") or store.state_dir() / "users.json")


BROKEN: dict[str, dict] = {"__unreadable__": {}}      # stands for "sign-in is on but nobody can sign in" (fail closed)


def load_users() -> dict:
    """The users, or {} when sign-in is off. A users file that exists but cannot be read keeps sign-in ON and lets
    nobody in - a damaged file must never turn the app into open admin access."""
    p = users_file()
    if not p.exists():
        return {}
    try:
        users = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(users, dict) and users and all(
                isinstance(u, dict) and {"role", "salt", "hash"} <= set(u) for u in users.values()):
            return users
    except (OSError, ValueError):
        pass
    return dict(BROKEN)


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    return salt, hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITER).hex()


def add_user(name: str, role: str, password: str) -> None:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    users = {k: v for k, v in load_users().items() if k != "__unreadable__"}
    salt, h = hash_password(password)
    users[name] = dict(role=role, salt=salt, hash=h)
    from . import store
    store.write_private(users_file(), json.dumps(users, indent=2))


def locked_for(name: str) -> int:
    """Seconds left before this name may try again (0 when it may)."""
    with _lock:
        n, last = _fails.get(name.lower(), [0, 0.0])
    left = LOCK_SECONDS - (time.time() - last) if n >= MAX_FAILS else 0
    return max(0, int(left + 0.999))


def _record(name: str, ok: bool) -> None:
    with _lock:
        if ok:
            _fails.pop(name.lower(), None)
        else:
            n, last = _fails.get(name.lower(), [0, 0.0])
            if n >= MAX_FAILS and time.time() - last >= LOCK_SECONDS:
                n = 0                                 # the lock expired: start counting again
            _fails[name.lower()] = [n + 1, time.time()]
            if len(_fails) > 10_000:                  # made-up names must not grow this without limit
                for k in sorted(_fails, key=lambda k: _fails[k][1])[:5_000]:
                    _fails.pop(k, None)


def check(name: str, password: str) -> str | None:
    if locked_for(name):
        return None
    r = _check(name, password)
    _record(name, r is not None)
    return r


def _check(name: str, password: str) -> str | None:
    u = load_users().get(name) if name != "__unreadable__" else None
    if not u:
        hash_password(password)            # same work either way, so timing does not reveal which names exist
        return None
    return u["role"] if hmac.compare_digest(hash_password(password, u["salt"])[1], u["hash"]) else None


def enabled() -> bool:
    return bool(load_users())


def current_user() -> str | None:
    return st.session_state.get("user")


def role() -> str | None:
    return st.session_state.get("role")


def can(action: str) -> bool:
    """With sign-in off, everyone can do everything (demo mode)."""
    if not enabled():
        return True
    r = role()
    if not r:
        return False
    return action in ROLES.get(r, set())


def started_by_me(dataset: str | None) -> bool:
    """True when the signed-in person started the run that produced this dataset's findings."""
    if not enabled() or not dataset:
        return False
    runs = [h for h in st.session_state.get("run_history", []) if h["label"] == dataset]
    return bool(runs) and runs[-1].get("user") == current_user()


def review_block_reason() -> str | None:
    """Why the current person may not approve or reject right now, or None."""
    from . import custom
    if not can("review"):
        return "Your role cannot approve or reject findings. A reviewer must do it."
    if started_by_me(custom.active_label()):
        return "Segregation of duties: you started this run, so another person must review its findings."
    return None


def gate() -> bool:
    """Draw the sign-in form when sign-in is on and nobody is signed in. Returns True when the page may continue."""
    S = st.session_state
    if not enabled() or S.get("user"):
        return True
    st.markdown("### Sign in to Tallyhound")
    if "__unreadable__" in load_users():
        st.error("The users file could not be read, so nobody can sign in. An admin must fix or recreate it "
                 "(python scripts/add_user.py).")
    with st.form("login"):
        name = st.text_input("Name")
        pw = st.text_input("Password", type="password")
        ok = st.form_submit_button("Sign in", type="primary")
    if ok:
        wait = locked_for(name.strip())
        r = None if wait else check(name.strip(), pw)
        if r:
            forget_work()                # nothing from before signing in carries over
            S["user"], S["role"] = name.strip(), r
            st.rerun()
        wait = wait or locked_for(name.strip())
        st.error(f"Too many wrong passwords. Try again in {wait} seconds." if wait else "Wrong name or password.")
    return False


def forget_work() -> None:
    """Clear this browser tab's copy of the work (it stays saved on the server for the team)."""
    S = st.session_state
    for k in list(S.keys()):
        if k not in ("nav",):
            del S[k]


def sign_out() -> None:
    forget_work()                    # the next person on this computer starts from the sign-in form, with nothing shown
