"""Reading what a person uploads: a zip, or loose CSV, Excel, text and PDF files, into exact lines per audit file.

Everything is read in memory; nothing is written to disk and no name from an upload is ever used as a path.
"""
from __future__ import annotations

import io
import re
import zipfile

from . import importer, llm, rules

MAX_ZIP_MB = 50
MAX_LINES = 300_000
STEMS = {"payments": "payments.csv", "approvals": "approvals.csv", "vendors": "vendors.csv",
         "contracts": "contracts.txt", "expenses": "expenses.csv", "payment_run": "payment_run.csv",
         "bank_statement": "bank_statement.csv", "bank": "bank_statement.csv",
         "invoices": "invoices.txt"}
MAX_LINE_CHARS = 4000
# a public copy (the hosted demo, ~1 GB of memory shared by every visitor) gets tighter limits than your own server
MAX_UNZIPPED_MB = 20 if llm.public() else 100          # everything in one zip, once unpacked (stops "zip bombs")
MAX_CELLS = 500_000 if llm.public() else 3_000_000     # cells read from one Excel workbook


def decode(raw: bytes) -> str:
    """Text of an uploaded file. UTF-8 (with or without BOM), UTF-16 from Excel's "Unicode text", else Windows-1252,
    which is what Excel on Windows writes for 'Save as CSV'. Never fails: unknown bytes become U+FFFD."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def parse_zip(data: bytes) -> tuple[dict[str, list[str]], list[str]]:
    """Returns (files, notes). Reads in memory only; nothing is written to disk and no path is ever used."""
    notes: list[str] = []
    if len(data) > MAX_ZIP_MB * 1024 * 1024:
        return {}, [f"That zip is larger than {MAX_ZIP_MB} MB."]
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
        return {}, ["That file is not a valid zip."]
    files: dict[str, list[str]] = {}
    origin: dict[str, str] = {}
    total = 0
    unpacked = 0
    pdfs: list[tuple[str, list[str]]] = []
    pdf_raw: list[tuple[str, bytes]] = []

    def read(info) -> bytes | None:
        nonlocal unpacked
        if info.flag_bits & 0x1:
            notes.append(f"Skipped {base}: it is password-protected. Zip it again without a password.")
            return None
        if info.file_size > MAX_ZIP_MB * 1024 * 1024:
            notes.append(f"Skipped {base}: too large.")
            return None
        if unpacked + info.file_size > MAX_UNZIPPED_MB * 1024 * 1024:
            notes.append(f"Stopped at {MAX_UNZIPPED_MB} MB of unpacked data; {base} and later files were skipped.")
            return None
        try:
            raw = zf.read(info)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, ValueError) as e:
            notes.append(f"Skipped {base}: it could not be unpacked ({type(e).__name__}).")
            return None
        unpacked += len(raw)
        return raw

    def kind(info) -> str | None:
        stem = info.filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        return next((v for k, v in STEMS.items() if stem == k or stem.startswith(k + "_") or stem.endswith("_" + k)), None)
    # files named the Tallyhound way first: a file recognised from its columns never takes a properly named one's place
    infos = sorted(infos, key=lambda i: kind(i) is None)
    for info in infos:
        base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
        if info.is_dir() or base.startswith(".") or "__MACOSX" in info.filename:
            continue
        stem = base.rsplit(".", 1)[0].lower()
        if base.lower().endswith(".pdf"):
            from . import invoices
            if len(pdf_raw) >= invoices.MAX_PDFS:
                notes.append(f"Skipped {base}: more than {invoices.MAX_PDFS} PDFs in one upload.")
                continue
            raw = read(info)
            if raw is not None:
                name, k = base, 2
                while any(n == name for n, _ in pdf_raw):     # north/invoice.pdf and south/invoice.pdf: keep both
                    name, k = f"{base.rsplit('.', 1)[0]} ({k}).pdf", k + 1
                pdf_raw.append((name, raw))
            continue
        if stem == "answer_key" and base.lower().endswith(".csv"):
            raw = read(info)
            if raw is not None:
                files["answer_key.csv"] = decode(raw).splitlines()
                notes.append("Found answer_key.csv: the Scorecard page will grade runs on this data.")
            continue
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
        if ext == "xls":
            notes.append(f"Skipped {base}: old .xls workbooks are not read. Save it as .xlsx or CSV.")
            continue
        match = kind(info)
        if match is None and ext not in ("csv", "xlsx", "txt", "tsv"):
            notes.append(f"Ignored {base} (not one of the audit files).")
            continue
        if match is not None and match in files:
            notes.append(f"Two files are read as {match}: kept {origin[match]}, skipped {info.filename}. "
                         "Upload one month per zip, or rename the extra file.")
            continue
        raw = read(info)
        if raw is None:
            continue
        said: list[str] = []
        table = match is None or match.endswith(".csv")       # contracts.txt and invoices.txt are text, not tables
        if ext == "xlsx":
            got, why = importer.xlsx_lines(raw, MAX_UNZIPPED_MB, MAX_CELLS)
            if got is None:
                notes.append(f"Skipped {base}: {'; '.join(why)}.")
                continue
            lines, said = got, list(why)
        else:
            lines = decode(raw).replace("\x00", "").splitlines()
            if table and (ext == "tsv" or (lines and "\t" in lines[0] and "," not in lines[0])):
                lines = _tsv_to_csv(lines)
            if table:
                lines, joined = _join_quoted(lines)
                if joined:
                    said.append(f"{joined} value(s) that ran over several lines were joined onto one line")
        if table:
            lines, tidied = importer.tidy(lines)
            said += tidied
        if match is None:
            match, conf = importer.guess(base, lines, set(files))
            if match is None:
                notes.append(f"Ignored {base}: its columns do not clearly match one of the audit files. Rename it "
                             "payments.csv, approvals.csv, vendors.csv, expenses.csv, bank_statement.csv or "
                             "payment_run.csv to read it as that file.")
                continue
            said.append(f"read as {match}, recognised from its columns ({conf:.0%} of the needed columns found) - "
                        f"if that is wrong, rename the file")
        if said:
            notes.append(f"{base}: " + "; ".join(said) + ".")
        long_ = [i for i, ln in enumerate(lines, start=1) if len(ln) > MAX_LINE_CHARS]
        if long_:                     # no real export has lines this long; cutting them keeps every check fast
            lines = [ln[:MAX_LINE_CHARS] for ln in lines]
            notes.append(f"{base}: {len(long_)} line(s) longer than {MAX_LINE_CHARS:,} characters were cut "
                         f"(first: line {long_[0]}).")
        total += len(lines)
        if total > MAX_LINES:
            notes.append(f"Stopped at {MAX_LINES} lines; {base} and later files were skipped.")
            break
        missing = rules.missing_columns(match, lines)
        if missing:
            import csv

            from . import columns
            guessed = columns.suggest(missing, next(csv.reader([lines[0]])) if lines else [])
            if len(guessed) == len(missing):
                notes.append(f"{match}: its column names differ from Tallyhound's. A match for every column is "
                             "suggested below; check it and press Save column matching.")
            else:
                notes.append(f"{match} is missing columns: {', '.join(missing)}. Match its columns below, or it is "
                             "skipped.")
        files[match] = lines
        origin[match] = info.filename
    if pdf_raw:
        from . import invoices
        for base, pdf_text in invoices.read_pdfs(pdf_raw).items():
            if pdf_text:
                pdfs.append((base, pdf_text))
            elif pdf_text is None:
                notes.append(f"Skipped {base}: it could not be read safely (too slow, too large, or more than "
                             f"{invoices.MAX_PDF_PAGES} pages).")
            else:
                notes.append(f"{base} has no readable text (a scan?). It was skipped; scanned PDFs need OCR first.")
    if pdfs:
        from . import invoices
        files[invoices.NAME] = invoices.combine(sorted(pdfs))
        notes.append(f"Read {len(pdfs)} invoice PDF(s) into invoices.txt for the Invoices agent.")
    return files, notes


def _tsv_to_csv(lines: list[str]) -> list[str]:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for row in csv.reader(lines, delimiter="\t"):
        w.writerow(row)
    return buf.getvalue().splitlines()


OPENS_QUOTE = re.compile(r'(?:^|,)\s*"')


def _join_quoted(lines: list[str], most: int = 5) -> tuple[list[str], int]:
    """A quoted value with a line break inside ("Paid by cheque<newline>second line") arrives as two lines; join
    them with a space so the row keeps its columns. Only a value that opens with a quote is joined, only when the
    next line is not a full row by itself, and only when the joined line has the header's number of columns - an
    inch mark (5" pipe) is an ordinary character to a CSV reader and never joins rows."""
    import csv
    if len(lines) < 2 or not any(ln.count('"') % 2 for ln in lines[1:]):
        return lines, 0

    def width(ln: str) -> int:
        try:
            return len(next(csv.reader([ln])))
        except (csv.Error, StopIteration):
            return -1
    want = width(lines[0])
    out: list[str] = [lines[0]]
    joined, i = 0, 1
    while i < len(lines):
        ln = lines[i]
        if ln.count('"') % 2 and OPENS_QUOTE.search(ln) and i + 1 < len(lines) and width(lines[i + 1]) != want:
            acc, j = ln, i
            while acc.count('"') % 2 and j + 1 < len(lines) and j - i < most:
                j += 1
                acc = acc + " " + lines[j]
            if acc.count('"') % 2 == 0 and width(acc) == want:
                out.append(acc)
                joined += 1
                i = j + 1
                continue
        out.append(ln)
        i += 1
    return out, joined


def bundle(uploads: list[tuple[str, bytes]]) -> tuple[bytes, list[str]]:
    """(one in-memory zip, notes) for loose files picked together (CSV, Excel, PDF), so they take the same path as a
    zip. A zip on its own is used as it is; zips picked with other files are unpacked into the bundle."""
    if len(uploads) == 1 and uploads[0][0].lower().endswith(".zip"):
        return uploads[0][1], []
    notes: list[str] = []
    buf = io.BytesIO()
    budget = MAX_UNZIPPED_MB * 1024 * 1024            # the same "zip bomb" limit parse_zip applies
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, raw in uploads:
            short = name.replace("\\", "/").rsplit("/", 1)[-1]
            if not name.lower().endswith(".zip"):
                z.writestr(short, raw)
                continue
            try:
                inner = zipfile.ZipFile(io.BytesIO(raw))
                for info in inner.infolist():
                    if info.is_dir():
                        continue
                    if info.flag_bits & 0x1:
                        notes.append(f"Skipped {info.filename} in {short}: it is password-protected.")
                        continue
                    if info.file_size > budget:
                        notes.append(f"Stopped at {MAX_UNZIPPED_MB} MB of unpacked data; {info.filename} in {short} "
                                     "and later files were skipped.")
                        break
                    data = inner.read(info)
                    budget -= len(data)
                    z.writestr(info.filename, data)
            except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, NotImplementedError, EOFError):
                notes.append(f"Skipped {short}: it is not a valid zip.")
    return buf.getvalue(), notes


def read_uploads(uploads: list[tuple[str, bytes]]) -> tuple[dict[str, list[str]], list[str]]:
    """(files, notes) for what a person picked: one zip, or loose files and zips together."""
    data, notes = bundle(uploads)
    files, more = parse_zip(data)
    return files, notes + more
