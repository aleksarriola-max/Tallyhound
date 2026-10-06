"""Benchmark the engines on fresh challenge data and write docs/benchmark.md and docs/benchmark.csv.

    python scripts/benchmark.py                                   # built-in rules only (seconds)
    python scripts/benchmark.py --engines rules,rules+skeptic,ollama,ollama-tools --models qwen3.5:9b
    python scripts/benchmark.py --url http://localhost:1234/v1 --models some-model     # LM Studio, vLLM...

Each run generates a new fictional month per seed and difficulty, runs the engine and grades it against the
planted answer key. AI runs take minutes each; start small (--seeds 1-2) and grow.
"""
import argparse
import logging
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.getLogger("streamlit").setLevel(logging.ERROR)
import pandas as pd  # noqa: E402

from tallyhound import challenge, custom, llm, score  # noqa: E402

NAMES = {"rules": "Built-in rules", "rules+skeptic": "Rules + Skeptic", "ollama": "AI agents", "ollama-tools": "AI agents with tools"}


def seeds(text: str) -> list[int]:
    if "-" in text:
        a, b = text.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in text.split(",")]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--engines", default="rules")
    p.add_argument("--models", default=llm.DEFAULT_MODEL)
    p.add_argument("--url", default=llm.DEFAULT_URL)
    p.add_argument("--seeds", default="1-10")
    p.add_argument("--difficulty", default="easy,medium,hard")
    p.add_argument("--out", default=str(ROOT / "docs"))
    a = p.parse_args()
    pol = {f"{r.clause}|{r.area}": r.text for r in pd.read_csv(ROOT / "data" / "policy.csv", dtype=str).itertuples()}
    rows = []
    for diff in a.difficulty.split(","):
        for seed in seeds(a.seeds):
            files, key_rows = challenge.generate(seed, diff)
            key = score.key_from_csv(challenge.key_csv(key_rows))
            for engine in a.engines.split(","):
                for model in (a.models.split(",") if engine != "rules" else [""]):
                    t = time.time()
                    job = custom.Job("bench", files, engine, model, a.url, pol, [])
                    job.thread.join()
                    if job.failed:
                        print(f"{diff} {seed} {engine} {model}: FAILED {job.failed[1]['msg']}")
                        continue
                    prop = [dict(source_file=r["source_file"], line_number=r["line_number"],
                                 related_lines=r.get("related_lines", []), verdict=r.get("verdict", ""),
                                 reason=r.get("reason", "")) for r in job.records]
                    s = score.score(prop, key)
                    sk = s.get("skeptic") or {}
                    rows.append(dict(difficulty=diff, seed=seed, engine=engine, model=model, planted=s["planted"],
                                     proposed=s["proposed"], found=s["found"], false_alarms=s["false_alarms"],
                                     recall=round(s["recall"], 4), precision=round(s["precision"], 4),
                                     traps=s["traps"], traps_flagged=s["traps_flagged"],
                                     precision_confirmed=round(sk.get("precision_confirmed", 0), 4) if sk else "",
                                     real_doubted=sk.get("real_doubted", "") if sk else "",
                                     seconds=round(time.time() - t, 1)))
                    print(f"{diff} seed {seed} {NAMES.get(engine, engine)} {model}: found {s['found']}/{s['planted']}, "
                          f"{s['false_alarms']} false alarms")
    if not rows:
        sys.exit("Nothing ran.")
    df = pd.DataFrame(rows)
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    df.to_csv(out / "benchmark.csv", index=False)
    g = df.groupby(["engine", "model", "difficulty"], sort=False)
    md = ["# Benchmark", "", f"Generated {datetime.now():%Y-%m-%d %H:%M} on fresh challenge data "
          f"({len(seeds(a.seeds))} seeds per difficulty). Planted problems per month: easy 8, medium 14, hard ~28.", "",
          "| Engine | Model | Difficulty | Runs | Recall | Precision | False alarms per run | Traps flagged | Seconds per run |",
          "|---|---|---|---|---|---|---|---|---|"]
    for (engine, model, diff), part in g:
        md.append(f"| {NAMES.get(engine, engine)} | {model or '-'} | {diff} | {len(part)} | "
                  f"{100 * statistics.mean(part.recall):.0f}% | {100 * statistics.mean(part.precision):.0f}% | "
                  f"{statistics.mean(part.false_alarms):.1f} | {part.traps_flagged.sum()} of {part.traps.sum()} | "
                  f"{statistics.mean(part.seconds):.0f} |")
    md += ["", "Recall: share of planted problems found. Precision: share of findings that were real. Hard mode words "
           "three problems so the fixed rules cannot see them; the rules' hard-mode recall is capped by design.", ""]
    (out / "benchmark.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {out / 'benchmark.md'}")


if __name__ == "__main__":
    main()
