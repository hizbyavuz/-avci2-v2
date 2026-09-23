import sqlite3
import tempfile
import unittest
from pathlib import Path

from avci_checkpoints import due
from binance_opportunity_observer import miss_reason
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
