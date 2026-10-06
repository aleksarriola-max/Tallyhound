"""Write a challenge zip (fictional data with planted problems + answer_key.csv).

    python scripts/make_challenge.py --difficulty hard --seed 4
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _quiet  # noqa: E402,F401  (silences Streamlit's "No runtime" warnings)
from tallyhound import challenge  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
p.add_argument("--seed", type=int, default=1)
p.add_argument("--out", default=None)
a = p.parse_args()
files, key, pdfs = challenge.generate_full(a.seed, a.difficulty)
out = Path(a.out or f"challenge-{a.difficulty}-{a.seed}.zip")
out.write_bytes(challenge.to_zip(files, key, pdfs))
traps = sum(k.get("expect") == "trap" for k in key)
print(f"Wrote {out} with {len(key) - traps} planted problems and {traps} traps (legitimate look-alikes).")
