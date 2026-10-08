import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from long_short_v32_room_report import report

class RoomReportTests(unittest.TestCase):
    def test_report_counts_old_overlap_vs_new_geometry(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"a.db"
            with sqlite3.connect(db) as c:
                c.execute("CREATE TABLE analyses(scan_time_utc TEXT,symbol TEXT,status TEXT,payload_json TEXT)")
                p={"structure_gate":{"room_ok":False,"qualified_precheck":False,
                         "room_pct":0.2,
                         "trigger_zone":{"low":100,"high":101},
                         "next_opposing_zone":{"low":100.6,"high":101.5}},
                   "v32_distinct_room_shadow":{"valid":True,"room_ok":True,
                        "fully_qualified_geometry":True,"room_pct":1.5,
                        "net_t1_r":1.2,"overlapping_zones_ignored":1}}
                c.execute("INSERT INTO analyses VALUES(?,?,?,?)",("2026-10-08T10:00:00+00:00","TESTUSDT","NO_TRADE",json.dumps(p)))
            r=report(str(db))
            self.assertEqual(r["counts"]["frozen_next_zone_overlapped_trigger"],1)
            self.assertEqual(r["counts"]["old_room_fail_new_room_and_r_pass"],1)
            self.assertEqual(r["counts"]["v32_room_and_r_pass"],1)
            self.assertFalse(r["changes_frozen_v31"])
    def test_missing_database_is_explicit(self):
        self.assertIn("NO_ANALYST_DATABASE",report("/no/test/db/exists")["issues"])
