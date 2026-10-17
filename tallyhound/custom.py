"""Uploaded files: read the zip, run the agents on it, and keep the results apart from the sample company."""
from __future__ import annotations

import io
import threading
import time
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from . import agents, llm, rules
from . import common as C
# reading a zip or loose files lives in uploads.py; these names stay importable from here
from .uploads import MAX_LINE_CHARS, MAX_LINES, MAX_UNZIPPED_MB, MAX_ZIP_MB, STEMS, bundle, decode, parse_zip  # noqa: F401

SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2}
COLS = ["id", "severity", "area", "clause", "amount", "title", "skeptic_verdict", "evidence", "related_evidence",
        "source_file", "line_number", "innocent_explanations", "skeptic_reason", "proposed_fix", "warning"]
NOT_CHECKED = "Not checked"          # the Skeptic's verdict when it never got to a finding: ranked like Doubtful


def add_upload(label: str, files: dict[str, list[str]], key: list[dict] | None = None) -> None:
    """Register uploaded files. An answer_key.csv inside them becomes this dataset's answer key."""
    from . import score
    S = st.session_state
    files = dict(files)
    key_lines = files.pop("answer_key.csv", None)
    if key_lines and key is None:
        key = score.key_from_csv("\n".join(key_lines))
    S.setdefault("uploads", {})[label] = files
    _apply_presets(label)
    from . import store
    store.save_uploads()
    if key:
        S.setdefault("answer_keys", {})[label] = key
    elif is_sample(files):
        S.setdefault("answer_keys", {})[label] = "sample"
    S.extra_opts.setdefault("audit", {})[label] = f"{len(files)} of 5 files: " + ", ".join(sorted(files))


def _apply_presets(label: str) -> None:
    """Reuse a saved column matching when a file has exactly the same header as one matched before."""
    import csv

    from . import columns
    S = st.session_state
    for name, lines in S.uploads[label].items():
        if lines and rules.missing_columns(name, lines):
            saved = S.get("presets", {}).get(columns.signature(name, next(csv.reader([lines[0]]))))
            if saved:
                mapping(label)[name] = dict(saved)


def is_sample(files: dict[str, list[str]]) -> bool:
    """True when the upload is exactly the sample company's files, so the sample answer key applies."""
    names = [n for n in files if n in ("payments.csv", "approvals.csv", "vendors.csv", "contracts.txt", "expenses.csv")]
    return bool(names) and all(files[n] == C.read_source(n) for n in names)


# ---------------------------------------------------------------- column matching
def mapping(label: str) -> dict[str, dict[str, str]]:
    """{file: {expected column: column in the file, or "" when the file has no such column}}"""
    return st.session_state.setdefault("mappings", {}).setdefault(label, {})


def view(label: str) -> dict[str, list[str]]:
    """The uploaded files as the checks see them: only the header line is renamed by the column matching.
    Data lines are untouched, so every quote is still an exact line of the file the person uploaded."""
    import csv
    out = {}
    orders = st.session_state.get("date_order", {}).get(label, {})
    for name, lines in st.session_state.uploads[label].items():
        m = mapping(label).get(name)
        marker = {"day-first": "tallyhound_dayfirst", "month-first": "tallyhound_monthfirst"}.get(orders.get(name, ""))
        if marker and lines:                       # a date order the person chose: carried as an extra header name
            lines = [lines[0] + "," + marker] + lines[1:]
        if not m or not lines:
            out[name] = lines
            continue
        head = next(csv.reader([lines[0]]))
        back = {src: dst for dst, src in m.items() if src}
        new = [back.get(h, h) for h in head] + [dst for dst, src in m.items() if not src]
        buf = io.StringIO()
        csv.writer(buf, lineterminator="").writerow(new)
        out[name] = [buf.getvalue()] + lines[1:]
    return out


def unmatched(label: str) -> dict[str, list[str]]:
    """Files that still miss expected columns after matching."""
    return {n: miss for n, ls in view(label).items() if (miss := rules.missing_columns(n, ls))}


def uploaded(option: str) -> bool:
    return option in st.session_state.get("uploads", {})


