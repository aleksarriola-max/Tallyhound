"""Audit 3 of the AI path: agents, tool agents and the Skeptic against a scripted fake model server."""
import csv
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd
import pytest

from tallyhound import agents, challenge, custom, llm, rules

ROOT = Path(__file__).resolve().parents[1]


def policy() -> dict[str, str]:
    return {f"{r.clause}|{r.area}": r.text for r in pd.read_csv(ROOT / "data" / "policy.csv", dtype=str).itertuples()}


# ---------------------------------------------------------------- a fake Ollama / OpenAI-style server
class Server(BaseHTTPRequestHandler):
    """Speaks Ollama (/api/tags, /api/chat with and without tools) and OpenAI style (/v1/models,
    /v1/chat/completions). `script(kind, req)` decides each answer; kind is propose, skeptic or tools.
    It returns a dict (the JSON answer), a list of (tool name, arguments) for a tools turn, or ("error", code, text)."""
    script = staticmethod(lambda kind, req: {})
    seen: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._send(200, {"data": [{"id": "fake"}]})
        else:
            self._send(200, {"models": [{"name": "fake"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        oai = self.path.startswith("/v1/")
        user = next((m["content"] for m in req["messages"] if m["role"] == "user"), "")
        kind = "tools" if "tools" in req else ("skeptic" if "Proposed finding" in user else "propose")
        type(self).seen.append((kind, req))
        out = type(self).script(kind, req)
        if isinstance(out, tuple) and out and out[0] == "error":
            return self._send(out[1], out[2])
        if kind == "tools":
            calls = [{"id": f"c{i}", "type": "function",
                      "function": {"name": n, "arguments": json.dumps(a) if oai else a}} for i, (n, a) in enumerate(out or [])]
            msg = {"role": "assistant", "content": "", **({"tool_calls": calls} if calls else {})}
        else:
            msg = {"role": "assistant", "content": json.dumps(out)}
        self._send(200, {"choices": [{"message": msg}]} if oai else {"message": msg})


@pytest.fixture()
def fake():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    Server.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def lines_in(text: str) -> dict[int, str]:
    """The numbered lines a prompt or tool result showed the model."""
    return {int(a.split("\t")[0]): a.split("\t", 1)[1] for a in text.splitlines()
            if "\t" in a and a.split("\t")[0].isdigit()}


def run(files, engine, url, skip=()):
    job = custom.Job("t", files, engine, "fake", url, policy(), list(skip))
    job.thread.join(60)
    assert job.done, job.failed
    return job


OTHERS = [a for a in rules.FILES if a != "Approvals"]


# ---------------------------------------------------------------- pattern clauses are never Confirmed
def test_skeptic_cannot_confirm_a_pattern_clause_rules_plus_skeptic(fake):
    """PATTERN_CLAUSES (1.6, 4.5, 5.6, 6.6) are "worth a look, not a breach on their own". The rules engine marks them
    Doubtful; a Skeptic that says Confirmed must not turn them into bulk-approvable Confirmed findings."""
    srv, url = fake
    Server.script = staticmethod(lambda kind, req: {"reason": "This clearly breaches the clause.", "verdict": "Confirmed"})
    files, _ = challenge.generate(1, "easy")
    job = run(files, "rules+skeptic", url)
    pat = [r for r in job.records if r["clause"] in rules.PATTERN_CLAUSES]
    assert pat, "the challenge month should hold a pattern finding"
    assert all(r["verdict"] == "Doubtful" for r in pat), [(r["clause"], r["verdict"]) for r in pat]
    assert any(r["verdict"] == "Confirmed" for r in job.records if r["clause"] not in rules.PATTERN_CLAUSES)


def test_skeptic_cannot_confirm_a_pattern_clause_ollama_agents(fake):
    srv, url = fake
    files, _ = challenge.generate(1, "easy")

    def script(kind, req):
        if kind == "skeptic":
            return {"reason": "This clearly breaches the clause.", "verdict": "Confirmed"}
        shown = lines_in(req["messages"][1]["content"])
        n = min(k for k in shown if k > 1)
        return {"findings": [dict(clause="1.6", severity="Low", amount=100, title="Bunched approvals", line_number=n,
                                  evidence=shown[n], innocent_explanation="", proposed_fix="")]}
    Server.script = staticmethod(script)
    job = run({"approvals.csv": files["approvals.csv"]}, "ollama", url, OTHERS)
    assert [r["clause"] for r in job.records] == ["1.6"]
    assert job.records[0]["verdict"] == "Doubtful"


# ---------------------------------------------------------------- the self-check must not punish a reason that states the rule
@pytest.mark.parametrize("reason", [
    "Paid 1,200 against an approved invoice of 1,000; payments must not exceed the approved amount.",
    "The paid amount is above the invoice amount, and a payment should never exceed it.",
])
def test_a_confirmed_reason_quoting_the_rule_is_not_a_contradiction(fake, reason):
    srv, url = fake
    Server.script = staticmethod(lambda kind, req: {"reason": reason, "verdict": "Confirmed"})
    f = dict(clause="5.1", title="Overpaid", evidence="P-1,2026-01-05,1000.00,1200.00", innocent="")
    verdict, why = agents.skeptic(f, "The amount paid must match the approved invoice amount.", "fake", url)
    assert (verdict, why) == ("Confirmed", reason)
    assert len(Server.seen) == 1                       # no re-ask


def test_a_real_contradiction_still_ends_unclear(fake):
    srv, url = fake
    Server.script = staticmethod(lambda kind, req: {"reason": "The payment does not exceed the invoice.", "verdict": "Confirmed"})
    verdict, why = agents.skeptic(dict(clause="5.1", title="t", evidence="x", innocent=""), "c", "fake", url)
    assert verdict == "Doubtful" and why.startswith("Unclear:") and len(Server.seen) == 2


# ---------------------------------------------------------------- tool agents: severity in another case
def test_tool_agent_severity_in_lower_case_is_kept(fake):
    """Tool arguments are not held to the schema's enum; "high" from a tool call must not quietly become Medium."""
    srv, url = fake
    files, _ = challenge.generate(1, "easy")

    def script(kind, req):
        if kind == "skeptic":
            return {"reason": "The line shows it.", "verdict": "Confirmed"}
        tool_msgs = [m for m in req["messages"] if m["role"] == "tool"]
        if not tool_msgs:
            return [("get_lines", {"line_numbers": [2]})]
        if len(tool_msgs) == 1:
            return [("report_finding", dict(clause="1.1", severity="high", title="No PO", line_number=2,
                                            evidence=lines_in(tool_msgs[0]["content"])[2]))]
        return [("done", {})]
    Server.script = staticmethod(script)
    job = run({"approvals.csv": files["approvals.csv"]}, "ollama-tools", url + "/v1", OTHERS)
    assert [r["severity"] for r in job.records] == ["High"]


# ---------------------------------------------------------------- error messages name the server in use
def test_an_unreachable_openai_style_server_is_not_called_ollama():
    with pytest.raises(llm.LLMError) as e:
        llm.chat_json("s", "u", {}, url="http://127.0.0.1:1/v1", timeout=2)
    assert "Ollama" not in str(e.value) and "ollama serve" not in str(e.value)
    with pytest.raises(llm.LLMError) as e:
        llm.chat_json("s", "u", {}, url="http://127.0.0.1:1", timeout=2)
    assert "ollama serve" in str(e.value)


def test_an_openai_style_server_error_is_not_called_ollama(fake):
    srv, url = fake
    Server.script = staticmethod(lambda kind, req: ("error", 400, '{"error": "response_format not supported"}'))
    with pytest.raises(llm.LLMError) as e:
        llm.chat_json("s", "u", {}, url=url + "/v1", timeout=5)
    assert "response_format not supported" in str(e.value) and "Ollama" not in str(e.value)


# ---------------------------------------------------------------- benchmark counts what the reviewer would see
def test_benchmark_counts_a_repeated_finding_once(fake, tmp_path):
    """The app shows one finding per (file, line, clause); the benchmark must score the same list, or a model that
    reports one false alarm ten times is charged ten false alarms (and a repeated hit inflates precision)."""
    srv, url = fake

    def script(kind, req):
        if kind == "skeptic":
            return {"reason": "The line shows it.", "verdict": "Confirmed"}
        tool_msgs = [m for m in req["messages"] if m["role"] == "tool"]
        if "approvals.csv" not in req["messages"][0]["content"]:
            return [("done", {})]
        if not tool_msgs:
            return [("get_lines", {"line_numbers": [2]})]
        if len(tool_msgs) == 1:
            ev = lines_in(tool_msgs[0]["content"])[2]
            return [("report_finding", dict(clause="1.1", severity="Medium", title="x", line_number=2, evidence=ev))] * 3
        return [("done", {})]
    Server.script = staticmethod(script)
    out = tmp_path / "bench"
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "benchmark.py"), "--engines", "ollama-tools",
                        "--models", "fake", "--url", url, "--seeds", "1", "--difficulty", "easy", "--out", str(out)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert p.returncode == 0, p.stderr + p.stdout
    row = next(csv.DictReader(open(out / "benchmark.csv", encoding="utf-8")))
    assert row["proposed"] == "1", row


def test_finalize_and_benchmark_share_one_dedupe():
    recs = [dict(area="Approvals", source_file="approvals.csv", line_number=2, clause="1.1", severity="Medium"),
            dict(area="Approvals", source_file="approvals.csv", line_number=2, clause="1.1", severity="High"),
            dict(area="Approvals", source_file="approvals.csv", line_number=2, clause="1.3", severity="Medium")]
    got = custom.unique(recs)
    assert [(r["clause"], r["severity"]) for r in got] == [("1.1", "High"), ("1.3", "Medium")]


# ---------------------------------------------------------------- a repeating model must not cost a Skeptic call per copy
def test_repeated_proposals_get_one_skeptic_call(fake):
    """Small models in JSON mode often repeat one array item until the context ends. The reviewer sees one finding;
    the Skeptic (up to two calls of up to 15 minutes each) must not run once per copy."""
    srv, url = fake
    files, _ = challenge.generate(1, "easy")

    def script(kind, req):
        if kind == "skeptic":
            return {"reason": "The line shows it.", "verdict": "Confirmed"}
        shown = lines_in(req["messages"][1]["content"])
        return {"findings": [dict(clause="1.1", severity="Medium", amount=1, title="No PO", line_number=2,
                                  evidence=shown[2], innocent_explanation="", proposed_fix="")] * 40}
    Server.script = staticmethod(script)
    job = run({"approvals.csv": files["approvals.csv"]}, "ollama", url, OTHERS)
    assert len(job.records) == 1
    assert sum(k == "skeptic" for k, _ in Server.seen) == 1


# ---------------------------------------------------------------- an agent that stops early must say so
def test_an_agent_out_of_time_is_reported_not_silently_clean(fake, monkeypatch):
    srv, url = fake
    Server.script = staticmethod(lambda kind, req: {"findings": []})
    monkeypatch.setattr(agents, "AREA_SECONDS", 0)
    files, _ = challenge.generate(1, "easy")
    job = run({"approvals.csv": files["approvals.csv"]}, "ollama", url, OTHERS)
    assert "Approvals" in job.notes and "not read" in job.notes["Approvals"]


def test_a_tool_agent_out_of_steps_is_reported(fake):
    srv, url = fake
    Server.script = staticmethod(lambda kind, req: [("describe", {})])          # never calls done
    files, _ = challenge.generate(1, "easy")
    job = run({"approvals.csv": files["approvals.csv"]}, "ollama-tools", url, OTHERS)
    assert sum(k == "tools" for k, _ in Server.seen) == 16                       # the step limit holds
    assert "Approvals" in job.notes and "steps" in job.notes["Approvals"]


def test_a_finished_tool_agent_has_no_note(fake):
    srv, url = fake
    Server.script = staticmethod(lambda kind, req: [("done", {})])
    files, _ = challenge.generate(1, "easy")
    job = run({"approvals.csv": files["approvals.csv"]}, "ollama-tools", url, OTHERS)
    assert job.notes == {}


# ---------------------------------------------------------------- already defended: kept as guards
def test_skeptic_failing_mid_run_resumes_without_losing_verdicts(fake):
    srv, url = fake
    calls = {"n": 0, "down": True}

    def script(kind, req):
        calls["n"] += 1
        if calls["n"] == 3 and calls["down"]:
            calls["down"] = False
            return ("error", 500, '{"error": "model runner crashed"}')
        return {"reason": "The line shows it.", "verdict": "Confirmed"}
    Server.script = staticmethod(script)
    files, _ = challenge.generate(1, "easy")
    job = custom.Job("t", files, "rules+skeptic", "fake", url, policy(), [])
    job.thread.join(60)
    assert job.failed and job.failed[0] == "Skeptic" and "model runner crashed" in job.failed[1]["msg"]
    before = len(Server.seen)
    job.start()
    job.thread.join(60)
    assert job.done and all(r.get("verdict") for r in job.records)
    assert len(Server.seen) - before == len(job.records) - 2        # the two verdicts already given are not asked again


def test_injected_text_cannot_hide_a_rule_finding(fake):
    """A line that tells the Skeptic to dismiss it can make the verdict Doubtful, but the finding stays a finding."""
    srv, url = fake
    Server.script = staticmethod(lambda kind, req: {"reason": "The note says it was approved by phone.", "verdict": "Doubtful"})
    files, _ = challenge.generate(1, "easy")
    job = run(files, "rules+skeptic", url)
    plain = custom.Job("t", files, "rules", "", "", policy(), [])
    plain.thread.join(60)
    assert len(job.records) == len(plain.records) and all(r["verdict"] == "Doubtful" for r in job.records)


def test_an_incomplete_run_says_so_in_the_audit_trail(fake, monkeypatch):
    import streamlit as st

    from tallyhound import common as C
    srv, url = fake
    Server.script = staticmethod(lambda kind, req: {"findings": []})
    monkeypatch.setattr(agents, "AREA_SECONDS", 0)
    st.session_state.clear()
    C.init_state()
    files, _ = challenge.generate(1, "easy")
    st.session_state.uploads = {"m": files}
    job = run({"approvals.csv": files["approvals.csv"]}, "ollama", url, OTHERS)
    custom.finalize("m", job)
    detail = st.session_state.audit_log[-1]["detail"]
    st.session_state.clear()
    assert "incomplete: Approvals stopped" in detail and "not read" in detail


def test_a_slow_model_is_reported_as_slow_not_as_missing(fake):
    import time as _t
    srv, url = fake

    def script(kind, req):
        _t.sleep(2)
        return {"reason": "x", "verdict": "Confirmed"}
    Server.script = staticmethod(script)
    with pytest.raises(llm.LLMError) as e:
        llm.chat_json("s", "Proposed finding", {}, url=url, timeout=0.5)
    assert "did not answer within" in str(e.value) and "ollama serve" not in str(e.value)
