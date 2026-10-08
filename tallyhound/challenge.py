"""Challenge generator: a fresh, fictional month of data with problems planted in it, plus the answer key.

Use it to test the engines on data they have never seen. Every planted problem is recorded with its file and line
numbers. So are planted TRAPS: things that look suspicious but are legitimate and common in real books - batch bank
transfers, rent paid without a PO, early-payment discounts, instalments, a voided and re-issued payment, bank fees
and payroll on the statement, day-first dates, supplier-name variants, team dinners, professional memberships,
weekend taxis on a business trip, remit-to vendor records, vendors closed after their last payment, invoices that
show a net total before tax. Any finding on a trap is a measured false alarm. "Hard" variants are
deliberately subtle, and a few are worded so that the fixed rules miss them - that is where an AI model can earn
its keep, and the scorecard shows whether it does.
"""
from __future__ import annotations

import csv
import io
import random
import zipfile
from datetime import date, timedelta
from typing import Any, cast

HEADERS = {
    "payments.csv": ["payment_id", "pay_date", "invoice_date", "vendor_id", "supplier", "invoice_no", "invoice_amount",
                     "paid_amount", "bank_last4", "approved_by"],
    "approvals.csv": ["record_id", "type", "doc_no", "date", "vendor", "amount", "requested_by", "approved_by",
                      "approver_role", "po_no"],
    "vendors.csv": ["vendor_id", "name", "tax_id", "status", "bank_acct", "bank_changed_on", "bank_verified", "created_on",
                    "w9_on_file", "last_paid_on", "last_paid_amount", "status_changed_on"],
    "expenses.csv": ["claim_id", "date", "employee", "category", "amount", "receipt_ref", "notes", "people"],
}
KEY_HEADER = ["id", "clause", "area", "source_file", "line_number", "related_lines", "description", "expect"]
PEOPLE = ["J. Okoro", "M. Lindqvist", "P. Herrera", "S. Nakamura", "D. Abara", "L. Fontaine", "R. Mehta", "T. Walsh"]
NAMES = ["Arden", "Bexley", "Calder", "Dunmore", "Ellery", "Fenwick", "Garrow", "Holloway", "Ilverton", "Jessamy",
         "Kestrel", "Lomond", "Marlby", "Northam", "Orchard", "Pembury", "Quarry", "Rushden", "Selby", "Thornbury",
         "Upton", "Varley", "Wexcombe", "Yarrow"]
TRADES = ["Components", "Plastics", "Packaging", "Fasteners", "Logistics", "Tooling", "Print", "Cleaning", "Office Supply",
          "Metals", "Software", "Electrical"]
ISSUES = ["pay_dup", "pay_over", "pay_noinv", "pay_early", "pay_weekend", "app_self", "app_nopo", "app_director",
          "app_split", "app_poafter", "ven_bank", "ven_inactive", "ven_w9", "ven_duptax", "exp_meal", "exp_norec",
          "exp_duprec", "exp_weekend", "exp_personal", "con_surcharge", "con_rate", "con_term", "con_renew",
          "bank_unrecorded", "bank_uncleared", "inv_bank", "inv_total", "inv_unknown", "inv_dup"]
COUNTS = {"easy": 8, "medium": 14, "hard": len(ISSUES)}
# added in 0.7: planted from their own random stream after everything else, so the problems above stay where they were
EXTRA_ISSUES = ["pay_neardup", "app_underlimit", "ven_newfast", "exp_underlimit"]
EXTRA_COUNTS = {"easy": 1, "medium": 2, "hard": len(EXTRA_ISSUES)}