# ---------------------------------------------------------------- running
class Job:
    """Runs the agents for one uploaded dataset on a background thread. Never touches Streamlit state."""

    def __init__(self, label, files, engine, model, url, policy, skip, limits=None, started_by: str = "",
                 skeptic_seconds: float | None = None):
        self.started_by = started_by        # who pressed Run: segregation of duties is checked against this person
        # the whole Skeptic stage of this run, over every retry; what it has not reached by then is "Not checked"
        self.skeptic_seconds = float(agents.SKEPTIC_MINUTES * 60 if skeptic_seconds is None else skeptic_seconds)
        self.skeptic_spent = 0.0
        self.cancel = llm.Cancel()          # stops the model call in flight, from the page's thread
        self.stopped_by: str | None = None  # set by Stop: the run ends, keeping only fully checked findings
        self.skip_skeptic: str | None = None    # set by "Finish without the Skeptic": the rest are Not checked
        self.progress: dict[str, str] = {}  # agent -> how far it got, for the note when a run is stopped
        self.stop_at = 0.0
        self.limits = dict(limits or rules.LIMITS)
        self.label, self.files, self.engine, self.model, self.url = label, files, engine, model, url
        self.policy, self.skip = policy, set(skip)
        self.agents: dict[str, dict[str, Any]] = {
            n: dict(status="Skipped" if n in self.skip else "Waiting", pct=0, secs=0, msg="") for n in
            ["Orchestrator", *rules.FILES, "Skeptic"]}
        self.records: list[dict] = []
        self.notes: dict[str, str] = {}     # agent -> why it stopped before reading everything
        self.run_id = ""                    # set by the caller after construction; read back with getattr
        self.thread: threading.Thread | None = None
        self.t0 = self.touched = time.time()
        self.lock = threading.Lock()
        self.start()

    def start(self) -> None:
        if (self.thread and self.thread.is_alive()) or self.stopped_by is not None:
            return
        for a in self.agents.values():
            if a["status"] == "Failed":
                a.update(status="Waiting", pct=0, msg="")
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    @property
    def failed(self):
        return next(((n, a) for n, a in self.agents.items() if a["status"] == "Failed"), None)

    @property
    def done(self) -> bool:
        return all(a["status"] in ("Done", "Skipped") for a in self.agents.values())

    def _set(self, name, **kw):
        with self.lock:
            self.agents[name].update(kw)

    def stop(self, who: str) -> None:
        """End the run now: the model call in flight is cut off and nothing more is started."""
        if self.stopped_by is None:
            self.stopped_by, self.stop_at = who, time.time()
        self.cancel.set()

    def finish_without_skeptic(self, who: str) -> None:
        """Leave the findings the Skeptic has not reached as Not checked, so a Skeptic that keeps failing (or is too
        slow) does not hold back every result."""
        self.skip_skeptic = who
        status = self.agents["Skeptic"]["status"]
        if status == "Running":
            self.cancel.set()               # the Skeptic's call in flight; nothing else runs after it
        elif status == "Failed":
            self.start()

    def _run(self) -> None:
        llm.cancel_scope(self.cancel)
        try:
            self._agents()
        finally:
            llm.cancel_scope(None)

    def _agents(self) -> None:
        for name in self.agents:
            a = self.agents[name]
            if a["status"] in ("Done", "Skipped"):
                continue
            if self.stopped_by is not None:
                return
            t = time.time()
            self._set(name, status="Running", pct=5)
            try:
                if name == "Orchestrator":
                    pass
                elif name == "Skeptic":
                    self._skeptic(t)
                else:
                    self._area(name, t)
            except llm.Cancelled:
                self._set(name, status="Stopped" if self.stopped_by is not None else "Failed",
                          msg="Stopped." if self.stopped_by is not None else "Cancelled.", secs=int(time.time() - t))
                return
            except llm.LLMError as e:
                self._set(name, status="Failed", msg=str(e), secs=int(time.time() - t))
                return
            except Exception as e:   # a bug or odd file must show up as a failed agent, not a frozen run
                self._set(name, status="Failed", msg=f"{type(e).__name__}: {e}", secs=int(time.time() - t))
                return
            self._set(name, status="Done", pct=100, secs=int(time.time() - t))

    def _area(self, area: str, t: float) -> None:
        lines = self.files.get(rules.FILES[area])
        if not lines:
            return
        if self.engine in ("rules", "rules+skeptic"):
            if any(r["area"] == area for r in self.records):
                return                       # already done before a retry
            recs: list[dict] = []
            for h in rules.run_area(area, self.files, self.limits):
                recs.append(dict(
                    area=h.area, clause=h.clause, severity=h.severity, amount=h.amount, title=h.title,
                    source_file=h.source_file, line_number=h.line_number, related_lines=[ln for _, ln in h.related],
                    innocent=rules.INNOCENT.get(h.clause, ""), fix=rules.FIXES.get(h.clause, ""),
                    **(dict(verdict="Doubtful" if h.clause in rules.PATTERN_CLAUSES else "Confirmed",
                            reason=("Fixed rule check, no AI. A pattern worth a look, not a breach on its own"
                                    if h.clause in rules.PATTERN_CLAUSES else "Fixed rule check, no AI")
                            + f". Clause {h.clause}: {rules.clause_text(self.policy, h.clause, h.area)}")
                       if self.engine == "rules" else {})))
                r = recs[-1]
                src = self.files.get(r["source_file"], [])          # cross-file checks quote other files
                r["evidence"] = src[r["line_number"] - 1] if 0 < r["line_number"] <= len(src) else ""
                self._screen(r, src)
            self.records.extend(recs)
            return
        if any(r["area"] == area for r in self.records):
            return                           # already done before a retry
        def note(msg: str) -> None:
            if msg.startswith("stopped"):
                self.notes[area] = msg
            elif msg.startswith("read lines"):
                self.progress[area] = msg
        if self.engine == "ollama-tools":
            from . import agents_tools
            raw = agents_tools.investigate(area, lines, self.policy, self.model, self.url, log=note)
        else:
            raw = agents.propose(area, lines, self.policy, self.model, self.url, log=note)
        clauses = agents.clauses_for(area, self.policy)
        keep: dict[tuple, dict] = {}         # a model repeating itself: one finding, one Skeptic call, most severe kept
        for v in (agents.verified(f, lines, area, clauses) for f in raw):
            if v is None:
                continue
            k = (v["line_number"], v["clause"])
            if k not in keep or SEV_ORDER[v["severity"]] < SEV_ORDER[keep[k]["severity"]]:
                keep[k] = v
        for v in keep.values():
            self._screen(v, lines)
        self.records.extend(keep.values())            # all at once, so a failed or retried agent never leaves half a list

    @staticmethod
    def _screen(r: dict, lines: list[str]) -> None:
        """Mark a finding whose cited lines (or the lines next to them) talk to an AI reviewer."""
        if agents.ai_text_near(lines, [r["line_number"], *r.get("related_lines", [])]):
            r["warning"] = agents.WARN_AI_TEXT

    def _guarded(self, r: dict, verdict: str, reason: str) -> tuple[str, str]:
        """Text addressed to an AI in the evidence: the Skeptic may not dismiss a rule finding, nor confirm an AI one."""
        if not r.get("warning"):
            return verdict, reason
        if self.engine in ("rules", "rules+skeptic") and verdict == "Doubtful":
            own = "Doubtful" if r["clause"] in rules.PATTERN_CLAUSES else "Confirmed"
            return own, ("The Skeptic's doubt was not used, because the line contains text addressed to an AI reviewer; "
                         "the rule's finding stands. The Skeptic said: " + reason)
        if self.engine not in ("rules", "rules+skeptic") and verdict == "Confirmed":
            return "Doubtful", ("Not confirmed, because the line contains text addressed to an AI reviewer. The Skeptic "
                                "said: " + reason)
        return verdict, reason

    def _not_checked(self, recs: list[dict], why: str) -> None:
        for r in recs:
            r["verdict"], r["reason"] = NOT_CHECKED, why
        if recs:
            self.notes["Skeptic"] = why.replace("Not checked by the Skeptic", "stopped", 1).rstrip(".") \
                + f"; {len(recs)} finding(s) not checked by the Skeptic"

    def _skeptic(self, t: float) -> None:
        todo = [r for r in self.records if "verdict" not in r]
        if self.skip_skeptic is not None:
            self._not_checked(todo, f"Not checked by the Skeptic (finished without it by {self.skip_skeptic or 'a person'}).")
            return
        began = time.time()
        self.cancel.deadline = began + max(self.skeptic_seconds - self.skeptic_spent, 0.0)
        try:
            for i, r in enumerate(todo, start=1):
                if time.time() >= self.cancel.deadline:
                    raise llm.OutOfTime("The time allowed ran out.")
                lines = self.files[r["source_file"]]
                header = lines[0] if r["source_file"].endswith(".csv") else ""
                r["evidence"] = lines[r["line_number"] - 1]
                related = [lines[n - 1] for n in r.get("related_lines", []) if 1 <= n <= len(lines)]
                clause = rules.clause_text(self.policy, r["clause"], r["area"])
                verdict, reason = agents.skeptic(r, clause, self.model, self.url, header, related)
                r["verdict"], r["reason"] = self._guarded(r, verdict, reason)
                self._set("Skeptic", pct=5 + int(90 * i / max(len(todo), 1)), secs=int(time.time() - t))
        except llm.OutOfTime:
            self._not_checked([r for r in todo if "verdict" not in r],
                              f"Not checked by the Skeptic (time limit: {_duration(self.skeptic_seconds)} for the "
                              "whole Skeptic stage).")
        except llm.Cancelled:
            if self.skip_skeptic is None or self.stopped_by is not None:
                raise
            self._not_checked([r for r in todo if "verdict" not in r],
                              f"Not checked by the Skeptic (finished without it by {self.skip_skeptic or 'a person'}).")
        finally:
            self.skeptic_spent += time.time() - began
            self.cancel.deadline = None


