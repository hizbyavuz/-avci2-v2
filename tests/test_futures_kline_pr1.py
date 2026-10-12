"""Deterministic PR-1 native Futures closed-candle tests; no network."""
import os
import tempfile
import unittest
from long_short_futures_kline_shadow import initialize, parse_closed_kline, store


class NativeFuturesKlineTests(unittest.TestCase):
    def setUp(self):
        self.bar = {
            "t": 1700000000000, "T": 1700000059999,
            "o": "100", "h": "101", "l": "99", "c": "100.5",
            "v": "8", "q": "802", "n": 13, "Q": "401",
            "i": "1m", "x": True, "s": "BTCUSDT",
        }

    def test_open_candle_is_never_persisted(self):
        self.bar["x"] = False
        self.assertIsNone(parse_closed_kline({"e": "kline", "s": "BTCUSDT", "k": self.bar}))

    def test_closed_native_candle_round_trip(self):
        row = parse_closed_kline({"e": "kline", "s": "BTCUSDT", "k": self.bar})
        self.assertEqual(row[0], "BTCUSDT")
        self.assertEqual(row[1], "1m")
        with tempfile.TemporaryDirectory() as folder:
            con = initialize(os.path.join(folder, "futures.db"))
            try:
                store(con, row)
                stored = con.execute("SELECT close,quote_volume,trade_count,source FROM futures_closed_klines").fetchone()
                self.assertEqual(stored, (100.5, 802.0, 13, "BINANCE_UM_FUTURES_WEBSOCKET"))
            finally:
                con.close()

    def test_non_usdt_symbol_is_rejected(self):
        self.bar["s"] = "BTCUSD"
        self.assertIsNone(parse_closed_kline({"e": "kline", "s": "BTCUSD", "k": self.bar}))


if __name__ == "__main__":
    unittest.main()
