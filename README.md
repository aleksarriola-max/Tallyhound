# Ledgerwatch

A finance audit console built with Streamlit. Local AI agents *propose* findings about accounts-payable data;
a person approves or rejects every one. **All data is fictional** (Bramblecourt Instruments Ltd).

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Needs Python 3.10+ and Streamlit 1.40 or newer.

## What is in here

- `app.py` - page setup, sidebar navigation and the run ticker.
- `lw/` - the app code: `run_analysis.py` (4-step flow), `review.py` (Review and Download steps),
  `pages.py` (Overview, Findings, Payment gate, Recovery, Subscriptions, Live activity, Evidence viewer, Guardrails),
  `sim.py` (simulated agent run), `exports.py` (Excel and PDF), `common.py` (data, styling, state).
- `data/*.csv` - all sample data. Nothing is hard-coded in the app.
- `data/source/` - the fake source files that every finding quotes. The app checks each quote against these files.
- `scripts/make_data.py` - regenerates all the data (`python scripts/make_data.py`).

## How it behaves

- **Agents only propose.** Approve, reject, hold and release happen only in the UI (Review step, Payment gate).
- **Quote check.** A finding is shown only if its evidence line is found, word for word, in its source file at the stated line.
- **Simulated run.** The run uses a simple timer (about 20 seconds per queue item). The Expenses agent fails once on purpose
  so you can use Retry / "Rerun this agent".
- **Exports.** Step 4 builds an Excel workbook (openpyxl) and a PDF memo (reportlab) from your decisions.

## Fictional-data checks built into the data

26 findings (High 7, Medium 12, Low 7), payment run 2026-10-01 with 8 HOLD ($64,165.30) and 6 RELEASE,
recoverable $6,864.61, subscription savings $25,850 across 15 tools and 911 user logins.
