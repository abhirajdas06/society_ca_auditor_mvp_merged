"""§26 staged import: upload -> validate -> preview -> import -> exception report."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from . import importer
from .models import Flat, FlatChargeRule, ImportBatch, ImportRow, Member
from .testing import LedgerFixture


def csv_file(text, name="register.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


class ImportTests(LedgerFixture, TestCase):
    def test_society_register_import_is_staged_and_never_overwrites(self):
        text = "Unit No,Member Name,Joint Member,Area,Wing\nA001,Somebody Else,,500,A\nB101,New Owner,Second Owner,538,B\nB102,,,,B\nB101,Dup In File,,1,B\n"
        batch = importer.stage_upload(self.society, ImportBatch.SOCIETY_REGISTER, csv_file(text), self.user)
        statuses = dict(batch.rows.values_list("row_number", "status"))
        self.assertEqual(statuses, {2: ImportRow.DUPLICATE, 3: ImportRow.VALID, 4: ImportRow.ERROR, 5: ImportRow.ERROR})
        self.assertFalse(Flat.objects.filter(unit_no="B101").exists(), "validation must not write master data")

        importer.run_import(batch, self.user)
        flat = Flat.objects.get(society=self.society, unit_no="B101")
        self.assertEqual(flat.area_sqft, D("538"))
        self.assertEqual(flat.member_display, "New Owner & Second Owner")
        self.assertEqual(Member.objects.get(pk=self.member.pk).full_name, "Member One", "existing member untouched")
        report = importer.exception_report_csv(batch)
        self.assertIn("already exists", report)
        self.assertIn("member is required", report)
        with self.assertRaises(ValidationError):
            importer.run_import(batch, self.user)

    def test_charge_rule_import_validates_heads_dates_and_overlaps(self):
        text = (
            "unit_no,charge_head,effective_from,effective_to,amount,quantity,rate\n"
            "A002,WATER,2026-04-01,,150,,\n"          # valid
            "A001,MAINT,01-05-2026,,850,,\n"          # overlaps open April rule
            "A001,NOPE,2026-04-01,,1,,\n"             # unknown head
            "A002,INT,2026-04-01,,1,,\n"              # interest is engine-calculated
            "A002,PARK,2026-04-01,,,1,75\n"           # variable ok
        )
        batch = importer.stage_upload(self.society, ImportBatch.CHARGE_RULES, csv_file(text, "rules.csv"), self.user)
        statuses = [r.status for r in batch.rows.order_by("row_number")]
        self.assertEqual(statuses, [ImportRow.VALID, ImportRow.ERROR, ImportRow.ERROR, ImportRow.ERROR, ImportRow.VALID])
        importer.run_import(batch, self.user)
        self.assertEqual(FlatChargeRule.objects.get(flat=self.flat2, charge_head=self.water).configured_amount, D("150.00"))
        self.assertEqual(FlatChargeRule.objects.get(flat=self.flat2, charge_head=self.park).default_rate, D("75.00"))

    def test_missing_required_columns_rejected_at_upload(self):
        with self.assertRaises(ValidationError):
            importer.stage_upload(self.society, ImportBatch.SOCIETY_REGISTER, csv_file("foo,bar\n1,2\n"), self.user)
