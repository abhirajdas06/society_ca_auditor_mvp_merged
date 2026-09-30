"""L. Reports tie back to the ledger rows."""

from datetime import date
from decimal import Decimal as D

from django.db.models import Sum
from django.test import TestCase

from . import reports
from .models import Bill, BillLine, Receipt
from .services import bounce_receipt, reverse_allocation, society_ledger_totals
from .testing import LedgerFixture


class ReportTieOutTests(LedgerFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.apr_bill = self.issued_bill(self.apr)
        self.apr_bill2 = self.issued_bill(self.apr, self.flat2)
        self.may_bill = self.issued_bill(self.may)
        main = self.line(self.apr_bill, self.main)
        water = self.line(self.apr_bill, self.water)
        self.r1 = self.receipt("1000", on=date(2026, 5, 10), allocations=[{"bill_line_id": main.pk, "amount": "800"}])  # 200 advance
        self.r2 = self.receipt("150", on=date(2026, 5, 12), mode=Receipt.CHEQUE, allocations=[{"bill_line_id": water.pk, "amount": "150"}])
        self.r3 = self.receipt("300", on=date(2026, 5, 15), flat=self.flat2,
                               allocations=[{"bill_line_id": self.line(self.apr_bill2, self.main).pk, "amount": "300"}])
        bounce_receipt(self.r2, self.user, "returned")
        reverse_allocation(self.r3.allocations.get(), D("100"), self.user, "wrong split")

    def test_bill_register_ties_to_bill_lines(self):
        data = reports.bill_register(self.apr)
        line_total = BillLine.objects.filter(bill__period=self.apr).exclude(bill__status=Bill.CANCELLED).aggregate(t=Sum("amount"))["t"]
        self.assertEqual(data["totals"]["bill_amount"], line_total)
        self.assertEqual(sum(data["totals"]["heads"]) + data["totals"]["interest"], line_total)
        for row in data["rows"]:
            self.assertEqual(row["payable"] - row["paid"], row["balance"])

    def test_may_register_arrears_point_to_april_and_balance_matches_outstanding(self):
        data = reports.bill_register(self.may)
        row = next(r for r in data["rows"] if r["bill"].flat_id == self.flat.pk)
        self.assertEqual(row["principal_arrear"], D("1200.00") - D("0"))  # snapshot at May generation (before receipts)
        outstanding = next(r for r in reports.outstanding_by_flat(self.society)["rows"] if r["flat"].pk == self.flat.pk)
        self.assertEqual(row["balance"], outstanding["balance"])

    def test_collection_sheet_ties_to_receipts(self):
        data = reports.collection_sheet(self.may, date(2026, 5, 1), date(2026, 5, 31))
        effective = Receipt.objects.filter(receipt_date__range=(date(2026, 5, 1), date(2026, 5, 31)), status__in=Receipt.EFFECTIVE_STATUSES)
        self.assertEqual(data["total_received"], effective.aggregate(t=Sum("amount"))["t"])
        self.assertEqual(data["total_received"], D("1300.00"))

    def test_flat_statement_reconstructs_balance(self):
        st = reports.flat_statement(self.flat)
        component_balance = sum(c["balance"] for c in st["components"])
        self.assertEqual(st["closing"], component_balance)
        self.assertEqual(st["opening"] + st["billed"] + st["interest"] - st["payments"], st["closing"])
        kinds = [e["kind"] for e in st["events"]]
        self.assertIn("bounce", kinds, "bounced receipt must stay visible in history")
        self.assertEqual(st["advance_total"], D("200.00"))

    def test_statement_opening_balance_with_date_filter(self):
        st = reports.flat_statement(self.flat, date_from=date(2026, 5, 1))
        self.assertEqual(st["opening"], D("1200.00"))
        self.assertEqual(st["opening"] + st["billed"] + st["interest"] - st["payments"], st["closing"])

    def test_advance_register_ties_to_receipt_residuals(self):
        data = reports.advance_register(self.society)
        self.assertEqual(data["total"], D("200.00") + D("100.00"))
        self.assertEqual({r["receipt"].pk for r in data["rows"]}, {self.r1.pk, self.r3.pk})

    def test_receipt_register_excludes_bounced_from_effective_totals(self):
        data = reports.receipt_register(self.society)
        self.assertEqual(data["totals"]["amount"], D("1300.00"))
        self.assertEqual(data["totals"]["ineffective"], D("150.00"))
        self.assertEqual(data["totals"]["amount"], data["totals"]["allocated"] + data["totals"]["advance"])

    def test_dashboard_and_charge_head_summary_agree_with_ledger(self):
        payable, paid, outstanding = society_ledger_totals(self.society)
        dash = reports.dashboard(self.society)
        heads = reports.charge_head_summary(self.society)
        out = reports.outstanding_by_flat(self.society)
        self.assertEqual(dash["outstanding"], outstanding)
        self.assertEqual(heads["totals"]["balance"], outstanding)
        self.assertEqual(out["totals"]["balance"], outstanding)
        self.assertEqual(paid, D("1000.00"))  # 800 (r1) + 200 (r3 after reversal); bounced r2 excluded
        self.assertEqual(dash["advance"], D("300.00"))
