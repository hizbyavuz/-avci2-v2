import unittest
from long_short_binance_evidence import evaluate

class EvidenceTests(unittest.TestCase):
    def test_coinalyze_funding_does_not_claim_native_source(self):
        oi={"source":"COINALYZE_BINANCE_A","oi_fresh":True,"oi_unit":"BASE_ASSET",
            "oi_change_1h":1.2,"ls_fresh":True,"long_short_ratio":1.1}
        funding={"source":"COINALYZE_BINANCE_A","funding_fresh":True,"funding_pct":0.01}
        result=evaluate(oi,funding)
        self.assertTrue(result["funding_available"])
        self.assertFalse(result["ready"])
        self.assertIn("funding_pct",result["missing"])
        self.assertIn("taker_ratio",result["missing"])
    def test_missing_is_not_neutral(self):
        result=evaluate({}, {})
        self.assertFalse(result["ready"])
        self.assertFalse(result["taker_available"])
        self.assertFalse(result["funding_available"])
if __name__=="__main__":
    unittest.main()
