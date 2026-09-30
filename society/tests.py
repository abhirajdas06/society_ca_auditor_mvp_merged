"""Ledger invariants — acceptance matrix A–I, K, N (CLAUDE_MASTER_PROMPT.md §32)."""

from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from .models import (
    AuditLog,
    Bill,
    BillingPeriod,
    BillLine,
    Flat,
    FlatChargeRule,
    InterestSegment,
    Receipt,
    ReceiptAllocation,
    ReceiptAllocationReversal,
    Society,
    Wing,
)
from .services import (
    allocate_receipt,
    approve_period,
    auto_allocate_receipt,
    bounce_receipt,
    cancel_bill,
    cancel_receipt,
    clear_receipt,
    confirm_variable_line,
    daily_simple_interest,
    generate_bill,
    generate_period_bills,
    issue_bill,
    lock_period,
    reallocate_receipt,
    receipt_allocated,
    reverse_allocation,
    simple_interest,
    submit_period_for_review,
)
from .testing import LedgerFixture


def alloc(*pairs):
    return [{"bill_line_id": line.pk, "amount": D(str(amount))} for line, amount in pairs]


class FixedChargeGenerationTests(LedgerFixture, TestCase):
    """A. Fixed charge generation and effective-dated rules."""

    def test_fixed_monthly_charges_appear_in_draft_bill(self):
        bill = generate_bill(self.apr, self.flat, self.maker)
        self.assertEqual(bill.status, Bill.DRAFT)
        self.assertEqual(self.line(bill, self.main).amount, D("800.00"))
        self.assertEqual(self.line(bill, self.water).amount, D("200.00"))
        self.assertTrue(self.line(bill, self.main).confirmed)
        self.assertEqual(self.line(bill, self.main).component_code, "MAIN-APR-2026")

    def test_effective_dated_change_applies_from_correct_period_and_never_rewrites_history(self):
        FlatChargeRule.objects.filter(flat=self.flat, charge_head=self.main).update(effective_to=date(2026, 4, 30))
        self.rule(self.flat, self.main, amount="850", start=date(2026, 5, 1))
        april = self.issued_bill(self.apr)
        may = self.issued_bill(self.may)
        self.assertEqual(self.line(april, self.main).amount, D("800.00"))
        self.assertEqual(self.line(may, self.main).amount, D("850.00"))
        self.assertEqual(self.line(may, self.main).component_code, "MAIN-MAY-2026")
        april.refresh_from_db()
        self.assertEqual(self.line(april, self.main).amount, D("800.00"))

    def test_future_rule_is_never_used(self):
        FlatChargeRule.objects.filter(flat=self.flat, charge_head=self.water).update(effective_from=date(2026, 6, 1))
        bill = generate_bill(self.apr, self.flat, self.maker)
        self.assertFalse(bill.lines.filter(charge_head=self.water).exists())

    def test_expired_rule_is_never_used(self):
        FlatChargeRule.objects.filter(flat=self.flat, charge_head=self.water).update(effective_to=date(2026, 4, 30))
        bill = generate_bill(self.may, self.flat, self.maker)
        self.assertFalse(bill.lines.filter(charge_head=self.water).exists())

    def test_overlapping_rules_are_rejected(self):
        overlapping = FlatChargeRule(flat=self.flat, charge_head=self.main, configured_amount=D("900"), effective_from=date(2026, 5, 1))
        with self.assertRaises(ValidationError):
            overlapping.full_clean()

    def test_regenerating_returns_existing_live_bill(self):
        first = generate_bill(self.apr, self.flat, self.maker)
        self.assertEqual(generate_bill(self.apr, self.flat, self.maker).pk, first.pk)


