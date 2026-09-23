import sqlite3
import tempfile
import unittest
from pathlib import Path

from avci_checkpoints import due
from binance_opportunity_observer import miss_reason
from binance_deception_observer import classify_cex_evidence
from gate_deception_observer import classify_gate_evidence
from gate_notify import observation_only_reason
from gate_security_confidence import assess
from gate_missing_evidence_notify import build_message as build_gate_watch_message
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

    def test_gate_missing_only_reason_is_observation_not_clean_candidate(self):
        self.assertTrue(observation_only_reason(
            "Risk işaretleri var veya güvenlik verisi eksik: EVM_EXIT_DATA_MISSING"))
        self.assertTrue(observation_only_reason("LP_DATA_MISSING"))
        self.assertFalse(observation_only_reason(
            "Risk işaretleri var veya güvenlik verisi eksik: LP_LOW_PROTECTION"))
        self.assertFalse(observation_only_reason(
            "Risk işaretleri var veya güvenlik verisi eksik: EXIT_1K_LOSS_HIGH"))
        self.assertFalse(observation_only_reason(
            "Risk işaretleri var veya güvenlik verisi eksik"))

    def test_gate_security_confidence_strong_medium_blocked(self):
        strong = {
            "network_id":"solana","token_contract":"A"*32,
            "security_risk_reasons":[],
            "solana_security":{"ok":True,"mint_authority_active":False,
                "freeze_authority_active":False,"top10_pct":40},
            "exit_1k":{"ok":True,"loss_pct":1.0},
            "exit_5k":{"ok":True,"loss_pct":2.0},
            "adjusted_holder":{"ok":True,"top10_pct":40},
            "lp_protection":{"protected_pct":90},
            "creator_reputation":{"status":"NO_FINDING"},
            "trade_cluster":{"wash_proxy":False},
        }
        self.assertEqual(assess(strong)["label"], "STRONG")

        blocked = dict(strong)
        blocked["security_risk_reasons"]=["EXIT_1K_LOSS_HIGH"]
        self.assertEqual(assess(blocked)["label"], "BLOCKED")

        weak = {"network_id":"bsc","token_contract":"0x"+"1"*40,
                "security_risk_reasons":["SECURITY_API_UNAVAILABLE"]}
        self.assertEqual(assess(weak)["label"], "WEAK")

    def test_gate_watch_message_matches_readable_candidate_shape(self):
        message = build_gate_watch_message(
            "VOLUME", "solana", "A"*32, "LP_DATA_MISSING",
            {"price":0.01,"change_24h":7.5,"volume_ratio":3.2,
             "buys_5m":20,"sells_5m":8,"liquidity":50000})
        self.assertIn("GATE AVCI 2 | YENİ İZLEME ADAYI", message)
        self.assertIn("👀 Neden geldi?", message)
        self.assertIn("🛡 Güvenlik", message)
        self.assertIn("🧭 Bu ne demek?", message)
        self.assertIn("Güvenlik güveni: ZAYIF", message)

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