class _Gen:
    def __init__(self, seed: int, difficulty: str, traps: bool = True):
        self.r = random.Random(seed)
        self.r2 = random.Random(seed * 7919 + 17)      # the 0.7 problems and traps
        self.difficulty = difficulty
        self.with_traps = traps
        self.dayfirst = False
        self.hard = difficulty == "hard"
        self.days = [date(2026, 9, d) for d in range(1, 31)]
        self.weekdays = [d for d in self.days if d.weekday() < 5]
        self.weekends = [d for d in self.days if d.weekday() >= 5]
        self.rows: dict[str, list[dict]] = {k: [] for k in HEADERS}
        self.contracts: list[list[dict]] = []      # pairs of text lines kept together
        self.key: list[dict] = []
        self.bank_extra: list[dict] = []           # bank lines with no recorded payment (planted)
        self.inv_issues: list[str] = []            # invoice-PDF problems, built after the clean month
        self.pdfs: dict[str, bytes] = {}
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
        self.key.append(dict(issue=issue, clause=clause, area=area, source_file=file, main=main, related=related,
                             description=desc, expect="problem"))

    def trap(self, kind: str, area: str, file: str, main: dict, related: list[dict], desc: str) -> None:
        self.key.append(dict(issue=kind, clause="-", area=area, source_file=file, main=main, related=related,
                             description=f"TRAP ({kind}): {desc}", expect="trap"))

    # -------- legitimate things that look suspicious
    def traps(self) -> None:
        r = self.r
        # batch: three supplier payments leave the bank as one transfer
        vs = r.sample(self.paid_vendors, 3)
        day = self.wd(8, 16)
        batch = []
        for v in vs:
            inv = self.approval(v["name"], self.amt(700, 4000), day - timedelta(days=4))
            p = self.payment(v, inv, pay_date=day.isoformat())
            p["_nobank"] = True
            batch.append(p)
        b = dict(date=(day + timedelta(days=1)).isoformat(), description=f"BACS BATCH {day:%d%b}".upper(),
                 amount=f"-{sum(float(p['paid_amount']) for p in batch):.2f}", reference=f"BATCH-{r.randint(100, 999)}")
        self.bank_extra.append(b)
        self.trap("batch", "Payments", "bank_statement.csv", b, [], "three payments in one bank transfer")
        for p in batch:                               # each batched payment is on the statement, inside the transfer
            self.trap("batch", "Payments", "payments.csv", p, [], f"{p['payment_id']} paid inside a batch transfer")
        # non-AP debits on the bank statement
        for desc, amt in (("BANK CHARGES SEP", 38.50), ("PAYROLL 2026-09", self.amt(40000, 60000)),
                          ("IRS EFTPS FEDERAL TAX", self.amt(8000, 15000)), ("AMEX CARD SETTLEMENT", self.amt(2000, 6000)),
                          ("TRANSFER TO SAVINGS ACCT 4471", 20000.00)):
            b = dict(date=self.wd(3, 28).isoformat(), description=desc, amount=f"-{amt:.2f}", reference="")
            self.bank_extra.append(b)
            self.trap("nonap", "Payments", "bank_statement.csv", b, [], desc.lower())
        # PO-exempt bills (rent, utilities, insurance); three utility bills for three sites on one day
        rent = self.approval("Northgate Property Rent", 8500.00, self.wd(1, 5), po_no="")
        ins = self.approval("Shield Insurance Co", self.amt(2600, 6000), self.wd(), po_no="")
        self.trap("poexempt", "Approvals", "approvals.csv", rent, [], "office rent, paid without a PO")
        self.trap("poexempt", "Approvals", "approvals.csv", ins, [], "insurance premium, paid without a PO")
        day = self.wd(8, 20)
        people = r.sample(PEOPLE, 3)
        bills = [self.approval("City Power & Light", self.amt(3500, 3900), day, po_no="", requested_by=people[i],
                               approved_by=people[(i + 1) % 3]) for i in range(3)]
        self.trap("poexempt", "Approvals", "approvals.csv", bills[0], bills[1:], "three site electricity bills, one supplier")
        # early-payment discount (paid 2% less) and a 40-cent rounding difference
        v = r.choice(self.paid_vendors)
        inv = self.approval(v["name"], 5000.00, self.wd(2, 12))
        p = self.payment(v, inv, paid_amount="4900.00")
        self.trap("discount", "Payments", "payments.csv", p, [], "2% early-payment discount")
        v = r.choice(self.paid_vendors)
        inv = self.approval(v["name"], self.amt(1000, 5000), self.wd(2, 12))
        p = self.payment(v, inv, paid_amount=f"{float(inv['amount']) + 0.40:.2f}")
        self.trap("rounding", "Payments", "payments.csv", p, [], "40-cent rounding difference")
        # instalments: one invoice paid in two halves
        v = r.choice(self.paid_vendors)
        inv = self.approval(v["name"], 6000.00, self.wd(1, 8))
        p1 = self.payment(v, inv, paid_amount="3000.00")
        p2 = self.payment(v, inv, paid_amount="3000.00", pay_date=self.wd(16, 22).isoformat())
        self.trap("instalment", "Payments", "payments.csv", p2, [p1], "invoice paid in two instalments")
        # void and re-issue
        v = r.choice(self.paid_vendors)
        inv = self.approval(v["name"], self.amt(1500, 4500), self.wd(2, 8))
        a = self.payment(v, inv)
        later = [d for d in self.weekdays if d > date.fromisoformat(a["pay_date"])]
        void = self.payment(v, inv, paid_amount=f"-{float(inv['amount']):.2f}", pay_date=later[0].isoformat())
        again = self.payment(v, inv, pay_date=later[min(2, len(later) - 1)].isoformat())
        self.trap("void", "Payments", "payments.csv", again, [a, void], "payment voided and re-issued")
        # day-first dates in the expenses export
        self.dayfirst = True
        # supplier-name variants: an open invoice paid on the run under another spelling (see payment_run)
        v = r.choice(self.paid_vendors)
        self.variant_inv = (v, self.approval(v["name"], self.amt(800, 4000), self.wd(18, 26)))
        # team dinner with the head count only in the notes; professional membership; weekend taxi on a trip
        e = self.expense(category="MEAL", amount="264.00", people="", notes="Team dinner - 5 people")
        self.trap("team_dinner", "Expenses", "expenses.csv", e, [], "team dinner, 5 people")
        e = self.expense(category="TRAINING", amount="185.00", notes="Annual CPA society membership")
        self.trap("membership", "Expenses", "expenses.csv", e, [], "professional membership")
        who = r.choice(PEOPLE)
        fri = r.choice([d for d in self.weekdays if d.weekday() == 4 and d.day < 26])
        trip = self.expense(employee=who, category="TRAVEL", amount="420.00", date=fri.isoformat(), notes="Flight to client site")
        taxi = self.expense(employee=who, category="TAXI", amount="46.00", date=(fri + timedelta(days=1)).isoformat(), notes="")
        self.trap("weekend_travel", "Expenses", "expenses.csv", taxi, [trip], "Saturday taxi during a business trip")
        # remit-to record sharing the supplier's tax ID; vendor closed after its final payment
        base = r.choice(self.paid_vendors)
        remit = self.vendor(name=f"{base['name']} - REMIT TO LEEDS", tax_id=base["tax_id"])
        self.trap("remit", "Vendors", "vendors.csv", remit, [base], "remit-to address record")
        closed = self.vendor(status="INACTIVE", last_paid_on="2026-09-04", status_changed_on="2026-09-20")
        self.trap("closed", "Vendors", "vendors.csv", closed, [], "vendor closed after its last payment")

    # -------- the clean month
    def clean(self) -> None:
        self.paid_vendors = [self.vendor() for _ in range(10)]
        for v in self.paid_vendors:
            for _ in range(2):
                inv = self.approval(v["name"], self.amt(600, 9500), self.wd(1, 24))
                self.payment(v, inv)
        self.clean_payments = list(self.rows["payments.csv"])
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
            vname = self.names.pop()
            self.contract_pair(vname, "Clause 2.1", "Prices are fixed for the term as per the schedule.",
                               f"Line 1: Contracted supply {self.amt(1000, 6000):.2f}")

    # -------- the planted problems
    def issue(self, kind: str) -> None:
        r, hard = self.r, self.hard
        v: Any                 # a vendor record in some branches, a vendor name in others
        if kind == "pay_dup":
            v = r.choice(self.paid_vendors)
            orig = r.choice([p for p in self.rows["payments.csv"] if p["vendor_id"] == v["vendor_id"]])
            dup = dict(orig, payment_id=f"P-{self.nxt('pay')}", invoice_no=orig["invoice_no"].lower() if hard else orig["invoice_no"])
            self.rows["payments.csv"].append(dup)
            self.plant(kind, "5.2", "Payments", "payments.csv", dup, [orig], f"{orig['invoice_no']} paid twice")
        elif kind == "pay_over":
            v = r.choice(self.paid_vendors)
            inv = self.approval(v["name"], self.amt(1500, 8000), self.wd(1, 20))
            extra = 12.5 if hard else round(r.uniform(150, 900), 2)
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
            e = self.expense(date=r.choice(self.weekends).isoformat(), notes="", category="SUPPLIES")
            self.plant(kind, "6.4", "Expenses", "expenses.csv", e, [], "Weekend claim with no reason")
        elif kind == "exp_personal":
            note = "Yoga classes for myself" if hard else "Monthly gym membership"
            e = self.expense(category="SUPPLIES", amount=f"{self.amt(40, 120):.2f}", notes=note)
            self.plant(kind, "6.3", "Expenses", "expenses.csv", e, [], f"Personal item claimed: {note.lower()}")
        elif kind.startswith("inv_"):
            self.inv_issues.append(kind)
        elif kind == "bank_unrecorded":
            day = self.wd(8, 28)
            who = r.choice(PEOPLE) if hard else self.names.pop()
            b = dict(date=day.isoformat(), description=f"TRANSFER {who.upper()}", amount=f"-{self.amt(900, 4800):.2f}",
                     reference=f"TRF{r.randint(100000, 999999)}")
            self.bank_extra.append(b)
            self.plant(kind, "5.5", "Payments", "bank_statement.csv", b, [], f"Bank transfer to {who} with no recorded payment")
        elif kind == "bank_uncleared":
            v = r.choice(self.paid_vendors)
            # early enough that the statement runs well past it: a payment younger than the clearing window is
            # still on its way, and no check (or person) should call it missing
            inv = self.approval(v["name"], self.amt(900, 7000), self.wd(5, 10))
            p = self.payment(v, inv)
            p["_nobank"] = True
            self.plant(kind, "5.5", "Payments", "payments.csv", p, [], f"{p['payment_id']} recorded but not on the bank statement")
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

    # -------- problems and traps added in 0.7 (own random stream)
    def extra_issue(self, kind: str) -> None:
        r, hard = self.r2, self.hard
        if kind == "pay_neardup":
            v = r.choice(self.paid_vendors)
            orig = r.choice([p for p in self.clean_payments if p["vendor_id"] == v["vendor_id"]])
            no = orig["invoice_no"]
            pre, num = no.rsplit("-", 1)
            twin = f"{pre}-{num[0]}{num[2]}{num[1]}{num[3:]}" if hard and len(num) >= 3 and num[1] != num[2] else f"{no}A"
            later = [d for d in self.weekdays if d > date.fromisoformat(orig["pay_date"])][:4] or [self.weekdays[-1]]
            dup = {k: x for k, x in orig.items() if not k.startswith("_")}
            dup.update(payment_id=f"P-{self.nxt('pay')}", invoice_no=twin, pay_date=r.choice(later).isoformat())
            self.rows["payments.csv"].append(dup)
            self.plant(kind, "5.6", "Payments", "payments.csv", dup, [orig], f"{no} paid again as {twin}")
        elif kind == "app_underlimit":
            who = r.choice(PEOPLE)
            lim = 10000.0
            parts = [self.approval(self.names.pop(), round(r.uniform(lim * 0.965, lim - 1), 2), self.wd(), requested_by=who,
                                   approved_by=r.choice([p for p in PEOPLE if p != who])) for _ in range(2)]
            self.plant(kind, "1.6", "Approvals", "approvals.csv", parts[-1], parts[:-1],
                       f"{who} raised two approvals just under the director limit")
        elif kind == "ven_newfast":
            made = self.wd(1, 12)
            v = self.vendor(created_on=made.isoformat(), last_paid_on=(made + timedelta(days=r.randint(3, 12))).isoformat(),
                            last_paid_amount=f"{(10400.0 if hard else r.uniform(14000, 38000)):.2f}")
            self.plant(kind, "4.5", "Vendors", "vendors.csv", v, [], f"{v['vendor_id']} paid a large amount soon after set-up")
        elif kind == "exp_underlimit":
            who = self.under_who = r.choice(PEOPLE)
            claims = [self.expense(employee=who, category="PARKING" if hard else "SUPPLIES", amount=f"{r.uniform(22.6, 24.99):.2f}",
                                   receipt_ref="", notes="Business purpose recorded") for _ in range(3)]
            self.plant(kind, "6.6", "Expenses", "expenses.csv", claims[-1], claims[:-1],
                       f"{who} claimed three times just under the receipt limit")

    def extra_traps(self) -> None:
        r = self.r2
        # a weekly bill: same amount, numbers in series, a week apart
        v = r.choice(self.paid_vendors)
        start = self.wd(1, 8)
        amount = self.amt(300, 900)
        series = []
        for k in range(3):
            day = start + timedelta(days=7 * k)
            inv = self.approval(v["name"], amount, day, doc_no=f"WK-{4100 + k}")
            series.append(self.payment(v, inv))
        self.trap("series", "Payments", "payments.csv", series[-1], series[:-1], "weekly bill, same amount, numbers in series")
        # two licences for two sites: same supplier, same amount, same day, consecutive numbers
        v = r.choice(self.paid_vendors)
        day = self.wd(5, 20)
        a = self.payment(v, self.approval(v["name"], 1250.00, day, doc_no="LIC-7730"))
        b = self.payment(v, self.approval(v["name"], 1250.00, day, doc_no="LIC-7731"))
        self.trap("two_sites", "Payments", "payments.csv", b, [a], "two site licences, same amount and day")
        # a new vendor paid a small amount soon after set-up
        made = self.wd(2, 10)
        n = self.vendor(created_on=made.isoformat(), last_paid_on=(made + timedelta(days=5)).isoformat(),
                        last_paid_amount=f"{self.amt(400, 2400):.2f}")
        self.trap("new_small", "Vendors", "vendors.csv", n, [], "new vendor, small first payment")
        # parking just under the receipt limit, twice: not a pattern yet
        who = r.choice([x for x in PEOPLE if x != getattr(self, "under_who", None)])
        p = [self.expense(employee=who, category="PARKING", amount="24.00", receipt_ref="", notes="Client visit parking")
             for _ in range(2)]
        self.trap("parking", "Expenses", "expenses.csv", p[-1], p[:-1], "two parking claims just under the receipt limit")

    # -------- supplier invoice PDFs: one per open invoice, some with planted problems
    def invoice_docs(self) -> list[tuple[str, list[str], str | None]]:
        docs = []

        def doc(v, no, day, po, total, acct):
            return [v["name"], f"From: {v['name']}", f"Invoice No: {no}", f"Invoice date: {day}", f"PO: {po}",
                    "Description: Goods as ordered", f"Total due: ${total:,.2f}", f"Pay to account: ****{acct}"]
        issues = list(self.inv_issues)
        for i, (v, a) in enumerate(self.open_invoices):
            acct, total, kind = v["bank_acct"][-4:], float(a["amount"]), None
            if issues and i >= 3:                    # open invoices 0-2 are also on the payment run; keep those clean
                kind = issues.pop(0)
                if kind == "inv_bank":
                    acct = f"{(int(acct) + (1 if self.hard else 4321)) % 10000:04d}"
                elif kind == "inv_total":
                    total += 0.1 if self.hard else round(self.r.uniform(120, 600), 2)
                elif kind == "inv_unknown":
                    a = dict(a, doc_no=f"{a['doc_no']}-X")
            lines = doc(v, a["doc_no"], a["date"], a["po_no"], total, acct)
            if self.with_traps and i == 0:            # trap: net total before tax shown above the total due
                net = round(total / 1.2, 2)
                lines[6:6] = [f"Net total: ${net:,.2f}", f"VAT 20%: ${total - net:,.2f}"]
                kind = "trap_net_total"
            docs.append((f"{a['doc_no']}.pdf", lines, kind))
            if kind == "inv_dup":
                docs.append((f"{a['doc_no']}-copy.pdf", doc(v, a["doc_no"], a["date"], a["po_no"], total, acct), "inv_dup_copy"))
        return docs

    # -------- the bank statement: one debit per recorded payment, a day or two later
    def bank_rows(self) -> list[dict]:
        out = []
        for p in self.rows["payments.csv"]:
            if p.get("_nobank"):
                continue
            d = date.fromisoformat(p["pay_date"]) + timedelta(days=self.r.randint(0, 2))
            amt = float(p["paid_amount"])
            out.append(dict(date=d.isoformat(), description=f"{'PAYMENT' if amt > 0 else 'RETURNED PAYMENT'} {p['supplier'].upper()}",
                            amount=f"{-amt:.2f}", reference=p["payment_id"]))
        return out + self.bank_extra

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
        if self.with_traps:                                                             # trap: same supplier, other spelling
            v4, a4 = self.variant_inv
            rows.append(dict(vendor_id=v4["vendor_id"], supplier=v4["name"].upper() + " LTD", invoice=a4["doc_no"],
                             amount=a4["amount"], bank_last4=v4["bank_acct"][-4:]))
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
        if self.with_traps:
            self.traps()
        for kind in self.r2.sample(EXTRA_ISSUES, EXTRA_COUNTS[self.difficulty]):
            self.extra_issue(kind)
        if self.with_traps:
            self.extra_traps()
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
                vals = [row.get(h, "") for h in head]
                if name == "expenses.csv" and self.dayfirst:
                    d = date.fromisoformat(row["date"])
                    vals[head.index("date")] = f"{d.day:02d}/{d.month:02d}/{d.year}"
                w.writerow(vals)
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
        from . import invoices
        docs = self.invoice_docs()
        for name, lines_, _ in docs:
            self.pdfs[name] = invoices.render_pdf(lines_)
        # our own small generated PDFs are never refused, so pdf_lines gives a list here, never None
        extracted = sorted((name, cast("list[str]", invoices.pdf_lines(self.pdfs[name]))) for name, _, _ in docs)
        files[invoices.NAME] = invoices.combine(extracted)
        start = {}
        for n, ln in enumerate(files[invoices.NAME], start=1):
            m = invoices.HEAD.match(ln)
            if m:
                start[m.group(1)] = n

        def find(doc_name: str, prefix: str) -> int:
            i = start[doc_name]
            while not files[invoices.NAME][i].startswith(prefix):
                i += 1
            return i + 1
        inv_key = []
        for name, _, doc_kind in docs:
            no_line = find(name, "Invoice No")
            if doc_kind == "inv_bank":
                inv_key.append(("8.2", find(name, "Pay to account"), [no_line], f"{name}: bank details differ from the vendor master"))
            elif doc_kind == "inv_total":
                inv_key.append(("8.1", find(name, "Total due"), [no_line], f"{name}: total differs from the approved amount"))
            elif doc_kind == "inv_unknown":
                inv_key.append(("8.1", no_line, [], f"{name}: invoice has no approval record"))
            elif doc_kind == "inv_dup":
                inv_key.append(("8.3", find(name.replace(".pdf", "-copy.pdf"), "Invoice No"), [no_line], f"{name} submitted twice"))
            elif doc_kind == "trap_net_total":
                inv_key.append(("-", find(name, "Net total"), [find(name, "Total due"), no_line],
                                f"TRAP (net_total): {name} shows the net total before tax"))
        bank = self.bank_rows()
        self.r.shuffle(bank)
        bank.sort(key=lambda x: x["date"])
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["date", "description", "amount", "reference"])
        for i, row in enumerate(bank, start=2):
            w.writerow([row["date"], row["description"], row["amount"], row["reference"]])
            where[id(row)] = i
        files["bank_statement.csv"] = buf.getvalue().splitlines()
        key = []
        for n, k in enumerate(self.key, start=1):
            key.append(dict(id=f"K-{n:02d}", clause=k["clause"], area=k["area"], source_file=k["source_file"],
                            line_number=where[id(k["main"])],
                            related_lines=";".join(str(where[id(x)]) for x in k["related"]), description=k["description"],
                            expect=k["expect"]))
        for clause, line, rel, desc in inv_key:
            key.append(dict(id=f"K-{len(key) + 1:02d}", clause=clause, area="Invoices", source_file=invoices.NAME,
                            line_number=line, related_lines=";".join(map(str, rel)), description=desc,
                            expect="trap" if desc.startswith("TRAP") else "problem"))
        return files, key


