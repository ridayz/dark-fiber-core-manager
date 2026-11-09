"""Tests for the core model, lease rules and summary math."""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "server"))

import store  # noqa: E402


REF = store.today()


def iso(days):
    return (REF + timedelta(days=days)).isoformat()


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        store.init_db(self.db_path, seed=False)
        self.conn = store.get_conn(self.db_path)
        self.tenant = store.create_tenant(self.conn, {"name": "PT Uji Fiber", "code": "UJI-001"})
        self.cable = store.create_cable(
            self.conn,
            {"code": "BB-UJI-01", "name": "Uji – Rute", "route": "A – B", "core_count": 96},
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def make_lease(self, cores, start=-10, end=200, purpose="Dark Fiber Lease"):
        return store.create_lease(
            self.conn,
            {
                "tenant_id": self.tenant["id"],
                "cable_id": self.cable["id"],
                "cores": cores,
                "purpose": purpose,
                "start_date": iso(start),
                "end_date": iso(end),
            },
        )


class FiberColorTest(unittest.TestCase):
    def test_standard_twelve_color_cycle(self):
        first = store.core_color(1)
        self.assertEqual((first["tube"], first["pos"], first["name"]), (1, 1, "Biru"))
        last_of_tube = store.core_color(12)
        self.assertEqual((last_of_tube["tube"], last_of_tube["pos"], last_of_tube["name"]), (1, 12, "Aqua"))
        second_tube = store.core_color(13)
        self.assertEqual((second_tube["tube"], second_tube["pos"], second_tube["name"]), (2, 1, "Biru"))

    def test_576_core_grid(self):
        last = store.core_color(576)
        self.assertEqual((last["tube"], last["pos"]), (48, 12))


class LeaseStateTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(store.lease_state(iso(-10), iso(200), None, REF), "active")
        self.assertEqual(store.lease_state(iso(-10), iso(20), None, REF), "expiring")
        self.assertEqual(store.lease_state(iso(-90), iso(-1), None, REF), "expired")
        self.assertEqual(store.lease_state(iso(10), iso(400), None, REF), "reserved")
        self.assertEqual(store.lease_state(iso(-10), iso(200), "2026-11-09T10:00:00", REF), "released")


class LeaseRulesTest(BaseCase):
    def test_bulk_allocate_and_counts(self):
        lease = self.make_lease([1, 2, 3, 4])
        self.assertEqual(lease["core_count"], 4)
        self.assertEqual(lease["core_label"], "1–4")
        self.assertTrue(lease["batch_id"].startswith("DF-"))

        cable = store.get_cable(self.conn, self.cable["id"])
        self.assertEqual(cable["leased"], 4)
        self.assertEqual(cable["available"], 92)
        self.assertEqual(cable["utilization"], 4.2)

    def test_conflicting_core_is_rejected(self):
        self.make_lease([10, 11])
        with self.assertRaises(store.StoreError) as ctx:
            self.make_lease([11, 12])
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("11", ctx.exception.message)

    def test_release_frees_cores_and_keeps_history(self):
        lease = self.make_lease([20, 21, 22])
        result = store.release_lease(self.conn, lease["batch_id"])
        self.assertEqual(result["released_cores"], 3)
        self.assertEqual(store.get_cable(self.conn, self.cable["id"])["leased"], 0)
        again = self.make_lease([20, 21, 22])
        self.assertNotEqual(again["batch_id"], lease["batch_id"])
        # The released lease is no longer in the active list.
        active = [l["batch_id"] for l in store.list_leases(self.conn)]
        self.assertNotIn(lease["batch_id"], active)

    def test_core_outside_capacity_rejected(self):
        with self.assertRaises(store.StoreError):
            self.make_lease([97])

    def test_duplicate_core_numbers_are_collapsed(self):
        lease = self.make_lease([5, 5, 6])
        self.assertEqual(lease["core_count"], 2)

    def test_end_before_start_rejected(self):
        with self.assertRaises(store.StoreError):
            self.make_lease([30], start=10, end=5)

    def test_maintenance_cable_blocks_new_lease(self):
        store.update_cable(self.conn, self.cable["id"], {"status": "maintenance"})
        with self.assertRaises(store.StoreError) as ctx:
            self.make_lease([1])
        self.assertEqual(ctx.exception.status, 409)

    def test_inactive_tenant_blocks_new_lease(self):
        store.update_tenant(self.conn, self.tenant["id"], {"status": "suspended"})
        with self.assertRaises(store.StoreError):
            self.make_lease([1])

    def test_extend_lease(self):
        lease = self.make_lease([40], end=30)
        updated = store.update_lease(self.conn, lease["batch_id"], {"end_date": iso(400)})
        self.assertEqual(updated["end_date"], iso(400))
        self.assertEqual(updated["state"], "active")

    def test_summary_totals(self):
        self.make_lease([1, 2, 3], end=10)  # expiring
        summary = store.summary(self.conn)
        self.assertEqual(summary["totals"]["capacity"], 96)
        self.assertEqual(summary["totals"]["leased"], 3)
        self.assertEqual(summary["totals"]["available"], 93)
        self.assertEqual(summary["totals"]["leases"], 1)
        self.assertEqual(len(summary["expiring"]), 1)
        self.assertEqual(summary["by_size"][0]["leased"], 3)


class SeedTest(unittest.TestCase):
    def test_seed_has_tenants_but_no_stock_cables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "seed.db")
            store.init_db(db_path)
            conn = store.get_conn(db_path)
            try:
                self.assertEqual(len(store.list_tenants(conn)), 6)
                summary = store.summary(conn)
                # Cables come from the real sheet plant sync, not from seed data.
                self.assertEqual(summary["totals"]["capacity"], 0)
                self.assertEqual(summary["totals"]["cables"], 0)
            finally:
                conn.close()

    def test_sync_plants_populates_cable_inventory(self):
        import sheets

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "plants.db")
            store.init_db(db_path, seed=False)
            conn = store.get_conn(db_path)
            try:
                conn.execute(
                    """INSERT INTO sheet_entries (gid, row_no, col_no, section, panel, port, destination, note)
                       VALUES ('0', 7, 2, 'RAK 001 BAGIAN BELAKANG', 'OTB 96.1 RAK 001 JK2',
                               'Port 11-12', 'otb96.4 port 55&56', 'alibaba')"""
                )
                touched = sheets.sync_plants(conn, sheets._now())
                self.assertGreater(touched, 0)
                cables = store.list_cables(conn)
                self.assertEqual(len(cables), 1)
                cable = cables[0]
                self.assertEqual(cable["core_count"], 96)
                self.assertEqual(cable["source"], "sheet")
                self.assertEqual(cable["tab_name"], "RAK JK-2")
                self.assertEqual(cable["total_ports"], 1)

                detail = store.get_cable(conn, cable["id"])
                self.assertEqual(len(detail["entries"]), 1)
                self.assertEqual(detail["entries"][0]["destination"], "otb96.4 port 55&56")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
