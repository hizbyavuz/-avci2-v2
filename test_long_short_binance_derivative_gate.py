import unittest
from long_short_binance_derivative_gate import assess

class GateTests(unittest.TestCase):
    def setUp(self):
        self.snap = {"source":"COINALYZE_BINANCE_A","oi_fresh":True,
                     "oi_unit":"BASE_ASSET","oi_change_1h":1.3,
                     "ls_fresh":True,"long_short_ratio":1.1,
                     "funding_fresh":True,"taker_fresh":True,"taker_coverage_ready":True}

    def test_missing_funding_and_taker_blocks(self):
        x=assess(self.snap)
        self.assertFalse(x["ready"])
        self.assertIn("funding_pct",x["missing"])
        self.assertIn("taker_ratio",x["missing"])

    def test_wrong_venue_blocks(self):
        x=assess(self.snap,0.01,1.1,"BYBIT","BINANCE_USDM")
        self.assertFalse(x["ready"])
        self.assertIn("funding_pct",x["missing"])

    def test_verified_binance_fields(self):
        x=assess(self.snap,0.01,1.1,"COINALYZE_BINANCE_A","BINANCE_UM_FUTURES_WEBSOCKET")
        self.assertTrue(x["ready"])

    def test_taker_gap_blocks(self):
        snap={**self.snap,"taker_coverage_ready":False}
        self.assertFalse(assess(snap,0.01,1.1,"COINALYZE_BINANCE_A","BINANCE_UM_FUTURES_WEBSOCKET")["ready"])

    def test_stale_funding_blocks(self):
        snap={**self.snap,"funding_fresh":False}
        self.assertFalse(assess(snap,0.01,1.1,"COINALYZE_BINANCE_A","BINANCE_UM_FUTURES_WEBSOCKET")["ready"])

    def test_missing_oi_history_blocks(self):
        snap={**self.snap,"oi_change_1h":None}
        self.assertFalse(assess(snap,0.01,1.1,"COINALYZE_BINANCE_A","BINANCE_UM_FUTURES_WEBSOCKET")["ready"])

if __name__=="__main__":
    unittest.main()
