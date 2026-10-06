"""Uploaded files: read the zip, run the agents on it, and keep the results apart from the sample company."""
from __future__ import annotations

import io
import threading
import time
import zipfile
from datetime import datetime

import pandas as pd
import streamlit as st

from . import agents, llm, rules
from . import common as C

MAX_ZIP_MB = 50
MAX_LINES = 300_000
STEMS = {"payments": "payments.csv", "approvals": "approvals.csv", "vendors": "vendors.csv",
         "contracts": "contracts.txt", "expenses": "expenses.csv", "payment_run": "payment_run.csv",
         "bank_statement": "bank_statement.csv", "bank": "bank_statement.csv",
         "invoices": "invoices.txt"}
SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2}
COLS = ["id", "severity", "area", "clause", "amount", "title", "skeptic_verdict", "evidence", "related_evidence",
        "source_file", "line_number", "innocent_explanations", "skeptic_reason", "proposed_fix"]


# ---------------------------------------------------------------- reading a zip
def parse_zip(data: bytes) -> tuple[dict[str, list[str]], list[str]]:
    """Returns (files, notes). Reads in memory only; nothing is written to disk and no path is ever used."""
    notes: list[str] = []
    if len(data) > MAX_ZIP_MB * 1024 * 1024:
        return {}, [f"That zip is larger than {MAX_ZIP_MB} MB."]
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {}, ["That file is not a valid zip."]
    files: dict[str, list[str]] = {}
    total = 0
    pdfs: list[tuple[str, list[str]]] = []
    for info in zf.infolist():
        base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
        if info.is_dir() or base.startswith(".") or "__MACOSX" in info.filename:
            continue
        stem = base.rsplit(".", 1)[0].lower()
        if base.lower().endswith(".pdf"):
            from . import invoices
            if info.file_size > MAX_ZIP_MB * 1024 * 1024:
                notes.append(f"Skipped {base}: too large.")
                continue
            lines = invoices.pdf_lines(zf.read(info))
            if lines:
                pdfs.append((base, lines))
            else:
                notes.append(f"{base} has no readable text (a scan?). It was skipped; scanned PDFs need OCR first.")
            continue
        if stem == "answer_key" and base.lower().endswith(".csv"):
            files["answer_key.csv"] = zf.read(info).decode("utf-8-sig", errors="replace").splitlines()
            notes.append("Found answer_key.csv: the Scorecard page will grade runs on this data.")
            continue
        match = next((v for k, v in STEMS.items() if stem == k or stem.startswith(k + "_") or stem.endswith("_" + k)), None)
        if match is None:
            notes.append(f"Ignored {base} (not one of the audit files).")
            continue
        if info.file_size > MAX_ZIP_MB * 1024 * 1024:
            notes.append(f"Skipped {base}: too large.")
            continue
        text = zf.read(info).decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        total += len(lines)
        if total > MAX_LINES:
            notes.append(f"Stopped at {MAX_LINES} lines; {base} and later files were skipped.")
            break
        missing = rules.missing_columns(match, lines)
        if missing:
            notes.append(f"{match} is missing columns: {', '.join(missing)}. Match its columns below, or it is skipped.")
        files[match] = lines
    if pdfs:
        from . import invoices
        files[invoices.NAME] = invoices.combine(sorted(pdfs))
        notes.append(f"Read {len(pdfs)} invoice PDF(s) into invoices.txt for the Invoices agent.")
    return files, notes


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
    import io
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

    def __init__(self, label, files, engine, model, url, policy, skip, limits=None):
        self.limits = dict(limits or rules.LIMITS)
        self.label, self.files, self.engine, self.model, self.url = label, files, engine, model, url
        self.policy, self.skip = policy, set(skip)
        self.agents = {n: dict(status="Skipped" if n in self.skip else "Waiting", pct=0, secs=0, msg="") for n in
                       ["Orchestrator", *rules.FILES, "Skeptic"]}
        self.records: list[dict] = []
        self.thread: threading.Thread | None = None
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.start()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
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

    def _run(self) -> None:
        for name in self.agents:
            a = self.agents[name]
            if a["status"] in ("Done", "Skipped"):
                continue
            t = time.time()
            self._set(name, status="Running", pct=5)
            try:
                if name == "Orchestrator":
                    pass
                elif name == "Skeptic":
                    self._skeptic(t)
                else:
                    self._area(name, t)
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
            for h in rules.run_area(area, self.files, self.limits):
                self.records.append(dict(
                    area=h.area, clause=h.clause, severity=h.severity, amount=h.amount, title=h.title,
                    source_file=h.source_file, line_number=h.line_number, related_lines=[ln for _, ln in h.related],
                    innocent=rules.INNOCENT.get(h.clause, ""), fix=rules.FIXES.get(h.clause, ""),
                    **(dict(verdict="Confirmed",
                            reason=f"Fixed rule check, no AI. Clause {h.clause}: {self.policy.get(h.clause + '|' + h.area, '')}")
                       if self.engine == "rules" else {})))
                r = self.records[-1]
                src = self.files.get(r["source_file"], [])          # cross-file checks quote other files
                r["evidence"] = src[r["line_number"] - 1] if 0 < r["line_number"] <= len(src) else ""
            return
        if self.engine == "ollama-tools":
            from . import agents_tools
            raw = agents_tools.investigate(area, lines, self.policy, self.model, self.url)
        else:
            raw = agents.propose(area, lines, self.policy, self.model, self.url)
        for f in raw:
            v = agents.verified(f, lines, area)
            if v:
                self.records.append(v)

    def _skeptic(self, t: float) -> None:
        todo = [r for r in self.records if "verdict" not in r]
        for i, r in enumerate(todo, start=1):
            lines = self.files[r["source_file"]]
            header = lines[0] if r["source_file"].endswith(".csv") else ""
            r["evidence"] = lines[r["line_number"] - 1]
            related = [lines[n - 1] for n in r.get("related_lines", []) if 1 <= n <= len(lines)]
            r["verdict"], r["reason"] = agents.skeptic(r, self.policy.get(f"{r['clause']}|{r['area']}", ""), self.model,
                                                       self.url, header, related)
            self._set("Skeptic", pct=5 + int(90 * i / max(len(todo), 1)), secs=int(time.time() - t))


