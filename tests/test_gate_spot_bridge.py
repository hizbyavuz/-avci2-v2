import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from gate_spot_bridge import exact_pool, review
from gate_spot_observer import save_snapshot
from gate_spot_observer import identity
from gate_early_observer import record_scan
from gate_volume_bridge import review as review_volume


ADDRESS = "0x" + "a" * 40
OTHER = "0x" + "b" * 40


class GateSpotBridgeTest(unittest.TestCase):
    def test_solana_mint_case_is_preserved(self):
        mint = "So11111111111111111111111111111111111111112"
        self.assertEqual(identity("solana", mint), mint)
        self.assertNotEqual(identity("solana", mint), mint.lower())

    def test_onchain_history_keeps_solana_mint_case(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.db")
            mint = "So11111111111111111111111111111111111111112"
            record_scan(path, "batch", [{"network_id": "solana",
                "token_contract": mint, "pool": "pool", "price_usd": 1,
                "liquidity": 100000, "volume_5m": 1000}], now_ts=100000)
            with sqlite3.connect(path) as con:
                self.assertEqual(con.execute("""SELECT token_contract FROM
                    gate_early_observations""").fetchone()[0], mint)

    def test_volume_anomaly_requires_complete_security(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.db")
            base = 100000
            item = {"network_id": "eth", "token_contract": ADDRESS,
                    "pool": "pool", "price_usd": 1, "liquidity": 50000,
                    "volume_5m": 1000, "volume_24h": 100000,
                    "buys_5m": 20, "sells_5m": 10,
                    "buys_24h": 200, "sells_24h": 100,
                    "change_24h": 5}
            for index in range(3):
                record_scan(path, str(index), [item], now_ts=base + index*600)
            current = {**item, "price_usd": 1.05, "volume_5m": 4000,
                       "buys_5m": 60, "sells_5m": 20}
            record_scan(path, "current", [current], now_ts=base + 1800)
            result = review_volume(path, "current", [current], lambda _: None,
                (lambda _: {"risk": False}, lambda _: {"risk": False}),
                now=base + 1810)
            self.assertEqual(result["qualified"], 1)
            self.assertEqual(result["pending"], 0)
            with sqlite3.connect(path) as con:
                self.assertEqual(con.execute("""SELECT status FROM
                    gate_volume_alert_audit""").fetchone()[0], "WITHHELD")

    def test_exact_contract_and_price_are_required(self):
        base = {"network_id": "eth", "token_contract": OTHER,
                "price_usd": 1, "liquidity": 30000, "volume_24h": 100000,
                "buys_5m": 20, "sells_5m": 10, "change_5m": 1,
                "change_24h": 8}
        self.assertIsNone(exact_pool([base], "eth", ADDRESS, 1)[0])
        self.assertIsNone(exact_pool([{**base, "token_contract": ADDRESS,
                                      "price_usd": 1.15}], "eth", ADDRESS, 1)[0])

    def test_incomplete_safety_review_never_creates_pending_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.db")
            now = int(time.time())
            save_snapshot(path, "fresh", [("COIN_USDT", "COIN", "Coin", 1, 500000, 5)],
                          [("COIN_USDT", "eth", ADDRESS)])
            with sqlite3.connect(path) as con:
                con.execute("""CREATE TABLE gate_spot_watch (
                    batch_id TEXT, pair TEXT, version TEXT, price REAL,
                    change_24h REAL, rise_pct REAL, round_trip_1k_pct REAL,
                    status TEXT)""")
                con.execute("""INSERT INTO gate_spot_watch VALUES
                    ('fresh','COIN_USDT','v0',1,5,2,1,'PAPER_WATCH')""")
            pool = {"network_id": "eth", "token_contract": ADDRESS,
                    "price_usd": 1, "liquidity": 30000,
                    "volume_24h": 100000, "volume_5m": 5000,
                    "buys_24h": 200, "sells_24h": 100,
                    "buys_5m": 20, "sells_5m": 10,
                    "change_5m": 1, "change_24h": 8}
            with patch("gate_spot_bridge.security_decision",
                       return_value="Holder veya likidite koruması doğrulanamadı"):
                result = review(path, lambda _: {"data": []},
                    lambda *args, **kwargs: [pool], lambda _: None,
                    (lambda _: {"risk": False}, lambda _: {"risk": False}),
                    now=now)
            self.assertEqual(result["pending"], 0)
            with sqlite3.connect(path) as con:
                self.assertEqual(con.execute("""SELECT status FROM
                    gate_spot_bridge_audit""").fetchone()[0], "WITHHELD")


if __name__ == "__main__":
    unittest.main()
