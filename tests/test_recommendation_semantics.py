import sqlite3
import tempfile
import unittest
from pathlib import Path

import recommendation_followup as rf
from binance_engine_status_v2 import report_bucket


class RecommendationSemanticsTests(unittest.TestCase):
    def test_report_bucket_separates_watch_from_trade_ready(self):
        self.assertEqual(report_bucket("PAPER_ELIGIBLE"), "TRADE_READY")
        self.assertEqual(report_bucket("WATCH"), "RESEARCH_WATCH")
        self.assertEqual(report_bucket("NOT_READY"), "NOT_READY")
        self.assertEqual(report_bucket(None), "NOT_READY")

    def test_binance_recommendation_ledger_only_accepts_paper_eligible(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/"x.db")
            with sqlite3.connect(path) as c:
                c.row_factory=sqlite3.Row
                rf.init(c)
                c.executescript("""
                CREATE TABLE scans(
                  scan_time_utc TEXT, health_status TEXT
                );
                CREATE TABLE binance_candidate_evidence(
                  scan_time_utc TEXT, symbol TEXT, summary TEXT,
                  evidence_count INTEGER, counter_count INTEGER
                );
                CREATE TABLE features(
                  scan_time_utc TEXT, symbol TEXT, price REAL,
                  wakeup INTEGER, reignition INTEGER, trigger INTEGER,
                  retention INTEGER, climax_risk INTEGER
                );
                CREATE TABLE trade_readiness(
                  source TEXT, batch_key TEXT, asset_key TEXT, readiness TEXT
                );
                """)
                ts="2026-09-27T00:00:00+00:00"
                c.execute("INSERT INTO scans VALUES(?,?)",(ts,"VALID"))
                for sym,readiness in (("GOODUSDT","PAPER_ELIGIBLE"),
                                      ("WATCHUSDT","WATCH"),
                                      ("BADUSDT","NOT_READY")):
                    c.execute("INSERT INTO binance_candidate_evidence VALUES(?,?,?,?,?)",
                              (ts,sym,"DAYANAK_ORTA",7,2))
                    c.execute("INSERT INTO features VALUES(?,?,?,?,?,?,?,?)",
                              (ts,sym,1.0,1,0,1,1,0))
                    c.execute("INSERT INTO trade_readiness VALUES(?,?,?,?)",
                              ("BINANCE",ts,sym,readiness))
                added=rf.register_binance(c)
                self.assertEqual(added,1)
                rows=c.execute("""SELECT asset_key,qualification
                                  FROM recommendation_followups""").fetchall()
                self.assertEqual([(r["asset_key"],r["qualification"]) for r in rows],
                                 [("GOODUSDT","PAPER_ELIGIBLE")])

    def test_legacy_unqualified_rows_are_not_due_recommendations(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/"x.db")
            with sqlite3.connect(path) as c:
                c.row_factory=sqlite3.Row
                rf.init(c)
                c.execute("""CREATE TABLE features(
                    scan_time_utc TEXT,symbol TEXT,price REAL)""")
                c.execute("INSERT INTO features VALUES(?,?,?)",
                          ("2026-09-27T00:00:00+00:00","OLDUSDT",2.0))
                c.execute("""INSERT INTO recommendation_followups
                    (source,asset_key,display_name,recommended_at_utc,
                     recommendation_price,evidence_tier,early_dot,due_at_utc,status)
                    VALUES('BINANCE','OLDUSDT','OLDUSDT',
                    '2026-09-20T00:00:00+00:00',1.0,'DAYANAK_ORTA','🟡',
                    '2026-09-23T00:00:00+00:00','PENDING')""")
                self.assertEqual(rf.process_due(c,"BINANCE"),[])


if __name__=="__main__":
    unittest.main()
