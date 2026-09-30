import os
import secrets
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from society.models import BillingPeriod, ChargeHead, Flat, FlatChargeRule, FlatMember, Member, Receipt, Society, SocietyMembership, Wing
from society.services import (
    approve_period,
    auto_allocate_receipt,
    bounce_receipt,
    confirm_variable_line,
    create_receipt,
    generate_period_bills,
    submit_period_for_review,
)

# (unit, member(s), maintenance, water, sinking fund, non-occupancy, 2W parking, 4W parking) — Chandresh Residency-style sample.
UNITS = [
("A001","Mr. Muhammed Raashid Ansari",538,189,53,0,50,0),("A002","Mr. Abdul Jameel Jalil",516,189,50,83,100,150),("A101","Mr. Farukh Ainul Shaikh",538,189,53,0,50,0),("A102","Mr. Abdul Rasheed",455,189,45,0,50,0),("A103","Mr. Tajuddin Nasiruddin Shaikh & Jt.",554,189,54,78,100,0),("A104","Ms. Syed Mehrunnisa",538,189,53,0,50,0),("A201","Mr. Zoaib Iqbal Kasmani",538,189,53,0,50,0),("A202","Mr. Saddam Husen",455,189,45,78,100,0),("A203","Mr. Asif Razzaq Mansuri",554,189,54,0,50,0),("A204","Mr. Rais Ismail Shaikh",538,189,53,0,50,0),("A301","Mrs. Rabiya Begum & Samid Shaikh",538,189,53,85,0,0),("A302","Mr. Sana Wasim Khan & Rahima Rahim Khan",455,189,53,78,0,0),("A303","Dr. Sayed A.A. Jaffri",554,189,54,0,50,0),("A304","Mrs. Shabanabano Shaikh & Rashid Ahmed",538,189,53,78,100,0),("B001","Mrs. Raziya Patel & Haffis K.",516,189,50,0,50,0),("B002","Mrs. Seema Navid Saeed",538,189,53,85,0,0),("B101","Mr. Kamarali Mehboob & Mrs.Sayila Parveen",538,189,53,0,50,0),("B102","Mr. Sirajuddin Tajuddin Shaikh",554,189,54,0,50,0),("B103","Mrs. Parveen Mohammed Ali Shaikh",455,189,45,0,50,100),("B104","Mrs. Mehfooz Abdul Aziz",538,189,53,78,100,0),("B201","Mrs. Sugra Yasinali Mohammed & Jt",538,189,53,85,0,0),("B202","Mrs. Shama Mohammed Raisuddin & Mohammed Raisuddin",554,189,54,78,50,0),("B203","Mr. Aijaz Mohammed Yusuf Tologi & Jt.",455,189,45,78,100,0),("B204","Mr. Iqteda H.Khan",538,189,53,83,0,0),("B301","Mr. Mohammed Sagir Idrisi S/o Mohammed Jamluddin Idrisi",538,189,53,0,0,0),("B302","Mr. Arif Idrish Shaikh",554,189,54,86,200,0),("B303","M/s. Spradecom Electro",455,189,45,0,0,0),("B304","Mrs. Soni Begum Salimuddin Siddiqui",538,189,53,83,100,0),("S001","Mr. Ansari Asif Shakir & Mrs.Ansari Khairunnisa Shakir",503,19,28,200,0,0),("S002","Mr. Saheblal Halway",503,19,28,200,0,0),("S003","Mr. Syed Qaim Haider S/o Zargam Haider",504,19,27,200,0,0),("S004","Mr. Mohd.Naseem A.Shaikh",504,19,27,200,0,0),("S005","Mrs. Vaishail S, Shivalkar",503,19,28,200,0,0),("S006","Mr. Gautmlal D. Sevak & Jt",510,19,21,0,0,0),("S007","Mrs. Salma Begum Mohd.Ayub",511,19,20,200,0,0),("S008","Mr. Mohd Irsad Mohd Harun Shaikh",503,19,28,0,50,0),("S009","Mrs. Iqbal Ahmed Iftekhar Ahmed Qurshi & Mrs. Sabiya Iqbal Ahmed Queshi",505,19,26,0,0,0),("S010","Mr. Estiyak A. Sazzad",505,19,26,200,0,0),("S011","Mr. Dr.Firdous A. Mailk Shaikh & Jt",503,19,28,200,0,0),("S012","Mrs. Shanti S.Mishra",510,19,21,0,0,0)
]

