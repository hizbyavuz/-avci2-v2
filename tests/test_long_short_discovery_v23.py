import json
import time
import unittest

import long_short_analyst as analyst
import long_short_data_router as router
import long_short_live_pool as live


class DiscoveryV23Tests(unittest.TestCase):
    def test_multi_venue_perp_universe_merges_sources(self):
        old_bybit, old_gate = router._bybit, router._gate
        try:
            router._bybit = lambda path, params=None: {
                "result": {"list": [
                    {
                        "symbol": "RLCUSDT",
                        "turnover24h": "1200000000",
                        "lastPrice": "0.8",
                        "price24hPcnt": "0.50",
                    },
                    {
                        "symbol": "ONLYBYBITUSDT",
                        "turnover24h": "50000000",
                        "lastPrice": "1.0",
                        "price24hPcnt": "0.10",
                    },
                ]}
            }
            router._gate = lambda path, params=None: [
                {
                    "contract": "RLC_USDT",
                    "last": "0.81",
                    "change_percentage": "48",
                    "volume_24h_quote": "900000000",
                },
                {
                    "contract": "CROSS_USDT",
                    "last": "2",
                    "change_percentage": "20",
                    "volume_24h_quote": "60000000",
                },
            ]
            rows = router.multi_venue_perp_universe()
            by_symbol = {x["symbol"]: x for x in rows}
            self.assertIn("RLCUSDT", by_symbol)
            self.assertEqual(set(by_symbol["RLCUSDT"]["providers"]), {"BYBIT_LINEAR", "GATE_FUTURES"})
            self.assertEqual(by_symbol["RLCUSDT"]["quote_volume"], 1_200_000_000.0)
            self.assertAlmostEqual(by_symbol["RLCUSDT"]["day_change_pct"], 50.0)
        finally:
            router._bybit, router._gate = old_bybit, old_gate

    def test_fetch_klines_uses_external_perp_when_spot_missing(self):
        old_fget = analyst.fget
        old_ext = analyst.multi_venue_perp_klines
        old_mode = analyst.DATA_MODE
        try:
            analyst.DATA_MODE = "BINANCE_SPOT_GRAPH_ONLY"
            analyst.fget = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("spot invalid symbol"))
            now = int(time.time() * 1000)
            rows = []
            for i in range(30):
                ts = now - (31 - i) * 300_000
                rows.append([
                    ts, "1", "1.1", "0.9", "1.05", "100",
                    ts + 299_999, "1000", 0, "0", "0", "0",
                ])
            analyst.multi_venue_perp_klines = lambda symbol, interval, limit=220: {
                "provider": "BYBIT_LINEAR",
                "rows": rows,
            }
            got = analyst.fetch_klines("FUTURESONLYUSDT", "5m", 90)
            self.assertGreaterEqual(len(got["close"]), 20)
            self.assertAlmostEqual(got["close"][-1], 1.05)
        finally:
            analyst.fget = old_fget
            analyst.multi_venue_perp_klines = old_ext
            analyst.DATA_MODE = old_mode

    def test_fresh_listing_does_not_crash_on_short_daily_history(self):
        old_fetch = analyst.fetch_htf_cached
        try:
            analyst.fetch_htf_cached = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("not enough closed candles (12)")
            )
            out = analyst.build_htf_gate("NEWUSDT", {
                "price": 1.0, "ema20": 0.9, "ema50": 0.8,
                "breakout20": False, "breakdown20": False,
                "change_4": 1.0,
            })
            self.assertFalse(out["qualified"])
            self.assertTrue(out["unavailable"])
            self.assertEqual(out["direction"], "NONE")
        finally:
            analyst.fetch_htf_cached = old_fetch

    def test_geo_blocked_universe_uses_perp_turnover_not_spot_turnover(self):
        old_fget = analyst.fget
        old_perps = analyst.multi_venue_perp_universe
        old_mode = analyst.DATA_MODE
        old_max = analyst.MAX_SYMBOLS
        old_min = analyst.MIN_24H_QUOTE_VOL
        try:
            analyst.DATA_MODE = "BINANCE_SPOT_GRAPH_ONLY"
            analyst.MAX_SYMBOLS = 80
            analyst.MIN_24H_QUOTE_VOL = 25_000_000

            def fake_fget(path, params=None):
                analyst.DATA_MODE = "BINANCE_SPOT_GRAPH_ONLY"
                if path.endswith("exchangeInfo"):
                    return {"symbols": [
                        {"symbol": "RLCUSDT", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "RLC"},
                        {"symbol": "BTCUSDT", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "BTC"},
                    ]}
                if path.endswith("ticker/24hr"):
                    return [
                        {"symbol": "RLCUSDT", "quoteVolume": "1000000", "lastPrice": "0.8", "priceChangePercent": "1"},
                        {"symbol": "BTCUSDT", "quoteVolume": "1000000000", "lastPrice": "70000", "priceChangePercent": "2"},
                    ]
                raise AssertionError(path)

            analyst.fget = fake_fget
            analyst.multi_venue_perp_universe = lambda: [
                {
                    "symbol": "RLCUSDT", "quote_volume": 1_200_000_000,
                    "last_price": 0.8, "day_change_pct": 50.0,
                    "providers": ["BYBIT_LINEAR"],
                },
                {
                    "symbol": "CROSSUSDT", "quote_volume": 80_000_000,
                    "last_price": 2.0, "day_change_pct": 30.0,
                    "providers": ["BYBIT_LINEAR", "GATE_FUTURES"],
                },
                {
                    "symbol": "RANDOMUSDT", "quote_volume": 90_000_000,
                    "last_price": 3.0, "day_change_pct": 40.0,
                    "providers": ["BYBIT_LINEAR"],
                },
            ]
            rows = analyst.universe()
            got = {x[0]: x for x in rows}
            self.assertIn("RLCUSDT", got)
            self.assertEqual(got["RLCUSDT"][1], 1_200_000_000.0)
            self.assertEqual(got["RLCUSDT"][3], 50.0)
            self.assertIn("CROSSUSDT", got)
            self.assertNotIn("RANDOMUSDT", got)
        finally:
            analyst.fget = old_fget
            analyst.multi_venue_perp_universe = old_perps
            analyst.DATA_MODE = old_mode
            analyst.MAX_SYMBOLS = old_max
            analyst.MIN_24H_QUOTE_VOL = old_min


    def test_radar_only_mover_cannot_become_confirmed_signal(self):
        class Row(dict):
            def keys(self):
                return super().keys()

        row=Row({
            "direction":"LONG",
            "trigger_level":100.0,
            "retest_low":99.5,
            "retest_high":100.0,
            "invalidation":98.0,
            "stage":"WATCH",
            "structure_gate_json":json.dumps({"_radar_only":True}),
        })
        state=live.next_stage(
            row,
            price=100.5,
            closed=100.4,
            structure_quality={"qualified":True},
        )
        self.assertEqual(state,"WATCH")

        state=live.next_stage(
            row,
            price=100.1,
            closed=100.4,
            structure_quality={"qualified":True},
        )
        self.assertEqual(state,"APPROACHING")


if __name__ == "__main__":
    unittest.main()
