# Changelog

All notable changes. Dates are release dates; the project follows [Semantic Versioning](https://semver.org/) from 1.0.

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
