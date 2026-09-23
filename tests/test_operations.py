import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import os
import shutil

import avci_state_guard as state
import avci_watchdog as watchdog
import binance_notify as notify


class OperationsTests(unittest.TestCase):
    def test_paper_alerts_are_clear_and_never_claim_real_trades(self):
        alerts = [
            {"symbol": "BCHUSDT", "price": 340.4,
             "alert_kind": "PAPER_EXIT_WARNING",
             "reason": "Kağıt üzerindeki girişten %10 yukarıda (kâr gözlemi)"},
            {"symbol": "EPICUSDT", "price": 0.5467,
             "alert_kind": "PAPER_EXIT_WARNING",
             "reason": "Kağıt üzerindeki girişten %7 aşağıda (zarar sınırı)"},
            {"symbol": "PEPEUSDT", "price": 0.00000486,
             "alert_kind": "PAPER_EXIT_WARNING",
             "reason": "Sinyal fiyatının %1,5 altına indi"},
        ]
        message = notify.format_paper_alerts(alerts)
        self.assertIn("Hesabından alım veya satım yapılmadı", message)
        self.assertIn("Kâğıt üzerinde +%10 görülenler", message)
        self.assertIn("Kâğıt üzerinde -%7 görülenler", message)
        self.assertIn("İzleme uyarıları (1)", message)
        self.assertIn("0.00000486 USDT", message)
        self.assertNotIn("SATIŞ UYARISI", message)

    def test_candidate_identity_points_to_exact_spot_pair(self):
        identity, link = notify.candidate_identity("TIAUSDT")
        self.assertEqual(identity, "Celestia (TIA) | Spot TIA/USDT")
        self.assertEqual(link, "https://www.binance.com/en/trade/TIA_USDT?type=spot")
        unknown, link = notify.candidate_identity("UNKNOWNUSDT")
        self.assertEqual(unknown, "UNKNOWN | Spot UNKNOWN/USDT")
        self.assertIn("UNKNOWN_USDT?type=spot", link)

    def test_candidate_notification_separates_signal_and_paper_entry(self):
        scan = {"scan_time_utc": "2026-09-22T23:35:27+00:00",
                "health_status": "VALID_SPOT_OBSERVATION", "btc_regime": "SIDEWAYS",
                "universe_size": 111, "config_version": "v2.4",
                "config_hash": "abc", "git_sha": "def", "btc_flash_crash": False}
        candidates = [{"symbol": "XPLUSDT", "stage": "TRIGGER",
                       "engine": "SPOT_LED_DEMAND", "score": 5,
                       "signal_price": .09681,
                       "validation_tier": "OBSERVATIONAL"}]
        message = notify.build_message(scan, candidates, {})
        self.assertIn("Plasma (XPL)", message)
        self.assertIn("Sinyal anındaki fiyat: 0.09681 USDT", message)
        self.assertIn("şu anki fiyat değil", message)
        self.assertIn("vadeli piyasa desteği kontrol edilemedi", message)
        self.assertNotIn("Sürüm:", message)
        paper = notify.format_paper_alerts([{
            "symbol": "XPLUSDT", "price": .09681,
            "alert_kind": "PAPER_ENTRY", "reason": "test"}])
        self.assertIn("senin alış fiyatın değildir", paper)
        self.assertIn("kâğıt üzerinde giriş", paper)

    def test_watchdog_detects_missing_recent_success(self):
        now = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)
        old = (now - timedelta(minutes=90)).isoformat()
        runs = [{"status": "completed", "conclusion": "success",
                 "updated_at": old}]
        self.assertIn("90 dakika", watchdog.workflow_problem(runs, "Binance", now))
        runs.append({"status": "completed", "conclusion": "failure",
                     "updated_at": now.isoformat()})
        self.assertIsNotNone(watchdog.workflow_problem(runs, "Binance", now))
        runs.append({"status": "completed", "conclusion": "success",
                     "updated_at": now.isoformat()})
        self.assertIsNone(watchdog.workflow_problem(runs, "Binance", now))

    def test_rejects_incomplete_or_damaged_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.db"
            self.assertFalse(state.valid_database(path, "scans"))
            path.write_text("not a database")
            self.assertFalse(state.valid_database(path, "scans"))
            path.unlink()
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE scans (id INTEGER)")
            self.assertTrue(state.valid_database(path, "scans"))
            self.assertFalse(state.valid_database(path, "validation_events"))

    def test_restores_gate_validation_database_with_other_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "archive"
            archive.mkdir()
            for filename, table in (("avci2.db", "snapshots"),
                                    ("avci_outcomes.db", "signals"),
                                    ("avci_validation_v5.db", "validation_events")):
                with sqlite3.connect(archive / filename) as conn:
                    conn.execute(f"CREATE TABLE {table} (id INTEGER)")
                    conn.execute(f"INSERT INTO {table} VALUES (1)")
            previous = os.getcwd()
            try:
                os.chdir(root)
                def copy_archive(_artifact, destination):
                    for path in archive.iterdir():
                        shutil.copyfile(path, Path(destination) / path.name)
                with patch.object(state, "artifacts", return_value=[{
                    "created_at": "2026-09-22T00:00:00Z"}]), patch.object(
                        state, "download", side_effect=copy_archive):
                    state.restore_gate()
                self.assertTrue(state.valid_database(
                    root / "avci_validation_v5.db", "validation_events"))
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
