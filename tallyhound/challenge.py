"""Challenge generator: a fresh, fictional month of data with problems planted in it, plus the answer key.

Use it to test the engines on data they have never seen. The clean rows are built so that none of the built-in
rules fire on them; every planted problem is recorded with its file and line numbers. "Hard" variants are
deliberately subtle, and a few are worded so that the fixed rules miss them - that is where an AI model can earn
its keep, and the scorecard shows whether it does.
"""
from __future__ import annotations

import csv
import io
import random
import zipfile
from datetime import date, timedelta

HEADERS = {
    "payments.csv": ["payment_id", "pay_date", "invoice_date", "vendor_id", "supplier", "invoice_no", "invoice_amount",
                     "paid_amount", "bank_last4", "approved_by"],
    "approvals.csv": ["record_id", "type", "doc_no", "date", "vendor", "amount", "requested_by", "approved_by",
                      "approver_role", "po_no"],
    "vendors.csv": ["vendor_id", "name", "tax_id", "status", "bank_acct", "bank_changed_on", "bank_verified", "created_on",
                    "w9_on_file", "last_paid_on", "last_paid_amount"],
    "expenses.csv": ["claim_id", "date", "employee", "category", "amount", "receipt_ref", "notes", "people"],
}
KEY_HEADER = ["id", "clause", "area", "source_file", "line_number", "related_lines", "description"]
PEOPLE = ["J. Okoro", "M. Lindqvist", "P. Herrera", "S. Nakamura", "D. Abara", "L. Fontaine", "R. Mehta", "T. Walsh"]
NAMES = ["Arden", "Bexley", "Calder", "Dunmore", "Ellery", "Fenwick", "Garrow", "Holloway", "Ingram", "Juniper",
         "Kestrel", "Lomond", "Marlby", "Northam", "Orchard", "Pembury", "Quarry", "Rushden", "Selby", "Thornbury",
         "Upton", "Varley", "Wexcombe", "Yarrow"]
TRADES = ["Components", "Plastics", "Packaging", "Fasteners", "Logistics", "Tooling", "Print", "Cleaning", "Office Supply",
          "Metals", "Software", "Electrical"]
ISSUES = ["pay_dup", "pay_over", "pay_noinv", "pay_early", "pay_weekend", "app_self", "app_nopo", "app_director",
          "app_split", "app_poafter", "ven_bank", "ven_inactive", "ven_w9", "ven_duptax", "exp_meal", "exp_norec",
          "exp_duprec", "exp_weekend", "exp_personal", "con_surcharge", "con_rate", "con_term", "con_renew"]
COUNTS = {"easy": 8, "medium": 14, "hard": len(ISSUES)}


