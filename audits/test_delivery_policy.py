"""Offline acceptance tests for the V3.1 delivery time policy."""
import unittest
from long_short_telegram_delivery_integrity import policy

class DeliveryPolicyTests(unittest.TestCase):
    def test_recent_closed_candle(self):
        result = policy("TRIGGERED","2026-10-10T12:00:30+00:00","2026-10-10T12:00:00+00:00")
        self.assertTrue(result["allowed"])
    def test_stale_candle_rejected(self):
        result = policy("TRIGGERED","2026-10-10T12:03:00+00:00","2026-10-10T12:00:00+00:00")
        self.assertFalse(result["allowed"])
    def test_future_close_rejected(self):
        result = policy("CLOSE_CONFIRMED","2026-10-10T12:00:00+00:00","2026-10-10T12:01:00+00:00")
        self.assertFalse(result["allowed"])
    def test_unannounced_invalidation_suppressed(self):
        result = policy("INVALIDATED","2026-10-10T12:00:00+00:00","2026-10-10T12:00:00+00:00")
        self.assertFalse(result["allowed"])

if __name__ == "__main__":
    unittest.main()
