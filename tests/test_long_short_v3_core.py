import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from long_short_v3_core import decide_setup
import long_short_live_pool as live


def k(closes, atr=1.0):
    raw=[]
    for i,c in enumerate(closes):
        raw.append([i*60000,str(c),str(c+0.4),str(c-0.4),str(c),"1",i*60000+59999,"1000","1","0","500","0"])
    return {
        "close":list(map(float,closes)),
        "open":list(map(float,closes)),
        "high":[float(x)+0.4 for x in closes],
        "low":[float(x)-0.4 for x in closes],
        "volume":[1000.0]*len(closes),
        "raw":raw,
    }


class V3CoreTests(unittest.TestCase):
    def base_args(self):
        closes=[100.0 + ((i%4)-1.5)*0.03 for i in range(80)]
        k5=k(closes)
        k15=k(closes)
        k1h=k([95+i*0.08 for i in range(80)])
        t5={"price":100.0,"ema20":99.9,"atr":0.5}
        t15={"price":100.0,"ema20":99.8,"ema50":99.5,"atr":1.0,"atr_pct":1.0,
             "change_4":0.2,"structure":0}
        t1h={"price":101.0,"ema20":99.0,"ema50":97.0,"change_1":0.5}
        return dict(
            symbol="TESTUSDT",k5=k5,k15=k15,k1h=k1h,t5=t5,t15=t15,t1h=t1h,
            levels={"support":99.2,"resistance":100.6},
            day_change_pct=1.0,deriv_ready=True,oi_change_1h=2.0,
            funding_pct=0.01,taker_ratio=1.1,long_short_ratio=1.1,
            spot_flow={"available":True,"delta_share":0.12},
            residual={"beta":1.0,"residual_3h_pct":0.4},
        )

    def test_price_up_oi_down_blocks_long_continuation(self):
        a=self.base_args()
        a["oi_change_1h"]=-3.0
        out=decide_setup(**a)
        if out["direction"]=="LONG":
            self.assertFalse(out["eligible"])
            self.assertIn("oi_not_liquidation_or_cover",out["veto_reasons"])

    def test_spot_flow_opposite_blocks_long(self):
        a=self.base_args()
        a["spot_flow"]={"available":True,"delta_share":-0.2}
        out=decide_setup(**a)
        if out["direction"]=="LONG":
            self.assertFalse(out["eligible"])
            self.assertIn("spot_flow",out["veto_reasons"])

    def test_btc_volatility_shock_vetoes_setup(self):
        a=self.base_args()
        a["residual"]=dict(a["residual"],btc_shock_atr=3.5)
        out=decide_setup(**a)
        if out["setup_type"]!="NONE":
            self.assertFalse(out["eligible"])
            self.assertIn("btc_shock",out["veto_reasons"])

    def test_fast_path_after_two_acceptance_closes(self):
        class Row(dict):
            def keys(self):
                return super().keys()
        row=Row({
            "direction":"LONG","trigger_level":100.0,"retest_low":99.7,"retest_high":100.0,
            "invalidation":98.0,"stage":"CLOSE_CONFIRMED",
            "close_confirmed_time":"2026-10-07T00:00:00+00:00",
            "structure_gate_json":json.dumps({"_radar_only":False}),
        })
        new=live.next_stage(row,101.0,100.8,{
            "qualified":True,"acceptance_bars_beyond_last3":2,"setup_type":"BREAKOUT"
        })
        self.assertEqual(new,"TRIGGERED")

    def test_retest_touch_alone_does_not_trigger(self):
        class Row(dict):
            def keys(self):
                return super().keys()
        row=Row({
            "direction":"LONG","trigger_level":100.0,"retest_low":99.7,"retest_high":100.0,
            "invalidation":98.0,"stage":"RETESTING",
            "close_confirmed_time":"2026-10-07T00:00:00+00:00",
            "structure_gate_json":json.dumps({"_radar_only":False}),
        })
        new=live.next_stage(row,100.2,100.1,{
            "qualified":False,"acceptance_bars_beyond_last3":1,"setup_type":"BREAKOUT"
        })
        self.assertEqual(new,"RETESTING")


if __name__=="__main__":
    unittest.main()