HEADS = [
    # code, name, type, reference prefix, apportionment basis, knock-off priority
    ("MAINT", "Maintenance Charges", ChargeHead.FIXED, "MAIN", ChargeHead.FLAT_EQUAL, 10),
    ("WATER", "Water Charges", ChargeHead.FIXED, "WATER", ChargeHead.SANCTIONED_INLET, 20),
    ("SINK", "Sinking Fund", ChargeHead.FIXED, "SINK", ChargeHead.GENERAL_BODY_RATE, 30),
    ("NONOCC", "Non Occupancy Charges", ChargeHead.VARIABLE, "NONOCC", ChargeHead.GENERAL_BODY_RATE, 40),
    ("2W", "2 Wheeler Parking", ChargeHead.VARIABLE, "2W", ChargeHead.GENERAL_BODY_RATE, 50),
    ("4W", "4 Wheeler Parking", ChargeHead.VARIABLE, "4W", ChargeHead.GENERAL_BODY_RATE, 60),
    ("INT", "Interest on Defaulted Charges", ChargeHead.FIXED, "INT", ChargeHead.GENERAL_BODY_RATE, 900),
]

# A second, smaller society so the multi-society portfolio and switcher have something to show.
SECOND_UNITS = [
    ("C001", "Mr. Rohit Deshpande", 650, 210, 65, 0, 50, 0),
    ("C002", "Mrs. Anjali Kulkarni & Mr. Prasad Kulkarni", 640, 210, 64, 90, 0, 150),
    ("C101", "Mr. Imran Qureshi", 650, 210, 65, 0, 50, 0),
    ("C102", "Ms. Leena Fernandes", 610, 210, 61, 90, 50, 0),
    ("C201", "Mr. Vikram Rao & Jt.", 650, 210, 65, 0, 0, 150),
    ("C202", "M/s. Northline Traders", 700, 260, 70, 120, 0, 0),
]

USERS = [
    ("demo_ca", SocietyMembership.CA),
    ("demo_operator", SocietyMembership.OPERATOR),
    ("demo_admin", SocietyMembership.SOCIETY_ADMIN),
    ("demo_auditor", SocietyMembership.AUDITOR),
]


def split_members(names):
    cleaned = names.replace("& Jt.", "").replace("& Jt", "")
    return [p.strip() for p in cleaned.split("&") if p.strip()]


