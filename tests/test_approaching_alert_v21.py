import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import long_short_live_pool as lp
import long_short_simple_notify as sn


class ApproachingAlertTests(unittest.TestCase):
    def test_approaching_transition_queues_watch_alert(self):
        old_db=sn.DB
        old_notify=lp.NOTIFY_DB
        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                sn.DB=tmp.name
                lp.NOTIFY_DB=tmp.name
                row={
                    "symbol":"TESTUSDT",
                    "direction":"LONG",
                    "trigger_level":101.0,
                    "invalidation":98.0,
                    "target1":103.0,
                    "target2":105.0,
                    "data_cohort":"SPOT_PLUS_GATE",
                    "structure_gate_version":lp.STRUCTURE_GATE_VERSION,
                    "analyst_scan_time":"2026-10-06T00:00:00+00:00",
                    "analyst_confidence":58,
                }
                ok=lp.queue_approaching_alert(row,100.8,{"qualified":False})
                self.assertTrue(ok)
                with sqlite3.connect(tmp.name) as con:
                    item=con.execute("""SELECT direction,level,message,payload_json
                                        FROM pending_alerts WHERE symbol='TESTUSDT'""").fetchone()
                    self.assertIsNotNone(item)
                    self.assertEqual(item[0],"LONG")
                    self.assertAlmostEqual(item[1],101.0)
                    self.assertIn("LONG İÇİN İZLE",item[2])
                    self.assertIn("Henüz işlem teyidi değil",item[2])
                    self.assertIn('"source":"APPROACHING_STAGE"',item[3])
        finally:
            sn.DB=old_db
            lp.NOTIFY_DB=old_notify


if __name__=="__main__":
    unittest.main()
