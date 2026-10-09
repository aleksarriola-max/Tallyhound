**What this changes and why**

**Checks**
- [ ] `ruff check .`, `mypy` and `pytest` pass
- [ ] Nothing approves, rejects, holds or releases on its own (decisions go through `common.decide` / `log_action`)
- [ ] Text from files or models shown on the page goes through `common.esc`
- [ ] New rule: clause in `scripts/make_data.py`, `INNOCENT` / `FIXES` / `CLEARS`, planted in `challenge.py`, traps considered
- [ ] Changed `scripts/make_data.py`: regenerated `data/` committed
- [ ] Only fictional data