def generate_full(seed: int = 1, difficulty: str = "medium", traps: bool = True
                  ) -> tuple[dict[str, list[str]], list[dict], dict[str, bytes]]:
    """Like generate, plus the invoice PDFs (name -> bytes) for writing a zip."""
    if difficulty not in COUNTS:
        raise ValueError("difficulty must be easy, medium or hard")
    g = _Gen(seed, difficulty, traps)
    files, key = g.build(COUNTS[difficulty])
    return files, key, g.pdfs


def generate(seed: int = 1, difficulty: str = "medium", traps: bool = True) -> tuple[dict[str, list[str]], list[dict]]:
    """Returns (files as exact lines, answer key rows). traps=False gives the old, clean-background challenge."""
    if difficulty not in COUNTS:
        raise ValueError("difficulty must be easy, medium or hard")
    return _Gen(seed, difficulty, traps).build(COUNTS[difficulty])


def key_csv(key: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=KEY_HEADER, lineterminator="\n")
    w.writeheader()
    w.writerows(key)
    return buf.getvalue()


def to_zip(files: dict[str, list[str]], key: list[dict], pdfs: dict[str, bytes] | None = None) -> bytes:
    """A zip as a person would upload it: CSVs, contracts.txt, invoice PDFs in invoices/, and answer_key.csv."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, lines in files.items():
            if name == "invoices.txt" and pdfs:
                continue
            z.writestr(name, "\n".join(lines) + "\n")
        for name, data in (pdfs or {}).items():
            z.writestr(f"invoices/{name}", data)
        z.writestr("answer_key.csv", key_csv(key))
    return buf.getvalue()