def _duration(secs: float) -> str:
    if secs >= 60:
        m = secs / 60
        return f"{m:g} minute{'s' if m != 1 else ''}"
    return f"{secs:g} second{'s' if secs != 1 else ''}"


def skeptic_seconds() -> float:
    """The Skeptic's time for a whole run, as set under Settings > AI."""
    try:
        m = float(st.session_state.get("skeptic_minutes") or agents.SKEPTIC_MINUTES)
    except (TypeError, ValueError):
        m = agents.SKEPTIC_MINUTES
    return max(m, 0.0) * 60


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
STOPS: dict[str, str] = {}      # job key -> who pressed Stop; any tab of the same workspace may press it
STOP_WAIT_SECONDS = 5.0         # how long a stopped job's thread is waited for before its results are taken as they are
MAX_RUNNING = 1 if llm.public() else 4   # analyses running at once on this server; more wait for a free slot
IDLE_DONE_SECONDS = 30 * 60     # a finished or failed job nobody has looked at for this long is let go
IDLE_ANY_SECONDS = 2 * 3600     # any job nobody has looked at for this long (its tab was closed) is let go


def prune_jobs(now: float | None = None) -> None:
    """Free the memory of jobs that are over, or whose browser tab went away. Each holds a copy of its files."""
    now = now or time.time()
    with JOBS_LOCK:
        for k, j in list(JOBS.items()):
            idle = now - j.touched
            alive = bool(j.thread and j.thread.is_alive())
            if idle > IDLE_ANY_SECONDS or (not alive and idle > IDLE_DONE_SECONDS):
                JOBS.pop(k, None)


