import unittest

from gate_weighted_discovery import score_item


class GateWeightedDiscoveryTests(unittest.TestCase):
    def base(self):
        return {
            "liquidity": 100000,
            "volume_24h": 500000,
            "volume_1h": 12000,
            "volume_5m": 1500,
            "change_24h": 8,
            "change_1h": 2,
            "change_5m": 0.8,
            "buys_5m": 18,
            "sells_5m": 7,
            "buys_1h": 120,
            "sells_1h": 70,
            "age_minutes": 10000,
            "climax": {"risk": False},
            "trap_proxy": {"risk": False},
        }

    def test_stronger_evidence_scores_higher(self):
        strong=self.base()
        weak=dict(strong)
        weak.update({
            "volume_24h": 20000,
            "volume_1h": 200,
            "volume_5m": 20,
            "change_24h": 38,
            "change_1h": 18,
            "change_5m": 12,
            "buys_5m": 2,
            "sells_5m": 8,
            "buys_1h": 8,
            "sells_1h": 20,
            "liquidity": 12000,
        })
        s1,_,_=score_item(strong,3.2)
        s2,_,_=score_item(weak,1.0)
        self.assertGreater(s1,s2)
        self.assertGreaterEqual(s1,70)

    def test_missing_history_is_not_a_hard_market_veto(self):
        score,evidence,missing=score_item(self.base(),None)
        self.assertGreater(score,40)
        self.assertIn("own_history",missing)

    def test_extended_climax_is_penalized(self):
        normal=self.base()
        risky=dict(normal)
        risky["change_24h"]=55
        risky["climax"]={"risk":True}
        a,_,_=score_item(normal,2.5)
        b,_,_=score_item(risky,2.5)
        self.assertGreater(a,b)

    def test_very_new_pool_can_score_when_activity_is_real(self):
        item=self.base()
        item.update({
            "age_minutes": 25,
            "liquidity": 50000,
            "volume_5m": 2500,
            "buys_5m": 24,
            "sells_5m": 8,
            "change_24h": 9,
        })
        score,evidence,missing=score_item(item,None)
        self.assertGreaterEqual(score,55)
        self.assertTrue(any("güçlü erken aktivite" in x for x in evidence))


if __name__=="__main__":
    unittest.main()
