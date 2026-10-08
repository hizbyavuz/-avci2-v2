import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from long_short_motion_coverage_audit import audit

class CoverageTests(unittest.TestCase):
    def test_deep_scan_gap(self):
        with tempfile.TemporaryDirectory() as d:
            db=os.path.join(d,"a.db")
            with sqlite3.connect(db) as c:
                c.execute("CREATE TABLE universe_observations(scan_time_utc TEXT,symbol TEXT,shortlisted INTEGER,quote_volume REAL,payload_json TEXT)")
                now=datetime(2026,10,8,2,10,tzinfo=timezone.utc)
                def add(symbol,p5,p15,short,vol,ago):
                    payload=json.dumps({"t5":{"change_1":p5},"t15":{"change_1":p15},
                                       "discovery_meta":{"source":"TEST"}})
                    c.execute("INSERT INTO universe_observations VALUES(?,?,?,?,?)",
                            ((now-timedelta(minutes=ago)).isoformat(),symbol,short,vol,payload))
                add("UPUSDT",3.2,5.4,0,11_000_000,13)
                add("UPUSDT",3.4,6.8,0,11_000_000,5)
                add("DOWNUSDT",-3.2,-5.7,1,31_000_000,5)
                add("OLDUSDT",9,11,0,11_000_000,95)
            r=audit(db,60,now.isoformat())
            x=r["movements"]["15m"]["LONG"]["5"]
            y=r["movements"]["15m"]["SHORT"]["5"]
            self.assertEqual(r["coins"],2)
            self.assertEqual(x["symbols_with_move"],1)
            self.assertEqual(x["snapshots_with_move"],2)
            self.assertEqual(x["symbols_not_shortlisted_during_move"],1)
            self.assertEqual(x["below_25m_volume_and_not_shortlisted"],1)
            self.assertEqual(y["symbols_shortlisted_during_move"],1)
            self.assertFalse(r["full_futures_universe"])
            self.assertFalse(r["signals_or_frozen_rules_changed"])

    def test_missing_db(self):
        self.assertIn("MISSING_DB",audit("/no/such/frozen.db")["issues"])
