"""Anonymise a real export (or a saved test case) before it goes into tests/cases or anywhere shared.

    python scripts/anonymise.py FOLDER_OR_ZIP_OR_CASE.json --out anonymised/

Replaced, consistently within one run (the same real value always becomes the same fake, so duplicates still look
like duplicates): names of people and companies, tax IDs, bank account digits and IBANs, receipt refs, email
addresses, phone, SSN and NI numbers, postcodes and street addresses, and person-like names in free text (notes,
bank descriptions, references, the reviewer's reason). Each run uses a fresh random key that is never saved, so a
fake cannot be turned back into the real value. Amounts, dates and the structure of every line are kept, so the
checks behave the same. It cannot know every name a free-text note might mention: check the output by eye.
"""
import argparse
import csv
import hashlib
import hmac
import io
import json
import re
import secrets
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


KEY = secrets.token_bytes(32)          # fresh for every run and never written anywhere
TEXT_COLS = {"notes", "description", "reference", "memo", "comment", "comments", "narrative", "details", "reason"}
SIGNAL = {w.lower() for w in """bank charges fees transfer savings payroll salary wages card settlement amex visa mastercard
loan interest tax team dinner client lunch business purpose recorded airport taxi hotel conference trip travel batch
bacs bulk example street personal gym membership society institute association professional certificate chamber own account
invoice payment credit note refund reversal void reissue site visit meeting training remit""".split()}
# space-separated digit groups, or + and a country code: "020 7946 0958", "+44-20-7946-0958" - never a date
PHONE = re.compile(r"(?<![\w.,+-])(?:\+\d{1,3}[ -]?\(?\d{1,5}\)?(?:[ -]\d{2,5}){1,4}|\(?\d{2,5}\)?(?: \d{2,5}){2,4})(?![\w.,-])")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
NINO = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?\b")
ACCOUNT = re.compile(r"((?:acc(?:oun)?t|acct|acc|a/c)\b\.?\s*(?:no\.?|number|#)?\s*[:#]?\s*[*xX\s-]*)(\d[\d -]{2,}\d)", re.I)
POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b|\b\d{5}(?:-\d{4})\b")
STREET = re.compile(r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Way|Close|"
                    r"Place|Court|Boulevard|Blvd)\b\.?")
PERSON = re.compile(r"\b(?:[A-Z]\.\s?[A-Z][a-z]{2,}|[A-Z][a-z]+\s[A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})?)\b")
_ids: dict[str, str] = {}


def _h(s: str, n: int = 8) -> int:
    return int(hmac.new(KEY, s.strip().lower().encode(), hashlib.sha256).hexdigest()[:n], 16)


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
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "someone@example.com", text)    # before names, which are inside emails
    for real, fake in mapping.items():
        text = re.sub(re.escape(real), lambda m, fake=fake: fake.upper() if m.group(0).isupper() else fake, text, flags=re.I)
    return text


def scrub(text: str, free: bool = False) -> str:
    """Identifiers anywhere in text; with free=True also addresses and person-like names (for notes and reasons)."""
    text = IBAN.sub(lambda m: fake_id(m.group(0)), text)
    text = ACCOUNT.sub(lambda m: m.group(1) + fake_id(m.group(2)), text)
    text = SSN.sub(lambda m: fake_id(m.group(0)), text)
    text = NINO.sub("AB 12 34 56 C", text)
    if free:
        text = PHONE.sub(lambda m: fake_id(m.group(0)), text)
        text = STREET.sub("1 Example Street", text)
        text = POSTCODE.sub("AB1 2CD", text)

        def person(m):
            words = re.findall(r"[A-Za-z]+", m.group(0))
            if any(w.lower() in SIGNAL for w in words) or m.group(0).isupper():
                return m.group(0)
            h = _h(m.group(0))
            return f"{FIRST[h % 12]} {LAST[(h // 12) % 12]}"
        text = PERSON.sub(person, text)
    return text


def fake_id(s: str) -> str:
    """Replace digits based on the digits alone, so '****1234' and '1234', or '77-123' and '77123', stay equal. Two
    different real values never get the same fake (that could turn "bank differs" into "bank matches")."""
    real = "".join(re.findall(r"\d", s))
    if not real:
        return s
    if real not in _ids:
        taken = set(_ids.values())
        for salt in range(1000):
            cand = (str(_h(f"{real}|{salt}", 15)) * 3)[:len(real)]
            if cand not in taken:
                break
        _ids[real] = cand
    fake = iter(_ids[real])
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
                vals = [fake_id(v) if h in ID_COLS else scrub(replace_names(v, mapping), free=h.strip().lower() in TEXT_COLS)
                        for h, v in zip(head, vals)]
                buf = io.StringIO()
                csv.writer(buf, lineterminator="").writerow(vals)
                new.append(buf.getvalue())
            out[n] = new
        else:
            out[n] = [scrub(replace_names(ln, mapping)) for ln in lines]
    return out


def files_of(path: Path) -> dict[str, list[str]]:
    from tallyhound import custom
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))["files"]
    if path.is_file():
        if not zipfile.is_zipfile(path):
            sys.exit(f"{path} is not a folder, a zip or a saved case (.json).")
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
        mapping = build_names(files_of(src))
        desc = re.sub(r"\(.*\)$", "", case.get("description", "")).strip()
        case["description"] = scrub(replace_names(desc, mapping), free=True)
        (out / src.name).write_text(json.dumps(case, indent=1), encoding="utf-8")
    else:
        for n, v in files.items():
            (out / n).write_text("\n".join(v) + "\n", encoding="utf-8")
    print(f"Wrote anonymised files to {out}. Check them by eye before sharing.")


if __name__ == "__main__":
    main()
