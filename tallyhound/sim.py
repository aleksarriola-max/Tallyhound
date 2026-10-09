"""Simulated agent run. One tick = one second. Agents only propose; nothing here approves anything."""
from __future__ import annotations

import json
import time
from datetime import datetime

import streamlit as st

from . import common as C
from . import llm

BEAT_SECONDS = 6          # a run whose driving tab has not ticked for this long is taken over by another tab

TICKS_PER_AGENT = 3
FAIL_AGENT = "Expenses"   # fails once in the demo, so a visitor can try Retry


def agent_names() -> list[str]:
    return list(C.read_csv("agents.csv")["agent"])


def new_item(label: str, workflow: str, option: str, skip: frozenset = frozenset()) -> dict:
    from . import custom
    row = C.opt_row(workflow, option)
    S = st.session_state
    extra = {}
    if workflow == "audit" and custom.uploaded(option):
        extra = dict(custom=option, engine={"Ollama agents": "ollama", "Built-in rules + Ollama Skeptic": "rules+skeptic",
                            "Ollama agents with tools": "ollama-tools"}.get(str(S.get("adv_engine")), "rules"),
                     model=S.get("adv_model") or llm.DEFAULT_MODEL, url=S.get("adv_url") or llm.DEFAULT_URL)
    import secrets
    from . import auth
    return dict(**extra, label=label, workflow=workflow, option=option, est=int(row["est_min"]),
                run_id=secrets.token_hex(6), started_by=auth.current_user() or "",
                result=int(row["result_findings"]), status="Queued", started="", ticks=0,
                agents=[dict(name=n, status="Skipped" if n in skip else "Waiting", pct=0, secs=0) for n in agent_names()])


def build_queue(selection: dict, skip: frozenset = frozenset()) -> list[dict]:
    """selection: workflow id -> list of options."""
    q = []
    for opt in selection.get("audit", []):
        q.append(new_item(f"{opt} audit", "audit", opt, skip))
    for wf, label in (("gate", None), ("recovery", "Supplier recovery"), ("subs", "Subscriptions")):
        for opt in selection.get(wf, []):
            q.append(new_item(label or opt, wf, opt, skip))
    return q


def tab_id() -> str:
    """This browser tab (one Streamlit session). Only one tab drives a run; others watch its saved progress."""
    import secrets
    S = st.session_state
    if "_tab" not in S:
        S["_tab"] = secrets.token_hex(8)
    return S["_tab"]


def start(queue: list[dict]) -> None:
    S = st.session_state
    S.sim = dict(queue=queue, running=True, log=[], tab=tab_id(), beat=time.time(), created=time.time())
    log("Orchestrator", f"Queue created with {len(queue)} item(s)")


def log(agent: str, msg: str) -> None:
    sim = st.session_state.sim
    entry = dict(time=datetime.now().strftime("%H:%M:%S"), agent=agent, message=msg)
    if sim is not None:
        sim["log"].append(entry)


def current(sim: dict | None):
    """The item to show: running/failed first, else the last finished, else None."""
    if not sim:
        return None
    for it in sim["queue"]:
        if it["status"] in ("Running", "Failed"):
            return it
    for it in reversed(sim["queue"]):
        if it["status"] == "Done":
            return it
    return sim["queue"][0] if sim["queue"] else None


def agent_summary(item: dict, agent: str) -> str:
    f = C.findings()
    if item["workflow"] == "audit" and item["option"] == "2026-09" and agent in set(f.area):
        return f"{agent} agent finished: {int((f.area == agent).sum())} finding(s)"
    return f"{agent} agent finished"


def _follow() -> bool | None:
    """Only one tab drives a run; the others show its saved progress. Returns None when this tab should drive (it
    started the run, or the driving tab went quiet - closed, reloaded or asleep), else whether to rerun the page.
    The driver checks too: if another tab took over while it slept, it steps aside instead of driving twice."""
    from . import store
    S = st.session_state
    mine = tab_id()
    if S.sim.get("tab") is None:
        return None
    if S.sim.get("tab") != mine and time.time() - S.get("_follow_at", 0) < 1:
        return False                            # a watching tab looks at most once a second
    S["_follow_at"] = time.time()
    latest = store.read_sim(S.get("sid")) or S.sim
    owner = latest.get("tab")
    if owner in (None, mine):
        return None
    if S.sim.get("tab") == mine and float(S.sim.get("created") or 0) > float(latest.get("created") or 0):
        return None                             # this tab started a newer run than the one saved: it drives
    if latest.get("running") and time.time() - float(latest.get("beat") or 0) > BEAT_SECONDS:
        latest["tab"] = mine                    # the driver went quiet: take over
        S.sim = latest
        return None
    finished = S.sim.get("running") and not latest.get("running")
    text = json.dumps(latest, sort_keys=True, default=str)
    changed = text != json.dumps(S.sim, sort_keys=True, default=str)
    S.sim = latest
    S["_sim_saved"] = text                      # not this tab's to save
    if finished:
        S["_store_ready"] = False               # the driver saved the results: load them
    return bool(changed)                        # redraw only when the driver's progress actually moved


