"""PR2: native Futures reader accepts only complete, contiguous closed candles."""
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from long_short_futures_kline_shadow import initialize
from long_short_live_pool import native_futures_closed_bars, futures_closed_snapshot, futures_market_snapshot, market_snapshot, futures_24h_closed_stats, futures_signal_authorization


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

    def test_native_book_mid_is_indicative_not_fill(self):
        with patch("long_short_live_pool.futures_closed_snapshot", return_value={
            "kl1":[[1]],"kl5":[[2]],"last_closed_5m_at":"2026-10-13T00:00:00+00:00"
        }), patch("long_short_live_pool.trigger_execution_proxy", return_value={
            "source":"BINANCE_FUTURES_BOOK","available":True,"mid":100.25
        }):
            snapshot = futures_market_snapshot("BTCUSDT")
        self.assertEqual(snapshot["price"],100.25)
        self.assertEqual(snapshot["source"],"BINANCE_FUTURES")
        self.assertEqual(snapshot["price_type"],"futures_orderbook_mid_indicative")

    def test_spot_book_never_approves_futures_price(self):
        with patch("long_short_live_pool.futures_closed_snapshot", return_value={
            "kl1":[],"kl5":[],"last_closed_5m_at":"2026-10-13T00:00:00+00:00"
        }), patch("long_short_live_pool.trigger_execution_proxy", return_value={
            "source":"BINANCE_SPOT_BOOK_PROXY","available":True,"mid":100.25
        }):
            with self.assertRaisesRegex(RuntimeError,"FUTURES_LIVE_BOOK_UNAVAILABLE"):
                futures_market_snapshot("BTCUSDT")

    def test_native_snapshot_excludes_current_breakout_bar(self):
        now=int(time.time()*1000)
        def bar(i, minutes):
            t=now-(30-i)*minutes*60000
            high=200.0 if i==29 and minutes==5 else 101.0
            return [t,100.0,high,99.0,100.5,8.0,t+minutes*60000-1,
                    800.0,12,None,400.0]
        native={"kl1":[bar(i,1) for i in range(30)],
                "kl5":[bar(i,5) for i in range(30)],
                "price":100.25}
        with patch.dict(os.environ, {"LS_NATIVE_FUTURES_SNAPSHOT":"1"}):
            with patch("long_short_live_pool.futures_market_snapshot",return_value=native):
                with patch("long_short_live_pool.futures_24h_closed_stats",return_value={"quoteVolume":20000.0,"priceChangePercent":1.5,"source":"BINANCE_FUTURES_24_CLOSED_1H_BARS"}):
                    with patch("long_short_live_pool.spot_get",side_effect=AssertionError("Spot accessed")):
                        price,closed,close_time,early=market_snapshot("BTCUSDT")
        self.assertEqual(price,100.25)
        self.assertEqual(early["live_price_source"],"BINANCE_FUTURES")
        self.assertEqual(early["local_high"],101.0)
        self.assertTrue(early["native_futures_observation_only"])
        self.assertTrue(early["rolling_24h_stats_verified"])

    def test_futures_24h_stats_from_closed_hourly_bars(self):
        def make(i):
            return (i*3600000,100,110,90,100+i,8,i*3600000+3599999,
                    1000,12,500,"BINANCE_UM_FUTURES_WEBSOCKET")
        with patch("long_short_live_pool.native_futures_closed_bars",
                   return_value=[make(i) for i in range(25)]) as read:
            stats=futures_24h_closed_stats("BTCUSDT")
        read.assert_called_once_with("BTCUSDT","1h",25,7200)
        self.assertEqual(stats["quoteVolume"],24000)
        self.assertAlmostEqual(stats["priceChangePercent"],24)
        self.assertFalse(stats["rolling_ticker"])

    def test_native_futures_never_authorizes_trade(self):
        evidence={"live_price_source":"BINANCE_FUTURES",
                  "native_futures_observation_only":True,
                  "rolling_24h_stats_verified":True}
        gate=futures_signal_authorization(evidence,{"qualified":True})
        self.assertFalse(gate["qualified"])
        self.assertEqual(gate["reason"],"native_futures_observation_only")

    def test_spot_price_cannot_authorize_futures_trade(self):
        evidence={"live_price_source":"BINANCE_SPOT",
                  "native_futures_observation_only":False,
                  "rolling_24h_stats_verified":True}
        gate=futures_signal_authorization(evidence,{"qualified":True})
        self.assertFalse(gate["qualified"])
        self.assertEqual(gate["reason"],"live_price_venue_mismatch")

    def test_missing_stats_cannot_authorize_futures_trade(self):
        evidence={"live_price_source":"BINANCE_FUTURES",
                  "native_futures_observation_only":False,
                  "rolling_24h_stats_verified":False}
        gate=futures_signal_authorization(evidence,{"qualified":True})
        self.assertFalse(gate["qualified"])
        self.assertEqual(gate["reason"],"futures_24h_stats_unverified")

    def test_gap_rejected(self):
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM futures_closed_klines WHERE open_ms=?", (self.base-60000,))
        with self.assertRaisesRegex(RuntimeError,"INSUFFICIENT"):
            self.read()

    def test_invalid_taker_volume_rejected(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE futures_closed_klines SET taker_buy_quote=quote_volume*2")
        with self.assertRaisesRegex(RuntimeError,"INVALID_VOLUME_OR_PRICE"):
            self.read()

    def test_negative_quote_volume_rejected(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE futures_closed_klines SET quote_volume=-1")
        with self.assertRaisesRegex(RuntimeError,"INVALID_VOLUME_OR_PRICE"):
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
