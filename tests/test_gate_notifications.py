import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gate_early_observer import early_context, record_scan
from gate_notify import pending_alerts, security_decision, send_pending, valid_contract


CONTRACT = "So11111111111111111111111111111111111111112"


def pool(volume=100, price=1.0, liquidity=20_000):
    return {"network_id": "solana", "token_contract": CONTRACT,
            "pool": "pool1", "price_usd": price, "liquidity": liquidity,
            "volume_5m": volume, "volume_1h": volume * 12,
            "buys_5m": 20, "sells_5m": 10, "change_24h": 10}


def safe_item():
    return {**pool(300), "name": "Example", "symbol": "EX",
            "risk_band": "LOW_FLAGS", "security_risk_reasons": [],
            "climax": {"risk": False}, "trap_proxy": {"risk": False},
            "adjusted_holder": {"ok": True, "top1_pct": 10,
                                "top5_pct": 30, "top10_pct": 50},
            "lp_protection": {"status": "PARTLY_PROTECTED", "protected_pct": 80},
            "solana_security": {"ok": True, "largest_accounts_ok": True,
                "token_program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                "mint_authority_active": False, "freeze_authority_active": False,
                "top1_pct": 10, "top5_pct": 30, "top10_pct": 50},
            "exit_1k": {"ok": True, "loss_pct": 2},
            "exit_5k": {"ok": True, "loss_pct": 5},
            "validation_event_id": 1}


class GateNotificationTests(unittest.TestCase):
    def test_own_history_anomaly_and_pre_signal_gain(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder) / "obs.db")
            for i, volume in enumerate((100, 110, 90, 350)):
                record_scan(db, f"b{i}", [pool(volume, price=1 + .01 * i)],
                            now_ts=10_000 + i * 600)
            with sqlite3.connect(db) as con:
                result = early_context(con, "b3", "solana", CONTRACT, 1.03)
            self.assertEqual(result["first_anomaly_ts"], 11_800)
            self.assertAlmostEqual(result["gain_before_signal_pct"], 0)
            self.assertGreater(result["own_volume_ratio"], 3)

    def test_bad_feed_marks_scan_invalid_even_with_a_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder) / "obs.db")
            result = record_scan(db, "b", [pool()], ["eth:new_pools"],
                                 now_ts=10_000)
            self.assertEqual(result["status"], "INVALID")

    def test_security_unknown_and_token2022_never_get_clean_alert(self):
        self.assertTrue(valid_contract("solana", CONTRACT))
        item = safe_item()
        self.assertIsNone(security_decision(item))
        item["solana_security"]["token_program"] = "Token2022"
        self.assertIn("Token-2022", security_decision(item))
        item = safe_item()
        item["exit_1k"] = {"ok": False}
        self.assertIn("Satış fiyatı", security_decision(item))
        item = safe_item()
        item["solana_security"]["freeze_authority_active"] = True
        self.assertIn("dondurma", security_decision(item))
        item = safe_item()
        item["adjusted_holder"] = {"ok": False}
        self.assertIn("Holder", security_decision(item))

    def test_empty_clean_scan_is_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            result = record_scan(str(Path(folder) / "obs.db"), "empty", [],
                                 now_ts=10_000)
            self.assertEqual(result["status"], "VALID")
            self.assertEqual(result["observed"], 0)

    def test_only_frozen_rule_candidate_is_queued_once(self):
        with tempfile.TemporaryDirectory() as folder:
            obs = str(Path(folder) / "obs.db")
            val = str(Path(folder) / "val.db")
            now = 10_000
            record_scan(obs, "batch", [pool()], now_ts=now)
            with sqlite3.connect(val) as con:
                con.execute("""CREATE TABLE validation_events
                    (id INTEGER, batch_id TEXT, group_type TEXT,
                     network_id TEXT, token_contract TEXT, signal_ts INTEGER,
                     signal_iso TEXT, signal_price REAL, rulesets TEXT)""")
                con.executemany("INSERT INTO validation_events VALUES (?,?,?,?,?,?,?,?,?)", [
                    (1, "batch", "CANDIDATE", "solana", CONTRACT, now,
                     "2026-09-23T00:00:00+00:00", 1,
                     "R1_WAKEUP_STRICT"),
                    (2, "batch", "CANDIDATE", "solana", CONTRACT, now,
                     "2026-09-23T00:00:00+00:00", 1, ""),
                    (3, "batch", "NEAR_MISS", "solana", CONTRACT, now,
                     "2026-09-23T00:00:00+00:00", 1, "R1_WAKEUP_STRICT"),
                ])
            with sqlite3.connect(obs) as con:
                con.execute("""CREATE TABLE snapshots (id INTEGER PRIMARY KEY,
                    network_id TEXT, token_contract TEXT, zaman_utc TEXT,
                    raw_json TEXT)""")
                con.execute("INSERT INTO snapshots VALUES (1,?,?,?,?)",
                            ("solana", CONTRACT, "2026-09-23T00:00:01+00:00",
                             json.dumps(safe_item())))
            alerts = pending_alerts(obs, val)
            self.assertEqual(len(alerts), 1)
            self.assertIn("Tam kontrat: " + CONTRACT, alerts[0][1])
            self.assertEqual(pending_alerts(obs, val), [])
            with sqlite3.connect(obs) as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM gate_alert_audit").fetchone()[0], 1)

            class Response:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"ok": True}

            class Session:
                def __init__(self):
                    self.calls = []

                def post(self, url, **kwargs):
                    self.calls.append((url, kwargs))
                    return Response()

            session = Session()
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test-token",
                                         "TELEGRAM_CHAT_ID": "test-chat"}):
                self.assertEqual(send_pending(obs, val, session), 1)
                self.assertEqual(send_pending(obs, val, session), 0)
            self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
