import unittest

import decision_quality_layer as dq
import performance_brake as pb


class DecisionQualityTests(unittest.TestCase):
    def test_independent_evidence_families_are_deduplicated(self):
        fam=dq.family_set([
            "Net gerçek alım akışı güçlü",
            "Piyasa emirlerinde alış üstün",
            "BTC'den belirgin güçlü",
            "15dk canlı izleme teyidi geçti",
        ])
        self.assertIn("FLOW",fam)
        self.assertIn("RELATIVE",fam)
        self.assertIn("LIVE",fam)
        self.assertEqual(len([x for x in fam if x=="FLOW"]),1)

    def test_lateness_flags_extended_move(self):
        risk,reason=dq.lateness(28.0,20.0)
        self.assertEqual(risk,"HIGH")
        self.assertIn("24 saatte",reason)

    def test_old_runner_is_context_not_hard_block(self):
        risk,_=dq.lateness(4.0,120.0)
        self.assertEqual(risk,"MEDIUM")

    def test_profit_factor(self):
        self.assertAlmostEqual(pb.pf([2.0,-1.0,1.0]),3.0)


if __name__=="__main__":
    unittest.main()
