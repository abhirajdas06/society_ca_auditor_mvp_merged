"""Multi-society handling (a CA or auditor with several societies) and dependent dropdowns."""

from datetime import date
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from . import reports
from .forms import FlatFilterForm, ReceiptForm
from .models import ChargeHead, Flat, FlatMember, Member, Receipt, Society, SocietyMembership, Wing
from .services import issue_bill
from .testing import LedgerFixture


class SecondSocietyFixture(LedgerFixture):
    """Adds a second society with its own wing, flats, heads, period and issued bill."""

    def setUp(self):
        super().setUp()
        self.other = Society.objects.create(name="Greenfield Heights CHS", interest_enabled=False)
        self.other_wing = Wing.objects.create(society=self.other, code="C")
        self.other_flat = Flat.objects.create(society=self.other, wing=self.other_wing, unit_no="C001")
        self.other_member = Member.objects.create(society=self.other, full_name="Other Member")
        FlatMember.objects.create(flat=self.other_flat, member=self.other_member, role=FlatMember.OWNER)
        self.other_head = self.head("MAINT", "Maintenance", ChargeHead.FIXED, "MAIN", 10, society=self.other)
        self.rule(self.other_flat, self.other_head, amount="1500", start=date(2026, 4, 1))
        self.other_apr = self.period("April 2026", date(2026, 4, 1), society=self.other)
        from .services import generate_bill

        self.other_bill = issue_bill(generate_bill(self.other_apr, self.other_flat, self.maker), self.user)


class PortfolioTests(SecondSocietyFixture, TestCase):
    def test_portfolio_shows_only_the_users_societies_with_their_own_figures(self):
        ca = self.membership("multi_ca", SocietyMembership.CA)
        SocietyMembership.objects.create(user=ca, society=self.other, role=SocietyMembership.AUDITOR)
        self.issued_bill(self.apr)  # 1200 payable in society 1
        self.client.force_login(ca)

        response = self.client.get(reverse("portfolio"))
        self.assertEqual(response.status_code, 200)
        rows = {r["society"].pk: r for r in response.context["rows"]}
        self.assertEqual(set(rows), {self.society.pk, self.other.pk})
        self.assertEqual(rows[self.society.pk]["outstanding"], D("1200.00"))
        self.assertEqual(rows[self.other.pk]["outstanding"], D("1500.00"))
        self.assertEqual(rows[self.society.pk]["role"], "CA / Accountant")
        self.assertEqual(rows[self.other.pk]["role"], "Auditor (read-only)")
        self.assertEqual(response.context["totals"]["outstanding"], D("2700.00"))
        self.assertEqual(response.context["totals"]["societies"], 2)

    def test_portfolio_hides_societies_the_user_does_not_handle(self):
        single = self.membership("single_ca", SocietyMembership.CA)
        self.client.force_login(single)
        rows = self.client.get(reverse("portfolio")).context["rows"]
        self.assertEqual([r["society"].pk for r in rows], [self.society.pk])

    def test_portfolio_flags_work_waiting_in_each_society(self):
        ca = self.membership("multi_ca2", SocietyMembership.CA)
        SocietyMembership.objects.create(user=ca, society=self.other, role=SocietyMembership.CA)
        from .services import generate_period_bills

        generate_period_bills(self.apr, self.maker)  # leaves unconfirmed variable lines
        self.client.force_login(ca)
        rows = {r["society"].pk: r for r in self.client.get(reverse("portfolio")).context["rows"]}
        self.assertEqual(rows[self.society.pk]["pending_review"], 1)
        self.assertTrue(rows[self.society.pk]["needs_attention"])
        self.assertFalse(rows[self.other.pk]["needs_attention"])

    def test_portfolio_report_is_per_society_not_pooled(self):
        data = reports.portfolio([self.society, self.other])
        self.assertEqual({r["society"].pk for r in data["rows"]}, {self.society.pk, self.other.pk})
        self.assertEqual(data["totals"]["flats"], sum(r["flats"] for r in data["rows"]))