class VariableChargeTests(LedgerFixture, TestCase):
    """B. Variable charge control."""

    def test_variable_line_starts_unconfirmed_with_quantity_rate_amount(self):
        bill = generate_bill(self.apr, self.flat, self.maker)
        park = self.line(bill, self.park)
        self.assertFalse(park.confirmed)
        self.assertEqual((park.quantity, park.rate, park.amount), (D("2.00"), D("100.00"), D("200.00")))

    def test_bill_cannot_be_issued_or_period_submitted_while_review_incomplete(self):
        generate_period_bills(self.apr, self.maker)
        bill = self.apr.bills.get(flat=self.flat)
        with self.assertRaises(ValidationError):
            issue_bill(bill, self.user)
        with self.assertRaises(ValidationError):
            submit_period_for_review(self.apr, self.maker)

    def test_confirmation_is_audited_with_user_and_time(self):
        bill = generate_bill(self.apr, self.flat, self.maker)
        park = confirm_variable_line(self.line(bill, self.park), self.maker, D("3"), D("100"))
        self.assertEqual(park.amount, D("300.00"))
        self.assertEqual(park.confirmed_by, self.maker)
        self.assertIsNotNone(park.confirmed_at)
        entry = AuditLog.objects.get(action="variable_line_confirmed", object_id=str(park.pk))
        self.assertEqual(entry.user, self.maker)
        self.assertEqual(entry.details["before"]["amount"], "200.00")
        self.assertEqual(entry.details["after"]["amount"], "300.00")

    def test_confirmed_lines_on_issued_bill_cannot_be_changed(self):
        bill = self.issued_bill(self.apr)
        with self.assertRaises(ValidationError):
            confirm_variable_line(self.line(bill, self.park), self.maker, D("9"), D("100"))


