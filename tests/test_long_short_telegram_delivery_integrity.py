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
