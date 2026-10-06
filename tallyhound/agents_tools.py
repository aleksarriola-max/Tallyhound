"""Tool-using agents (Ollama). Instead of reading a whole file, the agent asks questions of it with small tools -
filter rows, find duplicates, read given lines - and reports findings one by one. This scales to files far too big
to paste into a prompt, and the agent sees exact lines, which it must quote back. Every reported finding still goes
through the same quote check as everything else.
"""
from __future__ import annotations

import csv
import json

from . import agents, llm, rules

MAX_STEPS = 16
MAX_ROWS = 40


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


CSV_TOOLS = [
    _fn("describe", "Column names, row count and the first rows of the file.", {}, []),
    _fn("find_rows", "Rows where a column matches a test. ops: eq, ne, gt, lt (numbers), contains, empty, not_empty, weekend.",
        {"column": {"type": "string"}, "op": {"type": "string"}, "value": {"type": "string"}}, ["column", "op"]),
    _fn("duplicates", "Groups of rows that share the same values in the given columns (case-insensitive).",
        {"columns": {"type": "array", "items": {"type": "string"}}}, ["columns"]),
    _fn("get_lines", "The exact text of the given line numbers.", {"line_numbers": {"type": "array", "items": {"type": "integer"}}},
        ["line_numbers"]),
]
TEXT_TOOLS = [
    _fn("describe", "Line count and the first lines of the file.", {}, []),
    _fn("find_text", "Lines containing the given text (case-insensitive).", {"text": {"type": "string"}}, ["text"]),
    _fn("get_lines", "The exact text of the given line numbers.", {"line_numbers": {"type": "array", "items": {"type": "integer"}}},
        ["line_numbers"]),
]
REPORT = [
    _fn("report_finding", "Report one breach. evidence must be the exact text of line_number, copied from a tool result.",
        {"clause": {"type": "string"}, "severity": {"type": "string", "enum": agents.SEV}, "title": {"type": "string"},
         "amount": {"type": "number"}, "line_number": {"type": "integer"}, "evidence": {"type": "string"},
         "related_line_numbers": {"type": "array", "items": {"type": "integer"}},
         "innocent_explanation": {"type": "string"}, "proposed_fix": {"type": "string"}},
        ["clause", "severity", "title", "line_number", "evidence"]),
    _fn("done", "Call when every clause has been checked.", {}, []),
]


class FileTools:
    def __init__(self, name: str, lines: list[str]):
        self.name, self.lines = name, lines
        self.is_csv = name.endswith(".csv")
        self.head = next(csv.reader([lines[0]])) if self.is_csv and lines else []
        self.rows = rules.rows(lines) if self.is_csv else []

    def _show(self, nums: list[int]) -> str:
        nums = [n for n in nums if 1 <= n <= len(self.lines)]
        more = f"\n... {len(nums) - MAX_ROWS} more" if len(nums) > MAX_ROWS else ""
        return "\n".join(f"{n}\t{self.lines[n - 1]}" for n in nums[:MAX_ROWS]) + more or "(no lines)"

    def call(self, name: str, args: dict) -> str:
        if name == "describe":
            if self.is_csv:
                return f"Columns: {', '.join(self.head)}\nRows: {len(self.rows)}\n" + self._show(list(range(1, 6)))
            return f"Lines: {len(self.lines)}\n" + self._show(list(range(1, 9)))
        if name == "get_lines":
            return self._show([int(x) for x in args.get("line_numbers", []) if str(x).lstrip("-").isdigit()])
        if name == "find_text":
            t = str(args.get("text", "")).lower()
            return self._show([i for i, ln in enumerate(self.lines, start=1) if t and t in ln.lower()])
        if name == "find_rows":
            col, op, val = args.get("column", ""), args.get("op", "eq"), str(args.get("value", ""))
            if col not in self.head:
                return f"No column {col!r}. Columns: {', '.join(self.head)}"
            return self._show([ln for ln, r in self.rows if _test(r[col], op, val)])
        if name == "duplicates":
            cols = [c for c in args.get("columns", []) if c in self.head]
            if not cols:
                return f"Name columns from: {', '.join(self.head)}"
            groups: dict[tuple, list[int]] = {}
            for ln, r in self.rows:
                k = tuple(r[c].strip().lower() for c in cols)
                if all(k):
                    groups.setdefault(k, []).append(ln)
            dup = [g for g in groups.values() if len(g) > 1]
            return "\n\n".join(self._show(g) for g in dup[:15]) or "(no duplicates)"
        return f"Unknown tool {name}"


def _test(cell: str, op: str, val: str) -> bool:
    c = cell.strip()
    if op == "empty":
        return c == ""
    if op == "not_empty":
        return c != ""
    if op == "contains":
        return val.lower() in c.lower()
    if op == "weekend":
        d = rules._d(c)
        return bool(d and d.weekday() >= 5)
    if op in ("gt", "lt"):
        try:
            a, b = rules._f(c), float(val)
        except ValueError:
            return False
        return a > b if op == "gt" else a < b
    return (c.lower() == val.lower()) if op == "eq" else (c.lower() != val.lower())


def investigate(area: str, lines: list[str], policy: dict[str, str], model: str, url: str,
                log=lambda msg: None) -> list[dict]:
    """Run one agent with tools. Returns the raw reported findings (not yet quote-checked)."""
    name = rules.FILES[area]
    tools = FileTools(name, lines)
    spec = (CSV_TOOLS if tools.is_csv else TEXT_TOOLS) + REPORT
    system = (f"You are the {area} agent in a finance audit of the file {name}. Use the tools to look for breaches of "
              "each policy clause below. Check every clause. For each breach you find, call report_finding with the "
              "exact line text copied from a tool result. Do not guess lines you have not seen. When every clause has "
              f"been checked, call done.\n\nPolicy clauses:\n{agents.policy_for(area, policy)}")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "Start with describe."}]
    found: list[dict] = []
    for _ in range(MAX_STEPS):
        msg = llm.chat_tools(messages, spec, model=model, url=url)
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        calls = msg.get("tool_calls") or []
        if not calls:
            break
        finished = False
        for c in calls:
            fn = c.get("function", {})
            fname, args = fn.get("name", ""), fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if fname == "done":
                finished, result = True, "OK"
            elif fname == "report_finding":
                found.append(dict(args, area=area))
                result = "Recorded."
            else:
                result = tools.call(fname, args)
            log(f"{fname}({json.dumps(args)[:80]})")
            messages.append({"role": "tool", "content": result, "tool_name": fname})
        if finished:
            break
    return found
