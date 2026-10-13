"""Binance UM Futures is the only signal data source; Spot cannot leak in."""
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import long_short_futures_kline_shadow as k


def fill(db, symbol, interval, n, now_ms):
    period = k.PERIOD_MS[interval]
    last_open = (now_ms // period) * period - period
    con = k.initialize(db)
    rows = [(symbol, interval, last_open - i * period, last_open - i * period + period - 1,
             100.0, 101.0, 99.0, 100.5, 8.0, k.SOURCE, now_ms, 800.0, 12, 400.0) for i in range(n)]
    k.upsert_rows(con, rows)
    con.commit(); con.close()


class DaemonEnforcementTests(unittest.TestCase):
    def test_spot_paths_cannot_be_enabled_by_env(self):
        import long_short_daemon as d
        with patch.dict(os.environ, {"LS_NATIVE_FUTURES_SNAPSHOT": "0", "LS_BINANCE_FUTURES_ONLY": "0",
                                     "LS_REVERSAL_TELEGRAM": "1"}):
            with redirect_stdout(io.StringIO()) as out:
                overridden = d.enforce_futures_only()
            self.assertEqual(os.environ["LS_BINANCE_FUTURES_ONLY"], "1")
            self.assertEqual(os.environ["LS_NATIVE_FUTURES_SNAPSHOT"], "1")
            self.assertEqual(os.environ["LS_REVERSAL_TELEGRAM"], "0")
            self.assertEqual(set(overridden), {"LS_NATIVE_FUTURES_SNAPSHOT", "LS_BINANCE_FUTURES_ONLY",
                                               "LS_REVERSAL_TELEGRAM"})
            self.assertIn("FUTURES_ONLY_ENFORCED", out.getvalue())


class AnalystSpotFlowTests(unittest.TestCase):
    def test_spot_flow_not_requested_in_futures_only_mode(self):
        import long_short_analyst as a
        with patch.dict(os.environ, {"LS_BINANCE_FUTURES_ONLY": "1"}), \
             patch.object(a.requests, "get", side_effect=AssertionError("Spot HTTP call")):
            flow = a.spot_delta_proxy("BTCUSDT")
        self.assertFalse(flow["available"])
        self.assertIsNone(flow["delta_share"])
        self.assertEqual(flow["provider"], "DISABLED_FUTURES_ONLY")


class LivePoolNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "binance_futures_kline_shadow.db")
        now = int(time.time() * 1000)
        fill(self.db, "BTCUSDT", "1m", 30, now)
        fill(self.db, "BTCUSDT", "5m", 30, now)
        fill(self.db, "BTCUSDT", "1h", 25, now)
        self.env = patch.dict(os.environ, {"LS_FUTURES_KLINE_DB": self.db, "LS_NATIVE_FUTURES_SNAPSHOT": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def set_last_price(self, price, age_ms):
        con = k.initialize(self.db)
        con.execute("INSERT OR REPLACE INTO futures_last_price VALUES(?,?,?,?,?)",
                    ("BTCUSDT", price, int(time.time() * 1000) - age_ms, 0, k.SOURCE))
        con.commit(); con.close()

    def test_native_snapshot_uses_futures_last_trade_without_spot_or_rest(self):
        import long_short_live_pool as lp
        self.set_last_price(100.7, 500)
        with patch.object(lp, "spot_get", side_effect=AssertionError("Spot accessed")), \
             patch.object(lp.requests, "get", side_effect=AssertionError("REST accessed")):
            price, closed, _, early = lp.market_snapshot("BTCUSDT")
        self.assertEqual(price, 100.7)
        self.assertEqual(early["live_price_source"], "BINANCE_FUTURES")
        self.assertTrue(early["native_futures_observation_only"])
        gate = lp.futures_signal_authorization(early, {"qualified": True})
        self.assertFalse(gate["qualified"])  # existing safety gate stays in force

    def test_stale_price_and_no_futures_book_fail_closed_without_spot(self):
        import long_short_live_pool as lp
        self.set_last_price(100.7, 60000)
        with patch.object(lp, "spot_get", side_effect=AssertionError("Spot accessed")), \
             patch.object(lp.requests, "get", side_effect=OSError("451")), \
             patch.object(lp, "_futures_ws_depth", side_effect=OSError("ws api down")):
            with self.assertRaisesRegex(RuntimeError, "FUTURES_LIVE_BOOK_UNAVAILABLE"):
                lp.market_snapshot("BTCUSDT")

    def test_outcome_path_reads_only_stored_futures_minutes(self):
        import long_short_live_pool as lp
        now = int(time.time() * 1000)
        start = (now // 60000) * 60000 - 30 * 60000
        bars = lp.native_futures_path_bars("BTCUSDT", start, 180)
        self.assertEqual(len(bars), 30)  # future minutes are not invented
        self.assertTrue(all(b[0] >= start for b in bars))


class StreamLastPriceTests(unittest.TestCase):
    def test_forming_1m_updates_price_but_not_candles(self):
        with tempfile.TemporaryDirectory() as folder:
            db = os.path.join(folder, "k.db")
            s = k.FuturesKlineStream(db, sleep=lambda _: None)
            s.subscribed = ["BTCUSDT"]
            now = int(time.time() * 1000)
            s._handle(json.dumps({"data": {"e": "kline", "E": now, "s": "BTCUSDT",
                      "k": {"i": "1m", "t": 60000, "T": 119999, "o": "1", "h": "2", "l": "0.5",
                            "c": "1.7", "v": "3", "q": "4", "n": 1, "Q": "1", "x": False}}}))
            con = k.initialize(db)
            self.assertTrue(s.flush(con))
            self.assertEqual(con.execute("SELECT price,event_ms FROM futures_last_price").fetchone(), (1.7, now))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM futures_closed_klines").fetchone()[0], 0)
            con.close()


if __name__ == "__main__":
    unittest.main()
