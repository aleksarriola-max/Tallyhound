"""Saves the reviewer's work so a page reload does not lose it.

Each browser gets a short random id kept in the page address (?s=...). Its work is stored as one row in a small
SQLite database, .tallyhound_state/tallyhound.db (override the folder with TALLYHOUND_STATE_DIR). Nothing leaves the
server. SQLite handles several people saving at once safely, which loose files did not.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

import streamlit as st

KEYS = ["decisions", "audit_log", "cleared", "step", "fail_pending", "recent_extra", "tour_downloaded", "tour_off",
        "uploads", "custom", "by_dataset", "dataset", "extra_opts",
        "mappings", "answer_keys", "run_history", "limits", "notes", "suppressions", "presets",
        "po_exempt_words", "po_exempt_vendors", "rule_override", "shadow", "shadow_marks", "date_order"]
MAX_AGE_DAYS = 14
_SID = re.compile(r"^[0-9a-f]{12}$")


def state_dir() -> Path:
    d = Path(os.environ.get("TALLYHOUND_STATE_DIR") or Path(__file__).resolve().parent.parent / ".tallyhound_state")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(state_dir() / "tallyhound.db", timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS state (sid TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)")
    return con


def _read(sid: str) -> str | None:
    with _db() as con:
        row = con.execute("SELECT data FROM state WHERE sid = ?", (sid,)).fetchone()
    if row:
        return row[0]
    if ":" in sid:
        return None
    legacy = state_dir() / f"{sid}.json"          # saved by an older version: read it once, then it moves to SQLite
    return legacy.read_text(encoding="utf-8") if legacy.exists() else None


def _write(sid: str, text: str) -> None:
    with _db() as con:
        con.execute("INSERT INTO state (sid, data, updated) VALUES (?, ?, ?) "
                    "ON CONFLICT(sid) DO UPDATE SET data = excluded.data, updated = excluded.updated",
                    (sid, text, time.time()))


def _delete(sid: str) -> None:
    with _db() as con:
        con.execute("DELETE FROM state WHERE sid = ? OR sid = ?", (sid, sid + ":uploads"))
    (state_dir() / f"{sid}.json").unlink(missing_ok=True)


def _sweep() -> None:
    """Forget saved work not touched for MAX_AGE_DAYS."""
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        with _db() as con:
            con.execute("DELETE FROM state WHERE updated < ?", (cutoff,))
        for p in state_dir().glob("*.json"):
            if p.stat().st_mtime < cutoff:
                p.unlink()
    except (OSError, sqlite3.Error):
        pass


def snapshot() -> dict:
    S = st.session_state
    snap = {k: S.get(k) for k in KEYS if k != "uploads"}   # uploads are big: saved separately, only when they change
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
    try:
        raw = _read(S["sid"])
        data = json.loads(raw) if raw else None
    except (OSError, ValueError, sqlite3.Error):
        data = None
    if not data:
        S["_saved"] = ""
        return
    for k, v in data.items():
        if v is not None:
            S[k] = v
    try:
        up = _read(S["sid"] + ":uploads")
        if up:
            S["uploads"] = json.loads(up)
    except (OSError, ValueError, sqlite3.Error):
        pass
    S["_restored"] = bool(data.get("decisions") or data.get("sim"))
    S["_saved"] = json.dumps(snapshot(), sort_keys=True, default=str)


def save_uploads() -> None:
    """Save uploaded files once, when they change (not on every click)."""
    S = st.session_state
    sid = S.get("sid")
    if sid:
        try:
            _write(sid + ":uploads", json.dumps(S.get("uploads", {})))
        except (OSError, sqlite3.Error):
            pass


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
        _write(sid, text)
        S["_saved"] = text
    except (OSError, sqlite3.Error):
        pass  # a read-only disk must never break the app


def reset() -> None:
    """Forget everything for this browser and start over."""
    S = st.session_state
    sid = S.get("sid")
    if sid:
        try:
            _delete(sid)
        except (OSError, sqlite3.Error):
            pass
    for k in list(S.keys()):
        if k not in ("nav",):
            del S[k]
    S["nav"] = "Run analysis"
