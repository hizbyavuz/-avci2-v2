import unittest
from datetime import datetime,timedelta,timezone
from long_short_telegram_delivery_integrity import policy

class DeliveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,8,7,30,tzinfo=timezone.utc)

    def _age(self,seconds,stage="CLOSE_CONFIRMED"):
        return policy(stage,self.now.isoformat(),(self.now-timedelta(seconds=seconds)).isoformat())

    def test_fresh_close_allowed(self):
        self.assertTrue(self._age(5)["allowed"])
        self.assertTrue(self._age(60)["allowed"])

    def test_three_to_four_minute_old_close_is_never_sent(self):
        for seconds in (195,217,260):
            with self.subTest(seconds=seconds):
                r=self._age(seconds)
                self.assertFalse(r["allowed"])
                self.assertEqual(r["reason"],"STALE_OR_FUTURE_5M_CLOSE")

    def test_triggered_stale_same_guard(self):
        self.assertFalse(self._age(270,"TRIGGERED")["allowed"])

    def test_missing_time_fails_closed(self):
        r=policy("TRIGGERED",self.now.isoformat(),None)
        self.assertFalse(r["allowed"])
        self.assertEqual(r["reason"],"INVALID_CLOSE_TIMESTAMP")

    def test_future_candle_fails_closed(self):
        self.assertFalse(self._age(-60)["allowed"])

    def test_orphan_cancellation_silent_but_recorded(self):
        self.assertFalse(policy("INVALIDATED",self.now.isoformat(),None,False)["allowed"])
        self.assertTrue(policy("INVALIDATED",self.now.isoformat(),None,True)["allowed"])

    def test_non_trade_observation_not_modified_by_guard(self):
        self.assertTrue(policy("APPROACHING",self.now.isoformat(),None)["allowed"])


class DeliveredWatchCancellationTests(unittest.TestCase):
    def test_only_current_seen_setup_can_get_cancellation(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import long_short_live_pool as pool
        now=datetime(2026,10,8,7,30,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"notify.db"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE sent_alerts(symbol TEXT,direction TEXT,level REAL,sent_at_epoch REAL)")
                con.execute("INSERT INTO sent_alerts VALUES(?,?,?,?)",
                            ("TESTUSDT","LONG",101.0,now.timestamp()-40))
            with sqlite3.connect(":memory:") as con:
                con.execute("""CREATE TABLE events(symbol TEXT,direction TEXT,
                             event_time_utc TEXT,stage_to TEXT,telegram_status TEXT)""")
                r={"symbol":"TESTUSDT","direction":"LONG","trigger_level":101.0,
                   "setup_locked_at_utc":(now-timedelta(minutes=2)).isoformat()}
                with patch.object(pool,"NOTIFY_DB",str(db)):
                    self.assertTrue(pool._has_sent_confirmation_for_this_setup(con,r))
                    other=dict(r,trigger_level=110.0)
                    self.assertFalse(pool._has_sent_confirmation_for_this_setup(con,other))
                    with sqlite3.connect(db) as nc:
                        nc.execute("DELETE FROM sent_alerts")
                    self.assertFalse(pool._has_sent_confirmation_for_this_setup(con,r))
                    con.execute("INSERT INTO events VALUES(?,?,?,?,?)",
                               ("TESTUSDT","LONG",(now-timedelta(seconds=15)).isoformat(),
                                "CLOSE_CONFIRMED","SENT"))
                    self.assertTrue(pool._has_sent_confirmation_for_this_setup(con,r))
