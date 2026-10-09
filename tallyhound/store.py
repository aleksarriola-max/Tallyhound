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

KEYS = ["decisions", "audit_log", "cleared", "fail_pending", "recent_extra", "tour_downloaded", "tour_off",
        "uploads", "custom", "by_dataset", "dataset", "extra_opts",
        "mappings", "answer_keys", "run_history", "limits", "notes", "suppressions", "presets",
        "po_exempt_words", "po_exempt_vendors", "rule_override", "shadow", "shadow_marks", "date_order", "trail_seals"]
MAX_AGE_DAYS = 14
TEAM = "team"            # the shared workspace when sign-in is on
_SID = re.compile(r"^(?:[0-9a-f]{12}|[0-9a-f]{32})$")   # 12 = links made before the audit; new ones are 128-bit


def _default_state_dir() -> Path:
    """Next to the code, except on Windows: there a clone usually sits in Documents, which OneDrive syncs, and a
    synced SQLite database gets "database is locked" errors. %LOCALAPPDATA% is never synced. A folder already in use
    next to the code is kept, so nobody loses saved work."""
    here = Path(__file__).resolve().parent.parent / ".tallyhound_state"
    if os.name == "nt" and os.environ.get("LOCALAPPDATA") and not here.exists():
        return Path(os.environ["LOCALAPPDATA"]) / "Tallyhound"
    return here


def state_dir() -> Path:
    d = Path(os.environ.get("TALLYHOUND_STATE_DIR") or _default_state_dir())
    if not d.exists():
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)                  # saved work, password hashes and the trail key: the server user only
        except OSError:
            pass
    return d


