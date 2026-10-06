# Benchmark

Generated 2026-10-06 00:50 on fresh challenge data (20 seeds per difficulty). Planted problems per month: easy 8, medium 14, hard ~28.

| Engine | Model | Difficulty | Runs | Recall | Precision | False alarms per run | Seconds per run |
|---|---|---|---|---|---|---|---|
| Built-in rules | - | easy | 20 | 100% | 100% | 0.0 | 0 |
| Built-in rules | - | medium | 20 | 100% | 100% | 0.0 | 0 |
| Built-in rules | - | hard | 20 | 89% | 100% | 0.0 | 0 |

Recall: share of planted problems found. Precision: share of findings that were real. Hard mode words three problems so the fixed rules cannot see them; the rules' hard-mode recall is capped by design.

The rules score highly because the generator plants problems shaped like their checks. Real data is messier; treat these numbers as a regression test and as the baseline the AI engines must beat, not as a promise about your files.