def running_jobs() -> int:
    return sum(bool(j.thread and j.thread.is_alive()) for j in list(JOBS.values()))


def job_key(item: dict) -> str:
    return f"{st.session_state.get('sid', '')}|{item['label']}|{item.get('run_id', '')}"   # a run id: never a stale job


def is_ai(item: dict) -> bool:
    return bool(item.get("custom")) and item.get("engine", "rules") != "rules"


def request_stop(item: dict, who: str) -> None:
    """Stop an AI run. The tab that drives the run ends it on its next tick; the call in flight is cut off now."""
    key = job_key(item)
    with JOBS_LOCK:
        STOPS.setdefault(key, who)
        job = JOBS.get(key)
    if job is not None:
        job.stop(who)


def finish_without_skeptic(item: dict, who: str) -> bool:
    """Leave the findings the Skeptic has not checked as Not checked and finish the run. False if there is no job."""
    job = JOBS.get(job_key(item))
    if job is None:
        return False
    job.finish_without_skeptic(who)
    return True


def _unfinished(job: Job) -> list[str]:
    out = []
    for name, a in job.agents.items():
        if name == "Orchestrator" or a["status"] in ("Done", "Skipped") or \
                (name in rules.FILES and not job.files.get(rules.FILES[name])):
            continue                             # finished, switched off, or no file for it to read
        if a["status"] == "Waiting":
            out.append(f"{name} (not started)")
        elif name in job.progress:
            out.append(f"{name} (stopped after it {job.progress[name]})")
        else:
            out.append(f"{name} (stopped part-way)")
    return out


