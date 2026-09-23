import tempfile
import unittest
from pathlib import Path
import sqlite3
import time

from gate_spot_observer import build_snapshot, coverage_report, save_snapshot


ADDRESS = "0x" + "a" * 40


class GateSpotObserverTest(unittest.TestCase):
    def test_only_active_pairs_with_official_chain_address_are_mapped(self):
        currencies = [
            {"currency": "GOOD", "name": "Good", "delisted": False,
             "trade_disabled": False,
             "chains": [{"name": "ETH", "addr": ADDRESS},
                        {"name": "OTHER", "addr": ADDRESS}]},
            {"currency": "BAD", "name": "Bad", "delisted": False,
             "trade_disabled": False, "chains": [{"name": "ETH", "addr": ""}]},
            {"currency": "BCH5L", "name": "BCH5xLong", "delisted": False,
             "trade_disabled": False, "chains": []},
        ]
        pairs = [
            {"id": "GOOD_USDT", "base": "GOOD", "quote": "USDT",
             "trade_status": "tradable", "type": "normal"},
            {"id": "BAD_USDT", "base": "BAD", "quote": "USDT",
             "trade_status": "tradable", "type": "normal"},
            {"id": "GOOD_BTC", "base": "GOOD", "quote": "BTC",
             "trade_status": "tradable", "type": "normal"},
            {"id": "BCH5L_USDT", "base": "BCH5L", "quote": "USDT",
             "trade_status": "tradable", "type": "normal"},
        ]
        tickers = [
            {"currency_pair": pair["id"], "last": "1",
             "quote_volume": "40000", "change_percentage": "12"}
            for pair in pairs
        ]
        tickers[-1]["etf_leverage"] = "3.7"
        market, contracts = build_snapshot(currencies, pairs, tickers)
        self.assertEqual({row[0] for row in market}, {"GOOD_USDT", "BAD_USDT"})
        self.assertEqual(contracts, [("GOOD_USDT", "eth", ADDRESS)])
        del tickers[-1]["etf_leverage"]
        self.assertNotIn("BCH5L_USDT", {row[0] for row in build_snapshot(
            currencies, pairs, tickers)[0]})

    def test_error_clears_stale_market_data(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "state.db")
            save_snapshot(db, "batch1", [("GOOD_USDT", "GOOD", "Good", 1, 40000, 3)],
                          [("GOOD_USDT", "eth", ADDRESS)])
            save_snapshot(db, "batch2", [], [], "RequestException")
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute(
                    "SELECT count(*) FROM gate_spot_market").fetchone()[0], 0)
                self.assertEqual(con.execute(
                    "SELECT status FROM gate_spot_health WHERE batch_id='batch2'"
                ).fetchone()[0], "ERROR")
                self.assertEqual(con.execute(
                    "SELECT count(*) FROM gate_spot_history").fetchone()[0], 1)

    def test_mover_coverage_requires_official_contract_match(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "state.db")
            save_snapshot(db, "gate1", [
                ("GOOD_USDT", "GOOD", "Good", 1, 40000, 22),
                ("OTHER_USDT", "OTHER", "Other", 1, 40000, 15),
            ], [("GOOD_USDT", "eth", ADDRESS)])
            with sqlite3.connect(db) as con:
                con.execute("""CREATE TABLE gate_scan_health (
                    batch_id TEXT, status TEXT, scan_ts INTEGER)""")
                con.execute("""INSERT INTO gate_scan_health VALUES
                    ('chain1', 'VALID', ?)""", (int(time.time()),))
                con.execute("""CREATE TABLE gate_early_observations (
                    batch_id TEXT, network_id TEXT, token_contract TEXT)""")
                con.execute("""INSERT INTO gate_early_observations VALUES
                    ('chain1', 'eth', ?)""", (ADDRESS,))
            result = coverage_report(db, "gate1")
            self.assertIn("+%10 2 parite, +%20 1", result)
            self.assertIn("resmi kontratı eşleşen 1", result)
            self.assertIn("on-chain gözlemde görülen 1", result)

    def test_standalone_spot_coverage_without_onchain_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "state.db")
            save_snapshot(db, "spot1", [
                ("GOOD_USDT", "GOOD", "Good", 1, 40000, 12),
            ], [("GOOD_USDT", "eth", ADDRESS)])
            self.assertIn("on-chain gözlemde görülen karşılaştırma yok",
                          coverage_report(db, "spot1"))


if __name__ == "__main__":
    unittest.main()
