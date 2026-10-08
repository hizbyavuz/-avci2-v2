#!/usr/bin/env python3
"""Regression for observational Telegram delivery without changing trade gates."""
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import long_short_live_pool as live
import long_short_simple_notify as notify

class ObservationalDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.was=live.TELEGRAM_TRADE_ONLY
        live.TELEGRAM_TRADE_ONLY=False
        self.row={
            "symbol":"SOLUSDT","direction":"LONG","trigger_level":115.6,
            "retest_low":115.4,"retest_high":115.8,
            "invalidation":114.5,"target1":116.2,"target2":117.4,
            "data_mode":"BINANCE_SPOT_GRAPH_ONLY",
            "structure_gate_json":"{}",
        }

    def tearDown(self):
        live.TELEGRAM_TRADE_ONLY=self.was

    def test_early_close_marked_non_trade(self):
        msg=live.message_for(self.row,"CLOSE_CONFIRMED",115.7,115.6)
        self.assertIn("LONG TEYİT GELDİ",msg)
        self.assertIn("henüz işlem sinyali değil",msg)
        self.assertIsNone(live.message_for(self.row,"APPROACHING",115.7,115.6))
        self.assertIsNone(live.message_for(self.row,"RETESTING",115.7,115.6))

    def test_final_trigger_message_unchanged(self):
        msg=live.message_for(self.row,"TRIGGERED",115.7,115.6)
        self.assertTrue(msg.startswith("🟢 LONG SİNYALİ"))
        self.assertIn("SL:",msg)
        self.assertIn("TP1:",msg)
        self.assertIn("otomatik emir açılmadı",msg)
        self.assertNotIn("DEVAM MOTORU",msg)

    def test_stale_backlog_never_delivered(self):
        olddb=notify.DB
        try:
            with tempfile.TemporaryDirectory() as td:
                notify.DB=str(Path(td)/"notify.db")
                notify.init_db()
                ok=notify.queue_alert("OLDUSDT","LONG",101,"old, no longer actionable")
                self.assertTrue(ok)
                with sqlite3.connect(notify.DB) as c:
                    c.execute("UPDATE pending_alerts SET updated_at_epoch=?",
                              (time.time()-notify.STALE_AFTER_SECONDS-90,))
                self.assertIsNone(notify.claim_ready_alert())
                with sqlite3.connect(notify.DB) as c:
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM pending_alerts").fetchone()[0],0)
                ok=notify.queue_alert("NEWUSDT","SHORT",102,"fresh watch only")
                self.assertTrue(ok)
                item=notify.claim_ready_alert()
                self.assertIsNotNone(item)
                self.assertEqual(item["symbol"],"NEWUSDT")
        finally:
            notify.DB=olddb

if __name__=="__main__":
    unittest.main()
