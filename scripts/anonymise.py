"""Anonymise a real export (or a saved test case) before it goes into tests/cases or anywhere shared.

    python scripts/anonymise.py FOLDER_OR_ZIP_OR_CASE.json --out anonymised/

Names of people and companies, tax IDs, bank account digits, email addresses and free-text notes are replaced
consistently (the same name always becomes the same fake name, so duplicates still look like duplicates).
Amounts, dates and the structure of every line are kept, so the checks behave exactly the same.
Check the output by eye before sharing it.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NAME_COLS = {"supplier", "vendor", "name", "employee", "requested_by", "approved_by", "payee"}
ID_COLS = {"tax_id", "bank_acct", "bank_last4", "receipt_ref"}
FIRST = ["Alex", "Blair", "Casey", "Drew", "Eden", "Finley", "Gray", "Harper", "Indy", "Jules", "Kai", "Lane"]
LAST = ["Ash", "Birch", "Cedar", "Elm", "Fir", "Hazel", "Juniper", "Larch", "Maple", "Oak", "Pine", "Rowan"]
SUFFIX = re.compile(r"\s*\b(ltd|limited|inc|llc|plc|co|corp|gmbh)\.?$", re.I)
# words that carry meaning for the checks and are kept even inside names (rent, insurance, remit to...)
KEEP = re.compile(r"\b(rent|property|power|light|water|utility|utilities|insurance|tax|legal|subscription|"
                  r"remit to \w+)\b", re.I)


def _h(s: str, n: int = 8) -> int:
    return int(hashlib.sha256(s.strip().lower().encode()).hexdigest()[:n], 16)


def fake_base(base: str) -> str:
    if re.fullmatch(r"[A-Za-z]\. ?[A-Za-z]+", base.strip()):
        h = _h(base)
        return f"{FIRST[h % 12][0]}. {LAST[(h // 12) % 12]}"
    h = _h(KEEP.sub("", base))
    keep = " ".join(m.group(0) for m in KEEP.finditer(base))
    return " ".join(x for x in (f"{LAST[h % 12]} {FIRST[(h // 12) % 12]} {h % 97:02d}", keep) if x)


def build_names(files: dict[str, list[str]]) -> dict[str, str]:
    """Every person and company name found in name columns, mapped to a stable fake name (longest first)."""
    names = set()
    for n, lines in files.items():
        if n.endswith(".csv") and lines:
            head = next(csv.reader([lines[0]]))
            for ln in lines[1:]:
                for h, v in zip(head, next(csv.reader([ln])) if ln.strip() else []):
                    if h in NAME_COLS and v.strip():
                        names.add(SUFFIX.sub("", v.strip()).split(" - ")[0].strip())
    for ln in files.get("contracts.txt", []) + files.get("invoices.txt", []):
        for m in re.finditer(r"\| ([^|\]]+?) \|", ln):
            names.add(SUFFIX.sub("", m.group(1).strip()))
    return {n: fake_base(n) for n in sorted(names, key=len, reverse=True) if len(n) > 2}


def replace_names(text: str, mapping: dict[str, str]) -> str:
    for real, fake in mapping.items():
        text = re.sub(re.escape(real), lambda m, fake=fake: fake.upper() if m.group(0).isupper() else fake, text, flags=re.I)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "someone@example.com", text)
    return text


def fake_id(s: str) -> str:
    """Replace digits based on the digits alone, so '****1234' and '1234', or '77-123' and '77123', stay equal."""
    real = "".join(re.findall(r"\d", s))
    if not real:
        return s
    fake = iter((str(_h(real, 15)) * 3)[:len(real)])
    return re.sub(r"\d", lambda m: next(fake), s)


def anonymise(files: dict[str, list[str]]) -> dict[str, list[str]]:
    mapping = build_names(files)
    out = {}
    for n, lines in files.items():
        if n.endswith(".csv") and lines:
            head = next(csv.reader([lines[0]]))
            new = [lines[0]]
            for ln in lines[1:]:
                vals = next(csv.reader([ln])) if ln.strip() else []
                vals = [fake_id(v) if h in ID_COLS else replace_names(v, mapping) for h, v in zip(head, vals)]
                buf = io.StringIO()
                csv.writer(buf, lineterminator="").writerow(vals)
                new.append(buf.getvalue())
            out[n] = new
        else:
            out[n] = [re.sub(r"(account[^\d]*)(\d{4,})", lambda m: m.group(1) + fake_id(m.group(2)),
                             replace_names(ln, mapping), flags=re.I) for ln in lines]
    return out


def files_of(path: Path) -> dict[str, list[str]]:
    from tallyhound import custom
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))["files"]
    if path.suffix == ".zip":
        return custom.parse_zip(path.read_bytes())[0]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in path.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(path).as_posix())
    return custom.parse_zip(buf.getvalue())[0]


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("source")
    a.add_argument("--out", default="anonymised")
    args = a.parse_args()
    src, out = Path(args.source), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = anonymise(files_of(src))
    if src.suffix == ".json":
        case = json.loads(src.read_text(encoding="utf-8"))
        case["files"] = files
        case["description"] = re.sub(r"\(.*\)$", "", case.get("description", "")).strip()
        (out / src.name).write_text(json.dumps(case, indent=1), encoding="utf-8")
    else:
        for n, v in files.items():
            (out / n).write_text("\n".join(v) + "\n", encoding="utf-8")
    print(f"Wrote anonymised files to {out}. Check them by eye before sharing.")


if __name__ == "__main__":
    main()
