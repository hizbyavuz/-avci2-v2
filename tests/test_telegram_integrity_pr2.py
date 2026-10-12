"""PR-2 alert timing regression: confirmation vs retest trigger."""
import unittest
from datetime import datetime, timedelta, timezone
from long_short_telegram_delivery_integrity import policy


class AlertTimingTests(unittest.TestCase):
    def test_stale_confirmation_suppressed(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(seconds=180)).isoformat()
        decision = policy("CLOSE_CONFIRMED", now.isoformat(), old)
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "STALE_OR_FUTURE_5M_CLOSE")

    def test_fresh_trigger_after_retest_not_suppressed(self):
        now = datetime.now(timezone.utc)
        original_close = (now - timedelta(seconds=280)).isoformat()
        decision = policy("TRIGGERED", now.isoformat(), original_close)
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["reason"], "TRIGGER_OBSERVED_NOW")

    def test_trigger_future_candle_is_rejected(self):
        now = datetime.now(timezone.utc)
        future = (now + timedelta(seconds=20)).isoformat()
        self.assertFalse(policy("TRIGGERED", now.isoformat(), future)["allowed"])

    def test_unannounced_invalidation_stays_silent(self):
        now = datetime.now(timezone.utc).isoformat()
        self.assertFalse(policy("INVALIDATED", now, now, prior_visible=False)["allowed"])


if __name__ == "__main__":
    unittest.main()
