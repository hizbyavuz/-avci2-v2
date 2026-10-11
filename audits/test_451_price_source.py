import unittest
from datetime import datetime, timezone
from long_short_451_price_source import closed_quote


class ClosedQuoteTests(unittest.TestCase):
    def test_recent_closed_quote(self):
        now="2026-10-11T10:20:00+00:00"
        close_ms=int(datetime(2026,10,11,10,19,tzinfo=timezone.utc).timestamp()*1000)
        fetch=lambda symbol, interval, limit: {"raw":[[0,0,0,0,"105",0,close_ms]]}
        self.assertEqual(closed_quote("ETHUSDT",now,fetch)[0],105)

    def test_nonfinite_quote_rejected(self):
        close_ms=int(datetime(2026,10,11,10,19,tzinfo=timezone.utc).timestamp()*1000)
        for invalid in ("nan", "inf", "-inf"):
            with self.subTest(price=invalid):
                fetch=lambda symbol, interval, limit: {"raw":[[0,0,0,0,invalid,0,close_ms]]}
                self.assertIsNone(closed_quote("ETHUSDT","2026-10-11T10:20:00+00:00",fetch))

    def test_stale_quote_rejected(self):
        close_ms=int(datetime(2026,10,11,10,10,tzinfo=timezone.utc).timestamp()*1000)
        fetch=lambda symbol, interval, limit: {"raw":[[0,0,0,0,"105",0,close_ms]]}
        self.assertIsNone(closed_quote("ETHUSDT","2026-10-11T10:20:00+00:00",fetch))

    def test_future_quote_rejected(self):
        close_ms=int(datetime(2026,10,11,10,21,tzinfo=timezone.utc).timestamp()*1000)
        fetch=lambda symbol, interval, limit: {"raw":[[0,0,0,0,"105",0,close_ms]]}
        self.assertIsNone(closed_quote("ETHUSDT","2026-10-11T10:20:00+00:00",fetch))


if __name__=="__main__":
    unittest.main()