def _stopped(sim: dict, item: dict, job: Job | None, who: str, key: str) -> bool:
    """End a stopped run: keep the findings that were fully checked, and say plainly what was not finished."""
    S = st.session_state
    with JOBS_LOCK:
        STOPS.pop(key, None)
        JOBS.pop(key, None)
    by = who or "a person"
    item["status"], item["stopped_by"] = "Stopped", who
    for a in item["agents"]:
        if a["status"] in ("Waiting", "Running"):
            a["status"] = "Stopped"
    if job is None:                              # still waiting for a free slot: nothing was read
        item["result"], item["elapsed"] = 0, 0
        item["incomplete"] = f"stopped by {by} before it started; nothing was checked"
        sim_log(sim, "Orchestrator", f"Incomplete: {item['incomplete']}")
        from . import sim as simmod
        return simmod._finish_item(sim, item)
    kept = [r for r in job.records if r.get("verdict")]
    dropped = len(job.records) - len(kept)
    job.records = kept
    left = _unfinished(job)
    note = f"stopped by {by} before the end" + (f"; not finished: {', '.join(left)}" if left else "") \
        + (f"; {dropped} finding(s) not yet checked by the Skeptic were not kept" if dropped else "")
    job.notes["Run"] = note
    item["elapsed"] = int(time.time() - job.t0)
    for name, msg in job.notes.items():
        sim_log(sim, "Orchestrator" if name == "Run" else name, f"Incomplete: {msg}")
    item["incomplete"] = note
    finalize(item["custom"], job)
    C.log_action(f"Person ({who})" if who else "Person", "Run stopped", "-",
                 f"{len(kept)} fully checked finding(s) kept; {note}")
    item["result"] = len(S.custom[item["custom"]])
    from . import sim as simmod
    return simmod._finish_item(sim, item)


def tick_item(sim: dict, item: dict) -> bool:
    """Called once a second for a running uploaded-data item. Returns True when the page must rerun."""
    S = st.session_state
    key = job_key(item)
    prune_jobs()
    job = JOBS.get(key)
    who = STOPS.get(key)
    if who is not None and (job is None or not job.done):
        if job is not None:
            job.stop(who)
            if job.thread and job.thread.is_alive():
                job.thread.join(min(2.0, STOP_WAIT_SECONDS))
            if job.thread and job.thread.is_alive() and time.time() - job.stop_at < STOP_WAIT_SECONDS:
                return False                     # the call in flight is being cut off: look again next second
        return _stopped(sim, item, job, who, key)
    if who is not None:                          # it had already finished: the full results stand
        STOPS.pop(key, None)
    if job is None:
        if running_jobs() >= MAX_RUNNING:
            if not item.get("_waiting"):
                item["_waiting"] = True
                sim_log(sim, "Orchestrator", "The server is busy with other analyses; this one starts when a slot frees up")
            return False
        item.pop("_waiting", None)
        job = JOBS[key] = Job(item["custom"], view(item["custom"]), item.get("engine", "rules"),
                              item.get("model", llm.DEFAULT_MODEL), item.get("url", llm.DEFAULT_URL), C.policy(),
                              [a["name"] for a in item["agents"] if a["status"] == "Skipped"], C.limits(),
                              started_by=item.get("started_by", ""), skeptic_seconds=skeptic_seconds())
        job.run_id = item.get("run_id", "")
        sim_log(sim, "Orchestrator", f"Reading {len(job.files)} uploaded file(s) with "
                + ({"rules": "the built-in rules", "rules+skeptic": f"the built-in rules and an Ollama Skeptic ({job.model})",
                    "ollama-tools": f"Ollama agents with tools ({job.model})"}.get(job.engine, f"Ollama ({job.model})")))
        return True
    job.touched = time.time()
    for a in item["agents"]:
        j = job.agents.get(a["name"])
        if j and a["status"] != "Skipped":
            a["status"], a["pct"] = j["status"], j["pct"]
            a["secs"] = j["secs"] if j["status"] != "Running" else int(time.time() - job.t0) - sum(
                x["secs"] for n, x in job.agents.items() if n != a["name"] and x["status"] == "Done")
            a["secs"] = max(a["secs"], 0)
    bad = job.failed
    if bad:
        item["status"], sim["running"] = "Failed", False
        sim_log(sim, bad[0], f"Failed: {bad[1]['msg']}")
        return True
    if job.done:
        if any(h.get("run_id") and h.get("run_id") == item.get("run_id") for h in S.get("run_history", [])):
            JOBS.pop(key, None)                 # already finalized (by another tab): never twice
            from . import sim as simmod
            return simmod._finish_item(sim, item)
        item["elapsed"] = int(time.time() - job.t0)
        for name, msg in job.notes.items():
            sim_log(sim, name, f"Incomplete: {msg}")
        if job.notes:
            item["incomplete"] = "; ".join(f"{n} {m}" for n, m in job.notes.items())
        finalize(item["custom"], job)
        item["result"] = len(S.custom[item["custom"]])
        JOBS.pop(key, None)
        from . import sim as simmod
        return simmod._finish_item(sim, item)
    return False


