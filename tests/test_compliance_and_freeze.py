import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import compliance_ledger as cl


class ComplianceLedgerTests(unittest.TestCase):
    def test_fill_sync_is_idempotent_and_unclassified(self):
        with tempfile.TemporaryDirectory() as folder:
            db=str(Path(folder)/"x.db")
            with sqlite3.connect(db) as c:
                c.execute("""CREATE TABLE execution_fill_observations(
                    source TEXT,event_key TEXT,asset TEXT,event_time_utc TEXT,side TEXT,
                    quantity REAL,expected_price REAL,filled_price REAL,fee_pct REAL,
                    slippage_pct REAL,total_realized_cost_pct REAL,external_order_id TEXT,
                    notes TEXT,imported_at_utc TEXT)""")
                c.execute("""INSERT INTO execution_fill_observations VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("BINANCE","e1","ABCUSDT","2026-09-26T20:00:00+00:00","BUY",
                     2,10,10.1,0.1,1.0,None,"o1","read-only","2026-09-26T20:01:00+00:00"))
                cl.init(c)
                self.assertEqual(cl.sync_fill_table(c,"BINANCE"),1)
                self.assertEqual(cl.sync_fill_table(c,"BINANCE"),0)
                row=c.execute("""SELECT gross_value,fee_value,tax_status,evidence_type
                    FROM compliance_transactions""").fetchone()
                self.assertAlmostEqual(row[0],20.2)
                self.assertAlmostEqual(row[1],0.0202)
                self.assertEqual(row[2],"UNCLASSIFIED")
                self.assertEqual(row[3],"READ_ONLY_FILL_IMPORT")

    def test_transfer_preserves_jurisdiction_without_inferring_tax(self):
        class Args:
            source="GATE";time="2026-09-26T20:00:00+00:00";asset="SOL"
            quantity=1.0;direction="INTERNAL";from_account="exchange"
            to_account="wallet";network="solana";fee=0.001;txid="tx1"
            jurisdiction="TR";notes="test"
        with tempfile.TemporaryDirectory() as folder:
            db=str(Path(folder)/"x.db")
            with sqlite3.connect(db) as c:
                cl.init(c)
                self.assertEqual(cl.add_transfer(c,Args),1)
                row=c.execute("""SELECT jurisdiction,tax_status,direction
                    FROM compliance_transfers""").fetchone()
                self.assertEqual(row,("TR","UNCLASSIFIED","INTERNAL"))


class GenesisManifestTests(unittest.TestCase):
    def test_manifest_has_prospective_contract(self):
        m=json.loads(Path("genesis_freeze_manifest.json").read_text(encoding="utf-8"))
        self.assertRegex(m["baseline_git_commit"], r"^[0-9a-f]{40}$")
        self.assertNotEqual(m["baseline_git_commit"],
                            "9d3c428c63b1282e97c7a22a3925d3d679a2c8e2")
        self.assertEqual(m["genesis_holdout_start_utc"],
                         "2026-10-07T00:00:00+00:00")
        self.assertTrue(m["policy"]["no_retroactive_reseal"])
        self.assertIn("scanner.py",m["contamination_sensitive_files"])
        self.assertIn("binance_scanner.py",m["contamination_sensitive_files"])
        self.assertIn("gate_weighted_discovery.py",m["contamination_sensitive_files"])


if __name__=="__main__":
    unittest.main()
