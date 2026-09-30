"""J. Concurrency: simultaneous allocations cannot over-allocate a receipt or a bill line.

Real row locking needs PostgreSQL (SQLite serialises the whole database and ignores
select_for_update), so these threaded tests are skipped unless DATABASE_URL points at PostgreSQL.
"""

import threading
from datetime import date
from decimal import Decimal as D
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from .models import Receipt
from .services import allocate_receipt, create_receipt, generate_bill
from .testing import LedgerFixture


@skipUnless(connection.vendor == "postgresql", "row-level locking requires PostgreSQL")
class ConcurrentAllocationTests(LedgerFixture, TransactionTestCase):
    def _race(self, jobs):
        barrier = threading.Barrier(len(jobs))
        results = []

        def run(fn):
            try:
                barrier.wait(timeout=10)
                fn()
                results.append("ok")
            except ValidationError:
                results.append("rejected")
            finally:
                close_old_connections()
                connection.close()

        threads = [threading.Thread(target=run, args=(job,)) for job in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        return results

    def _bill(self):
        bill = generate_bill(self.apr, self.flat, self.maker)
        bill.lines.update(confirmed=True)
        from .services import issue_bill

        issue_bill(bill, self.user)
        return bill.lines.get(charge_head=self.main)

    def test_two_receipts_cannot_over_allocate_one_bill_line(self):
        main = self._bill()  # 800 payable
        r1, r2 = self.receipt("600"), self.receipt("600")
        results = self._race([
            lambda: allocate_receipt(r1, [{"bill_line_id": main.pk, "amount": "600"}], self.user),
            lambda: allocate_receipt(r2, [{"bill_line_id": main.pk, "amount": "600"}], self.user),
        ])
        self.assertEqual(sorted(results), ["ok", "rejected"])
        self.assertEqual(main.paid, D("600.00"))

    def test_one_receipt_cannot_be_over_allocated_by_two_requests(self):
        main = self._bill()
        water = main.bill.lines.get(charge_head=self.water)
        r = self.receipt("700")
        results = self._race([
            lambda: allocate_receipt(r, [{"bill_line_id": main.pk, "amount": "500"}], self.user),
            lambda: allocate_receipt(r, [{"bill_line_id": water.pk, "amount": "200"}, {"bill_line_id": main.pk, "amount": "100"}], self.user),
        ])
        self.assertIn("rejected", results)
        r.refresh_from_db()
        self.assertLessEqual(r.allocated_amount, D("700.00"))

    def test_parallel_receipts_get_unique_numbers(self):
        numbers = []
        lock = threading.Lock()

        def make():
            rec = create_receipt(Receipt(society=self.society, flat=self.flat, receipt_date=date(2026, 5, 1), payment_mode=Receipt.CASH, amount=D("1")), self.user)
            with lock:
                numbers.append(rec.receipt_no)

        self._race([make for _ in range(6)])
        self.assertEqual(len(numbers), 6)
        self.assertEqual(len(set(numbers)), 6)
