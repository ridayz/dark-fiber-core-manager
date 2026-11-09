"""Tests for the OTB sheet parser and the SQLite sync cache."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "server"))

import sheets  # noqa: E402
import store  # noqa: E402


SAMPLE = [
    ["RAK 001 BAGIAN BELAKANG", "", "", "", "", ""],
    ["OTB 96.1 RAK 001", "", "", "OTB 24.1 RAK 002", "", ""],
    ["No. Port", "Tujuan", "Ket", "No. Port", "Tujuan", "Ket"],
    ["Port 1-2", "OTB 24 port 3&4", "alibaba", "Port 5-6", "SW1 port 1", "trunk"],
    ["", "", "", "", "", ""],
    ["Port 3-4", "SW2 port 2", "backup", "", "", ""],
    ["OTB 96.2 RAK 001", "", "", "", "", ""],
    ["No. Port", "Tujuan", "Ket", "", "", ""],
    ["Port 1", "OLT PON 1", "akses", "", "", ""],
]

GENERIC = [
    ["DATA USER NON GPON", "", "", ""],
    ["No", "User", "Cust ID", "Core"],
    ["1", "PT Kiriu", "0030120497", "OTB 96 port 92"],
    ["2", "PT Jaya", "0047700794", "OTB 24 port 1"],
]

GOOD_CSV = "No. Port,Tujuan,Ket\nPort 1-2,SW1,trunk\nPort 3-4,SW2,backup\n"


class ParserCase(unittest.TestCase):
    def test_two_blocks_side_by_side(self):
        rows = sheets.parse_csv(
            "\n".join(",".join(cell for cell in row) for row in SAMPLE[:6])
        )
        self.assertEqual(len(rows), 3)
        first = rows[0]
        self.assertEqual(first["section"], "RAK 001 BAGIAN BELAKANG")
        self.assertEqual(first["panel"], "OTB 96.1 RAK 001")
        self.assertEqual(first["port"], "Port 1-2")
        self.assertEqual(first["note"], "alibaba")
        right = [row for row in rows if row["panel"] == "OTB 24.1 RAK 002"]
        self.assertEqual(len(right), 1)
        self.assertEqual(right[0]["port"], "Port 5-6")

    def test_new_panel_title_stops_previous_block(self):
        result = sheets.parse_tab(SAMPLE)
        panels = {row["panel"] for row in result}
        self.assertIn("OTB 96.2 RAK 001", panels)
        second = [row for row in result if row["panel"] == "OTB 96.2 RAK 001"]
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["destination"], "OLT PON 1")

    def test_generic_columns_are_kept(self):
        result = sheets.parse_tab(GENERIC)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["panel"], "DATA USER NON GPON")
        self.assertEqual(result[0]["port"], "1")
        self.assertEqual(result[0]["destination"], "PT Kiriu")
        self.assertIn("0030120497", result[0]["note"])
        self.assertIn("core", result[0]["fields"])


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        store.init_db(self.db_path, seed=False)
        self.conn = store.get_conn(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_sync_stores_entries_and_skips_unchanged(self):
        with mock.patch.object(sheets, "TABS", [("g1", "Gedung")]), mock.patch.object(
            sheets, "fetch_tab", return_value=GOOD_CSV
        ):
            first = sheets.sync(self.conn, force=True)
        self.assertTrue(first["ok"])
        self.assertEqual(first["changed"], 1)
        self.assertEqual(len(sheets.entries(self.conn, "g1")), 2)

        with mock.patch.object(sheets, "TABS", [("g1", "Gedung")]), mock.patch.object(
            sheets, "fetch_tab", return_value=GOOD_CSV
        ):
            second = sheets.sync(self.conn, force=True)
        self.assertTrue(second["ok"])
        self.assertEqual(second["changed"], 0)
        self.assertEqual(len(sheets.entries(self.conn, "g1")), 2)

    def test_failed_fetch_keeps_cached_entries(self):
        with mock.patch.object(sheets, "TABS", [("g1", "Gedung")]), mock.patch.object(
            sheets, "fetch_tab", return_value=GOOD_CSV
        ):
            sheets.sync(self.conn, force=True)

        with mock.patch.object(sheets, "TABS", [("g1", "Gedung")]), mock.patch.object(
            sheets, "fetch_tab", side_effect=RuntimeError("offline")
        ):
            result = sheets.sync(self.conn, force=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"], 1)
        self.assertEqual(len(sheets.entries(self.conn, "g1")), 2)
        row = self.conn.execute("SELECT error FROM sheet_tabs WHERE gid = 'g1'").fetchone()
        self.assertIn("offline", row["error"])

    def test_search_finds_destination(self):
        with mock.patch.object(sheets, "TABS", [("g1", "Gedung")]), mock.patch.object(
            sheets, "fetch_tab", return_value=GOOD_CSV
        ):
            sheets.sync(self.conn, force=True)
        with mock.patch.object(sheets, "TABS", [("g1", "Gedung")]):
            hits = sheets.search(self.conn, "SW2")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["port"], "Port 3-4")
        self.assertEqual(hits[0]["tab_name"], "Gedung")


if __name__ == "__main__":
    unittest.main()
