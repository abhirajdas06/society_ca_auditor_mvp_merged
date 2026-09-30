"""Shared fixtures for the test suite (kept out of tests.py so several test modules can reuse them)."""

import calendar
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model

from .models import BillingPeriod, BillLine, ChargeHead, Flat, FlatChargeRule, FlatMember, Member, Receipt, Society, SocietyMembership, Wing
from .services import confirm_variable_line, create_receipt, generate_bill, issue_bill

D = Decimal


class LedgerFixture:
    """Mixin for TestCase: one society, one flat A001, fixed MAIN/WATER, variable PARK, interest head INT."""

    def setUp(self):
        User = get_user_model()
        self.maker = User.objects.create_user(username="maker", password="x")
        self.user = User.objects.create_user(username="ca", password="x")
        self.society = Society.objects.create(name="Demo CHS", interest_rate_pa=D("12.00"), interest_enabled=False)
        self.wing = Wing.objects.create(society=self.society, code="A")
        self.flat = Flat.objects.create(society=self.society, wing=self.wing, unit_no="A001")
        self.flat2 = Flat.objects.create(society=self.society, wing=self.wing, unit_no="A002")
        self.member = Member.objects.create(society=self.society, full_name="Member One")
        FlatMember.objects.create(flat=self.flat, member=self.member, role=FlatMember.OWNER)
        self.main = self.head("MAINT", "Maintenance", ChargeHead.FIXED, "MAIN", 10)
        self.water = self.head("WATER", "Water", ChargeHead.FIXED, "WATER", 20)
        self.park = self.head("PARK", "2 Wheeler Parking", ChargeHead.VARIABLE, "2W", 50)
        self.interest = self.head("INT", "Interest", ChargeHead.FIXED, "INT", 900)
        self.rule(self.flat, self.main, amount="800", start=date(2026, 4, 1))
        self.rule(self.flat, self.water, amount="200", start=date(2026, 4, 1))
        self.rule(self.flat, self.park, qty="2", rate="100", start=date(2026, 4, 1))
        self.rule(self.flat2, self.main, amount="500", start=date(2026, 4, 1))
        self.apr = self.period("April 2026", date(2026, 4, 1))
        self.may = self.period("May 2026", date(2026, 5, 1))

    # -- builders ---------------------------------------------------------------------
    def head(self, code, name, typ, prefix, priority, society=None):
        return ChargeHead.objects.create(
            society=society or self.society, code=code, name=name, charge_type=typ, system_code=prefix,
            knockoff_priority=priority, apportionment_basis=ChargeHead.MANUAL,
        )

    def rule(self, flat, head, amount="0", qty="1", rate="0", start=date(2026, 4, 1), end=None):
        return FlatChargeRule.objects.create(
            flat=flat, charge_head=head, configured_amount=D(amount), default_quantity=D(qty), default_rate=D(rate), effective_from=start, effective_to=end
        )

    def period(self, name, start, society=None, **kw):
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        defaults = dict(bill_date=start.replace(day=5), due_date=start.replace(day=25), interest_rate_pa=D("12.00"))
        defaults.update(kw)
        return BillingPeriod.objects.create(society=society or self.society, name=name, period_start=start, period_end=end, **defaults)

    def issued_bill(self, period, flat=None):
        bill = generate_bill(period, flat or self.flat, self.maker)
        for line in bill.lines.filter(confirmed=False):
            confirm_variable_line(line, self.maker, line.quantity, line.rate)
        issue_bill(bill, self.user)
        bill.refresh_from_db()
        return bill

    def line(self, bill, head):
        return bill.lines.get(charge_head=head, line_type=BillLine.CHARGE)

    def interest_line(self, bill, amount="120.00", code="INT-APR-2026"):
        """A directly-created interest component, as in the spec example (MAIN-APR 800 / INT-APR 120)."""
        return BillLine.objects.create(
            bill=bill, charge_head=self.interest, line_type=BillLine.INTEREST, component_code=code,
            service_period_start=date(2026, 4, 1), service_period_end=date(2026, 4, 30), due_date=date(2026, 5, 25),
            description="Interest April 2026", amount=D(amount), confirmed=True,
        )

    def receipt(self, amount, on=date(2026, 5, 10), flat=None, mode=Receipt.UPI, allocations=None, **kw):
        r = Receipt(
            society=kw.pop("society", self.society), flat=flat or self.flat, receipt_date=on, payment_mode=mode, amount=D(amount),
            transaction_ref="UTR123" if mode != Receipt.CHEQUE else "", cheque_no="000111" if mode == Receipt.CHEQUE else "", **kw,
        )
        return create_receipt(r, self.user, allocations)

    def membership(self, username, role, society=None):
        user = get_user_model().objects.create_user(username=username, password="pw-" + username)
        SocietyMembership.objects.create(user=user, society=society or self.society, role=role)
        return user
