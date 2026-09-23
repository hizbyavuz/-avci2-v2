import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gate_early_observer import early_context, record_scan
from gate_intelligence import (creator_reputation, lp_lock_health,
                               x_contract_mentions)
from gate_research_report import build_report, summary


CONTRACT = "So11111111111111111111111111111111111111112"
EVM_CREATOR = "0x" + "a" * 40


class Response:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self.payload


class Session:
    def __init__(self, payload):
        self.payload, self.calls = payload, []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.payload)


class IntelligenceTests(unittest.TestCase):
    def test_creator_with_verified_malicious_contract_is_flagged(self):
        s = Session({"result": {"number_of_malicious_contracts_created": "2",
                                "honeypot_related_address": "1"}})
        result = creator_reputation("base", EVM_CREATOR, s, token="sample")
        self.assertEqual(result["status"], "FLAGGED")
        self.assertEqual(result["malicious_contracts"], 2)
        self.assertEqual(s.calls[0][1]["params"]["chain_id"], "8453")
        self.assertEqual(creator_reputation("solana", CONTRACT, s)["status"], "UNKNOWN")

    def test_expiring_lp_is_distinguished_from_undated_lock(self):
        now = datetime(2026, 9, 23, tzinfo=timezone.utc)
        self.assertEqual(lp_lock_health({"locked_detail": [
            {"end_time": int((now + timedelta(hours=2)).timestamp())}]}, now)["status"],
                         "EXPIRES_SOON")
        self.assertEqual(lp_lock_health({"locked_detail": []}, now)["status"],
                         "EXPIRY_UNKNOWN")
        self.assertEqual(lp_lock_health({"locked_detail": [
            {"end_time": int((now - timedelta(minutes=1)).timestamp())}]}, now)["status"],
                         "EXPIRED_OR_PARTIAL")

    def test_x_counts_use_exact_contract_and_do_not_use_ticker(self):
        now = datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc)
        s = Session({"data": [
            {"start": "2026-09-23T00:10:00Z", "tweet_count": 6},
            {"start": "2026-09-23T00:50:00Z", "tweet_count": 4}]})
        result = x_contract_mentions("solana", CONTRACT, s, token="sample", now=now)
        self.assertEqual((result["last_15m"], result["previous_45m"]), (4, 6))
        self.assertIn(CONTRACT, s.calls[0][1]["params"]["query"])
        self.assertNotIn("XPL", s.calls[0][1]["params"]["query"])

    def test_buyer_baseline_requires_history(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder) / "obs.db")
            for index, buyers in enumerate((4, 5, 4, 15)):
                pool = {"network_id": "solana", "token_contract": CONTRACT,
                        "pool": "p", "price_usd": 1, "liquidity": 20000,
                        "volume_5m": 100, "buys_5m": 20, "sells_5m": 10,
                        "unique_buyers_5m": buyers, "change_24h": 8}
                record_scan(db, f"b{index}", [pool], now_ts=10_000+index*600)
            with sqlite3.connect(db) as con:
                context = early_context(con, "b3", "solana", CONTRACT, 1)
            self.assertEqual(context["unique_buyers_5m"], 15)
            self.assertGreater(context["buyer_ratio"], 3)

    def test_unresolved_events_are_excluded_from_primary_rate(self):
        rows = [{"status": "CLOSED_72H", "result_10": "TARGET_FIRST",
                 "net_final_pct": 8, "cost_status": "QUOTE_PLUS_ASSUMPTION"},
                {"status": "DATA_FAILURE", "result_10": "STOP_FIRST",
                 "net_final_pct": None, "cost_status": "PARTIAL_DATA"}]
        self.assertEqual(summary(rows)["rate"], 100)
        self.assertEqual(summary(rows)["unresolved"], 1)

    def test_empty_report_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIn("henüz yok", build_report(str(Path(folder) / "absent.db")))

    def test_report_joins_signal_time_risk_without_counting_open_events(self):
        with tempfile.TemporaryDirectory() as folder:
            val = str(Path(folder) / "val.db")
            obs = str(Path(folder) / "obs.db")
            with sqlite3.connect(val) as con:
                con.execute("""CREATE TABLE validation_events (
                    batch_id TEXT, network_id TEXT, token_contract TEXT,
                    group_type TEXT, rulesets TEXT, result_10 TEXT,
                    status TEXT, net_final_pct REAL, cost_status TEXT,
                    signal_ts INTEGER)""")
                con.executemany("INSERT INTO validation_events VALUES (?,?,?,?,?,?,?,?,?,?)", [
                    ("b", "solana", CONTRACT, "CANDIDATE", "R1_WAKEUP_STRICT",
                     "TARGET_FIRST", "CLOSED_72H", 6, "QUOTE_PLUS_ASSUMPTION", 1000),
                    ("b", "solana", CONTRACT, "RANDOM_CONTROL", "",
                     None, "WAIT_ENTRY", None, "PARTIAL_DATA", 1000),
                ])
            record_scan(obs, "b", [{"network_id": "solana", "token_contract": CONTRACT,
                                     "price_usd": 1, "liquidity": 20_000,
                                     "volume_5m": 100, "buys_5m": 10,
                                     "sells_5m": 5}], now_ts=1000)
            with sqlite3.connect(obs) as con:
                con.execute("""CREATE TABLE gate_candidate_risk_history (
                    batch_id TEXT, network_id TEXT, token_contract TEXT,
                    scan_ts INTEGER, lp_protected_pct REAL,
                    top10_adjusted_pct REAL, unique_buyers_sample INTEGER)""")
                con.execute("INSERT INTO gate_candidate_risk_history VALUES (?,?,?,?,?,?,?)",
                            ("b", "solana", CONTRACT.lower(), 1000, 90, 40, 12))
            result = build_report(val, obs, now_ts=2000)
            self.assertIn("R1_WAKEUP_STRICT: 1 kapanmış", result)
            self.assertIn("LP ≥%80 ve ilk 10 holder <%50: 1 kapanmış", result)
            self.assertIn("| RANDOM_CONTROL | 1 | 0 | 1 |", result)


if __name__ == "__main__":
    unittest.main()
