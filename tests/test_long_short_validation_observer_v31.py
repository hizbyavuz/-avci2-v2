import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from long_short_validation_observer_v31 import (
    _cluster_ci, _mirror_levels, _momentum_direction, _random_direction,
    _simulate, _source_health
)

T0 = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)

def candle(i, op, hi, lo, close):
    ts=int((T0+timedelta(minutes=i)).timestamp()*1000)
    return [ts,str(op),str(hi),str(lo),str(close),"1",ts+59999,
            "100","1","0","50","0"]


class BaselineTests(unittest.TestCase):
    def test_mirror_same_geometry(self):
        self.assertEqual(_mirror_levels("SHORT",100,102,96,"LONG"), (102,96))
        self.assertEqual(_mirror_levels("LONG",100,102,96,"SHORT"), (98,104))
        self.assertEqual(_mirror_levels("SHORT",100,102,96,"SHORT"), (102,96))

    def test_seeded_random_stays_same_across_horizons(self):
        self.assertEqual(_random_direction(14,"ETHUSDT"),
                         _random_direction(14,"ETHUSDT"))

    def test_momentum_cannot_read_forming_or_future_candles(self):
        bars=[candle(i,100+i,100+i,100+i,100+i) for i in range(18)]
        cutoff=T0+timedelta(minutes=10,seconds=15)
        # Candle 10 closes after decision; therefore only 0..9 count.
        self.assertEqual(_momentum_direction(bars,cutoff.isoformat()),"LONG")
        bars[10][4]="1"
        bars[11][4]="1"
        self.assertEqual(_momentum_direction(bars,cutoff.isoformat()),"LONG")

    def test_short_mirror_stop_first_if_same_bar(self):
        row={
            "entry_price":100, "direction":"LONG", "invalidation":98,
            "target1":104, "event_payload_json":json.dumps({
                "_trigger_execution_proxy": {
                    "notional_usdt":250, "costs":{"250":{"buy_bps":10,"sell_bps":10}}
                }
            }),
        }
        # A SHORT baseline mirrors to stop=102, target=96.
        bars=[candle(0,100,103,95,99)]
        result=_simulate(row,bars,"SHORT")
        self.assertEqual(result["barrier"],"STOP")
        self.assertAlmostEqual(result["stop"],102)
        self.assertAlmostEqual(result["target1"],96)
        self.assertAlmostEqual(result["cost_pct"],0.30)
        self.assertLess(result["net_pct"],0)

    def test_cluster_bootstrap_counts_waves_not_signals(self):
        pairs=[("wave-a",1,0),("wave-a",-1,0),("wave-b",2,0)]
        out=_cluster_ci(pairs,repeats=80)
        self.assertEqual(out["clusters"],2)
        self.assertAlmostEqual(out["mean_diff_pct"],1.0)
        self.assertEqual(len(out["ci95_pct"]),2)
        one=_cluster_ci([("a",1,0)])
        self.assertIsNone(one["ci95_pct"])

    def test_provenance_separates_core_and_full(self):
        p={
            "data_cohort":"SPOT_PLUS_BYBIT",
            "derivatives_provider":"BYBIT_LINEAR",
            "derivatives_quality":"V3_CORE_FULL",
            "structure_gate_json":json.dumps({"_derivatives_ready":True}),
            "_frozen_config_hash":"abc",
        }
        health=_source_health({"payload_json":json.dumps(p)})
        self.assertTrue(health["core_single_venue"])
        self.assertFalse(health["full_single_venue"])
        self.assertEqual(health["outcome_venue"],"BINANCE_SPOT_1M")
        self.assertEqual(health["config_hash"],"abc")
        p["derivatives_quality"]="FULL"
        full=_source_health({"payload_json":json.dumps(p)})
        self.assertTrue(full["full_single_venue"])

    def test_missing_source_is_not_declared_full(self):
        h=_source_health({"payload_json":"{}"})
        self.assertFalse(h["full_single_venue"])
        self.assertEqual(h["config_hash"],"HISTORICAL_MISSING")


if __name__ == "__main__":
    unittest.main()
