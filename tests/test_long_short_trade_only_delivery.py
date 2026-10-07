#!/usr/bin/env python3
"""Regression: one user-facing alert only after all V3.1 trade gates."""
import unittest

import long_short_live_pool as live


class TradeOnlyTelegramTests(unittest.TestCase):
    def setUp(self):
        self.previous=live.TELEGRAM_TRADE_ONLY
        live.TELEGRAM_TRADE_ONLY=True
        self.row={
            "symbol":"SOLUSDT", "direction":"SHORT",
            "trigger_level":115.6, "retest_low":115.4, "retest_high":115.8,
            "invalidation":116.25, "target1":114.5057, "target2":114.0182,
            "data_mode":"BINANCE_FUTURES",
        }

    def tearDown(self):
        live.TELEGRAM_TRADE_ONLY=self.previous

    def test_non_trade_states_never_alert(self):
        for stage in ("WATCH","APPROACHING","CLOSE_CONFIRMED","RETESTING",
                      "INVALIDATED","EXECUTION_BLOCKED","CLUSTER_BLOCKED"):
            with self.subTest(stage=stage):
                self.assertIsNone(live.message_for(self.row,stage,115.3,115.5))

    def test_only_triggered_has_one_compact_directional_message(self):
        msg=live.message_for(self.row,"TRIGGERED",115.3,115.5)
        self.assertTrue(msg.startswith("🔴 SHORT SİNYALİ | SOLUSDT"))
        self.assertIn("115.6000",msg)
        self.assertIn("116.2500",msg)
        self.assertIn("TP1:",msg)
        self.assertIn("TP2:",msg)
        self.assertIn("otomatik emir açılmadı",msg)
        self.assertNotIn("DEVAM MOTORU",msg)
        self.assertNotIn("OYNAK RADAR",msg)

    def test_long_has_green_header(self):
        row=dict(self.row,direction="LONG",
                 trigger_level=115.6,invalidation=114.5,
                 target1=116.2,target2=117.4)
        self.assertTrue(live.message_for(row,"TRIGGERED",115.7,115.6).startswith(
            "🟢 LONG SİNYALİ | SOLUSDT"))

    def test_trade_message_notifies_external_data_mode(self):
        row=dict(self.row,data_mode="MULTI_VENUE_PERP")
        msg=live.message_for(row,"TRIGGERED",115.3,115.5)
        self.assertIn("çoklu-venue",msg)


if __name__=="__main__":
    unittest.main()
