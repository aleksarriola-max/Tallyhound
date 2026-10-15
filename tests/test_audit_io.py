"""Audit of the reading, PDF, folder-check, model-client and export paths: each test is one proven bug."""
import io
import json
import os
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tallyhound import headless, invoices, llm, rules, uploads

ROOT = Path(__file__).resolve().parent.parent
PAY_HEAD = "payment_id\tpay_date\tinvoice_date\tsupplier\tinvoice_no\tinvoice_amount\tpaid_amount"


def _server(get=None, post=None):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            b = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            self._send(get)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self._send(post["r"])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_a_stray_quote_in_a_tab_or_semicolon_file_does_not_swallow_the_rows_after_it():
    rows = [f"P-{i}\t2026-09-0{i}\t2026-08-30\tAcme\tA-{i}\t{i}00.00\t{i}00.00" for i in range(2, 6)]
    text = "\n".join([PAY_HEAD, 'P-1\t2026-09-01\t2026-08-30\t"Quote Freight\tR-1\t100.00\t100.00', *rows]) + "\n"
    for name, data in (("payments.tsv", text), ("payments.csv", text.replace("\t", ";"))):
        files, _ = uploads.read_uploads([(name, data.encode())])
        got = files["payments.csv"]
        assert len(got) == 6, (name, got)
        assert [r["payment_id"] for _, r in rules.rows(got)] == ["P-1", "P-2", "P-3", "P-4", "P-5"]


def test_a_multi_line_value_in_a_tab_file_is_still_joined():
    text = PAY_HEAD + "\tmemo\n" + 'P-1\t2026-09-01\t2026-08-30\tAcme\tA-1\t1.00\t1.00\t"two\nlines"\n'
    files, _ = uploads.read_uploads([("payments.tsv", text.encode())])
    assert files["payments.csv"][1].endswith(",two lines")      # joined with a space, as a comma CSV is


def test_a_word_or_spreadsheet_file_with_an_audit_name_is_not_read_as_text():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document>Contract with Ashby</w:document>" * 50)
    files, notes = uploads.read_uploads([("contracts.docx", b.getvalue()), ("payments.ods", b.getvalue()),
                                         ("vendors.csv", b"vendor_id,name\nV-1,Ashby\n")])
    assert set(files) == {"vendors.csv"}
    assert any("contracts.docx" in n and "Skipped" in n for n in notes)


