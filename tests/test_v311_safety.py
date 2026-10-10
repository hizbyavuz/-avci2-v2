import unittest
from long_short_r_math import trade_net_r
from long_short_direction_engine import decide_direction

class V311SafetyTests(unittest.TestCase):
    def test_mirror_net_r(self):
        long = trade_net_r("LONG",100,99,102,0.3)
        short = trade_net_r("SHORT",100,101,98,0.3)
        self.assertTrue(long["ok"] and short["ok"])
        self.assertAlmostEqual(long["net_r"],short["net_r"])
    def test_bad_geometry(self):
        self.assertEqual(trade_net_r("LONG",100,101,102,0.3)["reason"],"geometry_invalid")
    def test_nan_score_fail_closed(self):
        x=decide_direction(long_score=float("nan"),short_score=0,deriv_ready=True,actionable_liquidity_ok=True,external_only_unverified=False)
        self.assertFalse(x["eligible"])
        self.assertIn("score_not_finite",x["hard_blockers"])
    def test_invalid_residual_neutral(self):
        kwargs=dict(long_score=60,short_score=20,deriv_ready=True,actionable_liquidity_ok=True,external_only_unverified=False)
        a=decide_direction(**kwargs)
        b=decide_direction(**kwargs,residual={"residual_3h_pct":float("nan")})
        self.assertEqual(a["best_score"],b["best_score"])
if __name__=="__main__":
    unittest.main()
