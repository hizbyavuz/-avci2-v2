import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import avci_daily_followup as followup


class DailyFollowupTests(unittest.TestCase):
    def test_turkey_day_includes_evening_utc_of_previous_day(self):
        start, end = followup.utc_range(date(2026, 9, 22))
        self.assertEqual(start.isoformat(), "2026-09-21T21:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-09-22T21:00:00+00:00")

    def test_binance_uses_only_exact_target_minute_and_marks_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "binance.db"
            with sqlite3.connect(db) as conn:
                conn.executescript("""CREATE TABLE signal_events (
                    id INTEGER, signal_time_utc TEXT, symbol TEXT, signal_price REAL,
                    event_class TEXT, config_version TEXT);
                    CREATE TABLE raw_klines (symbol TEXT, interval_value TEXT,
                    open_time_ms INTEGER, close_price REAL);""")
                signal = datetime(2026, 9, 22, 10, 14, 23, tzinfo=timezone.utc)
                target_ms = followup.target_minute(int(signal.timestamp() * 1000))
                conn.execute("INSERT INTO signal_events VALUES (?,?,?,?,?,?)",
                             (1, signal.isoformat(), "TESTUSDT", 10, "CANDIDATE", "v2.4"))
                conn.execute("INSERT INTO raw_klines VALUES (?,?,?,?)",
                             ("TESTUSDT", "1m", target_ms - 60000, 11))
            start, end = followup.utc_range(date(2026, 9, 22))
            with patch.object(followup, "binance_price", return_value=None) as price:
                rows = followup.binance_rows(db, start, end,
                    now=datetime(2026, 9, 24, tzinfo=timezone.utc))
                self.assertIsNone(rows[0]["24s_fiyati_usd"])
                self.assertIn("verisi yok", rows[0]["olcum"])
                self.assertEqual(price.call_args.args[1], target_ms)
            with sqlite3.connect(db) as conn:
                conn.execute("INSERT INTO raw_klines VALUES (?,?,?,?)",
                             ("TESTUSDT", "1m", target_ms, 11))
                self.assertEqual(followup.binance_price("TESTUSDT", target_ms, conn), 11)

    def test_gate_missing_price_remains_unmeasured(self):
        row = followup.result_row("Gate", datetime(2026, 9, 22, 10,
                                   tzinfo=timezone.utc), "ABC", "solana", "addr",
                                   2, None, "v5", "mum yok")
        self.assertIsNone(row["24s_degisimi_yuzde"])
        self.assertIn("verisi eksik: 1", followup.build_message(
            date(2026, 9, 22), [row], "hizbyavuz/-avci2-v2", "123"))

    def test_report_describes_24h_snapshot_not_profit(self):
        row = followup.result_row("Binance", datetime(2026, 9, 22, 10,
                                  tzinfo=timezone.utc), "XPLUSDT", "", "",
                                  .096, .1056, "v2.4", "")
        message = followup.build_message(date(2026, 9, 22), [row],
                                         "hizbyavuz/-avci2-v2", "123")
        self.assertIn("+10.00%", message)
        self.assertIn("Arada erişilen en yüksek fiyatı", message)
        self.assertIn("Bot hesabında işlem yapmadı", message)


if __name__ == "__main__":
    unittest.main()
