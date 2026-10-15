"""Second look at the 0.7.0 / 0.7.1 fixes: each test is a case a fix left open or a path it did not reach."""
import io
import random
import zipfile

from tallyhound import gate, llm, rules, uploads

PAY = "payment_id,pay_date,invoice_date,supplier,invoice_no,invoice_amount,paid_amount"


# ---- decode: the Shift-JIS and UTF-16 detection
def test_a_french_name_in_a_windows_csv_is_not_read_as_japanese():
    # "Hélène" in Windows-1252 is H E9 6C E8 6E e: as Shift-JIS, two kanji in a row
    for name in ("Hélène Moreau", "Thérèse Conseil SARL"):
        text = f"vendor_id,name\nV-001,{name}\n"
        assert uploads.decode(text.encode("cp1252")) == text


def test_shift_jis_is_still_read():
    for text in ("vendor_id,name\nV-001,株式会社山田\nV-002,サイトウ商事\n", "vendor_id,name\nV-001,ﾔﾏﾀﾞ ﾀﾛｳ\n"):
        assert uploads.decode(text.encode("cp932")) == text      # the second in half-width kana, as banks write


def test_utf16_without_bom_with_an_ideographic_space_or_kanji_one_is_read():
    # U+3000 and U+4E00 (一) have a zero byte in the half of the pair that is never zero for ASCII
    text = "vendor_id,name\nV-001,第一商事　株式会社\n"
    for codec in ("utf-16-le", "utf-16-be"):
        assert uploads.decode(text.encode(codec)) == text


# ---- tab-separated files: a stray quote
def test_a_stray_quote_on_the_last_line_of_a_tab_file_keeps_its_columns():
    out = uploads._tsv_to_csv(["a\tb\tc", "1\tx\t2", '3\t"4\t5'])
    assert out[-1] == '3,"""4",5'


# ---- payment gate: same invoice twice in one run
def test_same_invoice_twice_in_a_run_is_held_when_only_one_line_has_the_vendor_id():
    run = ["line,supplier,invoice,amount,vendor_id", "1,Calder Logistics,INV-1,500.00,V-001",
           "2,Calder Logistics,INV-1,500.00,"]
    df = gate.evaluate({"payment_run.csv": run})
    assert list(df["decision"]) == ["RELEASE", "HOLD"]
    assert "5" in df["failed_checks"].iloc[1].split(",")


def test_two_vendors_with_the_same_invoice_number_in_one_run_are_both_released():
    run = ["line,supplier,invoice,amount,vendor_id", "1,Calder Logistics,1001,500.00,V-001",
           "2,Brightway Cleaning,1001,300.00,V-002", "3,Brightway Cleaning,1001,300.00,"]
    df = gate.evaluate({"payment_run.csv": run})
    assert list(df["decision"]) == ["RELEASE", "RELEASE", "HOLD"]


# ---- paid twice: which line is the repeat, whatever the file's order
def test_paid_twice_names_the_same_payments_when_the_file_is_newest_first():
    rows_ = ["P-1,2026-09-01,2026-08-25,Calder Logistics,INV-9,1000.00,400.00",
             "P-2,2026-09-10,2026-08-25,Calder Logistics,INV-9,1000.00,600.00",
             "P-3,2026-09-20,2026-08-25,Calder Logistics,INV-9,1000.00,1000.00"]

    def repeats(lines):
        R = dict(rules.rows(lines))
        return sorted((R[h.line_number]["payment_id"], h.amount) for h in rules.payments(lines) if h.clause == "5.2")
    assert repeats([PAY] + rows_) == [("P-3", 1000.0)]
    assert repeats([PAY] + rows_[::-1]) == [("P-3", 1000.0)]


def test_paid_twice_does_not_depend_on_row_order():
    rng = random.Random(7)
    for _ in range(300):
        rows_ = [f"P-{i},2026-09-{1 + i:02d},2026-08-25,Calder Logistics,INV-{rng.choice([1, 2])},1000.00,"
                 f"{rng.choice([400.00, 600.00, 1000.00, -1000.00])}" for i in range(rng.randint(1, 6))]
        shuffled = rng.sample(rows_, len(rows_))

        def found(lines):
            R = dict(rules.rows(lines))
            return sorted((h.clause, R[h.line_number]["payment_id"], h.amount) for h in rules.payments(lines)
                          if h.clause in ("5.1", "5.2"))
        assert found([PAY] + rows_) == found([PAY] + shuffled), rows_


# ---- OLLAMA_HOST read the way Ollama reads it
def test_ollama_host_with_a_trailing_slash_or_a_bare_ipv6_address():
    assert llm._from_ollama_host("localhost:11434/") == "http://localhost:11434"
    assert llm._from_ollama_host("127.0.0.1/") == "http://127.0.0.1:11434"
    assert llm._from_ollama_host("::1") == "http://[::1]:11434"
    assert llm._from_ollama_host("[::1]:8080") == "http://[::1]:8080"
    assert llm._from_ollama_host("0.0.0.0") == "http://localhost:11434"
    assert llm._from_ollama_host("https://ollama.example.com/") == "https://ollama.example.com/"


# ---- scans do not use up the unpacked-data budget before the CSVs are read
def test_csvs_are_read_before_scans_use_up_the_unpacked_limit(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UNZIPPED_MB", 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("scan_01.pdf", b"%PDF-1.4\n" + bytes(1024 * 1024 - 60))
        z.writestr("Bill Payment List.csv", PAY + "\nP-1,2026-09-01,2026-08-25,Calder Logistics,INV-1,100.00,100.00\n")
    files, notes = uploads.parse_zip(buf.getvalue())
    assert "payments.csv" in files, notes