def sim_log(sim: dict, agent: str, msg: str) -> None:
    sim["log"].append(dict(time=datetime.now().strftime("%H:%M:%S"), agent=agent, message=msg))


def retry_job(item: dict) -> None:
    job = JOBS.get(job_key(item))
    if job:
        job.start()


def unique(records: list[dict]) -> list[dict]:
    """The findings a reviewer sees: one per file, line and clause (the most severe), in area order. The benchmark
    scores this same list."""
    seen, out = set(), []
    for r in sorted(records, key=lambda r: (list(rules.FILES).index(r["area"]), SEV_ORDER[r["severity"]], r["line_number"])):
        k = (r["source_file"], r["line_number"], r["clause"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def finalize(label: str, job: Job) -> None:
    """Number the findings, store them, and make this dataset the one being reviewed."""
    S = st.session_state
    recs = unique(job.records)
    for i, r in enumerate(recs, start=1):
        r["id"] = f"F-{i:02d}"
    S.setdefault("custom", {})[label] = recs
    S["_fver"] = S.get("_fver", 0) + 1
    notes = getattr(job, "notes", {})
    S.setdefault("run_history", []).append(dict(
        incomplete="; ".join(f"{n} {m}" for n, m in notes.items()),
        label=label, run_id=getattr(job, "run_id", ""), engine=job.engine, model=job.model if job.engine != "rules" else "",
        user=getattr(job, "started_by", "") or S.get("user") or "",
        time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        proposed=[dict(source_file=r["source_file"], line_number=r["line_number"], related_lines=r.get("related_lines", []),
                       verdict=r.get("verdict", ""), reason=r.get("reason", ""), clause=r["clause"], area=r["area"],
                       severity=r["severity"], amount=float(r.get("amount") or 0)) for r in recs]))
    # keep the dataset's audit trail: a re-run is one more entry in it, never a fresh start
    store = S.setdefault("by_dataset", {})
    old_log = S.audit_log if S.get("dataset") == label else store.get(label, {}).get("audit_log", [])
    store[label] = dict(decisions={}, audit_log=old_log, cleared={}, notes={})
    activate(label, force_load=True)
    starter = getattr(job, "started_by", "") or ""
    who = f"Person ({starter})" if starter else "Person"
    engine = job.engine + (f" {job.model}" if job.engine != "rules" and job.model else "")
    if notes:                                    # an agent that stopped early: the result is not a full check
        engine += "; incomplete: " + "; ".join(f"{n} {m}" for n, m in notes.items())
    if S.audit_log:                              # a re-run: sealed in the trail where it happened
        C.log_action(who, C.NEW_RUN, "-", f"Ran the checks again: {len(recs)} findings from {engine}; earlier "
                     "decisions on this data no longer apply")
    else:
        C.log_action(who, "Ran the checks", "-", f"{len(recs)} findings from {engine}")


# ---------------------------------------------------------------- which data is in review
def activate(label: str | None, force_load: bool = False) -> None:
    """Switch the dataset in review. Each dataset keeps its own decisions."""
    S = st.session_state
    cur = S.get("dataset")
    if cur == label and not force_load:
        return
    store = S.setdefault("by_dataset", {})
    if cur != label:
        store[cur or "__sample__"] = dict(decisions=S.decisions, audit_log=S.audit_log, cleared=S.cleared,
                                          notes=S.get("notes", {}))
    pick = store.get(label or "__sample__", dict(decisions={}, audit_log=[], cleared={}, notes={}))
    S.decisions, S.audit_log, S.cleared = pick["decisions"], pick["audit_log"], pick["cleared"]
    S.notes = pick.get("notes", {})
    S.dataset = label


def incomplete_note(label: str | None) -> str:
    """Why the run whose findings are in review did not check everything ("" when it did, or for the sample)."""
    if not label:
        return ""
    return next((str(h.get("incomplete") or "") for h in reversed(st.session_state.get("run_history", []))
                 if h.get("label") == label), "")


def active_label() -> str | None:
    S = st.session_state
    d = S.get("dataset")
    return d if d and d in S.get("custom", {}) else None


def source_lines(name: str) -> list[str]:
    d = active_label()
    return st.session_state.uploads[d].get(name, []) if d else C.read_source(name)


def frame(label: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Findings for an uploaded dataset as (verified, hidden), same shape as the sample findings."""
    S = st.session_state
    files = S.uploads[label]
    key = (S.get("_fver", 0), id(S.custom[label]), len(S.custom[label]), id(files))   # same findings and files
    hit = S.get("_frame_cache", {}).get(label)
    if hit and hit[0] == key:
        return hit[1][0].copy(), hit[1][1].copy()          # copies: a caller changing its frame cannot spoil the cache
    rows = []
    for r in S.custom[label]:
        lines = files.get(r["source_file"], [])
        ok = 1 <= r["line_number"] <= len(lines)
        rows.append(dict(
            id=r["id"], severity=r["severity"], area=r["area"], clause=r["clause"], amount=float(r["amount"]),
            title=r["title"], skeptic_verdict=r.get("verdict", "Not checked"),
            evidence=lines[r["line_number"] - 1] if ok else "",
            related_evidence=" || ".join(lines[n - 1] for n in r.get("related_lines", []) if 1 <= n <= len(lines)),
            source_file=r["source_file"], line_number=r["line_number"], innocent_explanations=r.get("innocent", ""),
            skeptic_reason=r.get("reason", ""), proposed_fix=r.get("fix", ""), warning=r.get("warning", "")))
    df = pd.DataFrame(rows, columns=COLS)
    out = C.check_findings(df, lambda n: files.get(n, []))
    S.setdefault("_frame_cache", {})[label] = (key, out)
    return out[0].copy(), out[1].copy()


def apply_suppressions(ok: pd.DataFrame, label: str | None) -> pd.DataFrame:
    """Hide undecided findings that match a suppression ("don't flag this again"), for uploads and the sample."""
    S = st.session_state
    if S.get("show_suppressed") or not S.get("suppressions") or ok.empty:
        S["_n_suppressed"] = 0
        return ok
    from . import learn
    vf = view(label) if label else {}
    decided = S.get("decisions", {})

    def head(name):
        if not name.endswith(".csv"):
            return ""
        lines = vf.get(name) if label else C.read_source(name)
        return lines[0] if lines else ""
    heads = {n: head(n) for n in set(ok.source_file)}
    inv = (vf.get("invoices.txt") if label else C.read_source("invoices.txt")) or []   # for the supplier of a line
    keep = [r.id in decided or not learn.is_suppressed(
                str(r.clause), r.source_file,
                learn.entity(r.source_file, r.evidence, heads[r.source_file], inv, int(r.line_number)))
            for r in ok.itertuples()]
    S["_n_suppressed"] = len(ok) - sum(keep)
    return ok.loc[pd.Series(keep, index=ok.index, dtype=bool)].reset_index(drop=True)


# ---------------------------------------------------------------- data check before a run
def profile(label: str) -> list[dict]:
    """The data check for a dataset, worked out once per version of its files and settings."""
    import json
    S = st.session_state
    key = (id(S.get("uploads", {}).get(label)), json.dumps(S.get("mappings", {}).get(label), sort_keys=True, default=str),
           json.dumps(S.get("date_order", {}).get(label), sort_keys=True, default=str))
    hit = S.get("_profile_cache", {}).get(label)
    if hit and hit[0] == key:
        return [dict(x) for x in hit[1]]
    out = _profile(label)
    S.setdefault("_profile_cache", {})[label] = (key, out)
    return [dict(x) for x in out]


def _profile(label: str) -> list[dict]:
    """What Tallyhound believes about each uploaded file, so misread data is caught before it becomes findings.
    Each item: file, level (ok / warn), message."""
    import re as _re
    S = st.session_state
    files = view(label)
    out = []
    for name, lines in sorted(files.items()):
        if not lines:
            continue
        if name.endswith(".csv"):
            n = len(lines) - 1
            order = rules.date_order(lines)
            chosen = S.get("date_order", {}).get(label, {}).get(name)
            msg = f"{n} rows"
            if order == "ambiguous" and not chosen:
                out.append(dict(file=name, level="warn", kind="dates",
                                message=f"{msg}. Dates like 03/09/2026 could be 3 September or March 9 - choose below."))
            else:
                out.append(dict(file=name, level="ok", kind="info",
                                message=f"{msg}; dates {chosen or order}"))
            text = "\n".join(lines[1:])
            R = rules.rows(lines)
            for col in rules.REQUIRED.get(name, []):
                is_amt, is_date = "amount" in col, col.endswith("date") or col.endswith("_on") or col == "date"
                if not (is_amt or is_date):
                    continue
                bad = [i for i, r in R if r[col].strip() and r[col].strip().upper() not in ("N/A", "NA", "-")
                       and ((is_amt and rules._f(r[col]) == 0.0 and not _re.fullmatch(r"[\s$€£(]*-?0+([.,]0+)?\)?", r[col]))
                            or (is_date and r.d(col) is None))]
                odd = [i for i, r in R if is_amt and rules.EU_THOUSANDS.match(r[col].strip())] if is_amt else []
                if odd:
                    out.append(dict(file=name, level="warn", kind="amounts",
                                    message=f"{len(odd)} {col} value(s) like {R[[i for i, _ in R].index(odd[0])][1][col].strip()} "
                                            f"have three decimals (line {', '.join(map(str, odd[:5]))}) - is that "
                                            "one thousand and something? Check the export before trusting those rows"))
                if bad:
                    out.append(dict(file=name, level="warn", kind="unreadable",
                                    message=f"{len(bad)} {col} value(s) could not be read (line {', '.join(map(str, bad[:5]))}"
                                            f"{'...' if len(bad) > 5 else ''}) - those rows are checked as if blank"))
            if _re.search(r"\d\.\d{3},\d{2}\b", text):
                out.append(dict(file=name, level="ok", kind="info", message="amounts use decimal commas (1.234,50) - read as such"))
            cur = sorted(set(_re.findall(r"[$€£]", text)))
            if len(cur) > 1:
                out.append(dict(file=name, level="warn", kind="currency", message=f"more than one currency symbol: {' '.join(cur)}"))
            if name == "payments.csv" and any(rules._f(r["paid_amount"]) < 0 for _, r in rules.rows(lines)):
                out.append(dict(file=name, level="ok", kind="info", message="contains reversals (negative payments) - netted"))
            if name == "bank_statement.csv":
                non_ap = sum(bool(rules.NON_AP_BANK.search(r["description"] + " " + r["reference"])) for _, r in rules.rows(lines))
                if non_ap:
                    out.append(dict(file=name, level="ok", kind="info",
                                    message=f"{non_ap} bank line(s) look like fees, payroll, tax, cards or own-account transfers - not matched to suppliers"))
                if "payments.csv" not in files:
                    out.append(dict(file=name, level="warn", kind="missing", message="no payments.csv to reconcile against"))
            if name == "payment_run.csv":
                for need in ("vendors.csv", "approvals.csv", "payments.csv"):
                    if need not in files:
                        out.append(dict(file=name, level="warn", kind="missing", message=f"gate checks that need {need} will not run"))
        elif name == "invoices.txt":
            from . import invoices
            b = invoices.blocks(lines)
            out.append(dict(file=name, level="ok", kind="info", message=f"{len(b)} invoice PDF(s) read"))
            no_total = [x["name"] for x in b if "total" not in x]
            if no_total:
                out.append(dict(file=name, level="warn", kind="pdf", message="no total found in: " + ", ".join(no_total[:5])))
    return out
