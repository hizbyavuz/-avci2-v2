import os
import tempfile
import unittest

import binance_outcome_labeler as outcome
import binance_scanner as scanner
import binance_snapshot_store as store
import binance_report as report


def kline(open_ms, open_price=100, high=101, low=99, close=100,
          volume=1000, trades=100):
    return [open_ms, str(open_price), str(high), str(low), str(close), "0",
            open_ms + 299999, str(volume), trades, "0", str(volume * .6), "0"]


class CoreMathTests(unittest.TestCase):
    def test_confidence_interval_uses_scan_blocks(self):
        self.assertEqual(report.cohort_interval({"one": [1, 0, 1]}),
                         (None, None))
        low, high = report.cohort_interval({"one": [1, 1], "two": [0]})
        self.assertLessEqual(low, 50)
        self.assertGreaterEqual(high, 50)

    def test_same_scan_co_movement_is_observed(self):
        self.assertAlmostEqual(scanner.return_correlation([1, -1] * 6,
                                                           [2, -2] * 6), 1.0)
        self.assertIsNone(scanner.return_correlation([0] * 12, [1] * 12))

    def test_tokenized_securities_are_excluded_without_blocking_crypto(self):
        for base in ("AMDB", "NVDAB", "QQQB", "TSLAB", "AAPLX", "SPYX"):
            self.assertTrue(scanner.is_excluded_base(base), base)
        for base in ("ETH", "SOL", "LINK", "XRP", "BNB"):
            self.assertFalse(scanner.is_excluded_base(base), base)

    def test_kline_quality_detects_gap(self):
        rows = [kline(0), kline(600000)]
        with self.assertRaises(ValueError):
            scanner.validate_kline_rows(rows, 900000)

    def test_impulse_retention_uses_first_impulse(self):
        count = 50
        parsed = {
            "closes": [100.0] * 40 + [100.0, 100.5, 102.0] + [101.5] * 7,
            "lows": [99.5] * count,
            "highs": [100.5] * 40 + [100.5, 101.0, 102.5] + [102.5] * 7,
            "quote_volumes": [100.0] * 40 + [300.0, 300.0, 300.0] + [100.0] * 7,
            "open_times": [i * 300000 for i in range(count)],
        }
        result = scanner.impulse_retention(parsed, 300.0)
        self.assertIsNotNone(result["impulse_start_ms"])
        self.assertGreater(result["retention"], 0)

    def test_near_miss_distance(self):
        strong = {"volume_z_15m": 3, "trade_z_15m": 3, "return_z_15m": 3,
                  "volume_mult_1h": 2, "retention_proxy": .7,
                  "reignition_ratio": 2, "change_15m": 1,
                  "trigger_components": {"a": True, "b": True, "c": True}}
        weak = {"volume_z_15m": 0, "trade_z_15m": 0, "return_z_15m": 0,
                "volume_mult_1h": .5, "retention_proxy": .1,
                "reignition_ratio": .2, "change_15m": -1,
                "trigger_components": {}}
        self.assertLess(scanner.qualification_distance(strong),
                        scanner.qualification_distance(weak))

    def test_gap_stop_uses_executable_open(self):
        event = {"symbol": "TESTUSDT"}
        rows = [kline(0, 100, 101, 99, 100), kline(300000, 88, 90, 87, 89)]
        result = outcome.evaluate_barrier(event, rows, 100, 0, 0, 10, True)
        self.assertEqual(result["result"], "STOP")
        self.assertLess(result["net_return_pct"], -7)


class DatabaseTests(unittest.TestCase):
    def test_schema_and_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = store.DB_FILE
            try:
                store.DB_FILE = os.path.join(directory, "test.db")
                store.init_db()
                store.save_manual_trade("E1", "ENTRY", "2026-01-01T00:00:00+00:00", 10)
                conn = store.open_db()
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM manual_trades").fetchone()[0], 1)
                self.assertIn("raw_json", {r["name"] for r in conn.execute(
                    "PRAGMA table_info(orderbook_snap)")})
                self.assertIn("btc_flash_crash", {r["name"] for r in conn.execute(
                    "PRAGMA table_info(scans)")})
                conn.close()
            finally:
                store.DB_FILE = previous


if __name__ == "__main__":
    unittest.main()
