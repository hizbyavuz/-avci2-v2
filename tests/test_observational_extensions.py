import sqlite3
import tempfile
import unittest
from pathlib import Path

from avci_checkpoints import due
from binance_opportunity_observer import miss_reason
from binance_deception_observer import classify_cex_evidence
from gate_deception_observer import classify_gate_evidence
from telegram_readable import record_initial, due_followups, mark_followup

class ObservationalExtensionTests(unittest.TestCase):
    def test_checkpoint_window(self):
        self.assertTrue(due(900, 900))
        self.assertTrue(due(1200, 900))
        self.assertFalse(due(2000, 900))

    def test_missed_mover_reason_is_explainable(self):
        row={
            "climax_risk":1,"spread_bps":35,"retention_proxy":0.2,
            "persistence":0,"reignition":0,"cross_sectional_rarity_pct":20
        }
        reasons=miss_reason(row)
        self.assertIn("CLIMAX_RISK", reasons)
        self.assertIn("SPREAD_TOO_WIDE", reasons)
        self.assertIn("LOW_RETENTION", reasons)


    def test_binance_deception_flags_are_observational(self):
        risk, flags, positives = classify_cex_evidence(
            3.5,
            {
                "trade_count": 100,
                "repeated_notional_ratio": 0.45,
                "side_alternation_ratio": 0.90,
                "top5_notional_share": 0.70,
                "large_trades": 8,
                "large_buy_share": 0.70,
            },
            True,
        )
        self.assertEqual(risk, "HIGH")
        self.assertIn("REPEATED_SIZE_PATTERN", flags)
        self.assertIn("CROSS_VENUE_CONFIRMATION", positives)

    def test_gate_deception_combines_counter_evidence(self):
        risk, flags, positives = classify_gate_evidence(
            top10=90, lp=35, wash=True, creator="FLAGGED", buyer_ratio=2.0
        )
        self.assertEqual(risk, "HIGH")
        self.assertIn("TOP10_CONCENTRATION_HIGH", flags)
        self.assertIn("LP_PROTECTION_LOW", flags)
        self.assertIn("BUYER_BREADTH_GROWTH", positives)

    def test_followup_history_updates_without_new_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/"x.db")
            con=sqlite3.connect(path)
            record_initial(con,"h","event-1","ABCUSDT",100,100,None)
            con.commit()
            rows=due_followups(con,"h",lambda _key,_symbol:104,min_pp=3.0)
            self.assertEqual(len(rows),1)
            self.assertAlmostEqual(rows[0]["change"],4.0,places=6)
            mark_followup(con,"h","event-1",104,4.0)
            con.commit()
            rows=due_followups(con,"h",lambda _key,_symbol:105,min_pp=3.0)
            self.assertEqual(rows,[])
            con.close()

if __name__=="__main__":
    unittest.main()