def write_private(path: Path, text: str) -> None:
    """Write a file readable only by the server's user, atomically (a crash mid-write never leaves half a file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        for attempt in range(10):          # on Windows a virus scanner or sync tool may hold the old file a moment
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _db() -> sqlite3.Connection:
    path = state_dir() / "tallyhound.db"
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))      # every session's uploads live here
    con = sqlite3.connect(path, timeout=10)
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


def _write(sid: str, text: str, expect: float | None = None) -> float | None:
    """Save. With expect (the version this session loaded), save only if nobody else saved since; returns the new
    version, or None when someone else got there first."""
    now = time.time()
    with _db() as con:
        if expect is None:              # this tab saw no saved work: create it, unless someone else just did
            cur = con.execute("INSERT OR IGNORE INTO state (sid, data, updated) VALUES (?, ?, ?)", (sid, text, now))
        else:                           # changed or deleted (a reset) since this tab loaded it: refuse
            cur = con.execute("UPDATE state SET data = ?, updated = ? WHERE sid = ? AND updated = ?",
                              (text, now, sid, expect))
    return now if cur.rowcount else None


def _put(sid: str, text: str) -> None:
    """Save without a version check (rows only one writer changes at a time: run progress)."""
    with _db() as con:
        con.execute("INSERT INTO state (sid, data, updated) VALUES (?, ?, ?) "
                    "ON CONFLICT(sid) DO UPDATE SET data = excluded.data, updated = excluded.updated",
                    (sid, text, time.time()))


def _version(sid: str) -> float | None:
    with _db() as con:
        row = con.execute("SELECT updated FROM state WHERE sid = ?", (sid,)).fetchone()
    return row[0] if row else None


def _delete(sid: str) -> None:
    with _db() as con:
        con.execute("DELETE FROM state WHERE sid IN (?, ?, ?)", (sid, sid + ":uploads", sid + ":sim"))
    (state_dir() / f"{sid}.json").unlink(missing_ok=True)


def _sweep() -> None:
    """Forget saved work not touched for MAX_AGE_DAYS."""
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        with _db() as con:
            # a session's uploads row is only rewritten when files change, so it goes with its session, not on its own
            con.execute("DELETE FROM state WHERE updated < ? AND sid NOT LIKE '%:uploads' AND sid != ?", (cutoff, TEAM))
            con.execute("DELETE FROM state WHERE sid LIKE '%:uploads' AND "
                        "substr(sid, 1, length(sid) - 8) NOT IN (SELECT sid FROM state WHERE sid NOT LIKE '%:uploads')")
        for p in state_dir().glob("*.json"):
            if p.stat().st_mtime < cutoff:
                p.unlink()
    except (OSError, sqlite3.Error):
        pass


def snapshot() -> dict:
    S = st.session_state
    # uploads are big and the run's progress changes every second: both are saved in rows of their own, so a run
    # ticking never makes a teammate's click conflict
    return {k: S.get(k) for k in KEYS if k != "uploads"}


def read_sim(sid: str | None) -> dict | None:
    """The latest saved run progress (written by whichever tab drives the run)."""
    if not sid:
        return None
    try:
        raw = _read(sid + ":sim")
        sim = json.loads(raw) if raw else None
    except (OSError, ValueError, sqlite3.Error):
        return None
    return sim if SHAPES["sim"](sim) else None


def session_id() -> str:
    """Whose saved work this browser shows. With sign-in on, everyone signed in shares one team workspace on the
    server and the address is ignored - so nobody can plant a link that captures someone else's work. With sign-in
    off (the demo), the work belongs to a random 128-bit id in the address (?s=...)."""
    from . import auth
    if auth.enabled():
        if "s" in st.query_params:
            del st.query_params["s"]
        return TEAM
    sid = st.query_params.get("s", "")
    if not _SID.fullmatch(str(sid)):
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
        isinstance(r, dict) and need <= set(r) and isinstance(r["line_number"], int) and not isinstance(
            r["line_number"], bool) and isinstance(r.get("amount", 0), (int, float)) for r in rs) for k, rs in v.items())


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
    "tour_off": lambda v: isinstance(v, bool),
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
    from . import auth
    if auth.enabled() and not auth.current_user():
        S["sid"] = None              # nothing is loaded, or saved, until someone has signed in
        return
    S["_store_ready"] = True
    S["sid"] = session_id()
    _sweep()
    try:
        S["_ver"] = _version(S["sid"])
        raw = _read(S["sid"])
        data = json.loads(raw) if raw else None
    except (OSError, ValueError, sqlite3.Error):
        data = None
    for k in KEYS + ["sim"]:            # a fresh load replaces this tab's copy entirely - no stale value survives
        S.pop(k, None)
    sim = read_sim(S["sid"])
    if sim is not None:
        S["sim"] = sim
        S["_sim_saved"] = json.dumps(sim, sort_keys=True, default=str)
    if not data:
        S["_saved"] = ""
        return
    data, bad = clean(data)
    data.pop("sim", None)                # older saves kept the run in the main row
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
    S["_restored"] = bool(data.get("decisions") or S.get("sim"))
    S["_saved"] = json.dumps(snapshot(), sort_keys=True, default=str)
    _replay(S)                           # after _saved: the replayed changes differ from it, so they get saved


def _replay(S) -> None:
    """After a conflict: re-apply this tab's own decisions and undos on top of the teammate's newer work. A decision
    a teammate already made on the same finding wins; the person is told what was and was not re-applied."""
    ops = S.pop("_replay", None)
    if not ops:
        return
    from . import common as C
    from . import custom
    done, skipped = 0, 0
    for kind, dataset, fid, status, reason in ops:
        if (S.get("dataset") or None) != (dataset or None):
            custom.activate(dataset)
        if kind == "decide" and fid not in S.get("decisions", {}):
            C.decide(fid, status, reason)
            done += 1
        elif kind == "undo" and fid in S.get("decisions", {}):
            C.undo(fid)
            done += 1
        else:
            skipped += 1
    S["_restore_note"] = ("A teammate saved at the same moment. Their changes are loaded"
                          + (f" and your {done} change(s) re-applied on top" if done else "")
                          + (f"; {skipped} of yours were already decided by them and were left as they decided"
                             if skipped else "") + ". Other edits (notes, cleared holds) may need redoing.")


def save_uploads(removed: tuple[str, ...] = ()) -> None:
    """Save uploaded files, when they change. Merged with what is saved, so two people uploading at once both keep
    their files; removed names are taken out."""
    S = st.session_state
    sid = S.get("sid")
    if not sid:
        return
    try:
        with _db() as con:              # read and write in one transaction
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT data FROM state WHERE sid = ?", (sid + ":uploads",)).fetchone()
            saved = json.loads(row[0]) if row else {}
            saved = saved if _uploads(saved) else {}
            merged = {**saved, **S.get("uploads", {})}
            for k in removed:
                merged.pop(k, None)
            con.execute("INSERT INTO state (sid, data, updated) VALUES (?, ?, ?) ON CONFLICT(sid) DO UPDATE SET "
                        "data = excluded.data, updated = excluded.updated", (sid + ":uploads", json.dumps(merged),
                                                                            time.time()))
        S["uploads"] = merged
    except (OSError, ValueError, sqlite3.Error):
        pass


def save_if_changed() -> None:
    """Run at the end of every script run. Writes only when something changed."""
    S = st.session_state
    sid = S.get("sid")
    if not sid:
        return
    sim = json.dumps(S.get("sim"), sort_keys=True, default=str)
    if sim != S.get("_sim_saved") and S.get("sim") is not None:
        try:
            _put(sid + ":sim", sim)
            S["_sim_saved"] = sim
        except (OSError, sqlite3.Error):
            pass
    text = json.dumps(snapshot(), sort_keys=True, default=str)
    if text == S.get("_saved"):
        return
    try:
        ver = _write(sid, text, S.get("_ver"))
    except (OSError, sqlite3.Error):
        return  # a read-only disk must never break the app
    if ver is None:                  # a teammate saved since this page loaded: load their work, then redo ours on top
        S["_store_ready"] = False
        S["_replay"] = S.pop("_pending_ops", [])
        st.rerun()
    S["_ver"], S["_saved"] = ver, text
    S.pop("_pending_ops", None)


def reset() -> None:
    """Forget everything for this browser and start over."""
    S = st.session_state
    sid = S.get("sid")
    if sid:
        try:
            _delete(sid)
        except (OSError, sqlite3.Error):
            pass
    keep = ("nav", "user", "role", "_tab")      # resetting the work does not sign the admin out
    for k in list(S.keys()):
        if k not in keep:
            del S[k]
    S["nav"] = "Home"
