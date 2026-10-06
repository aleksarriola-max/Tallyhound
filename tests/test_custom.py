"""Uploaded files: zip reading, built-in rules, the Ollama agents (against a pretend server) and the full run."""
import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tallyhound import agents, custom, llm, rules
from tallyhound import common as C

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
SRC = ROOT / "data" / "source"


def sample_files() -> dict[str, list[str]]:
    return {p.name: p.read_text(encoding="utf-8").splitlines() for p in SRC.iterdir()}


def make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, t in files.items():
            z.writestr(n, t)
    return buf.getvalue()


def policy() -> dict:
    df = pd.read_csv(ROOT / "data" / "policy.csv", dtype=str)
    return {f"{r.clause}|{r.area}": r.text for r in df.itertuples()}


# ---- reading zips
def test_parse_zip_reads_the_five_files_and_ignores_junk():
    z = make_zip({**{p.name: p.read_text() for p in SRC.iterdir()}, "__MACOSX/._x": "junk", "notes.docx": "x",
                  "sub/Payments_Sept.csv": (SRC / "payments.csv").read_text()})
    files, notes = custom.parse_zip(z)
    assert set(files) == {"payments.csv", "approvals.csv", "vendors.csv", "contracts.txt", "expenses.csv"}
    assert any("notes.docx" in n for n in notes)


def test_parse_zip_rejects_bad_input():
    assert custom.parse_zip(b"not a zip")[0] == {}
    files, notes = custom.parse_zip(make_zip({"payments.csv": "a,b\n1,2\n"}))
    assert "payments.csv" in files and any("missing columns" in n for n in notes)   # kept, so columns can be matched


# ---- built-in rules
def test_rules_find_most_planted_findings_and_every_quote_verifies():
    files = sample_files()
    hits = rules.analyze(files)
    want = {(r.source_file, int(r.line_number)) for r in C.read_csv("findings.csv").itertuples()}
    got = {(h.source_file, h.line_number) for h in hits}
    assert len(want & got) >= 22
    for h in hits:
        assert 1 <= h.line_number <= len(files[h.source_file])


def test_rules_job_runs_to_the_end():
    job = custom.Job("t", sample_files(), "rules", "m", "http://127.0.0.1:1", policy(), [])
    job.thread.join(20)
    assert job.done and len(job.records) >= 22 and job.failed is None


# ---- Ollama agents against a pretend local server
class Fake(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": "fake:1b"}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        user = req["messages"][1]["content"]
        if "tools" in req:                                   # tool-using agent: answer "nothing to do"
            out = None
        elif "Proposed finding" in user:
            out = {"verdict": "Doubtful", "reason": "Could be a credit note."}
        else:
            lines = {int(a.split("\t")[0]): a.split("\t", 1)[1] for a in user.splitlines() if "\t" in a and a.split("\t")[0].isdigit()}
            n = 3 if 3 in lines else min(lines)
            good = dict(clause="5.1", severity="Medium", amount=12.5, title="Real one", line_number=n, evidence=lines[n],
                        innocent_explanation="x", proposed_fix="y")
            made_up = dict(good, title="Invented", evidence="this line is not in the file")
            wrong_line = dict(good, title="Wrong line", line_number=n + 1)
            out = {"findings": [good, made_up, wrong_line]}
        msg = {"role": "assistant", "content": ""} if out is None else {"content": json.dumps(out)}
        body = json.dumps({"message": msg}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)


@pytest.fixture()
def fake_ollama():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_ollama_agents_keep_only_quotes_that_are_exact_lines(fake_ollama):
    assert llm.models(fake_ollama) == ["fake:1b"]
    job = custom.Job("t", {"payments.csv": sample_files()["payments.csv"]}, "ollama", "fake:1b", fake_ollama, policy(),
                     ["Approvals", "Vendors", "Contracts", "Expenses"])
    job.thread.join(30)
    assert job.done, job.failed
    assert [r["title"] for r in job.records] == ["Real one"]          # invented and wrong-line quotes were dropped
    assert job.records[0]["verdict"] == "Doubtful"                    # the Skeptic's view is kept, not hidden


def test_unreachable_ollama_shows_as_a_failed_agent():
    job = custom.Job("t", sample_files(), "ollama", "m", "http://127.0.0.1:1", policy(), [])
    job.thread.join(30)
    assert job.failed and job.failed[0] == "Payments" and "Could not reach Ollama" in job.failed[1]["msg"]


# ---- the whole flow in the app
def test_upload_run_review_and_export_end_to_end():
    at = AppTest.from_file(APP, default_timeout=60).run()
    files, _ = custom.parse_zip(make_zip({p.name: p.read_text() for p in SRC.iterdir()}))
    at.session_state.uploads = {"mine": files}
    at.session_state.extra_opts = {"audit": {"mine": "5 of 5 files"}}
    at.run()
    at.button(key="open_run").click().run()              # Home: "Check new files" opens the run dialog
    assert at.selectbox(key="run_choice").value == "mine"
    at.button(key="run_go").click().run()
    for _ in range(40):
        if at.session_state.get("dataset") == "mine":
            break
        time.sleep(1)
        at.run()
    assert at.session_state.dataset == "mine", "the run did not finish"
    assert not at.exception, [e.value for e in at.exception]
    assert "Quotes verified 100%" in " ".join(m.value for m in at.sidebar.markdown)
    assert len(at.session_state.custom["mine"]) >= 22
    at.session_state.nav = "Review"
    at.run()
    assert not at.exception
    first = at.session_state.custom["mine"][0]["id"]
    at.button(key=f"appr_{first}").click().run()
    assert at.session_state.decisions[first]["status"] == "Approved"
    # switching back to the sample company brings back the sample's own (empty) decisions
    at.selectbox(key="ds_pick").select("Sample company").run()
    assert at.session_state.dataset is None and at.session_state.decisions == {}


def test_rules_with_ollama_skeptic_challenges_every_rule_hit(fake_ollama):
    job = custom.Job("t", sample_files(), "rules+skeptic", "fake:1b", fake_ollama, policy(), [])
    job.thread.join(60)
    assert job.done, job.failed
    assert len(job.records) >= 22
    assert all(r["verdict"] == "Doubtful" for r in job.records)       # the pretend Skeptic doubts everything


def test_skeptic_sees_labelled_fields():
    line = "V-4108,Marlowe,55-1,ACTIVE,****6120,2026-09-26,NO,2023-05-02,YES,2026-09-28,8820.00"
    head = "vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount"
    out = agents.labelled(line, head)
    assert "bank_verified=NO" in out and "bank_changed_on=2026-09-26" in out


def test_notice_when_upload_exists_but_sample_is_showing():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.warning
    at.session_state.custom = {"mine": []}
    at.session_state.uploads = {"mine": {}}
    at.run()
    assert any("SAMPLE" in w.value for w in at.warning)


def test_tools_engine_runs_through_the_job(fake_ollama):
    job = custom.Job("t", {"payments.csv": sample_files()["payments.csv"]}, "ollama-tools", "fake:1b", fake_ollama,
                     policy(), ["Approvals", "Vendors", "Contracts", "Expenses"])
    job.thread.join(30)
    assert job.done, job.failed


def test_rules_job_quotes_lines_from_the_right_file():
    from tallyhound import challenge as ch
    for seed in (2, 3, 6):
        files, _ = ch.generate(seed, "hard")
        job = custom.Job("t", files, "rules", "m", "http://127.0.0.1:1", policy(), [])
        job.thread.join(30)
        assert job.done and job.failed is None
        for r in job.records:
            assert r["evidence"] == files[r["source_file"]][r["line_number"] - 1]
