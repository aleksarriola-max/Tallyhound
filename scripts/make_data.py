"""Generate all fictional Ledgerwatch data: data/*.csv and data/source/*.

Run from the project root:  python scripts/make_data.py
Every finding's evidence is an exact line in a file under data/source/, and its
line_number is computed from the generated file, so the app can verify quotes.
"""
import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SRC = DATA / "source"
SRC.mkdir(parents=True, exist_ok=True)
rnd = random.Random(2026)


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def money(x):
    return f"{x:.2f}"


# --------------------------------------------------------------------------
# Suppliers
# --------------------------------------------------------------------------
SUPPLIERS = [
    ("Ashby Components", "ASH", "V-2201"),
    ("Corwin Plastics", "COR", "V-2315"),
    ("Dellmore Packaging", "DEL", "V-2402"),
    ("Elstree Fasteners", "ELS", "V-2518"),
    ("Fairholt Logistics", "FAI", "V-3102"),
    ("Gilbey Mills", "GIL", "V-2977"),
    ("Ivers Supply", "IVS", "V-3044"),
    ("Hanford Tooling", "HAN", "V-3205"),
]
APPROVERS = ["L. Marsh", "R. Singh", "K. Lowe", "D. Reyes", "J. Ames", "S. Duarte"]


class Source:
    """A generated source file. Groups of lines are inserted contiguously."""

    def __init__(self, name, header, filler):
        self.name, self.header, self.filler = name, header, list(filler)
        self.groups = []  # (position, lines)

    def add_group(self, lines, pos):
        self.groups.append((pos, list(lines)))

    def build(self):
        body = list(self.filler)
        for pos, lines in sorted(self.groups, key=lambda g: -g[0]):
            body[pos:pos] = lines
        lines = ([self.header] if self.header is not None else []) + body
        (SRC / self.name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.lines = lines

    def line_no(self, text):
        hits = [i + 1 for i, ln in enumerate(self.lines) if ln == text]
        assert len(hits) == 1, (self.name, text, hits)
        return hits[0]


# --------------------------------------------------------------------------
# Filler rows
# --------------------------------------------------------------------------
def payments_filler():
    rows, n = [], 4001
    for i in range(30):
        name, pre, vid = SUPPLIERS[i % len(SUPPLIERS)]
        amt = round(rnd.uniform(900, 9800), 2)
        day = 1 + (i * 3) % 27
        d = f"2026-09-{day:02d}"
        # keep fillers off weekends (Sat/Sun in Sept 2026: 5,6,12,13,19,20,26,27)
        while day in (5, 6, 12, 13, 19, 20, 26, 27):
            day += 1
            d = f"2026-09-{day:02d}"
        inv_d = f"2026-09-{max(1, day - 4):02d}"
        rows.append(",".join([f"P-{n}", d, inv_d, vid, name, f"{pre}-{6100 + i}",
                              money(amt), money(amt), str(rnd.randint(1000, 9999)),
                              APPROVERS[i % len(APPROVERS)]]))
        n += 1
    return rows


def approvals_filler():
    rows, n = [], 100
    reqs = ["T. Brandt", "A. Patel", "C. Yoon", "M. Okafor", "H. Vance"]
    for i in range(24):
        name, pre, _ = SUPPLIERS[i % len(SUPPLIERS)]
        amt = round(rnd.uniform(600, 9500), 2)
        role = "Manager" if amt < 10000 else "Director"
        rows.append(",".join([f"A-{n:04d}", "INVOICE", f"{pre}-{6100 + i}",
                              f"2026-09-{1 + (i * 2) % 28:02d}", name, money(amt),
                              reqs[i % len(reqs)], APPROVERS[(i + 2) % len(APPROVERS)],
                              role, f"PO-{2100 + i}"]))
        n += 1
    return rows


def vendors_filler():
    rows = []
    for i, (name, pre, vid) in enumerate(SUPPLIERS):
        rows.append(",".join([vid, name, f"55-{1000000 + i * 7919}", "ACTIVE",
                              f"****{rnd.randint(1000, 9999)}", "", "N/A",
                              f"2024-0{1 + i % 9}-1{i % 9}", "YES",
                              f"2026-09-{10 + i:02d}", money(rnd.uniform(1200, 9000))]))
    for i in range(10):
        rows.append(",".join([f"V-{3400 + i * 13}", f"Northgate Trading {i + 1}", f"55-{2200000 + i * 4231}",
                              "ACTIVE", f"****{rnd.randint(1000, 9999)}", "", "N/A",
                              f"2025-0{1 + i % 9}-0{1 + i % 9}", "YES",
                              f"2026-09-{1 + i:02d}", money(rnd.uniform(300, 4000))]))
    return rows


def contracts_filler():
    rows = []
    for i, (name, pre, _) in enumerate(SUPPLIERS[:6]):
        cid = f"C-{100 + i}"
        rows.append(f"[CONTRACT {cid} | {name} | Clause 2.1] Prices are fixed for the term as per the schedule.")
        rows.append(f"[INVOICE {pre}-62{i:02d} | {name} | 2026-09-0{1 + i}] Line 1: Contracted supply 4{i}00.00")
    return rows


def expenses_filler():
    rows, n = [], 1001
    people = ["A. Patel", "R. Chen", "S. Duarte", "M. Okafor", "T. Brandt", "C. Yoon"]
    cats = ["TRAVEL", "MEAL", "SUPPLIES", "TAXI", "TRAINING"]
    for i in range(24):
        cat = cats[i % len(cats)]
        amt = round(rnd.uniform(18, 70), 2) if cat in ("MEAL", "TAXI") else round(rnd.uniform(30, 240), 2)
        day = 1 + (i * 2) % 26
        while day in (5, 6, 12, 13, 19, 20, 26, 27):
            day += 1
        rows.append(",".join([f"E-{n}", f"2026-09-{day:02d}", people[i % len(people)], cat,
                              money(amt), f"RC-{70000 + i}", "Business purpose recorded",
                              "1" if cat == "MEAL" else ""]))
        n += 1
    return rows


payments = Source("payments.csv",
                  "payment_id,pay_date,invoice_date,vendor_id,supplier,invoice_no,invoice_amount,paid_amount,bank_last4,approved_by",
                  payments_filler())
approvals = Source("approvals.csv",
                   "record_id,type,doc_no,date,vendor,amount,requested_by,approved_by,approver_role,po_no",
                   approvals_filler())
vendors = Source("vendors.csv",
                 "vendor_id,name,tax_id,status,bank_acct,bank_changed_on,bank_verified,created_on,w9_on_file,last_paid_on,last_paid_amount",
                 vendors_filler())
contracts = Source("contracts.txt", None, contracts_filler())
expenses = Source("expenses.csv",
                  "claim_id,date,employee,category,amount,receipt_ref,notes,people",
                  expenses_filler())
SOURCES = {s.name: s for s in (payments, approvals, vendors, contracts, expenses)}

# --------------------------------------------------------------------------
# Findings: id, severity, area, clause, amount, title, verdict, file, group lines,
# index of evidence line within the group, innocent, skeptic reason, fix
# --------------------------------------------------------------------------
FINDINGS = []


def finding(fid, sev, area, clause, amount, title, verdict, src, lines, ev, pos,
            innocent, skeptic, fix):
    SOURCES[src].add_group(lines, pos)
    FINDINGS.append(dict(id=fid, severity=sev, area=area, clause=clause, amount=amount,
                         title=title, verdict=verdict, source_file=src, lines=lines,
                         ev=ev, innocent=innocent, skeptic=skeptic, fix=fix))


# ---- Vendors (4) ----
finding("F-01", "High", "Vendors", "4.3", 8820.00,
        "V-4108 paid to an unverified new bank account", "Confirmed", "vendors.csv",
        ["V-4108,Marlowe Fabrication,55-7741920,ACTIVE,****6120,2026-09-26,NO,2023-05-02,YES,2026-09-28,8820.00"],
        0, 6,
        "Supplier may have notified Treasury by phone and the check was done outside the system.",
        "The bank_verified flag is NO and no verification record exists between 2026-09-26 and the 2026-09-28 payment.",
        "Hold future payments to V-4108 until Treasury records a call-back verification; ask the bank to recall the 2026-09-28 payment if it fails.")
finding("F-16", "High", "Vendors", "4.2", 9640.00,
        "Vendor records V-3011 and V-3207 share tax ID 55-0192384 and were both paid", "Confirmed", "vendors.csv",
        ["V-3011,Brightwell Metals,55-0192384,ACTIVE,****2288,,N/A,2022-03-14,YES,2026-09-08,4820.00",
         "V-3207,Brightwell Metals Ltd,55-0192384,ACTIVE,****9031,,N/A,2026-08-30,YES,2026-09-21,4820.00"],
        1, 12,
        "A genuine rename or merger of the same supplier where the old record was never closed.",
        "Both records are ACTIVE with different bank accounts and each received a payment of 4,820.00 in September.",
        "Merge the two records, close V-3207 and confirm with the supplier which bank account is correct.")
finding("F-17", "Medium", "Vendors", "4.1", 3775.00,
        "Inactive vendor V-2890 was paid $3,775.00 on 2026-09-18", "Confirmed", "vendors.csv",
        ["V-2890,Penhallow Cleaning,55-4410387,INACTIVE,****5510,,N/A,2021-11-09,YES,2026-09-18,3775.00"],
        0, 15,
        "The vendor may have been reactivated briefly to settle an old invoice.",
        "No reactivation entry exists; the record is still INACTIVE on the date of the payment.",
        "Confirm the invoice was valid, then reactivate or archive the vendor with an approval note.")
finding("F-18", "Low", "Vendors", "4.4", 2250.00,
        "V-3350 paid $2,250.00 with no tax form on file", "Confirmed", "vendors.csv",
        ["V-3350,Orrin Logistics,55-6021743,ACTIVE,****7712,,N/A,2026-09-02,NO,2026-09-30,2250.00"],
        0, 9,
        "The form may be held by Procurement on paper.",
        "w9_on_file is NO and the vendor was created 28 days before the first payment.",
        "Request the tax form from the supplier before the next payment.")

# ---- Contracts (5) ----
finding("F-02", "Medium", "Contracts", "7.1", 1340.00,
        "SRS-50946 adds a $1,340.00 surcharge the contract excludes", "Confirmed", "contracts.txt",
        ["[CONTRACT C-014 | Sarnia Print Services | Clause 7.1] Surcharges of any kind are excluded from the contract price.",
         "[INVOICE SRS-50946 | Sarnia Print Services | 2026-09-12] Line 3: Handling surcharge 1340.00"],
        1, 4,
        "A variation order may have been agreed verbally.",
        "No signed variation exists and the clause lists surcharges as excluded.",
        "Dispute the $1,340.00 line and ask for a credit note.")
finding("F-19", "High", "Contracts", "7.2", 2860.00,
        "Hanford Tooling billed $118 per hour against a contracted $92", "Confirmed", "contracts.txt",
        ["[CONTRACT C-022 | Hanford Tooling | Rate card] Machinist rate is 92.00 per hour.",
         "[INVOICE HAN-7650 | Hanford Tooling | 2026-09-16] Line 1: Machinist 110 hours at 118.00 per hour = 12980.00"],
        1, 8,
        "Overtime rates might apply to part of the hours.",
        "The invoice shows one flat rate for all 110 hours and the rate card has no overtime tier.",
        "Claim the $2,860.00 difference (110 hours x $26.00).")
finding("F-20", "Medium", "Contracts", "7.3", 1450.00,
        "Pellam Cleaning billed for service after the contract ended", "Confirmed", "contracts.txt",
        ["[CONTRACT C-031 | Pellam Cleaning | Term] This contract ends on 2026-08-31.",
         "[INVOICE PEL-2209 | Pellam Cleaning | 2026-09-12] Service period 2026-09-01 to 2026-09-12 1450.00"],
        1, 11,
        "A renewal may be signed but not yet filed.",
        "No renewal is on file and the service period starts the day after the term ends.",
        "Hold payment until a renewal is produced or the contract is extended in writing.")
finding("F-21", "Low", "Contracts", "7.4", 4800.00,
        "Veridian Software auto-renewed after the cancellation window closed", "Confirmed", "contracts.txt",
        ["[CONTRACT C-040 | Veridian Software | Renewal] Auto-renews on 2026-09-30 unless cancelled 30 days before.",
         "[INVOICE VER-8801 | Veridian Software | 2026-10-01] Annual renewal 4800.00 - no notice of cancellation on file"],
        1, 14,
        "The tool may still be needed.",
        "No cancellation notice was sent before the 30-day deadline.",
        "Ask the supplier for a goodwill cancellation; diarise notice dates for all auto-renewing contracts.")
finding("F-22", "Medium", "Contracts", "7.1", 1112.40,
        "Fairholt Logistics added an 8 percent fuel surcharge the contract does not list", "Confirmed", "contracts.txt",
        ["[CONTRACT C-018 | Fairholt Logistics | Clause 7.1] Charges not listed in the price schedule may not be billed.",
         "[INVOICE FAI-7420 | Fairholt Logistics | 2026-09-14] Line 2: Fuel surcharge 8 percent 1112.40"],
        1, 17,
        "Fuel surcharges are common in logistics.",
        "The price schedule has no fuel line and clause 7.1 bars unlisted charges.",
        "Dispute the surcharge and request a credit note.")

# ---- Payments (6) ----
finding("F-05", "High", "Payments", "5.2", 6150.00,
        "CLW-50837 paid twice on the same day", "Confirmed", "payments.csv",
        ["P-5101,2026-09-22,2026-09-18,V-2650,Calloway Wire Ltd,CLW-50837,6150.00,6150.00,4471,R. Singh",
         "P-5102,2026-09-22,2026-09-18,V-2650,Calloway Wire Ltd,CLW-50837,6150.00,6150.00,4471,R. Singh"],
        1, 3,
        "A payment may have been re-sent after a bank rejection.",
        "No rejection or reversal exists for P-5101; both payments cleared the same day.",
        "Recover P-5102 or apply it as a credit against the next Calloway invoice.")
finding("F-06", "Medium", "Payments", "5.1", 72.00,
        "ASH-7090 paid $12,480.00 against an invoice of $12,408.00", "Confirmed", "payments.csv",
        ["P-5110,2026-09-08,2026-09-02,V-2201,Ashby Components,ASH-7090,12408.00,12480.00,5532,L. Marsh"],
        0, 8,
        "Rounding or foreign exchange could explain a small gap.",
        "The invoice is in dollars and the digits are transposed (12408 vs 12480), so it is a keying error.",
        "Recover the $72.00 overpayment.")
finding("F-07", "Low", "Payments", "5.3", 3210.50,
        "COR-7188 paid two days before the invoice date", "Confirmed", "payments.csv",
        ["P-5120,2026-09-03,2026-09-05,V-2315,Corwin Plastics,COR-7188,3210.50,3210.50,2290,K. Lowe"],
        0, 14,
        "A deposit may have been agreed in advance.",
        "No purchase order or deposit term supports payment before the invoice.",
        "Confirm the payment terms with Corwin and note the exception.")
finding("F-08", "High", "Payments", "5.2", 6480.00,
        "Invoice DEL-7255 paid twice under different letter case", "Confirmed", "payments.csv",
        ["P-5125,2026-09-14,2026-09-09,V-2402,Dellmore Packaging,DEL-7255,6480.00,6480.00,8812,D. Reyes",
         "P-5126,2026-09-16,2026-09-09,V-2402,Dellmore Packaging,del-7255,6480.00,6480.00,8812,J. Ames"],
        1, 20,
        "Two genuine invoices could share a number.",
        "Same supplier, same amount and the same invoice date; only the letter case differs.",
        "Recover the second payment of $6,480.00.")
finding("F-09", "Medium", "Payments", "5.3", 4900.00,
        "Payment to Ivers Supply has no invoice reference", "Confirmed", "payments.csv",
        ["P-5130,2026-09-24,,V-3044,Ivers Supply,,4900.00,4900.00,3308,S. Duarte"],
        0, 25,
        "The reference may have been left off a prepayment.",
        "The invoice number and invoice date are both blank and no prepayment is on record.",
        "Ask Ivers to match the payment to an invoice.")
finding("F-10", "Low", "Payments", "5.4", 2115.00,
        "Payment released on a Sunday", "Confirmed", "payments.csv",
        ["P-5135,2026-09-13,2026-09-08,V-3205,Hanford Tooling,HAN-7640,2115.00,2115.00,6140,R. Singh"],
        0, 27,
        "A scheduled batch may have run automatically.",
        "2026-09-13 is a Sunday and the policy only allows release on working days.",
        "Review who released the batch and why.")

# ---- Approvals (6) ----
finding("F-03", "Medium", "Approvals", "1.4", 13965.00,
        "Three Rook Street Marketing invoices ($4,620 / $4,780 / $4,565) in two days, no PO, total $13,965 - possible split to avoid director approval",
        "Downgraded (High to Medium)", "approvals.csv",
        ["A-0270,INVOICE,RSM-1201,2026-09-16,Rook Street Marketing,4620.00,H. Vance,K. Lowe,Manager,",
         "A-0271,INVOICE,RSM-1202,2026-09-17,Rook Street Marketing,4780.00,H. Vance,K. Lowe,Manager,",
         "A-0272,INVOICE,RSM-1203,2026-09-17,Rook Street Marketing,4565.00,H. Vance,K. Lowe,Manager,"],
        2, 5,
        "Three separate campaign jobs could each be billed on completion.",
        "Same requester, same approver and same supplier within two days; each is just under the $5,000 line but there is a plausible campaign explanation, so severity is lowered.",
        "Ask the requester for the job briefs; if one job, re-approve at director level.")
finding("F-11", "High", "Approvals", "1.2", 11200.00,
        "PO-2209 raised and approved by the same person", "Confirmed", "approvals.csv",
        ["A-0301,PO,PO-2209,2026-09-11,Whitlock Engineering,11200.00,M. Okafor,M. Okafor,Manager,PO-2209"],
        0, 10,
        "A small team may have no one else available.",
        "Requester and approver are the same user and the amount is over $10,000.",
        "Send the PO to a director for independent approval.")
finding("F-12", "Medium", "Approvals", "1.1", 6750.00,
        "Invoice STR-3304 for $6,750.00 has no PO", "Confirmed", "approvals.csv",
        ["A-0310,INVOICE,STR-3304,2026-09-15,Stratton Print,6750.00,D. Reyes,K. Lowe,Manager,"],
        0, 13,
        "The PO may exist but not be linked.",
        "No PO number is recorded and no matching PO exists for Stratton Print in September.",
        "Raise a retrospective PO and note the breach.")
finding("F-13", "Medium", "Approvals", "1.3", 23400.00,
        "Invoice of $23,400.00 approved by a manager, not a director", "Confirmed", "approvals.csv",
        ["A-0315,INVOICE,KDM-4410,2026-09-18,Kingsdale Machinery,23400.00,A. Patel,P. Nandi,Manager,PO-2207"],
        0, 16,
        "The director may have approved by email.",
        "The system shows a Manager as sole approver for an amount over $10,000.",
        "Obtain a director approval and attach it.")
finding("F-14", "Medium", "Approvals", "1.1", 1980.00,
        "PO-2231 raised 17 days after invoice BRK-9022", "Confirmed", "approvals.csv",
        ["A-0321,INVOICE,BRK-9022,2026-09-02,Brockley Stationers,1980.00,C. Yoon,L. Marsh,Manager,PO-2231",
         "A-0322,PO,PO-2231,2026-09-19,Brockley Stationers,1980.00,C. Yoon,L. Marsh,Manager,PO-2231"],
        1, 19,
        "The goods may have been ordered under a framework agreement.",
        "No framework agreement exists for Brockley and the PO date is after the invoice date.",
        "Remind the requester that POs come before orders.")
finding("F-15", "Medium", "Approvals", "1.5", 2740.00,
        "Invoice approved by T. Hale after leaving the company", "Confirmed", "approvals.csv",
        ["A-0330,INVOICE,LRN-3302,2026-09-09,Larne Office Supply,2740.00,H. Vance,T. Hale,Manager,PO-2218"],
        0, 22,
        "A delegate may have used the account.",
        "T. Hale's account was closed on 2026-08-31, so the approval date should be impossible.",
        "Re-approve under a current approver and investigate the account use.")

# ---- Expenses (5) ----
finding("F-04", "Low", "Expenses", "6.1", 156.00,
        "Meal at $156.00 per person (limit $75.00)", "Confirmed", "expenses.csv",
        ["E-1042,2026-09-10,A. Patel,MEAL,156.00,RC-80412,Client dinner,1"],
        0, 5,
        "The meal may have been a client event for several people.",
        "The claim lists one person and no extra attendees.",
        "Pay $75.00 and ask for the excess to be repaid or justified.")
finding("F-23", "Medium", "Expenses", "6.2", 412.00,
        "Hotel claim of $412.00 has no receipt", "Confirmed", "expenses.csv",
        ["E-1050,2026-09-15,S. Duarte,TRAVEL,412.00,,Hotel two nights,"],
        0, 9,
        "The receipt may be on a card statement.",
        "No receipt reference is recorded and the amount is over $25.",
        "Request the hotel folio.")
finding("F-24", "Low", "Expenses", "6.3", 89.00,
        "Gym membership claimed as an expense", "Confirmed", "expenses.csv",
        ["E-1055,2026-09-17,R. Chen,SUPPLIES,89.00,RC-80440,Monthly gym membership,"],
        0, 14,
        "It might be part of a wellness allowance.",
        "No wellness allowance is in the policy and the item is personal.",
        "Reject the claim.")
finding("F-25", "High", "Expenses", "6.5", 64.00,
        "The same taxi receipt TX-88213 was claimed by two employees", "Confirmed", "expenses.csv",
        ["E-1077,2026-09-21,R. Chen,TAXI,64.00,TX-88213,Airport taxi,",
         "E-1081,2026-09-22,S. Duarte,TAXI,64.00,TX-88213,Airport taxi,"],
        1, 18,
        "Two people may have shared the taxi and each claimed their half incorrectly.",
        "Both claims show the full amount and the same receipt number.",
        "Pay one claim only and ask both employees to explain.")
finding("F-26", "Low", "Expenses", "6.4", 38.50,
        "Weekend expense with no reason given", "Confirmed", "expenses.csv",
        ["E-1090,2026-09-20,M. Okafor,SUPPLIES,38.50,RC-80455,,"],
        0, 22,
        "A weekend project could explain it.",
        "2026-09-20 is a Sunday and the notes field is blank.",
        "Ask for a business reason.")

# Build sources, compute line numbers
for s in SOURCES.values():
    s.build()

rows = []
for f in sorted(FINDINGS, key=lambda x: int(x["id"][2:])):
    src = SOURCES[f["source_file"]]
    evidence = f["lines"][f["ev"]]
    related = [ln for i, ln in enumerate(f["lines"]) if i != f["ev"]]
    rows.append([f["id"], f["severity"], f["area"], f["clause"], money(f["amount"]), f["title"],
                 f["verdict"], evidence, " || ".join(related), f["source_file"], src.line_no(evidence),
                 f["innocent"], f["skeptic"], f["fix"]])
write_csv(DATA / "findings.csv",
          ["id", "severity", "area", "clause", "amount", "title", "skeptic_verdict", "evidence",
           "related_evidence", "source_file", "line_number", "innocent_explanations",
           "skeptic_reason", "proposed_fix"], rows)

# sanity: counts promised by the spec
import collections
sev = collections.Counter(r[1] for r in rows)
area = collections.Counter(r[2] for r in rows)
assert sev == {"High": 7, "Medium": 12, "Low": 7}, sev
assert area == {"Payments": 6, "Approvals": 6, "Vendors": 4, "Contracts": 5, "Expenses": 5}, area
assert len(rows) == 26

# --------------------------------------------------------------------------
# Policy clauses
# --------------------------------------------------------------------------
write_csv(DATA / "policy.csv", ["clause", "area", "text"], [
    ["1.1", "Approvals", "Every purchase over $2,500 needs an approved purchase order raised before the order is placed."],
    ["1.2", "Approvals", "A person may not approve a request they raised themselves."],
    ["1.3", "Approvals", "Any amount over $10,000 needs approval from a director."],
    ["1.4", "Approvals", "Orders must not be split to stay under an approval limit."],
    ["1.5", "Approvals", "Only current, authorised approvers may approve."],
    ["4.1", "Vendors", "Payments may only go to active vendors."],
    ["4.2", "Vendors", "Each supplier has one vendor record, identified by its tax ID."],
    ["4.3", "Vendors", "No payment may be released to a changed bank account until verification is recorded."],
    ["4.4", "Vendors", "A tax form must be on file before the first payment to a vendor."],
    ["5.1", "Payments", "The amount paid must match the approved invoice amount."],
    ["5.2", "Payments", "A single invoice may be paid once only."],
    ["5.3", "Payments", "Every payment must reference an approved invoice dated on or before the payment date."],
    ["5.4", "Payments", "Payments are released on working days only."],
    ["6.1", "Expenses", "Meals are limited to $75.00 per person."],
    ["6.2", "Expenses", "A receipt is required for any claim over $25.00."],
    ["6.3", "Expenses", "Personal items are not reimbursable."],
    ["6.4", "Expenses", "Weekend expenses need a business reason."],
    ["6.5", "Expenses", "Each receipt may be claimed once only."],
    ["7.1", "Contracts", "Charges not listed in the contract price schedule may not be billed."],
    ["7.2", "Contracts", "Rates billed must match the contract rate card."],
    ["7.3", "Contracts", "Services may only be billed within the contract term."],
    ["7.4", "Contracts", "Auto-renewing contracts need a cancellation decision before the notice date."],
])

# --------------------------------------------------------------------------
# Payment runs
# --------------------------------------------------------------------------
GATE = [
    (1, "Ashby Components", "ASH-7101", 18640.00, "RELEASE", "All 8 checks passed", []),
    (2, "Ashby Components", "ASH-7102", 18640.00, "RELEASE", "Same amount as line 1, different invoice", []),
    (3, "Corwin Plastics", "COR-7201", 9377.25, "RELEASE", "All 8 checks passed", []),
    (4, "Dellmore Packaging", "DEL-7301", 6212.80, "RELEASE", "Bank changed 2026-09-24, verified by Treasury", []),
    (5, "Elstree Fasteners", "ELS-7401", 5108.00, "HOLD", "Amount is more than the open invoice ($4,688.00)", [6]),
    (6, "Fairholt Logistics", "FAI-7501", 13905.00, "HOLD", "Bank account changed 2026-09-27, no verification", [7]),
    (7, "Corwin Plastics", "COR-7201", 9377.25, "HOLD", "Same invoice already on line 3", [5]),
    (8, "Ashby Components", "ASH-7088", 17920.40, "HOLD", "Already paid on 2026-09-29", [4]),
    (9, "Gilbey Mills", "GIL-7601", 2486.00, "HOLD", "Vendor inactive; invoice not open and approved", [1, 2]),
    (10, "Ivers Supply", "IVS-7801", 3972.15, "HOLD", "Invoice still pending approval", [2]),
    (11, "Ivers Supply", "ELS-7402", 4055.60, "HOLD", "Invoice belongs to Elstree Fasteners, not Ivers", [3]),
    (12, "Hanford Tooling", "HAN-7701", 7340.90, "HOLD", "Bank account differs from vendor master", [8]),
    (13, "Ivers Supply", "IVS-7802", 1500.00, "RELEASE", "Part payment of a $3,180.00 invoice", []),
    (14, "Dellmore Packaging", "DEL-7302", 2870.40, "RELEASE", "All 8 checks passed", []),
]
GATE2 = [
    (1, "Ashby Components", "ASH-7201", 14220.00, "RELEASE", "All 8 checks passed", []),
    (2, "Corwin Plastics", "COR-7301", 8640.50, "RELEASE", "All 8 checks passed", []),
    (3, "Dellmore Packaging", "DEL-7401", 3115.00, "RELEASE", "All 8 checks passed", []),
    (4, "Fairholt Logistics", "FAI-7601", 5780.00, "HOLD", "Bank account changed 2026-10-02, no verification", [7]),
    (5, "Hanford Tooling", "HAN-7801", 6905.75, "RELEASE", "All 8 checks passed", []),
]


def write_gate(fname, gate):
    out = []
    for line, sup, inv, amt, dec, reason, failed in gate:
        checks = [0 if c in failed else 1 for c in range(1, 9)]
        out.append([line, sup, inv, money(amt), dec, reason,
                    ",".join(str(c) for c in failed) if failed else "-"] + checks)
    write_csv(DATA / fname, ["line", "supplier", "invoice", "amount", "decision", "reason",
                              "failed_checks"] + [f"c{i}" for i in range(1, 9)], out)


write_gate("payment_run_2026-10-01.csv", GATE)
write_gate("payment_run_2026-10-08.csv", GATE2)
hold = sum(g[3] for g in GATE if g[4] == "HOLD")
total = sum(g[3] for g in GATE)
assert money(hold) == "64165.30" and money(total) == "121405.75", (hold, total)
assert sum(1 for g in GATE if g[4] == "HOLD") == 8 and sum(1 for g in GATE if g[4] == "RELEASE") == 6
assert len({g[1] for g in GATE}) == 8

# --------------------------------------------------------------------------
# Recovery claims (sum 6,864.61)
# --------------------------------------------------------------------------
REC = [
    ("Ashby Components", "ASH-6702", "Invoice line 4", "Unit price above contract (4.15 vs 3.90)", 1240.00),
    ("Ashby Components", "ASH-6719", "Credit note CN-311", "Credit note issued but not applied", 862.50),
    ("Corwin Plastics", "COR-6655", "Invoice line 2", "Short delivery: 150 units billed, 112 received", 1508.75),
    ("Corwin Plastics", "COR-6671", "Invoice line 1", "Early-payment discount (2 percent) not taken", 315.20),
    ("Dellmore Packaging", "DEL-6810", "Credit note CN-402", "Returned pallets not credited", 945.00),
    ("Dellmore Packaging", "DEL-6824", "Invoice line 3", "Duplicate line billed twice", 722.16),
    ("Elstree Fasteners", "ELS-6903", "Invoice line 5", "Price above contract (0.42 vs 0.38)", 384.00),
    ("Elstree Fasteners", "ELS-6911", "Credit note CN-077", "Rebate for Q3 volume not applied", 614.00),
    ("Dellmore Packaging", "DEL-6830", "Invoice line 2", "Freight charged on a free-delivery order", 273.00),
]
assert money(sum(r[4] for r in REC)) == "6864.61", sum(r[4] for r in REC)
write_csv(DATA / "recovery.csv", ["supplier", "invoice", "source", "reason", "claim", "status"],
          [[a, b, c, d, money(e), "Proposed"] for a, b, c, d, e in REC])

# --------------------------------------------------------------------------
# Subscriptions: 15 tools, 911 licences, annual saving 25,850
# --------------------------------------------------------------------------
TOOLS = [  # name, licences, active, price, action, reduce_to
    ("Adobe Acrobat", 60, 22, 180, "Reduce", 30),
    ("Figma", 40, 9, 180, "Reduce", 15),
    ("Miro", 35, 6, 120, "Reduce", 10),
    ("Tableau Viewer", 25, 0, 300, "Cancel", 0),
    ("Smartsheet", 8, 0, 300, "Cancel", 0),
    ("Slack Business+", 120, 111, 96, "Keep", 0),
    ("Zoom Workplace", 70, 58, 160, "Keep", 0),
    ("Microsoft 365", 150, 142, 264, "Keep", 0),
    ("Salesforce", 35, 33, 840, "Keep", 0),
    ("DocuSign", 15, 12, 300, "Keep", 0),
    ("1Password", 100, 96, 60, "Keep", 0),
    ("Notion", 50, 41, 96, "Keep", 0),
    ("Asana", 40, 34, 130, "Keep", 0),
    ("Jira", 45, 40, 90, "Keep", 0),
]


def saving(l, a, p, act, n):
    return l * p if act == "Cancel" else ((l - n) * p if act == "Reduce" else 0)


others = sum(saving(l, a, p, act, n) for _, l, a, p, act, n in TOOLS)
need_l = 911 - sum(t[1] for t in TOOLS)
rem = 25850 - others
final = None
for d in range(10, 80):
    if rem % d == 0 and 90 <= rem // d <= 220 and need_l - d >= 40:
        final = ("Lucidchart", need_l, need_l - d - 12, rem // d, "Reduce", need_l - d)
        break
assert final, (others, need_l, rem)
TOOLS.append(final)
assert len(TOOLS) == 15 and sum(t[1] for t in TOOLS) == 911
assert sum(saving(l, a, p, act, n) for _, l, a, p, act, n in TOOLS) == 25850
sub_rows = []
for name, l, a, p, act, n in TOOLS:
    action = "Cancel" if act == "Cancel" else (f"Reduce to {n}" if act == "Reduce" else "Keep")
    sub_rows.append([name, l, a, l * p, action, saving(l, a, p, act, n)])
write_csv(DATA / "subscriptions.csv",
          ["tool", "licences", "active_90d", "annual_cost", "proposed_action", "saving"], sub_rows)

# --------------------------------------------------------------------------
# Workflow cards, options, runs, events, guardrails, monitor, agents
# --------------------------------------------------------------------------
write_csv(DATA / "workflow_cards.csv", ["id", "name", "purpose", "picker", "default"], [
    ["audit", "Monthly audit", "Find problems after the money is spent", "multi", "2026-09"],
    ["gate", "Payment gate", "Stop risky payments before they go out", "single", "Payment run 2026-10-01"],
    ["recovery", "Supplier recovery", "Find money to claim back", "single", "Q3 supplier invoices"],
    ["subs", "Subscriptions", "Cut software waste", "single", "Software register"],
])
write_csv(DATA / "workflow_options.csv",
          ["workflow", "option", "preview_title", "preview", "est_min", "result_findings", "last_run", "file"], [
    ["audit", "2026-07", "Preview · 2026-07", "37 invoices · 37 payments · 18 POs · 22 expenses · 14 contracts · 0.3 MB", 8, 0, "Done", ""],
    ["audit", "2026-08", "Preview · 2026-08", "43 invoices · 43 payments · 20 POs · 26 expenses · 14 contracts · 0.3 MB", 8, 13, "Done", ""],
    ["audit", "2026-09", "Preview · 2026-09", "48 invoices · 48 payments · 21 POs · 29 expenses · 14 contracts · 0.3 MB", 8, 26, "Done", ""],
    ["gate", "Payment run 2026-10-01", "Preview · Payment run 2026-10-01", "", 2, 8, "Done", "payment_run_2026-10-01.csv"],
    ["gate", "Payment run 2026-10-08", "Preview · Payment run 2026-10-08", "", 2, 1, "Done", "payment_run_2026-10-08.csv"],
    ["recovery", "Q3 supplier invoices", "Preview · analysis date 2026-10-20", "12 invoices · 27 item lines · 4 suppliers · 3 credit notes", 4, len(REC), "Never run", ""],
    ["subs", "Software register", "Preview · analysis date 2026-10-31", "15 tools · 911 user logins", 3,
     sum(1 for t in sub_rows if t[4] != "Keep"), "Never run", ""],
])
write_csv(DATA / "recent_runs.csv", ["Run", "Data", "Started", "Duration", "Findings", "Status"], [
    ["2026-08 audit", "43 invoices", "10:05", "7m 40s", 13, "Done"],
    ["2026-07 audit", "37 invoices", "09:50", "7m 12s", 0, "Done (clean)"],
])
write_csv(DATA / "agents.csv", ["agent", "role"], [
    ["Orchestrator", "Reads the files and hands work to the other agents"],
    ["Payments", "Duplicates, wrong amounts, weekend and undated payments"],
    ["Approvals", "POs, self-approval, split orders and approval limits"],
    ["Vendors", "Inactive vendors, bank changes, duplicate vendor records"],
    ["Contracts", "Rates, surcharges and terms against signed contracts"],
    ["Expenses", "Limits, receipts, personal items and duplicate claims"],
    ["Skeptic", "Tries to disprove every finding before a person sees it"],
])
write_csv(DATA / "events.csv", ["time", "agent", "message"], [
    ["14:32:10", "Skeptic", "26 findings reviewed; 1 downgraded (F-03 High to Medium)"],
    ["14:31:42", "Expenses", "5 findings from expenses.csv; all quotes matched"],
    ["14:30:58", "Contracts", "5 findings from contracts.txt; all quotes matched"],
    ["14:30:07", "Vendors", "4 findings from vendors.csv; all quotes matched"],
    ["14:29:15", "Approvals", "6 findings from approvals.csv; all quotes matched"],
    ["14:28:21", "Payments", "6 findings from payments.csv; all quotes matched"],
    ["14:27:03", "Orchestrator", "Read 5 source files, 48 invoices and 48 payments"],
    ["14:26:50", "Inbox watcher", "New folder detected: inbox/2026-09"],
    ["14:20:11", "Payment gate", "Payment run 2026-10-01 checked: 8 HOLD, 6 RELEASE"],
    ["09:57:45", "Skeptic", "2026-07 audit finished with 0 findings (clean)"],
    ["09:50:02", "Orchestrator", "Started 2026-07 audit"],
])
write_csv(DATA / "monitor.csv", ["item", "value"], [
    ["Inbox watcher", "Watching inbox/"],
    ["Local model", "Loaded, offline"],
    ["Sandbox", "On, read-only inbox"],
    ["Network", "0 attempts blocked"],
])
write_csv(DATA / "guardrails.csv", ["name", "description", "metric"], [
    ["Agents only propose", "Approve, reject, hold and release happen only on the Review step and the Payment gate.", "Human decides"],
    ["Offline", "The model runs on the local GPU. No cloud calls.", "0 requests"],
    ["Sandbox", "Agents read the inbox folder and write only to the results folder.", "Read-only inbox"],
    ["Quote check", "Each quoted line is matched to the source file before it is shown.", "{quote_pct}% matched"],
    ["Skeptic review", "A second agent tries to disprove every finding. Always on, locked.", "{n_findings} reviewed"],
    ["Audit trail", "Every proposal, verdict and decision is logged with time and actor.", "{n_findings} findings logged"],
])
print("Generated data. Findings:", len(rows), "| files:", sorted(p.name for p in DATA.iterdir()))
