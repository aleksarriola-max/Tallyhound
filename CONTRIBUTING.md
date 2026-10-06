# Contributing to Tallyhound

Thank you for helping. Tallyhound is small on purpose: plain Python, Streamlit, no database server, no build step.
This page explains how to set up, how a finding travels through the code, and how to add a rule - the most common
contribution. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems go through
[SECURITY.md](SECURITY.md), not public issues.

## Set up

```bash
git clone https://github.com/aleksarriola-max/Tallyhound.git
cd Tallyhound
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
streamlit run app.py                   # the app
ruff check .                           # lint (configured in pyproject.toml)
pytest                                 # about a minute; CI runs the same
```

Before opening a pull request, `ruff check .` and `pytest` must pass. If you change anything under `scripts/make_data.py`,
run it and commit the regenerated `data/` too: CI checks that running it reproduces `data/` exactly.

## The one rule

**Agents and rules propose; people decide.** No code path may approve, reject, hold or release anything on its own,
and every decision goes through `common.decide` / `common.undo` / `common.log_action`, which write the sealed audit
trail. A pull request that bypasses this will not be merged, however useful it is.

## How a finding travels through the code

1. **Upload** - `custom.parse_zip` reads the zip in memory: it maps file names to the expected files (`payments.csv`,
   ...), decodes them, applies size limits, and sends invoice PDFs to `invoices.read_pdfs` (a sandboxed worker,
   `pdfworker.py`). `custom.view` renames headers if the person matched columns (`columns.py`).
2. **Run** - pressing Run queues the dataset (`layout._start` -> `sim.start`). For uploads, `custom.tick_item` starts a
   `custom.Job` in a background thread. The job runs each area:
   - built-in rules: `rules.run_area(area, files, limits)` -> one of `rules.payments`, `approvals`, `vendors`,
     `contracts`, `expenses` (plus `cross_file` and `reconcile` for Payments, `invoices.check` for Invoices). Each
     returns `rules.Hit(area, clause, severity, amount, title, source_file, line_number, related)`;
   - AI engines: `agents.propose` or `agents_tools.investigate`, then `agents.verified` keeps only findings whose quote
     is the exact line at the stated number and whose clause exists;
   - the Skeptic (`agents.skeptic`) labels each finding Confirmed or Doubtful.
3. **Store** - `custom.finalize` numbers the findings (F-01, ...) and keeps them per dataset in session state.
4. **Quote check, again** - `common.check_findings` re-checks every quote against the files before anything is shown.
5. **Review** - `triage.cases` groups findings that share a line; `layout.findings_tab` / `case_row` / `case_details`
   show them; the buttons call `review._decide_all`, `review.reject_clicked`, `review._undo_all`.
6. **Export** - `exports.build_workbook` and `build_memo`.

The sample company works the same way, except its findings are precomputed in `data/findings.csv` (written by
`scripts/make_data.py`) and its run is simulated by `sim.py`.

## Adding a rule

Say you want to flag *payments to a supplier whose vendor record has no tax ID*.

1. **Policy clause** - add the clause to the policy list in `scripts/make_data.py` (it writes `data/policy.csv`), for
   example `["4.5", "Vendors", "No payment may be made to a supplier without a tax ID on file."]`, then run
   `python scripts/make_data.py`. Clause ids are `<area number>.<n>`; keep the wording short and testable.
2. **The check** - in `tallyhound/rules.py`, add it to the area function (`vendors()` here). Return a `Hit`: the line
   number must be the line of the file that shows the problem (its text becomes the evidence), and `related` lists
   other lines that support it. Use the helpers: `rows()`, `_f()` for amounts, `r.d(col)` for dates, `norm_name()`
   and `same_person()` for names, and the limits in `L` (add new ones to `LIMITS` and `LIMIT_LABELS`).
3. **Explain it** - add one line each to `rules.INNOCENT` (an honest innocent explanation), `rules.FIXES` (what to do)
   and `triage.CLEARS` (what would clear it).
4. **Plant it** - in `tallyhound/challenge.py`, add an issue kind to `ISSUES` and a branch in `_Gen.issue()` that plants
   one realistic example and records it with `self.plant(...)`. Note that hard mode plants every kind, so this changes
   the generated months; re-run `python scripts/benchmark.py`.
5. **Think about traps** - what legitimate data looks like this? (A new supplier not yet invoiced? A remit-to record?)
   If there is a common one, plant it as a trap with `self.trap(...)` so the quality gates prove the rule does not
   raise it.
6. **Test** - `pytest`. `tests/test_quality.py` fails if any trap is flagged or precision or recall drops; add a small
   focused test of your rule next to the others in `tests/test_audit3.py` (the "real-world shapes" tests are a good
   model). Rules must stay fast: no regex that can backtrack badly on a long line (one `\s*` per gap, bounded repeats).

## Style

- Plain words in the interface: say what a person should do next. Avoid jargon or explain it.
- Everything shown on the page that comes from a file or a model goes through `common.esc` (or `html.escape` inside
  HTML). Spreadsheet output goes through `exports.defuse_formulas` / `headless.safe_cell`.
- Keep functions small and give public ones a one-line docstring. Line length is 125.
- Fictional data only: invented company, people and product names.

## Reporting bugs and ideas

Open an issue with what you did, what you expected and what happened. For a false alarm or a missed problem, the most
useful thing is a small anonymised example: reject the finding, press *Save as test case*, run
`python scripts/anonymise.py <file> --out anonymised/`, check the output by eye, and attach it.
