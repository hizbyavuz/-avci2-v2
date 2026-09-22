import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import avci_monthly_report as report


class MonthlyReportTests(unittest.TestCase):
    def test_month_filters_events_and_excludes_open_from_success_count(self):
        with tempfile.TemporaryDirectory() as folder:
            binance = Path(folder) / "binance.db"
            with sqlite3.connect(binance) as conn:
                conn.executescript("""CREATE TABLE signal_events (
                    id INTEGER, event_id TEXT, signal_time_utc TEXT,
                    symbol TEXT, event_class TEXT, config_version TEXT,
                    validation_tier TEXT, outcome_status TEXT);
                    CREATE TABLE outcome_labels (event_id TEXT, label_status TEXT,
                    barrier_results_json TEXT, net_return_pct REAL,
                    excess_vs_btc_pct REAL);
                    CREATE TABLE scans (scan_time_utc TEXT, health_status TEXT);""")
                conn.execute("INSERT INTO scans VALUES (?, ?)",
                             ("2026-09-22T01:00:00+00:00", "VALID_FULL"))
                conn.executemany("INSERT INTO signal_events VALUES (?,?,?,?,?,?,?,?)", [
                    (1, "one", "2026-09-22T01:00:00+00:00", "AAAUSDT",
                     "CANDIDATE", "v2.4", "PRIMARY", "CLOSED"),
                    (2, "two", "2026-09-23T01:00:00+00:00", "BBBUSDT",
                     "CANDIDATE", "v2.4", "PRIMARY", "OPEN"),
                    (3, "old", "2026-08-31T01:00:00+00:00", "OLDUSDT",
                     "CANDIDATE", "v2.3", "PRIMARY", "CLOSED"),
                ])
                conn.execute("INSERT INTO outcome_labels VALUES (?,?,?,?,?)",
                             ("one", "CLOSED", json.dumps({"72": {
                                 "5.0": {"result": "TARGET"}}}), 4.2, 3.0))
            rows, scans, invalid = report.read_binance(binance, "2026-09")
            self.assertEqual((len(rows), scans, invalid), (2, 1, 0))
            group = report.summarize(rows, "Binance")[0]
            self.assertEqual((group["total"], group["closed"], group["hits"]),
                             (2, 1, 1))

    def test_gate_keeps_controls_and_contract_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            gate = Path(folder) / "gate.db"
            with sqlite3.connect(gate) as conn:
                conn.execute("""CREATE TABLE validation_events (signal_iso TEXT,
                    signal_ts INTEGER, id INTEGER, network_id TEXT,
                    token_contract TEXT, group_type TEXT, config_version TEXT,
                    status TEXT, result_5 TEXT, result_10 TEXT,
                    net_final_pct REAL, cost_status TEXT)""")
                conn.execute("INSERT INTO validation_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             ("2026-09-15T01:00:00+00:00", 1, 1, "solana",
                              "CONTRACT", "RANDOM_CONTROL", "v5", "CLOSED_72H",
                              "STOP_FIRST", "STOP_FIRST", -7.2, "ESTIMATED"))
            rows = report.read_gate(gate, "2026-09")
            self.assertEqual(rows[0]["kontrat"], "CONTRACT")
            self.assertEqual(report.summarize(rows, "Gate")[0]["hits"], 0)

    def test_month_format(self):
        with self.assertRaises(ValueError):
            report.month_window("2026-13")


if __name__ == "__main__":
    unittest.main()
