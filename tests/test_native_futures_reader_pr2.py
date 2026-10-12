"""PR2: native Futures reader accepts only complete, contiguous closed candles."""
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from long_short_futures_kline_shadow import initialize
from long_short_live_pool import native_futures_closed_bars, futures_closed_snapshot


class NativeFuturesReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "bars.db")
        self.now = int(time.time() * 1000)
        self.base = ((self.now - 150000) // 60000) * 60000
        conn = initialize(self.db)
        for i in range(4):
            t = self.base - (3-i)*60000
            conn.execute("""INSERT INTO futures_closed_klines
                (symbol,interval,open_ms,close_ms,open,high,low,close,volume,
                 source,received_ms,quote_volume,trade_count,taker_buy_quote)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("BTCUSDT","1m",t,t+59999,100,101,99,100.5,8,
                 "BINANCE_UM_FUTURES_WEBSOCKET",self.now,800,12,400))
        conn.commit()
        conn.close()

    def read(self):
        with patch.dict(os.environ, {"LS_FUTURES_KLINE_DB": self.db}):
            return native_futures_closed_bars("BTCUSDT","1m",4,240)

    def test_contiguous_fresh_futures_bars(self):
        self.assertEqual(len(self.read()), 4)

    def test_snapshot_does_not_claim_live_price(self):
        with patch("long_short_live_pool.native_futures_closed_bars") as read:
            read.side_effect = lambda symbol, interval, count, max_age_seconds: [
                (1000+i*(60000 if interval=="1m" else 300000),
                 100,101,99,100.5,8,
                 1000+i*(60000 if interval=="1m" else 300000)+59999,
                 802,13,401,"BINANCE_UM_FUTURES_WEBSOCKET")
                for i in range(count)]
            snapshot = futures_closed_snapshot("BTCUSDT")
        self.assertEqual(snapshot["source"], "BINANCE_FUTURES")
        self.assertFalse(snapshot["price_is_live"])
        self.assertEqual(len(snapshot["kl1"]), 30)
        self.assertEqual(len(snapshot["kl5"]), 30)
        self.assertEqual(snapshot["last_closed_5m"], 100.5)

    def test_gap_rejected(self):
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM futures_closed_klines WHERE open_ms=?", (self.base-60000,))
        with self.assertRaisesRegex(RuntimeError,"INSUFFICIENT"):
            self.read()

    def test_spot_source_rejected(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE futures_closed_klines SET source='BINANCE_SPOT'")
        with self.assertRaisesRegex(RuntimeError,"UNVERIFIED"):
            self.read()

    def test_stale_rejected(self):
        with patch.dict(os.environ, {"LS_FUTURES_KLINE_DB": self.db}):
            with self.assertRaisesRegex(RuntimeError,"STALE"):
                native_futures_closed_bars("BTCUSDT","1m",4,1)


if __name__ == "__main__":
    unittest.main()
