"""Futures-only data gate: unchanged fail-closed rules, more specific diagnostics."""
import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import long_short_analyst as a
from long_short_futures_kline_shadow import initialize, upsert_rows, SOURCE, ARCHIVE_SOURCE

P5 = 300000


class DataGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "binance_futures_kline_shadow.db")
        self.env = patch.dict(os.environ, {"LS_STATE_DIR": self.tmp.name, "LS_BINANCE_FUTURES_ONLY": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        a.SHADOW_DIAG.clear()
        now = int(time.time() * 1000)
        self.last_open = (now // P5) * P5 - P5          # last fully closed 5m bar

    def write(self, opens, source=SOURCE, fields=True):
        con = initialize(self.db)
        rows = [("BTCUSDT", "5m", o, o + P5 - 1, 10.0, 12.0, 9.0, 11.0, 20.0, source, 1,
                 200.0 if fields else None, 7 if fields else None, 90.0 if fields else None)
                for o in opens]
        if fields:
            upsert_rows(con, rows)
        else:
            con.executemany("""INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        con.commit(); con.close()

    def bars(self, n, skip=()):
        return [self.last_open - i * P5 for i in range(n) if i not in skip]

    def read(self, limit=220):
        with redirect_stdout(io.StringIO()):
            return a.native_futures_shadow_klines("BTCUSDT", "5m", limit)

    def test_220_contiguous_closed_bars_pass(self):
        self.write(self.bars(220), source=ARCHIVE_SOURCE)
        rows = self.read()
        self.assertEqual(len(rows), 220)
        self.assertTrue(all(b[0] - x[0] == P5 for x, b in zip(rows, rows[1:])))
        self.assertNotIn(("BTCUSDT", "5m"), a.SHADOW_DIAG)

    def test_gap_fails_closed_with_location_and_eta(self):
        self.write(self.bars(240, skip=(10, 11)))
        self.assertIsNone(self.read())
        info = a.SHADOW_DIAG[("BTCUSDT", "5m")]
        self.assertEqual(info["reason"], "gap")
        self.assertEqual(info["latest_gap_missing_bars"], 2)
        self.assertEqual(info["latest_gap_first_missing_open_ms"], self.last_open - 11 * P5)
        self.assertEqual(info["contiguous_tail_bars"], 10)
        self.assertEqual(info["eta_minutes_without_new_gap"], (220 - 10) * 5)

    def test_null_fields_fail_closed(self):
        self.write(self.bars(220), fields=False)
        self.assertIsNone(self.read())
        self.assertEqual(a.SHADOW_DIAG[("BTCUSDT", "5m")]["null_field_rows"], 220)

    def test_spot_source_fails_closed(self):
        self.write(self.bars(220), source="BINANCE_SPOT")
        self.assertIsNone(self.read())
        self.assertEqual(a.SHADOW_DIAG[("BTCUSDT", "5m")]["bad_source_rows"], 220)

    def test_short_history_and_stale(self):
        self.write(self.bars(50))
        self.assertIsNone(self.read())
        self.assertEqual(a.SHADOW_DIAG[("BTCUSDT", "5m")]["reason"], "short_history")
        a.SHADOW_DIAG.clear()
        os.remove(self.db)
        self.write([self.last_open - (i + 10) * P5 for i in range(220)])
        self.assertIsNone(self.read())
        self.assertEqual(a.SHADOW_DIAG[("BTCUSDT", "5m")]["reason"], "stale")

    def test_strict_mode_never_falls_back_and_explains_reason(self):
        self.write(self.bars(240, skip=(3,)))
        with patch.object(a, "fget", side_effect=AssertionError("REST/Spot must not be called")), \
             patch.object(a, "multi_venue_perp_klines", side_effect=AssertionError("other venue")):
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, r"BINANCE_FUTURES_ONLY.*reason=gap"):
                    a.fetch_klines("BTCUSDT", "5m", 220)

    def test_summary_counts_reasons(self):
        self.write(self.bars(240, skip=(3,)))
        self.read()
        summary = a.data_gate_summary([("BTCUSDT", 1, 1, 0), ("ETHUSDT", 1, 1, 0)], [],
                                      {"BTCUSDT": {"error_type": "RuntimeError"},
                                       "ETHUSDT": {"error_type": "RuntimeError"}})
        self.assertEqual(summary["prefilter_failed"], 2)
        self.assertEqual(summary["by_reason"], {"gap": 1, "other:RuntimeError": 1})
        self.assertEqual(summary["by_interval_reason"]["5m:gap"], 1)


if __name__ == "__main__":
    unittest.main()
