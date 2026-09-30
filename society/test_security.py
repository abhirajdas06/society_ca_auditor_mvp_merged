"""M. Server-side permissions: roles, direct URLs, society scoping."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from . import urls as app_urls
from .models import BillingPeriod, Receipt, ReceiptAllocation, Society, SocietyMembership
from .permissions import CAPABILITIES
from .services import confirm_variable_line, generate_period_bills, submit_period_for_review
from .testing import LedgerFixture


class SecurityTests(LedgerFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.ca = self.membership("ca_user", SocietyMembership.CA)
        self.operator = self.membership("op_user", SocietyMembership.OPERATOR)
        self.admin = self.membership("admin_user", SocietyMembership.SOCIETY_ADMIN)
        self.auditor = self.membership("aud_user", SocietyMembership.AUDITOR)
        self.bill = self.issued_bill(self.apr)
        self.main = self.line(self.bill, self.main)
        self.rcpt = self.receipt("300", mode=Receipt.CHEQUE, allocations=[{"bill_line_id": self.main.pk, "amount": "300"}])
        self.allocation = self.rcpt.allocations.get()

    def login(self, user):
        self.client.force_login(user)

    def mutation_urls(self):
        return [
            reverse("society_edit"),
            reverse("flat_create"),
            reverse("member_create"),
            reverse("charge_head_create"),
            reverse("charge_rule_create"),
            reverse("period_create"),
            reverse("period_generate", args=[self.may.pk]),
            reverse("period_submit", args=[self.may.pk]),
            reverse("period_approve", args=[self.may.pk]),
            reverse("period_lock", args=[self.apr.pk]),
            reverse("bill_issue", args=[self.bill.pk]),
            reverse("bill_cancel", args=[self.bill.pk]),
            reverse("receipt_create"),
            reverse("receipt_allocate", args=[self.rcpt.pk]),
            reverse("receipt_reallocate", args=[self.rcpt.pk]),
            reverse("receipt_auto_allocate", args=[self.rcpt.pk]),
            reverse("receipt_clear", args=[self.rcpt.pk]),
            reverse("receipt_bounce", args=[self.rcpt.pk]),
            reverse("receipt_cancel", args=[self.rcpt.pk]),
            reverse("allocation_reverse", args=[self.allocation.pk]),
            reverse("import_list"),
        ]

    def test_every_view_declares_a_capability(self):
        public = {"health"}
        for pattern in app_urls.urlpatterns:
            if pattern.name in public:
                continue
            cap = getattr(pattern.callback, "required_capability", None)
            self.assertIn(cap, CAPABILITIES, f"{pattern.name} is not protected by permissions.require")

    def test_anonymous_is_redirected_to_login(self):
        for url in [reverse("dashboard"), reverse("receipt_list"), reverse("bill_register")]:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_auditor_cannot_post_any_financial_mutation(self):
        self.login(self.auditor)
        for url in self.mutation_urls():
            response = self.client.post(url, {"reason": "x", "amount": "10", "allocations_json": "[]"})
            self.assertEqual(response.status_code, 403, url)
        self.main.refresh_from_db()
        self.assertEqual(self.main.paid, D("300"))
        self.assertEqual(ReceiptAllocation.objects.count(), 1)
        self.rcpt.refresh_from_db()
        self.assertEqual(self.rcpt.status, Receipt.RECEIVED)

    def test_auditor_can_read_reports_statements_and_audit_log(self):
        self.login(self.auditor)
        for url in [reverse("dashboard"), reverse("bill_register"), reverse("collection_sheet"), reverse("receipt_register"),
                    reverse("outstanding_report"), reverse("advance_report"), reverse("charge_head_summary"),
                    reverse("flat_statement", args=[self.flat.pk]), reverse("audit_log"), reverse("receipt_detail", args=[self.rcpt.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_operator_cannot_bypass_approval_or_corrections_by_direct_url(self):
        generate_period_bills(self.may, self.maker)
        for line in self.may.bills.get(flat=self.flat).lines.filter(confirmed=False):
            confirm_variable_line(line, self.maker, line.quantity, line.rate)
        submit_period_for_review(self.may, self.maker)
        self.login(self.operator)
        forbidden = [
            reverse("period_approve", args=[self.may.pk]),
            reverse("period_lock", args=[self.apr.pk]),
            reverse("bill_issue", args=[self.bill.pk]),
            reverse("bill_cancel", args=[self.bill.pk]),
            reverse("receipt_reallocate", args=[self.rcpt.pk]),
            reverse("receipt_cancel", args=[self.rcpt.pk]),
            reverse("allocation_reverse", args=[self.allocation.pk]),
            reverse("society_edit"),
            reverse("charge_head_create"),
            reverse("charge_rule_create"),
        ]
        for url in forbidden:
            self.assertEqual(self.client.post(url, {"reason": "x", "amount": "10"}).status_code, 403, url)
        self.may.refresh_from_db()
        self.assertEqual(self.may.status, BillingPeriod.REVIEW)
        self.assertEqual(self.allocation.reversals.count(), 0)

    def test_operator_can_record_receipts_and_bounce(self):
        self.login(self.operator)
        response = self.client.post(reverse("receipt_bounce", args=[self.rcpt.pk]), {"reason": "returned"})
        self.assertEqual(response.status_code, 302)
        self.rcpt.refresh_from_db()
        self.assertEqual(self.rcpt.status, Receipt.BOUNCED)

    def test_society_admin_cannot_record_receipts(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("receipt_create")).status_code, 403)

    def test_maker_cannot_approve_own_period_even_as_ca(self):
        generate_period_bills(self.may, self.ca)
        for line in self.may.bills.get(flat=self.flat).lines.filter(confirmed=False):
            confirm_variable_line(line, self.ca, line.quantity, line.rate)
        submit_period_for_review(self.may, self.ca)
        self.login(self.ca)
        self.client.post(reverse("period_approve", args=[self.may.pk]))
        self.may.refresh_from_db()
        self.assertEqual(self.may.status, BillingPeriod.REVIEW)

    def test_other_society_objects_are_not_reachable(self):
        other = Society.objects.create(name="Other CHS")
        stranger = self.membership("stranger", SocietyMembership.CA, society=other)
        self.login(stranger)
        for url in [reverse("bill_detail", args=[self.bill.pk]), reverse("receipt_detail", args=[self.rcpt.pk]),
                    reverse("flat_statement", args=[self.flat.pk]), reverse("receivable_lines_api", args=[self.flat.pk])]:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(reverse("receipt_clear", args=[self.rcpt.pk])).status_code, 404)
        # switching to a society you are not a member of is refused
        self.assertEqual(self.client.post(reverse("switch_society"), {"society": self.society.pk}).status_code, 404)

    def test_user_without_membership_sees_nothing(self):
        from django.contrib.auth import get_user_model

        nobody = get_user_model().objects.create_user(username="nobody", password="x")
        self.login(nobody)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("receipt_create")).status_code, 302)
        self.assertEqual(self.client.get(reverse("bill_detail", args=[self.bill.pk])).status_code, 404)


class ViewFlowTests(LedgerFixture, TestCase):
    """End-to-end through HTTP: pages render and the receipt screen posts component allocations."""

    def setUp(self):
        super().setUp()
        self.ca = self.membership("ca_user", SocietyMembership.CA)
        self.client.force_login(self.ca)

    def test_receipt_create_with_component_allocation_via_http(self):
        bill = self.issued_bill(self.apr)
        main, interest = self.line(bill, self.main), self.interest_line(bill)
        payload = f'[{{"bill_line_id": {main.pk}, "amount": "300"}}, {{"bill_line_id": {interest.pk}, "amount": "50"}}]'
        response = self.client.post(reverse("receipt_create"), {
            "flat": self.flat.pk, "member": self.member.pk, "receipt_date": "2026-05-10", "payment_mode": "upi", "amount": "350",
            "transaction_ref": "UTR9", "allocations_json": payload,
        })
        self.assertEqual(response.status_code, 302, response.content[:500])
        receipt = Receipt.objects.get()
        self.assertEqual((main.paid, interest.paid, receipt.unallocated_amount), (D("300"), D("50"), D("0")))

    def test_over_allocation_via_http_is_rejected_and_nothing_saved(self):
        main = self.line(self.issued_bill(self.apr), self.main)
        response = self.client.post(reverse("receipt_create"), {
            "flat": self.flat.pk, "receipt_date": "2026-05-10", "payment_mode": "cash", "amount": "100",
            "allocations_json": f'[{{"bill_line_id": {main.pk}, "amount": "150"}}]',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Receipt.objects.count(), 0)
        self.assertEqual(main.paid, D("0"))

    def test_all_pages_render(self):
        bill = self.issued_bill(self.apr)
        r = self.receipt("1500", allocations=[{"bill_line_id": self.line(bill, self.main).pk, "amount": "800"}])
        pages = [
            reverse("dashboard"), reverse("flat_list"), reverse("member_list"), reverse("charge_head_list"), reverse("charge_rule_list"),
            reverse("period_list"), reverse("bill_list"), reverse("bill_detail", args=[bill.pk]), reverse("receipt_list"),
            reverse("receipt_detail", args=[r.pk]), reverse("receipt_create"), reverse("receipt_allocate", args=[r.pk]),
            reverse("receipt_reallocate", args=[r.pk]), reverse("bill_register"), reverse("collection_sheet"), reverse("receipt_register"),
            reverse("outstanding_report"), reverse("advance_report"), reverse("charge_head_summary"), reverse("flat_statement", args=[self.flat.pk]),
            reverse("audit_log"), reverse("import_list"), reverse("society_edit"), reverse("period_create"), reverse("flat_create"),
            reverse("bill_register") + "?format=csv", reverse("collection_sheet") + "?format=csv", reverse("outstanding_report") + "?format=csv",
            reverse("receipt_register") + "?format=csv", reverse("receivable_lines_api", args=[self.flat.pk]),
        ]
        for url in pages:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_health_endpoint(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("health")).json()["status"], "ok")
