import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import long_short_v32_shadow as shadow


def kline(i, o, h, l, c, v=100.0, q=10000.0, tbq=5000.0):
    return [i*300000, str(o), str(h), str(l), str(c), str(v),
            i*300000+299999, str(q), 100, "0", str(tbq), "0"]


class V32ShadowTests(unittest.TestCase):
    def test_support_sweep_reclaim_is_detected(self):
        rows=[]
        for i in range(50):
            c=100.0 + ((i%6)-3)*0.05
            rows.append(kline(i,c,c+0.2,c-0.2,c))
        rows[38]=kline(38,99.5,99.7,99.0,99.4)
        rows[39]=kline(39,99.4,99.8,99.2,99.6)
        rows[-3]=kline(47,99.4,99.7,98.8,99.35,120,12000,4800)
        rows[-2]=kline(48,99.35,99.9,99.2,99.75,140,14000,8400)
        rows[-1]=kline(49,99.75,100.2,99.5,100.0,150,15000,9750)
        m=shadow.micro_features(rows, taker_available=True)
        self.assertTrue(m["support_sweep_reclaim"])
        self.assertGreater(m["taker_buy_share"],0.5)

    def test_long_reversal_can_exist_while_v31_direction_is_short(self):
        m={
            "dist_support_pct":0.2,
            "dist_resistance_pct":2.0,
            "support_sweep_reclaim":True,
            "resistance_sweep_reject":False,
            "higher_low":True,
            "lower_high":False,
            "structure_5m":0,
            "ema7_slope_pct":0.05,
            "volume_mult":1.2,
            "taker_buy_share":0.58,
            "taker_delta":0.08,
        }
        payload={
            "v3":{"direction":"SHORT","residual":{"residual_3h_pct":0.3}},
            "spot_flow":{"delta_share":0.12},
            "oi":{"oi_change_1h":-2.0},
            "funding_pct":0.01,
            "long_short_ratio":1.0,
        }
        h=shadow.independent_hypotheses(m,payload)
        self.assertGreaterEqual(h["long_rev_evidence"],5)
        self.assertGreater(h["long_rev_evidence"],h["short_rev_evidence"])

    def test_shadow_is_isolated_from_live_and_telegram(self):
        src=Path(shadow.__file__).read_text()
        self.assertNotIn("long_short_live_pool",src)
        self.assertNotIn("send_telegram",src)
        self.assertNotIn("TELEGRAM_BOT_TOKEN",src)


if __name__=="__main__":
    unittest.main()
