# Changelog

All notable changes. Dates are release dates; the project follows [Semantic Versioning](https://semver.org/) from 1.0.

## 0.7.0 - 2026-10-08

**Real exports**
- Pick files directly (CSV, Excel `.xlsx`, tab-separated, PDF) or a zip; several at once become one dataset.
- Excel workbooks are read from their fullest sheet. Report-style exports from QuickBooks, Xero and similar are
  tidied (title lines, `Total for ...` lines and footers dropped; vendor group headings written onto their rows), and
  every change is listed in the upload notes.
- Files with unfamiliar names are recognised from their columns, only when columns that belong to one kind of file
  are there (a bank statement or payment run also needs its name or a balance column), and never in place of a file
  named the Tallyhound way. camelCase headers such as Xero's `*ContactName` match; a note says when every column has
  a suggested match. `docs/example-exports/` has a fictional month to try.
- Excel: hidden sheets are skipped, a stale stored sheet size never cuts columns, formulas without a saved value are
  reported, and a cell cap keeps big workbooks in bounds. A quoted value that runs over two lines stays in its row.
- Next month's exports with this month's file names become a new dataset ("name (2)") instead of being taken for
  files already read. A dataset is read once per pick, not on every click.
- Tested against an export pack (`tests/exports/pack`, 15 fictional files in the layouts of QuickBooks Online and
  Desktop, Xero, Sage 50, NetSuite, Expensify and bank downloads): each is read as the right file or refused with the
  reason. That added semicolon-separated files, month-name dates (`02 Sep 2026`, `Sep 2, 2026`), section headings,
  headings in a named first column, Xero bill exports folded from line items into one row per bill, vendor lists with
  fewer columns when their name says so, and a reason on every refusal ("comes closest to payments.csv, but has no
  invoice no").
- Hardened after an audit: long lines are cut before any tidying (no slow patterns on hostile input), workbooks are
  bounded by cells and text read rather than refused outright, upload notes are escaped (no links or formatting from
  file names), and `defusedxml` is a dependency so workbooks cannot carry XML bombs.

**New checks**, each with its own policy clause, planted in generated months with a trap that must stay quiet. They
are patterns, not breaches, so the rules engine marks them doubted and "Approve all confirmed" leaves them alone.
- 5.6: the same invoice paid twice under a re-keyed number with the same invoice date (a suffix added, two digits
  swapped far apart in the series, a look-alike letter) - never the next numbers in a series or instalments.
- 4.5: a new vendor paid more than the director limit within 30 days of set-up (the window is a setting); a set-up
  date shared by most vendors is read as the date the list was imported and ignored.
- 1.6: one requester's approvals bunched just under the director limit, to different suppliers, with no director
  approval.
- 6.6: three or more receipt-free claims by one person just under the receipt limit within 30 days (not mileage or
  allowances).

**Reports > Patterns**: a first-digit (Benford's law) test with a verdict only on enough data, the share of round
amounts, and amounts bunched just under each limit. Not findings; nothing here reaches Review or the downloads.

**Found by fuzz testing** (thousands of random hostile files): a damaged zip or Excel file is skipped with a note
instead of crashing the upload; a plain file whose first row is all text keeps its column names; control characters
in a cell no longer break the workbook download; placeholder dates such as 0001-01-01 or 9999-12-31 are read as no
date instead of crashing the bank check.

**Found by mutation testing** (changing a limit or comparison in the code and checking a test fails): the edges of
every limit and window - the PO and director limits, the $1 tolerance, the split-order, new-vendor, receipt and
near-duplicate windows, head counts, the import-date and header rules - are now pinned by tests
(`tests/test_boundaries.py`), so a rule cannot drift by a cent or a day unnoticed.

**Found by security testing** (`tests/test_security.py`):
- Roles and "whoever ran it cannot approve it" were enforced by greying out buttons; on Streamlit 1.55-1.60 a
  forged click on a disabled button still acted. Every decision, undo, hold, run, upload and policy action now checks
  the role itself.
- Sign-in tries sent at the same moment could get past the 5-try lockout. The check and the count are now one step.
- A vendor name written as markdown could show a live image or link on Review, and escaped text still showed
  clickable addresses. Both now show as plain text.
- The payment run's "Export release file" CSV kept spreadsheet formulas (`=HYPERLINK(...)`); they are now text.
  Numbers such as `-500.00` stay numbers.
- Slack alerts could carry `<!channel>` or a disguised link from an uploaded file; they are now escaped.
- A zip with hundreds of thousands of empty entries slowed the page; zips are cut at 1,000 entries, with a note.

**Found by scale and locale testing** (`tests/test_scale_locale.py`):
- The split-order check, its name matching, the payments-against-vendors check and the headless report were
  quadratic: 20,000 small bills from one vendor took 158 s, now under 1 s. 200,000 payments run in about 11 s.
- The headless runner checked nothing on a folder over about 50 MB; it now compresses the folder first.
- `12,5` was read as 125 (one-decimal commas, as Excel writes them). Narrow and thin spaces, `1’234.50` and more
  currency marks (¥, zł, ₹, SEK, US$...) were read as 0. `02/09/2026 14:30` in a day-first file was read as
  9 February.
- Names in non-Latin scripts all looked the same, so two Japanese or Arabic suppliers' INV-1 counted as one invoice
  paid twice, and self-approval in those scripts was missed. Names now compare with accents folded, so Muller and
  Müller match.
- UTF-16 files without a byte-order mark and Japanese (Shift-JIS) CSVs are read instead of garbled.

**Found by metamorphic testing** (changing the input in ways that must not change the result;
`tests/test_metamorphic.py`):
- Payments listed newest first (as many exports are) turned two instalments into a false "paid twice" and could
  report an overpayment twice. Invoice amounts now come from the earliest payment.
- Bank matching could pair a bank line with the wrong payment and report "money left with no record". Lines are
  now matched in date order, so every line that can be paired is.
- A second split order, or a second run of claims just under the receipt limit, by the same person later in the
  file was not reported.
- Optional columns spelt with other capitals or spaces (`Type`, ` Bank_Last4 `) were read as blank, which caused
  false "no PO" findings and let the payment gate skip the bank-details check.
- The source viewer now sets its text colour, and keyboard focus in the page has a clear outline. Axe (WCAG 2.1 AA)
  finds no violations of the app's own on any page, at desktop or phone width.

**Checked and fine**: work saved by 0.6.1 opens in 0.7.0 with decisions, uploads and a sealed trail intact; three
reviewers clicking the same findings in the same second lose nothing and keep one valid trail (the first decision on
a finding wins); the AI engines treat a misbehaving model server (wrong shapes, empty or huge replies, invented
quotes and line numbers, unknown tools) as a failed agent with a plain message, and never keep an invented quote.

**Known limit**: on a 2-CPU test machine, 6 visitors using the public demo at once worked smoothly (pages 1-4 s,
the demo run about 70 s); with 10 at once, the simulated demo run often froze on screen until the page was reloaded,
although the server had moved on. Uploaded-data runs were not affected. A busy public copy needs more CPU.

**Bank reconciliation**: exact amounts are matched across the whole statement before amounts a few cents off, so a
batch transfer within cents of one supplier's payment no longer takes it (the one trap the rules still flagged, in 1
of 180 generated months). Rules now flag 0 of 4,680 traps.

**Generator**: a planted unrecorded payment is always early enough for the statement to show it missing.

**Accessibility**: the Patterns chart's bars and axis text meet WCAG AA contrast.

**Found by clicking through the whole app in a browser** (default, sign-in and public mode, and a phone-sized screen):
- A run started after another tab's run had finished could be thrown away; in a team workspace nobody could start a
  second run until a reset. The newest run now always goes ahead.
- Retry on Home, and "Review the results", now work straight away (they waited for the page to be redrawn).
- Home says when a run has finished until its results have been opened.
- Adding and removing files one at a time no longer leaves a dataset for every intermediate pick.
- With uploaded data in review, the sample's payment run is view-only and Home shows no holds for an upload without
  a payment run - holds can no longer be cleared against the wrong data.
- Every run is a sealed audit-trail entry naming the person who started it, with the run's real time; Trends shows
  who ran each run.
- Latest events are newest first; the challenge message counts problems and traps apart; signing out returns to
  Home; Reset demo keeps the admin signed in.
- The folder watcher matches columns the way the app suggests (and says so in its report) instead of skipping
  files with other column names, and reports any file it could not check.

**Code health**: type checking with mypy (no errors, run inside the project's environment so Streamlit's and
Altair's own types are checked; pandas is left untyped), a coverage report with an 85% floor in CI (about 90%), upload
reading moved into `uploads.py`, and `scripts/screenshots.py` to re-make the screenshots and the demo GIF.

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
