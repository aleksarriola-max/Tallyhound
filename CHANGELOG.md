# Changelog

All notable changes. Dates are release dates; the project follows [Semantic Versioning](https://semver.org/) from 1.0.

## 0.6.1 - 2026-10-06

**Team workspace**
- Saves are refused if the work changed or was deleted (a reset) since the tab loaded it; the refused tab reloads
  and re-applies its own decisions on top, and every signed-in tab is told when a teammate saved.
- Uploads from two people are merged, never overwritten. Run progress has its own record and only one tab drives a
  run (another takes over if it goes quiet); a run is finalised once, and records who started it, so segregation of
  duties holds whoever's tab finishes it.
- Two PDFs with the same name in one zip are both read.

**Scale**: pages stay around a second with 13,000 findings (indexed quote check, cached findings and data check,
paged review list, downloads prepared on request, memo tables capped at 200 rows).

**Numbers**: a case counts once in the approved value (its largest finding); shadow-mode findings are not pending;
suppressions never hide a decided finding (and now apply to the sample too); the memo includes an uploaded payment
run; negative amounts read -$500.00 and never lower a total; payment-run lines without a usable amount are held;
findings about risk rather than money are never "minor"; a meal over the limit with no head count is flagged.

**Accessibility**: badge, chip and table colours meet WCAG AA; visible focus and current-page marking in the
sidebar; quoted lines are marked by symbol as well as colour and the source view is keyboard-scrollable; text at
least 12.8 px.

**Platforms**: `.gitattributes` keeps the sample data byte-identical on every OS; Windows keeps saved work out of
OneDrive; private files are written binary-safe with retries; explicit UTF-8 everywhere; tighter limits on public
copies; dependency upper bounds in `requirements.txt`.

## 0.6.0 - 2026-10-06 - first public release

**Open-source readiness**
- CONTRIBUTING (with how to add a rule), SECURITY (threat model, hardening, private reporting), Code of Conduct,
  issue and pull-request templates, a glossary and a status notice in the README.
- Dead code from the old page layout removed; stale wording fixed; demo product names are now invented.

**Security**
- Invoice PDFs are parsed in a separate process with a time and memory limit; page-tree "bombs" are refused.
- Regular expressions on uploaded text rewritten so they cannot backtrack catastrophically; lines are cut at 4,000
  characters; a test sweeps every pattern with hostile input.
- With sign-in on, work is one server-side team workspace loaded only after sign-in (no session fixation through
  links); signing out clears the tab; saves never overwrite a teammate's newer save.
- A users file that cannot be read keeps sign-in on and lets nobody in. Users, the trail key and the database are
  written privately (0600) and atomically.
- Dataset names and data-check messages are escaped; the folder-check CSV keeps formula-like cells as text; finished
  or abandoned analyses free their memory, and at most four run at once.

**Fixes**
- The workbook and memo say "partly reviewed" while findings are pending.
- A reload during a run keeps the run.
- Reject and Clear hold no longer need Enter before they can be pressed; an empty reason gets a clear message.
- Bulk approval asks for confirmation; Approve explains what it means.
- Counts agree between Home, Review and Reports; the details view shows column names, the policy clause and related
  lines.
- `scripts/benchmark.py` writes to `benchmark-results/` unless told `--out docs`; `scripts/watch.py` exits 2 for a
  missing folder; `make_data.py --help` shows help; scripts no longer print Streamlit warnings.

## 0.5.0 - 2026-10-06

Built in a series of stages and three error audits:

- Sample company with a guided tour, saved work across reloads, and a review queue of cases.
- Uploads (zip of CSVs and invoice PDFs) with column matching and a data check; four engines (built-in rules,
  rules + Ollama Skeptic, Ollama agents, tool-using agents); the quote check and the Skeptic self-check.
- Payment gate, bank reconciliation, invoice PDF checks, cross-file checks.
- Scorecard, challenge generator with planted problems and traps, benchmark, CI quality gates against false alarms.
- Sign-in with roles and segregation of duties; sealed, hash-chained audit trail; Excel and PDF exports.
- Folder check with Slack and email alerts; Docker setup.
- Hardening from three audits: escaping, zip limits, model-address guard, safe model-output parsing, rules tested on
  real-world data shapes, restored-state checks, anonymiser privacy.