class _Gen:
    def __init__(self, seed: int, difficulty: str):
        self.r = random.Random(seed)
        self.hard = difficulty == "hard"
        self.days = [date(2026, 9, d) for d in range(1, 31)]
        self.weekdays = [d for d in self.days if d.weekday() < 5]
        self.weekends = [d for d in self.days if d.weekday() >= 5]
        self.rows = {k: [] for k in HEADERS}
        self.contracts: list[list[dict]] = []      # pairs of text lines kept together
        self.key: list[dict] = []
        self.n = {"pay": 6000, "app": 300, "ven": 5000, "exp": 2000, "doc": 7000, "po": 4000, "con": 200, "rc": 50000}
        self.names = [f"{a} {b}" for a in NAMES for b in TRADES]
        self.r.shuffle(self.names)

    # -------- helpers
    def nxt(self, k: str) -> int:
        self.n[k] += 1
        return self.n[k]

    def wd(self, lo: int = 0, hi: int = 99) -> date:
        return self.r.choice([d for d in self.weekdays if lo <= d.day <= hi] or self.weekdays)

    def amt(self, lo: float, hi: float) -> float:
        return round(self.r.uniform(lo, hi), 2)

    def two_people(self) -> tuple[str, str]:
        a, b = self.r.sample(PEOPLE, 2)
        return a, b

    def vendor(self, **kw) -> dict:
        name = kw.pop("name", None) or self.names.pop()
        row = dict(vendor_id=f"V-{self.nxt('ven')}", name=name, tax_id=f"77-{self.r.randint(1000000, 9999999)}",
                   status="ACTIVE", bank_acct=f"****{self.r.randint(1000, 9999)}", bank_changed_on="", bank_verified="N/A",
                   created_on=f"2024-{self.r.randint(1, 12):02d}-{self.r.randint(1, 28):02d}", w9_on_file="YES",
                   last_paid_on=self.wd(5, 28).isoformat(), last_paid_amount=f"{self.amt(800, 9000):.2f}")
        row.update(kw)
        self.rows["vendors.csv"].append(row)
        return row

    def approval(self, vendor: str, amount: float, day: date, **kw) -> dict:
        req, app = self.two_people()
        row = dict(record_id=f"A-{self.nxt('app')}", type="INVOICE", doc_no=f"{vendor[:3].upper()}-{self.nxt('doc')}",
                   date=day.isoformat(), vendor=vendor, amount=f"{amount:.2f}", requested_by=req, approved_by=app,
                   approver_role="Manager", po_no=f"PO-{self.nxt('po')}")
        row.update(kw)
        self.rows["approvals.csv"].append(row)
        return row

    def payment(self, v: dict, inv: dict, **kw) -> dict:
        inv_day = date.fromisoformat(inv["date"])
        pay_day = next((d for d in self.weekdays if d >= inv_day + timedelta(days=self.r.randint(1, 4))), self.weekdays[-1])
        row = dict(payment_id=f"P-{self.nxt('pay')}", pay_date=pay_day.isoformat(), invoice_date=inv["date"],
                   vendor_id=v["vendor_id"], supplier=v["name"], invoice_no=inv["doc_no"], invoice_amount=inv["amount"],
                   paid_amount=inv["amount"], bank_last4=v["bank_acct"][-4:], approved_by=self.r.choice(PEOPLE))
        row.update(kw)
        self.rows["payments.csv"].append(row)
        return row

    def expense(self, **kw) -> dict:
        row = dict(claim_id=f"E-{self.nxt('exp')}", date=self.wd().isoformat(), employee=self.r.choice(PEOPLE),
                   category=self.r.choice(["TRAVEL", "TAXI", "SUPPLIES", "TRAINING"]), amount=f"{self.amt(20, 280):.2f}",
                   receipt_ref=f"RC-{self.nxt('rc')}", notes="Business purpose recorded", people="")
        row.update(kw)
        self.rows["expenses.csv"].append(row)
        return row

    def contract_pair(self, vendor: str, clause: str, ctext: str, itext: str, day: date | None = None) -> tuple[dict, dict]:
        c = dict(text=f"[CONTRACT C-{self.nxt('con')} | {vendor} | {clause}] {ctext}")
        i = dict(text=f"[INVOICE {vendor[:3].upper()}-{self.nxt('doc')} | {vendor} | {(day or self.wd()).isoformat()}] {itext}")
        self.contracts.append([c, i])
        return c, i

    def plant(self, issue: str, clause: str, area: str, file: str, main: dict, related: list[dict], desc: str) -> None:
        self.key.append(dict(issue=issue, clause=clause, area=area, source_file=file, main=main, related=related, description=desc))

    # -------- the clean month
    def clean(self) -> None:
        self.paid_vendors = [self.vendor() for _ in range(10)]
        for v in self.paid_vendors:
            for _ in range(2):
                inv = self.approval(v["name"], self.amt(600, 9500), self.wd(1, 24))
                self.payment(v, inv)
        self.open_invoices = []
        for v in self.r.sample(self.paid_vendors, 6):      # approved, not yet paid: these go on the payment run
            self.open_invoices.append((v, self.approval(v["name"], self.amt(600, 9000), self.wd(20, 30))))
        for _ in range(16):
            if self.r.random() < 0.25:
                n = self.r.randint(1, 4)
                self.expense(category="MEAL", amount=f"{self.amt(15, 60) * n:.2f}", people=str(n), notes="Team lunch")
            else:
                self.expense()
        for _ in range(5):
            v = self.names.pop()
            self.contract_pair(v, "Clause 2.1", "Prices are fixed for the term as per the schedule.",
                               f"Line 1: Contracted supply {self.amt(1000, 6000):.2f}")

    # -------- the planted problems
    def issue(self, kind: str) -> None:
        r, hard = self.r, self.hard
        if kind == "pay_dup":
            v = r.choice(self.paid_vendors)
            orig = r.choice([p for p in self.rows["payments.csv"] if p["vendor_id"] == v["vendor_id"]])
            dup = dict(orig, payment_id=f"P-{self.nxt('pay')}", invoice_no=orig["invoice_no"].lower() if hard else orig["invoice_no"])
            self.rows["payments.csv"].append(dup)
            self.plant(kind, "5.2", "Payments", "payments.csv", dup, [orig], f"{orig['invoice_no']} paid twice")
        elif kind == "pay_over":
            v = r.choice(self.paid_vendors)
            inv = self.approval(v["name"], self.amt(1500, 8000), self.wd(1, 20))
            extra = 0.5 if hard else round(r.uniform(150, 900), 2)
            p = self.payment(v, inv, paid_amount=f"{float(inv['amount']) + extra:.2f}")
            self.plant(kind, "5.1", "Payments", "payments.csv", p, [], f"{inv['doc_no']} overpaid by ${extra:,.2f}")
        elif kind == "pay_noinv":
            v = r.choice(self.paid_vendors)
            p = dict(payment_id=f"P-{self.nxt('pay')}", pay_date=self.wd().isoformat(), invoice_date="", vendor_id=v["vendor_id"],
                     supplier=v["name"], invoice_no="", invoice_amount=f"{self.amt(900, 6000):.2f}", paid_amount="",
                     bank_last4=v["bank_acct"][-4:], approved_by=r.choice(PEOPLE))
            p["paid_amount"] = p["invoice_amount"]
            self.rows["payments.csv"].append(p)
            self.plant(kind, "5.3", "Payments", "payments.csv", p, [], f"Payment to {v['name']} with no invoice reference")
        elif kind == "pay_early":
            v = r.choice(self.paid_vendors)
            inv = self.approval(v["name"], self.amt(900, 7000), self.wd(10, 28))
            early = max(d for d in self.weekdays if d < date.fromisoformat(inv["date"]) - timedelta(days=1))
            p = self.payment(v, inv, pay_date=early.isoformat())
            self.plant(kind, "5.3", "Payments", "payments.csv", p, [], f"{inv['doc_no']} paid before its invoice date")
        elif kind == "pay_weekend":
            v = r.choice(self.paid_vendors)
            inv = self.approval(v["name"], self.amt(900, 7000), self.wd(1, 12))
            p = self.payment(v, inv, pay_date=r.choice([d for d in self.weekends if d.day > 14]).isoformat())
            self.plant(kind, "5.4", "Payments", "payments.csv", p, [], "Payment released on a weekend")
        elif kind == "app_self":
            who = r.choice(PEOPLE)
            a = self.approval(self.names.pop(), self.amt(3000, 9000), self.wd(), requested_by=who, approved_by=who)
            self.plant(kind, "1.2", "Approvals", "approvals.csv", a, [], f"{a['doc_no']} raised and approved by {who}")
        elif kind == "app_nopo":
            a = self.approval(self.names.pop(), 2510.0 if hard else self.amt(4000, 9000), self.wd(), po_no="")
            self.plant(kind, "1.1", "Approvals", "approvals.csv", a, [], f"{a['doc_no']} over the PO limit with no PO")
        elif kind == "app_director":
            a = self.approval(self.names.pop(), 10050.0 if hard else self.amt(14000, 26000), self.wd())
            self.plant(kind, "1.3", "Approvals", "approvals.csv", a, [], f"{a['doc_no']} over the director limit, approved by a manager")
        elif kind == "app_split":
            v, start = self.names.pop(), self.wd(1, 20)
            parts = [self.approval(v, self.amt(3400, 4900), start + timedelta(days=d), po_no="") for d in (0, 1, 1)]
            self.plant(kind, "1.4", "Approvals", "approvals.csv", parts[-1], parts[:-1], f"Three {v} invoices without a PO - possible split")
        elif kind == "app_poafter":
            v, day = self.names.pop(), self.wd(1, 12)
            inv = self.approval(v, self.amt(1200, 2400), day)
            po = self.approval(v, float(inv["amount"]), day + timedelta(days=r.randint(9, 16)), type="PO",
                               doc_no=inv["po_no"], po_no=inv["po_no"])
            self.plant(kind, "1.1", "Approvals", "approvals.csv", po, [inv], f"{inv['po_no']} raised after invoice {inv['doc_no']}")
        elif kind == "ven_bank":
            d = self.wd(10, 22)
            v = self.vendor(bank_changed_on=d.isoformat(), bank_verified="NO", last_paid_on=(d + timedelta(days=3)).isoformat())
            self.plant(kind, "4.3", "Vendors", "vendors.csv", v, [], f"{v['vendor_id']} paid to an unverified new bank account")
        elif kind == "ven_inactive":
            v = self.vendor(status="INACTIVE")
            self.plant(kind, "4.1", "Vendors", "vendors.csv", v, [], f"Inactive vendor {v['vendor_id']} was paid")
        elif kind == "ven_w9":
            v = self.vendor(w9_on_file="NO")
            self.plant(kind, "4.4", "Vendors", "vendors.csv", v, [], f"{v['vendor_id']} paid with no tax form on file")
        elif kind == "ven_duptax":
            a = self.vendor()
            b = self.vendor(name=a["name"] + (" Ltd" if hard else ""),
                            tax_id=a["tax_id"].replace("-", "") if hard else a["tax_id"])   # hard: same ID, other format
            self.plant(kind, "4.2", "Vendors", "vendors.csv", b, [a], f"{a['vendor_id']} and {b['vendor_id']} share a tax ID")
        elif kind == "exp_meal":
            n = 2 if hard else 1
            e = self.expense(category="MEAL", amount=f"{(79 if hard else self.amt(120, 190)) * n:.2f}", people=str(n),
                             notes="Client dinner")
            self.plant(kind, "6.1", "Expenses", "expenses.csv", e, [], "Meal over the per-person limit")
        elif kind == "exp_norec":
            e = self.expense(amount=f"{self.amt(60, 420):.2f}", receipt_ref="", notes="Hotel one night")
            self.plant(kind, "6.2", "Expenses", "expenses.csv", e, [], "Claim with no receipt")
        elif kind == "exp_duprec":
            a = self.expense(category="TAXI", amount="58.00", notes="Airport taxi")
            b = self.expense(category="TAXI", amount="58.00", notes="Airport taxi", receipt_ref=a["receipt_ref"],
                             employee=r.choice([p for p in PEOPLE if p != a["employee"]]))
            self.plant(kind, "6.5", "Expenses", "expenses.csv", b, [a], f"Receipt {a['receipt_ref']} claimed twice")
        elif kind == "exp_weekend":
            e = self.expense(date=r.choice(self.weekends).isoformat(), notes="")
            self.plant(kind, "6.4", "Expenses", "expenses.csv", e, [], "Weekend claim with no reason")
        elif kind == "exp_personal":
            note = "Yoga classes for myself" if hard else "Monthly gym membership"
            e = self.expense(category="SUPPLIES", amount=f"{self.amt(40, 120):.2f}", notes=note)
            self.plant(kind, "6.3", "Expenses", "expenses.csv", e, [], f"Personal item claimed: {note.lower()}")
        elif kind == "con_surcharge":
            v = self.names.pop()
            if hard:   # no word "surcharge": the fixed rules miss it, a careful reader does not
                c, i = self.contract_pair(v, "Clause 7.1", "Charges not listed in the price schedule may not be billed.",
                                          f"Line 2: Rush delivery fee {self.amt(150, 700):.2f}")
            else:
                c, i = self.contract_pair(v, "Clause 7.1", "Surcharges of any kind are excluded from the contract price.",
                                          f"Line 2: Fuel surcharge 6 percent {self.amt(150, 700):.2f}")
            self.plant(kind, "7.1", "Contracts", "contracts.txt", i, [c], f"{v} billed a surcharge the contract excludes")
        elif kind == "con_rate":
            v, rate = self.names.pop(), r.choice([72, 85, 92])
            hrs, billed = r.randint(20, 80), rate + (2 if hard else r.choice([14, 21, 26]))
            c, i = self.contract_pair(v, "Rate card", f"Technician rate is {rate:.2f} per hour.",
                                      f"Line 1: Technician {hrs} hours at {billed:.2f} per hour = {hrs * billed:.2f}")
            self.plant(kind, "7.2", "Contracts", "contracts.txt", i, [c], f"{v} billed above the contract rate")
        elif kind == "con_term":
            v = self.names.pop()
            c, i = self.contract_pair(v, "Term", "This contract ends on 2026-08-31.",
                                      f"Service period 2026-09-01 to 2026-09-15 {self.amt(400, 2000):.2f}")
            self.plant(kind, "7.3", "Contracts", "contracts.txt", i, [c], f"{v} billed after the contract ended")
        elif kind == "con_renew":
            v = self.names.pop()
            c, i = self.contract_pair(v, "Renewal", "Auto-renews on 2026-09-30 unless cancelled 30 days before.",
                                      f"Annual renewal {self.amt(1200, 6000):.2f} - no notice of cancellation on file",
                                      day=date(2026, 10, 1))
            self.plant(kind, "7.4", "Contracts", "contracts.txt", i, [c], f"{v} auto-renewed with no decision on file")

    # -------- the proposed payment run for the Payment gate (four lines should be held)
    def payment_run(self) -> list[str]:
        rows = [dict(vendor_id=v["vendor_id"], supplier=v["name"], invoice=a["doc_no"], amount=a["amount"],
                     bank_last4=v["bank_acct"][-4:]) for v, a in self.open_invoices]
        paid = self.r.choice(self.rows["payments.csv"])
        v0, a0 = self.open_invoices[0]
        v1, a1 = self.open_invoices[1]
        v2, a2 = self.open_invoices[2]
        rows += [dict(rows[0]),                                                         # same invoice twice in the run
                 dict(vendor_id=paid["vendor_id"], supplier=paid["supplier"], invoice=paid["invoice_no"],
                      amount=paid["paid_amount"], bank_last4=paid["bank_last4"])]       # already paid
        rows[1]["bank_last4"] = f"{(int(v1['bank_acct'][-4:]) + 1111) % 10000:04d}"   # bank differs from master
        rows[2]["amount"] = f"{float(a2['amount']) + 250:.2f}"                          # more than the invoice
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["line", "vendor_id", "supplier", "invoice", "amount", "bank_last4"])
        for i, r in enumerate(rows, start=1):
            w.writerow([i, r["vendor_id"], r["supplier"], r["invoice"], r["amount"], r["bank_last4"]])
        return buf.getvalue().splitlines()

    # -------- writing it out
    def build(self, n_issues: int) -> tuple[dict[str, list[str]], list[dict]]:
        self.clean()
        for kind in self.r.sample(ISSUES, n_issues):
            self.issue(kind)
        files, where = {}, {}
        for name, head in HEADERS.items():
            rows = list(self.rows[name])
            datecol = {"payments.csv": "pay_date", "approvals.csv": "date", "vendors.csv": "vendor_id", "expenses.csv": "date"}[name]
            self.r.shuffle(rows)
            rows.sort(key=lambda x: x[datecol])
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(head)
            for i, row in enumerate(rows, start=2):
                w.writerow([row[h] for h in head])
                where[id(row)] = i
            files[name] = buf.getvalue().splitlines()
        self.r.shuffle(self.contracts)
        lines = []
        for pair in self.contracts:
            for item in pair:
                lines.append(item["text"])
                where[id(item)] = len(lines)
        files["contracts.txt"] = lines
        files["payment_run.csv"] = self.payment_run()
        key = []
        for n, k in enumerate(self.key, start=1):
            key.append(dict(id=f"K-{n:02d}", clause=k["clause"], area=k["area"], source_file=k["source_file"],
                            line_number=where[id(k["main"])],
                            related_lines=";".join(str(where[id(x)]) for x in k["related"]), description=k["description"]))
        return files, key


def generate(seed: int = 1, difficulty: str = "medium") -> tuple[dict[str, list[str]], list[dict]]:
    """Returns (files as exact lines, answer key rows)."""
    if difficulty not in COUNTS:
        raise ValueError("difficulty must be easy, medium or hard")
    return _Gen(seed, difficulty).build(COUNTS[difficulty])


def key_csv(key: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=KEY_HEADER, lineterminator="\n")
    w.writeheader()
    w.writerows(key)
    return buf.getvalue()


def to_zip(files: dict[str, list[str]], key: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, lines in files.items():
            z.writestr(name, "\n".join(lines) + "\n")
        z.writestr("answer_key.csv", key_csv(key))
    return buf.getvalue()