class SwitchingTests(SecondSocietyFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.ca = self.membership("multi_ca", SocietyMembership.CA)
        SocietyMembership.objects.create(user=self.ca, society=self.other, role=SocietyMembership.CA)
        self.client.force_login(self.ca)

    def test_switching_changes_the_active_society_and_the_data_shown(self):
        self.issued_bill(self.apr)
        first = self.client.get(reverse("bill_list"))
        self.assertEqual([b.pk for b in first.context["page"]], [self.apr.bills.get(flat=self.flat).pk])

        self.client.post(reverse("switch_society"), {"society": self.other.pk})
        second = self.client.get(reverse("bill_list"))
        self.assertEqual([b.pk for b in second.context["page"]], [self.other_bill.pk])
        self.assertEqual(second.context["active_society"].pk, self.other.pk)

    def test_switching_returns_to_the_page_you_were_on(self):
        response = self.client.post(reverse("switch_society"), {"society": self.other.pk, "next": reverse("outstanding_report")})
        self.assertRedirects(response, reverse("outstanding_report"))

    def test_external_next_is_ignored(self):
        response = self.client.post(reverse("switch_society"), {"society": self.other.pk, "next": "https://evil.example.com/steal"})
        self.assertRedirects(response, reverse("dashboard"))

    def test_auditor_may_switch_society_but_still_cannot_mutate(self):
        auditor = self.membership("multi_auditor", SocietyMembership.AUDITOR)
        SocietyMembership.objects.create(user=auditor, society=self.other, role=SocietyMembership.AUDITOR)
        self.client.force_login(auditor)
        self.assertEqual(self.client.post(reverse("switch_society"), {"society": self.other.pk}).status_code, 302)
        self.assertEqual(self.client.get(reverse("dashboard")).context["active_society"].pk, self.other.pk)
        self.assertEqual(self.client.post(reverse("receipt_create"), {}).status_code, 403)

    def test_switching_to_a_society_you_do_not_handle_is_refused(self):
        outsider = Society.objects.create(name="Not mine CHS")
        response = self.client.post(reverse("switch_society"), {"society": outsider.pk})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard")).context["active_society"].pk, self.society.pk)

    def test_dropdowns_never_offer_another_societys_records(self):
        form = ReceiptForm(society=self.society)
        self.assertNotIn(self.other_flat, form.fields["flat"].queryset)
        self.assertNotIn(self.other_member, form.fields["member"].queryset)
        self.assertNotIn((str(self.other_wing.pk), "C"), form.fields["wing"].choices)


class DependentDropdownTests(LedgerFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.wing_b = Wing.objects.create(society=self.society, code="B")
        self.flat_b = Flat.objects.create(society=self.society, wing=self.wing_b, unit_no="B001")
        self.member_b = Member.objects.create(society=self.society, full_name="Member B")
        FlatMember.objects.create(flat=self.flat_b, member=self.member_b, role=FlatMember.OWNER)

    def test_receipt_form_flat_options_carry_their_wing_and_payer_options_carry_their_flats(self):
        html = ReceiptForm(society=self.society).as_p()
        self.assertIn('data-depends-on="id_wing"', html)   # flat follows wing
        self.assertIn('data-depends-on="id_flat"', html)   # payer follows flat
        self.assertIn(f'value="{self.flat.pk}" data-parent="{self.wing.pk}"', html)
        self.assertIn(f'value="{self.flat_b.pk}" data-parent="{self.wing_b.pk}"', html)
        self.assertIn(f'value="{self.member.pk}" data-parent="{self.flat.pk}"', html)
        self.assertIn(f'value="{self.member_b.pk}" data-parent="{self.flat_b.pk}"', html)

    def test_filter_form_links_wing_to_flat(self):
        html = FlatFilterForm(society=self.society).as_p()
        self.assertIn('data-depends-on="id_wing"', html)
        self.assertIn(f'value="{self.flat_b.pk}" data-parent="{self.wing_b.pk}"', html)

    def test_wing_is_only_a_filter_and_is_not_saved(self):
        ca = self.membership("ca_user", SocietyMembership.CA)
        self.client.force_login(ca)
        response = self.client.post(reverse("receipt_create"), {
            "wing": self.wing.pk, "flat": self.flat.pk, "member": self.member.pk, "receipt_date": "2026-05-10",
            "payment_mode": "cash", "amount": "100", "allocations_json": "[]",
        })
        self.assertEqual(response.status_code, 302)
        receipt = Receipt.objects.get()
        self.assertEqual((receipt.flat_id, receipt.member_id), (self.flat.pk, self.member.pk))
        self.assertFalse(hasattr(receipt, "wing"))

    def test_server_rejects_a_payer_who_is_not_linked_to_the_chosen_flat(self):
        """The dropdown filtering is display only — a hand-crafted POST must still fail."""
        ca = self.membership("ca_user", SocietyMembership.CA)
        self.client.force_login(ca)
        response = self.client.post(reverse("receipt_create"), {
            "flat": self.flat.pk, "member": self.member_b.pk, "receipt_date": "2026-05-10",
            "payment_mode": "cash", "amount": "100", "allocations_json": "[]",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "member", "Payer is not linked to this flat.")
        self.assertEqual(Receipt.objects.count(), 0)

    def test_wing_filter_narrows_list_pages(self):
        ca = self.membership("ca_user", SocietyMembership.CA)
        self.client.force_login(ca)
        page = self.client.get(reverse("flat_list"), {"wing": self.wing_b.pk})
        self.assertEqual([f.pk for f in page.context["flats"]], [self.flat_b.pk])
        page = self.client.get(reverse("flat_list"), {"flat": self.flat.pk})
        self.assertEqual([f.pk for f in page.context["flats"]], [self.flat.pk])
