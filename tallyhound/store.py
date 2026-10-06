"""Saves the reviewer's work so a page reload does not lose it.

Each browser gets a random 128-bit id kept in the page address (?s=...). Anyone with that address sees that work,
so share it only with people who should. Its work is stored as one row in a small
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
        "po_exempt_words", "po_exempt_vendors", "rule_override", "shadow", "shadow_marks", "date_order", "trail_seals"]
MAX_AGE_DAYS = 14
_SID = re.compile(r"^(?:[0-9a-f]{12}|[0-9a-f]{32})$")   # 12 = links made before the audit; new ones are 128-bit


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
            # a session's uploads row is only rewritten when files change, so it goes with its session, not on its own
            con.execute("DELETE FROM state WHERE updated < ? AND sid NOT LIKE '%:uploads'", (cutoff,))
            con.execute("DELETE FROM state WHERE sid LIKE '%:uploads' AND "
                        "substr(sid, 1, length(sid) - 8) NOT IN (SELECT sid FROM state WHERE sid NOT LIKE '%:uploads')")
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
        sid = secrets.token_hex(16)
        st.query_params["s"] = sid
    return sid


def _dict_of(t):
    return lambda v: isinstance(v, dict) and all(isinstance(k, str) and isinstance(x, t) for k, x in v.items())


def _list_of(t):
    return lambda v: isinstance(v, list) and all(isinstance(x, t) for x in v)


def _records(v) -> bool:
    need = {"id", "source_file", "line_number", "area", "clause", "severity", "title"}
    return isinstance(v, dict) and all(isinstance(k, str) and isinstance(rs, list) and all(
        isinstance(r, dict) and need <= set(r) for r in rs) for k, rs in v.items())


def _uploads(v) -> bool:
    return isinstance(v, dict) and all(isinstance(k, str) and isinstance(fs, dict) and all(
        isinstance(n, str) and isinstance(ls, list) and all(isinstance(x, str) for x in ls) for n, ls in fs.items())
        for k, fs in v.items())


def _by_dataset(v) -> bool:
    return isinstance(v, dict) and all(isinstance(x, dict) and _dict_of(dict)(x.get("decisions", {}))
                                       and _list_of(dict)(x.get("audit_log", [])) and isinstance(x.get("cleared", {}), dict)
                                       for x in v.values())


# what each saved value must look like; anything else is dropped on load instead of breaking the session forever
SHAPES = {
    "decisions": _dict_of(dict), "audit_log": _list_of(dict), "cleared": _dict_of(str), "custom": _records,
    "by_dataset": _by_dataset, "dataset": lambda v: isinstance(v, str), "uploads": _uploads,
    "run_history": _list_of(dict), "limits": _dict_of((int, float, list, str)), "notes": _dict_of(dict),
    "suppressions": lambda v: _list_of(dict)(v) and all({"clause", "source_file", "entity"} <= set(x) for x in v),
    "trail_seals": _dict_of(str), "mappings": _dict_of(dict), "answer_keys": _dict_of((list, str)), "presets": lambda v: isinstance(v, dict),
    "extra_opts": _dict_of(dict), "recent_extra": _list_of(dict), "po_exempt_words": _list_of(str),
    "po_exempt_vendors": _list_of(str), "rule_override": _dict_of(str), "shadow": _list_of(str),
    "shadow_marks": _dict_of(int), "date_order": _dict_of(dict),
    "sim": lambda v: isinstance(v, dict) and isinstance(v.get("queue"), list) and isinstance(v.get("log", []), list),
    "step": lambda v: isinstance(v, int), "tour_off": lambda v: isinstance(v, bool),
    "tour_downloaded": lambda v: isinstance(v, bool), "fail_pending": lambda v: isinstance(v, bool),
}


def clean(data) -> tuple[dict, list[str]]:
    """Saved values that have the right shape, and the names of those that did not."""
    if not isinstance(data, dict):
        return {}, ["everything"]
    good, bad = {}, []
    for k, v in data.items():
        if v is None or k not in KEYS + ["sim"]:
            continue
        check = SHAPES.get(k)
        if check is None or check(v):
            good[k] = v
        else:
            bad.append(k)
    if "dataset" in good and good["dataset"] not in good.get("custom", {}):
        good.pop("dataset")
    return good, bad


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
    data, bad = clean(data)
    for k, v in data.items():
        S[k] = v
    try:
        up = _read(S["sid"] + ":uploads")
        up = json.loads(up) if up else None
        if up is not None and _uploads(up):
            S["uploads"] = up
        elif up is not None:
            bad.append("uploads")
    except (OSError, ValueError, sqlite3.Error):
        pass
    gone = [k for k in S.get("custom", {}) if k not in S.get("uploads", {})]
    if gone:
        # findings whose files are no longer stored cannot be shown (their quotes cannot be checked); their
        # decisions and audit trail stay in by_dataset and come back when the same files are uploaded and run again
        for k in gone:
            S["custom"].pop(k)
        if S.get("dataset") in gone:            # its decisions and trail are the active ones: park them with it
            store = S.setdefault("by_dataset", {})
            store[S["dataset"]] = dict(decisions=S.get("decisions", {}), audit_log=S.get("audit_log", []),
                                       cleared=S.get("cleared", {}), notes=S.get("notes", {}))
            pick = store.get("__sample__", {})
            S["decisions"], S["audit_log"] = pick.get("decisions", {}), pick.get("audit_log", [])
            S["cleared"], S["notes"] = pick.get("cleared", {}), pick.get("notes", {})
            S.pop("dataset")
        S["_restore_note"] = (f"The files for {', '.join(gone)} are no longer stored, so those findings are not shown. "
                              "Upload the same files and run them again; the earlier audit trail is kept.")
    if bad:
        S["_restore_note"] = ("Some saved work could not be read and was set aside: " + ", ".join(sorted(set(bad)))
                              + ". Everything else was restored.")
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
    S["nav"] = "Home"