def test_a_damaged_pdf_is_not_called_a_scan():
    raw = invoices.render_pdf(["Ashby Components", "Invoice No: INV-9", "Total due 10.00"])
    _, notes = uploads.read_uploads([("cut.pdf", raw[:len(raw) // 2]), ("junk.pdf", b"not a pdf at all")])
    assert notes and not any("a scan" in n for n in notes), notes
    assert all("could not be read" in n for n in notes)


def test_pdf_lines_reports_running_out_of_memory_as_refused(monkeypatch):
    import pypdf

    def boom(*a, **k):
        raise MemoryError
    monkeypatch.setattr(pypdf, "PdfReader", boom)
    assert invoices.pdf_lines(b"%PDF-1.4") is None          # the worker's "too large" answer, not "no text"


def test_reading_pdfs_leaves_no_pipe_open():
    import gc
    import time
    import warnings
    raw = invoices.render_pdf(["Ashby", "Invoice No: A-1"])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        invoices.read_pdfs([("a.pdf", raw), ("b.pdf", b"junk")])
        time.sleep(0.5)
        gc.collect()
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)], [str(w.message) for w in caught]


def test_a_total_before_tax_is_not_taken_for_the_invoice_total():
    inv = ["[PDF a.pdf]", "Ashby Components", "Invoice No: ASH-1", "Total (excl. VAT): £1,000.00", "VAT 20%: £200.00",
           "Total (incl. VAT): £1,200.00"]
    ap = ["record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no",
          "A-1,INVOICE,ASH-1,2026-09-01,Ashby Components,1200.00,T. Brandt,K. Osei,Manager,PO-1"]
    assert invoices.check({"invoices.txt": inv, "approvals.csv": ap}) == []


def test_a_folder_of_large_scans_does_not_hide_its_csv_files(tmp_path):
    (tmp_path / "scans").mkdir()
    (tmp_path / "payments.csv").write_text("payment_id,pay_date,invoice_date,supplier,invoice_no,invoice_amount,"
                                           "paid_amount\nP-1,2026-09-01,2026-08-30,Acme,A-1,1.00,1.00\n")
    for i in range(52):                                    # 52 MB that does not compress: more than one 50 MB zip
        (tmp_path / "scans" / f"scan{i}.pdf").write_bytes(b"%PDF-1.4\n" + os.urandom(1024 * 1024))
    files, notes = headless.load_folder(tmp_path)
    assert "payments.csv" in files, notes


def test_two_folder_checks_in_one_minute_keep_both_reports(tmp_path):
    (tmp_path / "payments.csv").write_text("payment_id,pay_date,invoice_date,supplier,invoice_no,invoice_amount,"
                                           "paid_amount\nP-1,2026-09-01,2026-08-30,Acme,A-1,500.00,500.00\n"
                                           "P-2,2026-09-02,2026-08-30,Acme,A-1,500.00,500.00\n")
    res = headless.run(tmp_path)
    first, new1 = headless.write_report(tmp_path, res)
    import time
    time.sleep(1.1)
    second, new2 = headless.write_report(tmp_path, res)
    assert new1 and not new2 and first != second and first.exists()


def test_ollama_host_is_read_the_way_ollama_reads_it():
    code = "from tallyhound import llm; print(llm.DEFAULT_URL, llm.check_url(llm.DEFAULT_URL))"
    for host, want in (("127.0.0.1", "http://127.0.0.1:11434 None"), ("0.0.0.0", "http://localhost:11434 None"),
                       ("0.0.0.0:11435", "http://localhost:11435 None"), ("http://ollama:11434", "http://ollama:11434 None")):
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                             env=dict(os.environ, OLLAMA_HOST=host), check=True).stdout.strip()
        assert out == want, (host, out)


def test_odd_tool_call_replies_become_plain_errors_or_are_ignored():
    from tallyhound import agents_tools
    reply: dict = {}
    srv = _server(post=reply)
    url = f"http://127.0.0.1:{srv.server_port}"
    for path, r in [("/v1", {"choices": [{"message": "text"}]}), ("/v1", {"choices": [{"message": {"tool_calls": ["x"]}}]}),
                    ("/v1", {"choices": [{"message": {"tool_calls": [{"function": "describe"}]}}]}),
                    ("", {"message": {"tool_calls": "describe"}}), ("", {"message": {"tool_calls": [None]}})]:
        reply["r"] = r
        try:
            assert agents_tools.investigate("Payments", ["a,b", "1,2"], {}, "m", url + path) == []
        except llm.LLMError:
            pass
    srv.shutdown()


def test_a_model_server_with_no_models_is_not_called_unreachable():
    srv = _server(get={"models": []})

    def app():
        from tallyhound import run_analysis
        run_analysis.engine_settings(False)
    at = AppTest.from_function(app, default_timeout=30)
    at.session_state["adv_engine"] = "Ollama agents"
    at.session_state["adv_url"] = f"http://127.0.0.1:{srv.server_port}"
    at.run()
    srv.shutdown()
    warn = " ".join(w.value for w in at.warning)
    assert "not reachable" not in warn and "no models" in warn, warn


def test_memo_does_not_call_shadow_findings_decided():
    def app():
        import streamlit as st

        from tallyhound import common as C
        from tallyhound import exports
        C.init_state()
        f = C.findings()
        last = str(f.clause.iloc[-1])
        st.session_state.shadow = [last]
        for i, c in zip(f.id, f.clause):
            if str(c) != last:
                C.decide(i, "Approved")
        st.session_state.pdf = exports.build_memo()
        st.session_state.live = int((f.clause.astype(str) != last).sum())
    at = AppTest.from_function(app, default_timeout=60).run()
    assert not at.exception
    from pypdf import PdfReader
    text = " ".join(PdfReader(io.BytesIO(at.session_state.pdf)).pages[0].extract_text().split())
    assert f"Findings: {at.session_state.live}, all decided by a person" in text and "shadow" in text, text[:400]


def test_export_scripts_answer_help_without_writing_files():
    for script in ("make_example_exports.py", "make_export_pack.py"):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--help"], capture_output=True, text=True)
        assert out.returncode == 0 and "usage:" in out.stdout and "Wrote" not in out.stdout, (script, out.stdout)
