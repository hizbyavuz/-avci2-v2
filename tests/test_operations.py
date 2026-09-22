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
    def test_candidate_identity_points_to_exact_spot_pair(self):
        identity, link = notify.candidate_identity("TIAUSDT")
        self.assertEqual(identity, "Celestia (TIA) | Spot TIA/USDT")
        self.assertEqual(link, "https://www.binance.com/en/trade/TIA_USDT?type=spot")
        unknown, link = notify.candidate_identity("UNKNOWNUSDT")
        self.assertEqual(unknown, "UNKNOWN | Spot UNKNOWN/USDT")
        self.assertIn("UNKNOWN_USDT?type=spot", link)

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
