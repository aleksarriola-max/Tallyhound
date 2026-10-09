"""Big files stay fast, and exports from non-US systems are read right: amounts, dates, encodings and names in any
script. Timing checks use generous bounds; each takes well under a second once fixed."""
import time
from datetime import date

from tallyhound import gate, headless, rules, uploads

PAY = "payment_id,pay_date,invoice_date,supplier,invoice_no,invoice_amount,paid_amount"
APPR = "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no"
VEND = "vendor_id,name,tax_id,status,bank_changed_on,bank_verified,w9_on_file,last_paid_on,last_paid_amount"


def _seconds(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


# ---- scale
def test_a_long_run_of_small_bills_without_a_po_is_checked_fast():
    # a cleaner billed $30-$49 a day all year, no PO: the split check looked at every later bill for each bill
    lines = [APPR] + [f"A-{i},INVOICE,CL-{i},{date.fromordinal(date(2025, 1, 1).toordinal() + i * 365 // 5000)},"
                      f"Brightway Cleaning,{30 + i % 20}.00,A. Patel,K. Lowe,Manager," for i in range(5000)]
    assert _seconds(lambda: rules.approvals(lines)) < 4


def test_split_check_does_not_scan_every_vendor_for_each_bill():
    lines = [APPR] + [f"A-{i},INVOICE,D-{i},2026-09-0{1 + i % 9},Vendor {i} Supplies,100.00,K. Lowe,J. Ames,Manager,"
                      for i in range(12000)]
    R = rules.rows(lines)
    assert _seconds(lambda: rules._split_orders(R, rules.LIMITS)) < 3


def test_payments_without_vendor_ids_against_a_big_master_are_fast():
    vendors = [VEND] + [f"V-{i},Supplier {i} Trading Ltd,55-{i:07d},ACTIVE,,,YES,2026-09-01,10.00" for i in range(5000)]
    pays = [PAY] + [f"P-{i},2026-09-02,2026-09-01,Unknown Payee {i},INV-{i},10.00,10.00" for i in range(20000)]
    hits: list = []
    assert _seconds(lambda: hits.extend(rules.cross_file({"payments.csv": pays, "vendors.csv": vendors}))) < 3
    assert len(hits) == 20000
    # a longer or shorter spelling of a known name is still not reported as unknown
    near = [PAY, "P-1,2026-09-02,2026-09-01,Supplier 7,INV-1,10.00,10.00",
            "P-2,2026-09-02,2026-09-01,Supplier 7 Trading Ltd Leeds Branch,INV-2,10.00,10.00"]
    assert rules.cross_file({"payments.csv": near, "vendors.csv": vendors}) == []


def test_a_report_with_thousands_of_findings_is_written_fast(tmp_path):
    hits = [rules.Hit("Payments", "5.4", "Low", 10.0, f"Payment P-{i} released on a Saturday", "payments.csv", i + 2)
            for i in range(8000)]
    import pandas as pd
    res = dict(files={"payments.csv": [PAY]}, notes=[], hits=hits, gate=pd.DataFrame())
    assert _seconds(lambda: headless.write_report(tmp_path, res)) < 3
    _, new = headless.write_report(tmp_path, res)
    assert new == []                                    # remembered from the first report


def test_a_folder_bigger_than_the_zip_limit_is_still_read(tmp_path, monkeypatch):
    # the scheduled job packs the folder into a zip in memory; uncompressed, a folder of ordinary CSVs hit the upload
    # zip limit and nothing at all was checked ("That zip is larger than 50 MB")
    monkeypatch.setattr(uploads, "MAX_ZIP_MB", 1)               # each file is under it, the two together are not
    pays = [f"P-{i},2026-09-02,2026-09-01,Ashby Components,ASH-{i},100.00,100.00" for i in range(12000)]
    appr = [f"A-{i},INVOICE,ASH-{i},2026-09-01,Ashby Components,100.00,T. Brandt,K. Lowe,Manager,PO-{i}" for i in range(11000)]
    (tmp_path / "payments.csv").write_text("\n".join([PAY] + pays) + "\n", encoding="utf-8")
    (tmp_path / "approvals.csv").write_text("\n".join([APPR] + appr) + "\n", encoding="utf-8")
    assert 2 ** 20 < sum(p.stat().st_size for p in tmp_path.iterdir()) and all(p.stat().st_size < 2 ** 20 for p in tmp_path.iterdir())
    files, notes = headless.load_folder(tmp_path)
    assert len(files.get("payments.csv", [])) == 12001 and len(files.get("approvals.csv", [])) == 11001, notes


# ---- amounts
def test_decimal_comma_with_one_decimal_is_not_ten_times_too_big():
    # Excel in Germany writes 12.5 as "12,5" unless the cell is formatted with two decimals
    assert rules._f("12,5") == 12.5 and rules._f("1234,5") == 1234.5 and rules._f("1.234,5") == 1234.5
    assert rules._f("-0,5") == -0.5 and rules._f("(12,5)") == -12.5
    assert rules._f("1,234") == 1234.0 and rules._f("1,234.5") == 1234.5         # US thousands are unchanged
    files, _ = uploads.read_uploads([("payments.csv", (
        "payment_id;pay_date;invoice_date;supplier;invoice_no;invoice_amount;paid_amount\n"
        "P-1;02.09.2026;01.09.2026;Müller GmbH;MU-1;1.960,17;1.960,17\n"
        "P-2;03.09.2026;01.09.2026;Müller GmbH;MU-2;250,5;250,5\n").encode("cp1252"))])
    assert [rules._f(r["paid_amount"]) for _, r in rules.rows(files["payments.csv"])] == [1960.17, 250.5]


def test_thousands_marks_and_currencies_of_other_countries():
    assert rules._f("1 234,56") == 1234.56          # French: narrow no-break space
    assert rules._f("1 234,56") == 1234.56          # thin space
    assert rules._f("1’234.50") == 1234.5           # Swiss, typographic apostrophe
    assert rules._f("¥120,000") == 120000 and rules._f("JPY 120000") == 120000 and rules._f("120,000円") == 120000
    assert rules._f("1.234,56 zł") == 1234.56 and rules._f("PLN 1.234,56") == 1234.56 and rules._f("SEK 1 234,56") == 1234.56
    assert rules._f("₹1,23,456.78") == 123456.78 and rules._f("US$1,200.00") == 1200 and rules._f("R$ 1.234,56") == 1234.56
    assert rules._f("120.00 CR") == -120 and rules._f("120.00 DR") == 120 and rules._f("CHF 1'234.50") == 1234.5


# ---- dates
def test_day_first_dates_with_a_time_are_not_read_month_first():
    lines = ["date,description,amount", "02/09/2026 14:30,ACME LTD,-100.00", "13/09/2026 09:05,ACME LTD,-50.00"]
    assert rules.date_order(lines) == "day-first"
    assert [r.d("date") for _, r in rules.rows(lines)] == [date(2026, 9, 2), date(2026, 9, 13)]
    assert rules._d("2/9/2026 14:30", True) == date(2026, 9, 2)


# ---- names in any script
def test_two_suppliers_with_non_latin_names_are_two_suppliers():
    pay = [PAY, "P-1,2026-09-01,2026-09-01,株式会社山田,INV-1,500.00,500.00",
           "P-2,2026-09-02,2026-09-01,شركة النور,INV-1,500.00,500.00"]
    assert not [h for h in rules.payments(pay) if h.clause in ("5.2", "5.6")]
    assert rules.norm_name("株式会社山田") != rules.norm_name("株式会社佐藤")
    assert rules.norm_name("Müller GmbH") == rules.norm_name("MULLER GMBH") != rules.norm_name("Mäller GmbH")


def test_self_approval_is_found_whatever_the_script():
    lines = [APPR, "A-1,INVOICE,X-1,2026-09-01,Acme,100.00,محمد علي,محمد علي,Manager,PO-1",
             "A-2,INVOICE,X-2,2026-09-01,Acme,100.00,山田 太郎,山田 太郎,Manager,PO-2",
             "A-3,INVOICE,X-3,2026-09-01,Acme,100.00,山田 太郎,佐藤 花子,Manager,PO-3"]
    assert sorted(h.line_number for h in rules.approvals(lines) if h.clause == "1.2") == [2, 3]


def test_payment_run_to_another_non_latin_vendor_is_held():
    files = {"payment_run.csv": ["line,vendor_id,supplier,invoice,amount", "1,V-1,株式会社佐藤,INV-9,100.00"],
             "vendors.csv": [VEND + ",bank_acct", "V-1,株式会社山田,1,ACTIVE,,,YES,2026-09-01,10.00,****1234"]}
    g = gate.evaluate(files)
    assert g.decision[0] == "HOLD" and "3" in g.failed_checks[0].split(",")


# ---- encodings
def test_utf16_without_bom_and_shift_jis_are_read():
    text = "supplier,amount\nŁódź Sp. z o.o.,10.00\n株式会社山田,20.00\n"
    assert uploads.decode(text.encode("utf-16-le")) == text
    assert uploads.decode(text.encode("utf-16-be")) == text
    sjis = "supplier,amount\n株式会社山田,20.00\nヤマダ商事,5.00\n"
    assert uploads.decode(sjis.encode("cp932")) == sjis
    western = "supplier,amount\nCafé Müller,1.00\nSociété Générale,2.00\nZoë Brandt,3.00\nÆgir ÅS,4.00\n"
    assert uploads.decode(western.encode("cp1252")) == western      # still Windows-1252, never taken for Japanese
