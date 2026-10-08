import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from long_short_delivered_direction_audit import report

class DeliveredDirectionAuditTests(unittest.TestCase):
    def test_counts_only_sent_and_respects_missing_pending_outcomes(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"live.db"
            with sqlite3.connect(db) as c:
                c.execute("""CREATE TABLE events(id INTEGER,event_time_utc TEXT,symbol TEXT,direction TEXT,
                    stage_to TEXT,telegram_status TEXT,telegram_sent_time_utc TEXT,
                    condition_time_utc TEXT,payload_json TEXT)""")
                c.execute("""CREATE TABLE delivered_signal_outcomes(event_id INTEGER,horizon_min INTEGER,
                   direction_correct INTEGER,net_return_pct REAL,first_barrier TEXT,
                   price_source TEXT,price_source_matched INTEGER)""")
                c.execute("INSERT INTO events VALUES(1,?,?,?,?,?,?,?,?)",
                    ("2026-10-08T08:04:00+00:00","BADUSDT","SHORT","CLOSE_CONFIRMED","SENT",
                     "2026-10-08T08:04:00+00:00","2026-10-08T08:00:00+00:00","{}"))
                c.execute("INSERT INTO events VALUES(2,?,?,?,?,?,?,?,?)",
                    ("2026-10-08T08:04:00+00:00","HIDDENUSDT","SHORT","CLOSE_CONFIRMED","SUPPRESSED",
                     None,"2026-10-08T08:00:00+00:00","{}"))
                c.execute("INSERT INTO delivered_signal_outcomes VALUES(1,60,0,-0.6,'TIMEOUT','BINANCE_SPOT',1)")
            r=report(str(db),str(Path(td)/"missing"))
            self.assertEqual(r["counts"]["delivered_first_candle"],1)
            self.assertEqual(r["counts"]["delivered_stale_5m_close"],1)
            self.assertEqual(r["by_horizon"]["15"]["evaluated"],0)
            self.assertEqual(r["by_horizon"]["60"]["direction_wrong"],1)
            self.assertEqual(r["by_horizon"]["60"]["futures_native_outcomes"],0)
            self.assertIn("ANALYST_DB_NOT_FOUND",r["issues"])
