"""Write a challenge zip (fictional data with planted problems + answer_key.csv).

    python scripts/make_challenge.py --difficulty hard --seed 4
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tallyhound import challenge  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
p.add_argument("--seed", type=int, default=1)
p.add_argument("--out", default=None)
a = p.parse_args()
files, key, pdfs = challenge.generate_full(a.seed, a.difficulty)
out = Path(a.out or f"challenge-{a.difficulty}-{a.seed}.zip")
out.write_bytes(challenge.to_zip(files, key, pdfs))
print(f"Wrote {out} with {len(key)} planted problems.")