def _heartbeat() -> None:
    """The driver saves its progress (and so its heartbeat) every 2 s, also between full page reruns."""
    from . import store
    S = st.session_state
    S.sim["beat"] = time.time()
    if time.time() - S.get("_beat_saved", 0) >= 2 and S.get("sid"):
        text = json.dumps(S.sim, sort_keys=True, default=str)
        try:
            store._put(S["sid"] + ":sim", text)
            S["_sim_saved"], S["_beat_saved"] = text, time.time()
        except Exception:  # noqa: BLE001 - progress display must never break the run
            pass


def tick() -> bool:
    """Advance the simulation by one step. Returns True when a full page rerun is needed."""
    S = st.session_state
    if S.sim and S.sim.get("running"):
        follow = _follow()
        if follow is not None:
            return follow
        _heartbeat()
    sim = S.sim
    if not sim or not sim["running"]:
        return False
    now = time.time()
    if now - S.last_tick < 0.8:
        return False
    S.last_tick = now

    item = next((i for i in sim["queue"] if i["status"] == "Running"), None)
    if item is not None and item.get("custom"):
        from . import custom
        return custom.tick_item(sim, item)
    if item is None:
        item = next((i for i in sim["queue"] if i["status"] == "Queued"), None)
        if item is None:
            sim["running"] = False
            return True
        item["status"] = "Running"
        item["started"] = datetime.now().strftime("%H:%M")
        log("Orchestrator", f"Started {item['label']}")
        return True

    a = next((x for x in item["agents"] if x["status"] not in ("Done", "Skipped")), None)
    if a is None:
        return _finish_item(sim, item)
    a["status"] = "Running"
    a["secs"] += 1
    item["ticks"] += 1
    a["pct"] = min(100, a["pct"] + 100 // TICKS_PER_AGENT + 1)
    if a["name"] == FAIL_AGENT and S.fail_pending and a["pct"] >= 60:
        a["status"], item["status"], sim["running"] = "Failed", "Failed", False
        log(a["name"], "Failed: could not read expenses.csv (simulated fault). Use Retry.")
        return True
    if a["pct"] >= 100:
        a["status"], a["pct"] = "Done", 100
        log(a["name"], agent_summary(item, a["name"]))
    return False


def _finish_item(sim: dict, item: dict) -> bool:
    S = st.session_state
    item["status"] = "Done"
    item["findings"] = item["result"]
    secs = item["elapsed"] if "elapsed" in item else int(
        item["ticks"] * item["est"] * 60 / (len(item["agents"]) * TICKS_PER_AGENT))
    S.recent_extra.insert(0, dict(Run=item["label"], Data=item["option"], Started=item["started"],
                                  Duration=f"{secs // 60}m {secs % 60:02d}s", Findings=item["result"],
                                  Status="Done" if item["result"] else "Done (clean)"))
    log("Skeptic", f"{item['label']} finished with {item['result']} finding(s)")
    nxt = any(i["status"] == "Queued" for i in sim["queue"])
    if not nxt:
        sim["running"] = False
    return True


def retry(item: dict | None = None) -> None:
    """Rerun the failed agent and resume the queue."""
    S = st.session_state
    sim = S.sim
    if not sim:
        return
    for it in sim["queue"]:
        if it["status"] == "Failed" and it.get("custom"):
            from . import custom
            custom.retry_job(it)
            it["status"] = "Running"
            for a in it["agents"]:
                if a["status"] == "Failed":
                    a.update(status="Waiting", pct=0)
            sim["running"] = True
            log("Orchestrator", "Rerunning the failed agent")
            return
    S.fail_pending = False
    for it in sim["queue"]:
        if it["status"] == "Failed":
            for a in it["agents"]:
                if a["status"] == "Failed":
                    a.update(status="Waiting", pct=0, secs=0)
            it["status"] = "Running"
    sim["running"] = True
    log("Orchestrator", "Rerunning the failed agent")