class AllocationTests(LedgerFixture, TestCase):
    """C–G. Partial, split, multiple, multi-month, advance."""

    def test_partial_receipt_leaves_balance(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        receipt = self.receipt("300", allocations=alloc((main, 300)))
        self.assertEqual((main.amount, main.paid, main.balance), (D("800.00"), D("300.00"), D("500.00")))
        main.refresh_from_db()
        self.assertEqual(main.amount, D("800.00"), "billed demand must never change")
        self.assertEqual(receipt.unallocated_amount, D("0.00"))

    def test_split_receipt_principal_and_interest(self):
        bill = self.issued_bill(self.apr)
        main, interest = self.line(bill, self.main), self.interest_line(bill)
        receipt = self.receipt("350", allocations=alloc((main, 300), (interest, 50)))
        self.assertEqual((main.amount, main.paid, main.balance), (D("800"), D("300"), D("500")))
        self.assertEqual((interest.amount, interest.paid, interest.balance), (D("120"), D("50"), D("70")))
        self.assertEqual((receipt.amount, receipt.allocated_amount, receipt.unallocated_amount), (D("350"), D("350"), D("0")))

    def test_three_receipts_fully_settle_one_component_as_separate_records(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        for amount in ("300", "200", "300"):
            self.receipt(amount, allocations=alloc((main, amount)))
        self.assertEqual((main.paid, main.balance), (D("800"), D("0")))
        self.assertEqual(main.receipt_allocations.count(), 3)
        self.assertEqual(Receipt.objects.count(), 3)

    def test_one_receipt_settles_multiple_months(self):
        apr, may = self.issued_bill(self.apr), self.issued_bill(self.may)
        lines = [self.line(apr, self.main), self.line(apr, self.water), self.line(may, self.main), self.line(may, self.water)]
        receipt = self.receipt("2000", allocations=alloc(*[(l, l.amount) for l in lines]))
        self.assertTrue(all(l.balance == 0 for l in lines))
        self.assertEqual(receipt.unallocated_amount, D("0"))

    def test_excess_becomes_advance_and_later_allocation_reduces_it(self):
        apr = self.issued_bill(self.apr)
        receipt = self.receipt("1500", allocations=alloc((self.line(apr, self.main), 800), (self.line(apr, self.water), 200)))
        self.assertEqual(receipt.unallocated_amount, D("500.00"))
        self.assertFalse(BillLine.objects.filter(amount__lt=0).exists(), "advance must never create a negative bill line")
        may = self.issued_bill(self.may)
        allocate_receipt(receipt, alloc((self.line(may, self.main), 350)), self.user)
        self.assertEqual(receipt.unallocated_amount, D("150.00"))

    def test_auto_principal_then_interest_orders_by_period_head_priority_then_interest(self):
        apr = self.issued_bill(self.apr)
        interest = self.interest_line(apr, "120")
        receipt = self.receipt("1400")
        auto_allocate_receipt(receipt, self.user, Society.PRINCIPAL_THEN_INTEREST)
        rows = list(receipt.allocations.order_by("id").values_list("bill_line__component_code", "amount", "mode"))
        self.assertEqual(
            [(c, a) for c, a, _ in rows],
            [("MAIN-APR-2026", D("800.00")), ("WATER-APR-2026", D("200.00")), ("2W-APR-2026", D("200.00")), ("INT-APR-2026", D("120.00"))],
        )
        self.assertTrue(all(m == ReceiptAllocation.AUTO for _, _, m in rows), "auto knock-off still posts ordinary allocation rows")
        self.assertEqual((interest.paid, receipt.unallocated_amount), (D("120.00"), D("80.00")))

    def test_member_directed_policy_refuses_silent_auto_allocation(self):
        self.issued_bill(self.apr)
        receipt = self.receipt("100")
        with self.assertRaises(ValidationError):
            auto_allocate_receipt(receipt, self.user)

    def test_explicit_allocation_precedes_auto_policy(self):
        apr = self.issued_bill(self.apr)
        water = self.line(apr, self.water)
        r = self.receipt("300", allocations=alloc((water, 200)))
        auto_allocate_receipt(r, self.user, Society.OLDEST_DUE)
        self.assertEqual(water.paid, D("200"))
        self.assertEqual(r.allocations.filter(mode=ReceiptAllocation.MANUAL).count(), 1)


class RejectionTests(LedgerFixture, TestCase):
    """§22 rejection list."""

    def test_allocation_greater_than_receipt_remaining(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        r = self.receipt("500")
        with self.assertRaises(ValidationError):
            allocate_receipt(r, alloc((main, 501)), self.user)

    def test_allocation_greater_than_target_balance(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        r = self.receipt("900")
        with self.assertRaises(ValidationError):
            allocate_receipt(r, alloc((main, 801)), self.user)
        self.assertEqual(r.allocations.count(), 0)

    def test_duplicate_targets_in_one_request_are_summed_before_checking(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        r = self.receipt("900")
        with self.assertRaises(ValidationError):
            allocate_receipt(r, alloc((main, 500), (main, 400)), self.user)

    def test_cross_flat_allocation(self):
        other = self.line(self.issued_bill(self.apr, self.flat2), self.main)
        r = self.receipt("100")
        with self.assertRaises(ValidationError):
            allocate_receipt(r, alloc((other, 100)), self.user)

    def test_cross_society_allocation(self):
        s2 = Society.objects.create(name="Other CHS")
        flat = Flat.objects.create(society=s2, wing=Wing.objects.create(society=s2, code="A"), unit_no="A001")
        head = self.head("MAINT", "Maintenance", "fixed", "MAIN", 10, society=s2)
        self.rule(flat, head, amount="100")
        other_bill = generate_bill(self.period("Apr", date(2026, 4, 1), society=s2), flat, self.maker)
        issue_bill(other_bill, self.user)
        r = self.receipt("100")
        with self.assertRaises(ValidationError):
            allocate_receipt(r, alloc((other_bill.lines.get(), 100)), self.user)

    def test_draft_bill_line_is_not_receivable(self):
        draft = generate_bill(self.apr, self.flat, self.maker)
        r = self.receipt("100")
        with self.assertRaises(ValidationError):
            allocate_receipt(r, alloc((self.line(draft, self.main), 100)), self.user)

    def test_bounced_and_cancelled_receipts_cannot_be_allocated(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        bounced = self.receipt("100", mode=Receipt.CHEQUE)
        bounce_receipt(bounced, self.user, "returned")
        cancelled = self.receipt("100")
        cancel_receipt(cancelled, self.user, "duplicate entry")
        for r in (bounced, cancelled):
            with self.assertRaises(ValidationError):
                allocate_receipt(r, alloc((main, 50)), self.user)

    def test_reversal_greater_than_effective_allocation(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        r = self.receipt("300", allocations=alloc((main, 300)))
        a = r.allocations.get()
        reverse_allocation(a, D("200"), self.user, "first")
        with self.assertRaises(ValidationError):
            reverse_allocation(a, D("101"), self.user, "too much")

    def test_negative_or_non_numeric_amounts(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        r = self.receipt("300")
        for bad in ("-5", "abc"):
            with self.assertRaises(ValidationError):
                allocate_receipt(r, [{"bill_line_id": main.pk, "amount": bad}], self.user)

    def test_receipt_amount_must_be_positive_and_cheque_needs_number(self):
        with self.assertRaises(ValidationError):
            self.receipt("0")
        with self.assertRaises(ValidationError):
            create_r = Receipt(society=self.society, flat=self.flat, payment_mode=Receipt.CHEQUE, amount=D("10"))
            from .services import create_receipt

            create_receipt(create_r, self.user)


class ReallocationTests(LedgerFixture, TestCase):
    """H. Reallocation keeps every posted event."""

    def test_spec_example_300_50_corrected_to_250_100(self):
        bill = self.issued_bill(self.apr)
        main, interest = self.line(bill, self.main), self.interest_line(bill)
        r = self.receipt("350", allocations=alloc((main, 300), (interest, 50)))
        original = main.receipt_allocations.get()
        reallocate_receipt(r, alloc((main, 250), (interest, 100)), self.user, "Correct payer instruction")

        self.assertEqual(main.paid, D("250"))
        self.assertEqual(interest.paid, D("100"))
        self.assertEqual(r.unallocated_amount, D("0"))
        # original 300 kept, one 50 reversal against it, and a new 50 interest allocation
        original.refresh_from_db()
        self.assertEqual(original.amount, D("300.00"))
        self.assertEqual(list(original.reversals.values_list("amount", flat=True)), [D("50.00")])
        self.assertEqual(list(interest.receipt_allocations.order_by("id").values_list("amount", flat=True)), [D("50.00"), D("50.00")])
        self.assertEqual(interest.receipt_allocations.order_by("id").last().mode, ReceiptAllocation.REALLOCATION)
        entry = AuditLog.objects.get(action="receipt_reallocated")
        self.assertEqual(entry.details["reason"], "Correct payer instruction")
        self.assertEqual(entry.user, self.user)

    def test_no_allocation_is_ever_deleted(self):
        bill = self.issued_bill(self.apr)
        main, water = self.line(bill, self.main), self.line(bill, self.water)
        r = self.receipt("400", allocations=alloc((main, 400)))
        before = ReceiptAllocation.objects.count()
        reallocate_receipt(r, alloc((water, 200)), self.user, "move")
        self.assertEqual(ReceiptAllocation.objects.count(), before + 1)
        self.assertEqual((main.paid, water.paid, r.unallocated_amount), (D("0"), D("200"), D("200")))

    def test_reallocation_requires_reason_and_respects_capacity(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        r = self.receipt("900", allocations=alloc((main, 300)))
        with self.assertRaises(ValidationError):
            reallocate_receipt(r, alloc((main, 200)), self.user, "")
        with self.assertRaises(ValidationError):
            reallocate_receipt(r, alloc((main, 801)), self.user, "x")


class ReceiptLifecycleTests(LedgerFixture, TestCase):
    """I. Bounced cheque and lifecycle."""

    def test_bounce_removes_paid_but_keeps_history(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        r = self.receipt("500", mode=Receipt.CHEQUE, allocations=alloc((main, 500)))
        self.assertEqual(main.paid, D("500"))
        bounce_receipt(r, self.user, "Cheque returned")
        r.refresh_from_db()
        self.assertEqual(main.paid, D("0"))
        self.assertEqual(main.balance, D("800"))
        self.assertEqual(r.allocations.count(), 1)
        self.assertEqual((r.allocated_amount, r.unallocated_amount), (D("0"), D("0")))
        self.assertEqual(r.bounced_by, self.user)
        self.assertIsNotNone(r.bounced_at)
        self.assertTrue(AuditLog.objects.filter(action="receipt_bounced", object_id=str(r.pk)).exists())

    def test_lifecycle_transitions(self):
        r = self.receipt("100", mode=Receipt.CHEQUE)
        self.assertEqual(r.status, Receipt.RECEIVED)
        clear_receipt(r, self.user)
        with self.assertRaises(ValidationError):
            bounce_receipt(r, self.user, "late")
        with self.assertRaises(ValidationError):
            cancel_receipt(r, self.user, "late")
        cash = self.receipt("50", mode=Receipt.CASH)
        self.assertEqual(cash.status, Receipt.CLEARED)
        with self.assertRaises(ValidationError):
            bounce_receipt(self.receipt("10", mode=Receipt.CHEQUE), self.user, "")


class InterestTests(LedgerFixture, TestCase):
    """K. Interest engine."""

    def setUp(self):
        super().setUp()
        self.society.interest_enabled = True
        self.society.save(update_fields=["interest_enabled"])

    def test_payment_date_reduces_future_interest_base(self):
        main = self.line(self.issued_bill(self.apr), self.main)  # due 25-Apr
        self.receipt("400", on=date(2026, 5, 10), allocations=alloc((main, 400)))
        value, days, _ = daily_simple_interest(main, date(2026, 5, 31), D("12"))
        expected = simple_interest(D("800"), D("12"), 15) + simple_interest(D("400"), D("12"), 21)
        self.assertEqual(days, 36)
        self.assertAlmostEqual(value, expected, delta=D("0.01"))
        self.assertLess(value, simple_interest(D("800"), D("12"), 36))

    def test_interest_is_a_separate_line_with_reproducible_segments(self):
        apr = self.issued_bill(self.apr)
        main = self.line(apr, self.main)
        self.receipt("400", on=date(2026, 5, 10), allocations=alloc((main, 400)))
        self.may.interest_calculation_date = date(2026, 5, 31)
        self.may.save()
        may = self.issued_bill(self.may)
        interest = may.lines.get(line_type=BillLine.INTEREST)
        self.assertEqual(interest.display_code, "INT-APR-2026")
        self.assertEqual(interest.service_period_start, date(2026, 4, 1))
        charge = interest.interest_charge
        self.assertEqual(charge.rate_pa, D("12.00"))
        segs = list(charge.segments.filter(source_line=main).order_by("from_date"))
        self.assertEqual([(s.from_date, s.to_date, s.principal) for s in segs],
                         [(date(2026, 4, 25), date(2026, 5, 10), D("800.00")), (date(2026, 5, 10), date(2026, 5, 31), D("400.00"))])
        self.assertIsNotNone(segs[0].ended_by_allocation)
        self.assertEqual(self.line(apr, self.main).amount, D("800.00"), "principal line untouched")

    def test_no_interest_on_interest_and_no_double_charging_across_periods(self):
        self.issued_bill(self.apr)
        self.may.interest_calculation_date = date(2026, 5, 31)
        self.may.save()
        may = self.issued_bill(self.may)
        jun = self.period("June 2026", date(2026, 6, 1), interest_calculation_date=date(2026, 6, 30))
        jun_bill = self.issued_bill(jun)
        may_int = may.lines.get(line_type=BillLine.INTEREST, service_period_start=date(2026, 4, 1))
        jun_apr_int = jun_bill.lines.get(line_type=BillLine.INTEREST, service_period_start=date(2026, 4, 1))
        # April principal 800+200+200: May bill covers 25-Apr..31-May; June covers 31-May..30-Jun only.
        self.assertEqual(may_int.amount, simple_interest(D("1200"), D("12"), 36))
        self.assertEqual(jun_apr_int.amount, simple_interest(D("1200"), D("12"), 30))
        # Interest lines are never used as an interest base.
        self.assertFalse(InterestSegment.objects.filter(source_line__line_type=BillLine.INTEREST).exists())
        self.society.interest_on_interest = True
        self.society.save()
        jul = self.period("July 2026", date(2026, 7, 1), interest_calculation_date=date(2026, 7, 31))
        with self.assertRaises(ValidationError):
            generate_bill(jul, self.flat, self.maker)

    def test_no_interest_without_explicit_calculation_date(self):
        self.issued_bill(self.apr)
        may = self.issued_bill(self.may)
        self.assertFalse(may.lines.filter(line_type=BillLine.INTEREST).exists())

    def test_compliance_ceiling_blocks_new_rate_above_12_but_keeps_historical_rate(self):
        historical = self.period("Mar 2026", date(2026, 3, 1), interest_rate_pa=D("21.00"), interest_calculation_date=date(2026, 3, 31))
        generate_bill(historical, self.flat, self.maker)  # pre-compliance legacy rate is reproducible
        sep = self.period("Sep 2026", date(2026, 9, 1), interest_rate_pa=D("21.00"), interest_calculation_date=date(2026, 9, 5))
        with self.assertRaises(ValidationError):
            generate_bill(sep, self.flat, self.maker)

    def test_society_cannot_postpone_ceiling_past_rule_date(self):
        self.society.compliance_effective_date = date(2030, 1, 1)
        self.society.save()
        sep = self.period("Sep 2026", date(2026, 9, 1), interest_rate_pa=D("18.00"), interest_calculation_date=date(2026, 9, 5))
        with self.assertRaises(ValidationError):
            generate_bill(sep, self.flat, self.maker)


class ArrearsAndWorkflowTests(LedgerFixture, TestCase):
    """§17 arrears, §20 maker-checker, bill cancellation."""

    def test_arrears_are_snapshots_not_duplicate_receivables(self):
        apr = self.issued_bill(self.apr)
        may = self.issued_bill(self.may)
        self.assertEqual(may.opening_principal, D("1200.00"))
        receivable_total = sum(l.amount for l in BillLine.objects.filter(bill__flat=self.flat, bill__status=Bill.ISSUED))
        self.assertEqual(receivable_total, D("2400.00"), "April debt must not be re-billed in May")
        self.receipt("1200", allocations=alloc(*[(l, l.amount) for l in apr.lines.all()]))
        self.assertEqual(may.current_outstanding, D("1200.00"))

    def test_maker_checker_and_period_workflow(self):
        generate_period_bills(self.apr, self.maker)
        for line in BillLine.objects.filter(bill__period=self.apr, confirmed=False):
            confirm_variable_line(line, self.maker, line.quantity, line.rate)
        submit_period_for_review(self.apr, self.maker)
        with self.assertRaises(ValidationError):
            approve_period(self.apr, self.maker)
        approve_period(self.apr, self.user)
        self.apr.refresh_from_db()
        self.assertEqual(self.apr.status, BillingPeriod.APPROVED)
        self.assertEqual((self.apr.approved_by, self.apr.generated_by), (self.user, self.maker))
        self.assertFalse(self.apr.bills.filter(status=Bill.DRAFT).exists())
        lock_period(self.apr, self.user)
        self.apr.refresh_from_db()
        self.assertEqual(self.apr.status, BillingPeriod.LOCKED)
        with self.assertRaises(ValidationError):
            generate_bill(self.apr, self.flat, self.maker)
        with self.assertRaises(ValidationError):
            cancel_bill(self.apr.bills.first(), self.user, "late change")

    def test_cancel_issued_bill_requires_reversal_of_allocations_first(self):
        bill = self.issued_bill(self.apr)
        main = self.line(bill, self.main)
        r = self.receipt("100", allocations=alloc((main, 100)))
        with self.assertRaises(ValidationError):
            cancel_bill(bill, self.user, "wrong amount")
        reverse_allocation(r.allocations.get(), D("100"), self.user, "bill to be cancelled")
        cancel_bill(bill, self.user, "wrong amount")
        replacement = generate_bill(self.apr, self.flat, self.maker)
        self.assertNotEqual(replacement.pk, bill.pk)
        self.assertEqual(r.unallocated_amount, D("100"))


class NumberingTests(LedgerFixture, TestCase):
    """N. Document numbering."""

    def test_sequences_are_unique_per_society_and_never_reused(self):
        numbers = [self.receipt("10").receipt_no for _ in range(3)]
        self.assertEqual(numbers, ["R0001", "R0002", "R0003"])
        s2 = Society.objects.create(name="Other")
        flat = Flat.objects.create(society=s2, wing=Wing.objects.create(society=s2, code="A"), unit_no="X1")
        self.assertEqual(self.receipt("10", flat=flat, society=s2).receipt_no, "R0001")
        b1, b2 = generate_bill(self.apr, self.flat, self.maker), generate_bill(self.apr, self.flat2, self.maker)
        self.assertNotEqual(b1.bill_no, b2.bill_no)

    def test_database_rejects_duplicate_receipt_number(self):
        r = self.receipt("10")
        with self.assertRaises(IntegrityError):
            Receipt.objects.create(receipt_no=r.receipt_no, society=self.society, flat=self.flat, payment_mode=Receipt.CASH, amount=D("1"), created_by=self.user)

    def test_receipt_allocated_is_derived(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        r = self.receipt("300", allocations=alloc((main, 300)))
        reverse_allocation(r.allocations.get(), D("100"), self.user, "x")
        self.assertEqual(receipt_allocated(r), D("200.00"))
        self.assertEqual(ReceiptAllocationReversal.objects.count(), 1)
