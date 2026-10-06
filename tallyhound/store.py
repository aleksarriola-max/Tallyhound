"""Saves the reviewer's work so a page reload does not lose it.

Each browser gets a short random id kept in the page address (?s=...). Its work is stored as one small JSON
file under .tallyhound_state/ (override with the TALLYHOUND_STATE_DIR environment variable). Nothing leaves the
server, and only the review state is kept - never the source data.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

import streamlit as st

KEYS = ["decisions", "audit_log", "cleared", "step", "fail_pending", "recent_extra", "tour_downloaded", "tour_off",
        "uploads", "custom", "by_dataset", "dataset", "extra_opts",
        "mappings", "answer_keys", "run_history", "limits", "notes"]
MAX_AGE_DAYS = 14
_SID = re.compile(r"^[0-9a-f]{12}$")


def state_dir() -> Path:
    d = Path(os.environ.get("TALLYHOUND_STATE_DIR") or Path(__file__).resolve().parent.parent / ".tallyhound_state")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(sid: str) -> Path:
    return state_dir() / f"{sid}.json"


def _sweep() -> None:
    """Delete saved files not touched for MAX_AGE_DAYS."""
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    for p in state_dir().glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def snapshot() -> dict:
    S = st.session_state
    snap = {k: S.get(k) for k in KEYS}
    sim = S.get("sim")
    # a run in progress is not saved; a finished or failed one is
    snap["sim"] = sim if sim and not sim["running"] else None
    return snap


def session_id() -> str:
    sid = st.query_params.get("s", "")
    if not _SID.match(str(sid)):
        sid = secrets.token_hex(6)
        st.query_params["s"] = sid
    return sid


def load_into_session() -> None:
    """Run once per browser session, before the page draws."""
    S = st.session_state
    if S.get("_store_ready"):
        return
    S["_store_ready"] = True
    S["sid"] = session_id()
    _sweep()
    p = _path(S["sid"])
    if not p.exists():
        S["_saved"] = ""
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        S["_saved"] = ""
        return
    for k, v in data.items():
        if v is not None:
            S[k] = v
    S["_restored"] = bool(data.get("decisions") or data.get("sim"))
    S["_saved"] = json.dumps(snapshot(), sort_keys=True, default=str)


def save_if_changed() -> None:
    """Run at the end of every script run. Writes only when something changed."""
    S = st.session_state
    sid = S.get("sid")
    if not sid:
        return
    text = json.dumps(snapshot(), sort_keys=True, default=str)
    if text == S.get("_saved"):
        return
    try:
        tmp = _path(sid).with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(_path(sid))
        S["_saved"] = text
    except OSError:
        pass  # a read-only disk must never break the app


def reset() -> None:
    """Forget everything for this browser and start over."""
    S = st.session_state
    sid = S.get("sid")
    if sid:
        try:
            _path(sid).unlink(missing_ok=True)
        except OSError:
            pass
    for k in list(S.keys()):
        if k not in ("nav",):
            del S[k]
    S["nav"] = "Run analysis"