JOBS: dict[str, Job] = {}


def tick_item(sim: dict, item: dict) -> bool:
    """Called once a second for a running uploaded-data item. Returns True when the page must rerun."""
    S = st.session_state
    key = f"{S.get('sid', '')}|{item['label']}"
    job = JOBS.get(key)
    if job is None:
        job = JOBS[key] = Job(item["custom"], view(item["custom"]), item.get("engine", "rules"),
                              item.get("model", llm.DEFAULT_MODEL), item.get("url", llm.DEFAULT_URL), C.policy(),
                              [a["name"] for a in item["agents"] if a["status"] == "Skipped"], C.limits())
        sim_log(sim, "Orchestrator", f"Reading {len(job.files)} uploaded file(s) with "
                + ({"rules": "the built-in rules", "rules+skeptic": f"the built-in rules and an Ollama Skeptic ({job.model})",
                    "ollama-tools": f"Ollama agents with tools ({job.model})"}.get(job.engine, f"Ollama ({job.model})")))
        return True
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
        item["elapsed"] = int(time.time() - job.t0)
        finalize(item["custom"], job)
        item["result"] = len(S.custom[item["custom"]])
        JOBS.pop(key, None)
        from . import sim as simmod
        return simmod._finish_item(sim, item)
    return False


def sim_log(sim: dict, agent: str, msg: str) -> None:
    sim["log"].append(dict(time=datetime.now().strftime("%H:%M:%S"), agent=agent, message=msg))


def retry_job(item: dict) -> None:
    job = JOBS.get(f"{st.session_state.get('sid', '')}|{item['label']}")
    if job:
        job.start()


def finalize(label: str, job: Job) -> None:
    """Number the findings, store them, and make this dataset the one being reviewed."""
    S = st.session_state
    seen, recs = set(), []
    for r in sorted(job.records, key=lambda r: (list(rules.FILES).index(r["area"]), SEV_ORDER[r["severity"]], r["line_number"])):
        k = (r["source_file"], r["line_number"], r["clause"])
        if k not in seen:
            seen.add(k)
            recs.append(r)
    for i, r in enumerate(recs, start=1):
        r["id"] = f"F-{i:02d}"
    S.setdefault("custom", {})[label] = recs
    S.setdefault("run_history", []).append(dict(
        label=label, engine=job.engine, model=job.model if job.engine != "rules" else "", user=S.get("user") or "",
        time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        proposed=[dict(source_file=r["source_file"], line_number=r["line_number"], related_lines=r.get("related_lines", []),
                       verdict=r.get("verdict", ""), reason=r.get("reason", ""), clause=r["clause"], area=r["area"],
                       severity=r["severity"], amount=float(r.get("amount") or 0)) for r in recs]))
    S.setdefault("by_dataset", {})[label] = dict(decisions={}, audit_log=[], cleared={}, notes={})
    activate(label, force_load=True)


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
            skeptic_reason=r.get("reason", ""), proposed_fix=r.get("fix", "")))
    df = pd.DataFrame(rows, columns=COLS)
    ok, hidden = C.check_findings(df, lambda n: files.get(n, []))
    if not st.session_state.get("show_suppressed") and st.session_state.get("suppressions"):
        from . import learn
        vf = view(label)
        keep = [not learn.is_suppressed(str(r.clause), r.source_file, learn.entity(
            r.source_file, r.evidence, vf.get(r.source_file, [""])[0] if r.source_file.endswith(".csv") else ""))
            for r in ok.itertuples()]
        st.session_state["_n_suppressed"] = len(ok) - sum(keep)
        ok = ok[keep].reset_index(drop=True)
    else:
        st.session_state["_n_suppressed"] = 0
    return ok, hidden


# ---------------------------------------------------------------- data check before a run
def profile(label: str) -> list[dict]:
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