class Command(BaseCommand):
    help = "Seed a Chandresh Residency-style demo society (idempotent). --with-activity adds issued bills and receipts."

    def add_arguments(self, parser):
        parser.add_argument("--with-activity", action="store_true", help="Generate, approve and collect August and September 2026.")
        parser.add_argument("--second-society", action="store_true", help="Also seed a second society for the same users (multi-society demo).")

    @transaction.atomic
    def handle(self, *args, **options):
        society = self._society(
            name="CHANDRESH RESIDENCY A B CO-OPERATIVE HOUSING SOCIETY LIMITED",
            defaults={
                "registration_number": "TNA/(TNA)/HSG/(TC)/13150/2001-2002",
                "registration_date": date(2001, 4, 12),
                "address": "Lodha Complex, Mira Road (East), Thane - 401107",
                "city": "Thane",
            },
            units=UNITS,
            wing_codes=["A", "B", "S"],
        )
        password, created_users = self._users(society)
        aug, sep = self._periods(society)
        if options["with_activity"] and aug.status == BillingPeriod.DRAFT:
            self._activity(society, aug, sep)

        if options["second_society"]:
            second = self._society(
                name="GREENFIELD HEIGHTS CO-OPERATIVE HOUSING SOCIETY LIMITED",
                defaults={
                    "registration_number": "TNA/(TNA)/HSG/(TC)/20871/2011-2012",
                    "registration_date": date(2011, 7, 19),
                    "address": "Sector 9, Kharghar, Navi Mumbai - 410210",
                    "city": "Navi Mumbai",
                },
                units=SECOND_UNITS,
                wing_codes=["C"],
            )
            self._users(second, password)
            s_aug, s_sep = self._periods(second)
            if options["with_activity"] and s_aug.status == BillingPeriod.DRAFT:
                self._activity(second, s_aug, s_sep)
            self.stdout.write(self.style.SUCCESS(f"Second society ready: {second.name}"))

        self.stdout.write(self.style.SUCCESS(f"Demo society ready: {society.name}"))
        if created_users:
            self.stdout.write(f"Created users {', '.join(created_users)} with password: {password}")
            self.stdout.write(self.style.WARNING("Change these demo credentials before any shared use."))

    def _society(self, name, defaults, units, wing_codes):
        society, _ = Society.objects.get_or_create(
            name=name,
            defaults={
                **defaults,
                "interest_rate_pa": Decimal("12.00"),
                "interest_enabled": True,
                "interest_resolution_reference": "DEMO - replace with the society's General Body resolution",
                "allocation_policy": Society.PRINCIPAL_THEN_INTEREST,
            },
        )
        heads = {}
        for code, name, typ, prefix, basis, priority in HEADS:
            heads[code], _ = ChargeHead.objects.update_or_create(
                society=society,
                code=code,
                defaults={"name": name, "charge_type": typ, "system_code": prefix, "apportionment_basis": basis,
                          "knockoff_priority": priority, "is_active": True},
            )
        wings = {c: Wing.objects.get_or_create(society=society, code=c, defaults={"floors": 4})[0] for c in wing_codes}
        for unit, names, maint, water, sink, nonocc, p2, p4 in units:
            flat, _ = Flat.objects.get_or_create(
                society=society, unit_no=unit, defaults={"wing": wings[unit[0]], "floor": unit[1], "unit_type": Flat.RESIDENTIAL}
            )
            for i, name in enumerate(split_members(names)):
                member, _ = Member.objects.get_or_create(society=society, full_name=name)
                FlatMember.objects.get_or_create(flat=flat, member=member, role=FlatMember.OWNER if i == 0 else FlatMember.JOINT)
            for code, amount in [("MAINT", maint), ("WATER", water), ("SINK", sink)]:
                FlatChargeRule.objects.get_or_create(
                    flat=flat, charge_head=heads[code], effective_from=date(2026, 1, 1), defaults={"configured_amount": Decimal(amount)}
                )
            for code, amount in [("NONOCC", nonocc), ("2W", p2), ("4W", p4)]:
                if amount:
                    FlatChargeRule.objects.get_or_create(
                        flat=flat, charge_head=heads[code], effective_from=date(2026, 1, 1),
                        defaults={"default_quantity": Decimal("1"), "default_rate": Decimal(amount)},
                    )
        return society

    def _users(self, society, password=None):
        User = get_user_model()
        password = password or os.getenv("DEMO_PASSWORD") or secrets.token_urlsafe(12)
        created_users = []
        for username, role in USERS:
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password(password)
                user.save()
                created_users.append(username)
            SocietyMembership.objects.get_or_create(user=user, society=society, defaults={"role": role})
        return password, created_users

    def _periods(self, society):
        aug, _ = BillingPeriod.objects.get_or_create(
            society=society, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
            defaults={"name": "August 2026", "bill_date": date(2026, 8, 5), "due_date": date(2026, 8, 25), "interest_rate_pa": Decimal("12.00")},
        )
        sep, _ = BillingPeriod.objects.get_or_create(
            society=society, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            defaults={"name": "September 2026", "bill_date": date(2026, 9, 5), "due_date": date(2026, 9, 25),
                      "interest_rate_pa": Decimal("12.00"), "interest_calculation_date": date(2026, 9, 5)},
        )
        return aug, sep

    def _activity(self, society, aug, sep):
        User = get_user_model()
        maker, checker = User.objects.get(username="demo_operator"), User.objects.get(username="demo_ca")
        for period in (aug, sep):
            generate_period_bills(period, maker)
            for bill in period.bills.all():
                for line in bill.lines.filter(confirmed=False):
                    confirm_variable_line(line, maker, line.quantity, line.rate)
            submit_period_for_review(period, maker)
            approve_period(period, checker)
            if period == aug:
                self._collect(society, maker, period.name)

    def _collect(self, society, maker, period_name):
        """August collections: most flats pay in full, some part-pay, one overpays, one cheque bounces, some default."""
        for i, flat in enumerate(society.flats.order_by("unit_no")):
            if i % 7 == 3:
                continue
            bill = flat.bills.get(period__name=period_name)
            total = sum((line.amount for line in bill.lines.all()), Decimal("0"))
            amount = total - Decimal("300") if i % 5 == 1 else total + (Decimal("500") if i == 0 else Decimal("0"))
            cheque = i % 3 == 0
            receipt = Receipt(
                society=society, flat=flat, member=flat.primary_member, receipt_date=date(2026, 8, 20 + i % 8),
                payment_mode=Receipt.CHEQUE if cheque else Receipt.UPI, amount=amount,
                cheque_no=f"{400100 + i}" if cheque else "", bank_name="HDFC Bank" if cheque else "",
                transaction_ref="" if cheque else f"UPI2608{i:04d}",
            )
            create_receipt(receipt, maker)
            auto_allocate_receipt(receipt, maker, Society.PRINCIPAL_THEN_INTEREST)
            if i == 6:
                bounce_receipt(receipt, maker, "Funds insufficient (demo)")
