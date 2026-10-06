"""Audit-trail chain, sign-in and segregation of duties, bank reconciliation, invoice PDFs, column suggestions,
suppressions, vendor risk, the folder watcher, OpenAI-compatible servers and saved uploads."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tallyhound import challenge, rules, score

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
SRC = ROOT / "data" / "source"


def sample_files():
    return {p.name: p.read_text(encoding="utf-8").splitlines() for p in SRC.iterdir()}


# ---- tamper-evident trail
def _trail_app():
    import streamlit as st

    from tallyhound import common as C
    C.init_state()
    for i in range(3):
        C.log_action("Reviewer", "Approved", f"F-0{i}", "ok")
    st.session_state.ok = C.verify_trail(st.session_state.audit_log)
    st.session_state.audit_log[1]["detail"] = "changed later"
    st.session_state.broken = C.verify_trail(st.session_state.audit_log)


def test_audit_trail_detects_an_edited_entry():
    at = AppTest.from_function(_trail_app, default_timeout=60).run()
    assert at.session_state.ok == (True, None) and at.session_state.broken == (False, 1)


# ---- sign-in, roles, segregation of duties
@pytest.fixture()
def users(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYHOUND_USERS", str(tmp_path / "users.json"))
    from tallyhound import auth
    auth.add_user("rita", "reviewer", "correct horse 1")
    auth.add_user("pete", "preparer", "battery staple 2")
    return auth


def test_passwords_are_hashed_and_checked(users):
    stored = json.loads(Path(users.users_file()).read_text())
    assert "correct horse 1" not in json.dumps(stored)
    assert users.check("rita", "correct horse 1") == "reviewer" and users.check("rita", "wrong") is None
    assert users.check("nobody", "x") is None


def test_sign_in_is_required_when_users_exist(users):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert any("Sign in" in m.value for m in at.markdown)
    at.text_input[0].set_value("pete")
    at.text_input[1].set_value("battery staple 2")
    at.button[0].click().run()
    assert at.session_state.user == "pete" and at.session_state.role == "preparer"
    at.session_state.nav = "Review"
    at.run()
    assert at.button(key="appr_F-01").disabled           # a preparer cannot approve


def test_segregation_of_duties_blocks_reviewing_your_own_run(users):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state.user, at.session_state.role = "rita", "reviewer"
    at.session_state.custom = {"mine": [dict(id="F-01", area="Payments", clause="5.2", severity="High", amount=1.0,
                                            title="t", source_file="payments.csv", line_number=2, related_lines=[],
                                            verdict="Confirmed", reason="r", innocent="", fix="")]}
    at.session_state.uploads = {"mine": {"payments.csv": sample_files()["payments.csv"]}}
    at.session_state.dataset = "mine"
    at.session_state.run_history = [dict(label="mine", engine="rules", model="", user="rita", time="now", proposed=[])]
    at.session_state.nav = "Review"
    at.run()
    assert at.button(key="appr_F-01").disabled
    assert any("Segregation of duties" in w.value for w in at.warning)


# ---- bank reconciliation
def test_bank_statement_matches_payments_and_flags_the_rest():
    files = sample_files()
    pays = rules.rows(files["payments.csv"])
    bank = ["date,description,amount,reference"]
    for _, p in pays[:-1]:                         # every payment but the last clears the bank
        bank.append(f"{p['pay_date']},PAYMENT {p['supplier']},-{p['paid_amount']},{p['payment_id']}")
    bank.append("2026-09-20,TRANSFER UNKNOWN,-999.00,TRF1")
    files["bank_statement.csv"] = bank
    hits = [h for h in rules.analyze(files) if h.clause == "5.5"]
    assert any(h.source_file == "bank_statement.csv" and "999.00" in h.title and h.severity == "High" for h in hits)


# ---- invoice PDFs
def test_invoice_pdf_checks_on_a_challenge():
    files, key, pdfs = challenge.generate_full(2, "hard")
    assert pdfs and "invoices.txt" in files
    hits = [h for h in rules.analyze(files) if h.area == "Invoices"]
    planted = [k for k in score.key_from_csv(challenge.key_csv(key)) if k["area"] == "Invoices"]
    assert planted and score.score([dict(source_file=h.source_file, line_number=h.line_number,
                                         related_lines=[ln for _, ln in h.related]) for h in hits], planted)["recall"] == 1


def test_pdf_without_text_is_reported_not_crashed():
    from tallyhound import custom
    files, notes = custom.parse_zip(challenge.to_zip({}, [], {"scan.pdf": b"%PDF-1.4 not really"}))
    assert "invoices.txt" not in files and any("scan.pdf" in n for n in notes)


# ---- column suggestions
def test_column_suggestions_from_export_names():
    from tallyhound import columns
    head = ["Num", "Date", "Bill Date", "Vendor", "Bill No", "Bill Amount", "Amount Paid"]
    s = columns.suggest(["payment_id", "pay_date", "invoice_date", "supplier", "invoice_no", "invoice_amount", "paid_amount"], head)
    assert s == {"payment_id": "Num", "pay_date": "Date", "invoice_date": "Bill Date", "supplier": "Vendor",
                 "invoice_no": "Bill No", "invoice_amount": "Bill Amount", "paid_amount": "Amount Paid"}


# ---- learning from reviewers
def test_entity_and_limit_hints():
    import pandas as pd

    from tallyhound import learn
    head = "claim_id,date,employee,category,amount,receipt_ref,notes,people"
    assert learn.entity("expenses.csv", "E-1,2026-09-01,R. Chen,TAXI,30.00,,x,", head) == "R. Chen"
    assert learn.entity("contracts.txt", "[INVOICE X-1 | Hanford Tooling | 2026-09-16] Line 1") == "Hanford Tooling"
    f = pd.DataFrame(dict(id=["F-1", "F-2"], clause=["1.1", "1.1"], amount=[2600.0, 2700.0]))
    hints = learn.limit_hints(f, {"F-1": {"status": "Rejected"}, "F-2": {"status": "Rejected"}}, rules.LIMITS)
    assert hints and hints[0]["suggested"] == 2701


def _suppress_app():
    from pathlib import Path

    import streamlit as st

    from tallyhound import common as C
    from tallyhound import custom, learn
    C.init_state()
    lines = (Path(C.SRC) / "expenses.csv").read_text(encoding="utf-8").splitlines()
    custom.add_upload("mine", {"expenses.csv": lines})
    rec = dict(id="F-01", area="Expenses", clause="6.1", severity="Low", amount=156.0, title="Meal", source_file="expenses.csv",
               line_number=next(i for i, ln in enumerate(lines, 1) if ln.startswith("E-1042")), related_lines=[],
               verdict="Confirmed", reason="", innocent="", fix="")
    st.session_state.custom = {"mine": [rec]}
    st.session_state.dataset = "mine"
    st.session_state.before = len(C.findings())
    learn.suppress("6.1", "expenses.csv", "A. Patel", "client dinners are fine")
    st.session_state.after = len(C.findings())


def test_suppressed_findings_are_set_aside():
    at = AppTest.from_function(_suppress_app, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state.before == 1 and at.session_state.after == 0


# ---- vendor risk
def test_vendor_risk_ranks_by_severity_and_money():
    from tallyhound import common as C
    from tallyhound import pages_extra
    f = C.load_findings_checked()[0]
    g = pages_extra.vendor_risk(f)
    assert len(g) > 5 and g["Risk score"].is_monotonic_decreasing


# ---- folder watcher and alerts
class Hook(BaseHTTPRequestHandler):
    got = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        Hook.got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


def test_watch_folder_reports_and_alerts_once(tmp_path, monkeypatch):
    import io
    import zipfile

    from tallyhound import headless
    f, k, p = challenge.generate_full(2, "medium")
    zipfile.ZipFile(io.BytesIO(challenge.to_zip(f, k, p))).extractall(tmp_path)
    srv = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("TALLYHOUND_SLACK_WEBHOOK", f"http://127.0.0.1:{srv.server_port}/hook")
    res = headless.run(tmp_path)
    report, new = headless.write_report(tmp_path, res)
    assert report.exists() and len(new) == len(res["hits"]) > 0
    assert headless.send_alert(headless.alert_text(new, 4, report)) == ["Slack"]
    assert "new finding" in Hook.got[-1]["text"]
    _, again = headless.write_report(tmp_path, headless.run(tmp_path))
    assert again == []                              # same problems: nothing new to alert about
    srv.shutdown()


# ---- OpenAI-compatible model servers
class OpenAIFake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "local-model"}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "tools" in req:
            msg = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "done", "arguments": "{}"}}]}
        else:
            msg = {"role": "assistant", "content": json.dumps({"reason": "fine", "verdict": "Confirmed"})}
        body = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)


def test_openai_compatible_server():
    from tallyhound import agents_tools, llm
    srv = HTTPServer(("127.0.0.1", 0), OpenAIFake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/v1"
    assert llm.models(url) == ["local-model"]
    assert llm.chat_json("s", "u", {"type": "object"}, model="local-model", url=url)["verdict"] == "Confirmed"
    assert agents_tools.investigate("Payments", sample_files()["payments.csv"], {}, "local-model", url) == []
    srv.shutdown()


# ---- saved uploads
def test_uploads_survive_a_reload():
    at = AppTest.from_file(APP, default_timeout=60).run()
    sid = at.query_params["s"][0] if isinstance(at.query_params["s"], list) else at.query_params["s"]
    at.radio(key="nav").set_value("Trust").run()
    at.button[[b.label for b in at.button].index("Make challenge")].click().run()
    at.button[[b.label for b in at.button].index("Add to my data")].click().run()
    at2 = AppTest.from_file(APP, default_timeout=60)
    at2.query_params["s"] = sid
    at2.run()
    assert "challenge-medium-1" in at2.session_state.uploads


def test_identical_related_lines_still_verify():
    import pandas as pd

    from tallyhound import common as C
    lines = ["head", "Invoice No: X-1", "Invoice No: X-1"]
    df = pd.DataFrame([dict(id="F-1", severity="High", area="Invoices", clause="8.3", amount=1.0, title="dup",
                            skeptic_verdict="Confirmed", evidence="Invoice No: X-1", related_evidence="Invoice No: X-1",
                            source_file="invoices.txt", line_number=3, innocent_explanations="", skeptic_reason="",
                            proposed_fix="")])
    ok, hidden = C.check_findings(df, lambda n: lines)
    assert len(ok) == 1 and hidden.empty
