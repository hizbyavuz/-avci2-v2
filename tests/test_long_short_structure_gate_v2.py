import json
import sys
import unittest
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import long_short_live_pool as lp
from long_short_live_pool import live_structure_confirmation, STRUCTURE_GATE_VERSION
from long_short_analyst import structure_required_room_pct, structure_min_round_trip_cost_pct, structure_net_t1_r


def _row(direction="LONG", trigger=100.0):
    return {
        "direction": direction,
        "trigger_level": trigger,
        "structure_gate_json": json.dumps({
            "version": STRUCTURE_GATE_VERSION,
            "qualified_precheck": True,
            "room_pct": 0.8,
            "timeframe_confluence": 3,
            "level_touches": 2,
        }),
    }


class StructureGateV2Tests(unittest.TestCase):
    def test_v21_cost_room_floor_is_ninety_bps(self):
        self.assertAlmostEqual(structure_min_round_trip_cost_pct(),0.30,places=6)
        self.assertAlmostEqual(structure_required_room_pct(),0.90,places=6)

    def test_v21_net_t1_r_uses_real_message_levels(self):
        # LONG: trigger 100, stop 99 = 1% risk, T1 101.5 = 1.5% gross reward.
        # After 0.30% round trip cost => 1.20R net.
        self.assertAlmostEqual(structure_net_t1_r("LONG",100,99,101.5),1.20,places=6)

    def test_strong_long_breakout_passes(self):
        q=live_structure_confirmation(_row(),102.0,{
            "closed_5m_open":99.8,
            "closed_5m_high":102.3,
            "closed_5m_low":99.7,
            "closed_5m_close":102.0,
            "closed_5m_body_ratio":0.846,
            "closed_5m_close_location":0.885,
            "closed_5m_volume_mult":1.45,
        })
        self.assertTrue(q["qualified"])
        self.assertFalse(q["fake_breakout"])

    def test_long_wick_fake_breakout_is_rejected(self):
        q=live_structure_confirmation(_row(),99.7,{
            "closed_5m_open":99.5,
            "closed_5m_high":100.8,
            "closed_5m_low":99.2,
            "closed_5m_close":99.7,
            "closed_5m_body_ratio":0.125,
            "closed_5m_close_location":0.3125,
            "closed_5m_volume_mult":1.80,
        })
        self.assertFalse(q["qualified"])
        self.assertTrue(q["fake_breakout"])

    def test_weak_volume_breakout_is_rejected(self):
        q=live_structure_confirmation(_row(),101.2,{
            "closed_5m_open":99.7,
            "closed_5m_high":101.4,
            "closed_5m_low":99.6,
            "closed_5m_close":101.2,
            "closed_5m_body_ratio":0.833,
            "closed_5m_close_location":0.889,
            "closed_5m_volume_mult":0.82,
        })
        self.assertFalse(q["qualified"])
        self.assertFalse(q["volume_ok"])


    def test_wait_candidate_enters_watchlist_before_full_structure_precheck(self):
        old_db=lp.ANALYST_DB
        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                lp.ANALYST_DB=tmp.name
                with sqlite3.connect(tmp.name) as con:
                    con.execute("""CREATE TABLE analyses(
                        scan_time_utc TEXT,symbol TEXT,status TEXT,long_score INTEGER,
                        short_score INTEGER,confidence INTEGER,price REAL,payload_json TEXT
                    )""")
                    payload={
                        "derivatives_ready":True,
                        "derivatives_source":"BINANCE_FUTURES",
                        "setup_plan":{
                            "direction":"LONG","trigger_level":100.0,
                            "retest_low":99.5,"retest_high":100.0,
                            "invalidation":98.0,"target1":102.0,"target2":104.0,
                        },
                        "htf_gate":{"direction":"LONG","score":4,"reasons":[]},
                        "structure_gate":{
                            "version":STRUCTURE_GATE_VERSION,
                            "direction":"LONG",
                            "qualified_precheck":False,
                            "trigger_zone":{"low":99.8,"high":100.2,"center":100.0},
                            "room_pct":1.2,
                        },
                        "derivatives_quality":"NATIVE",
                    }
                    con.execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",
                                ("2026-10-06T00:00:00+00:00","TESTUSDT","WAIT",51,0,51,99.5,
                                 json.dumps(payload)))
                items=lp.load_watchlist()
                self.assertEqual(len(items),1)
                self.assertEqual(items[0]["symbol"],"TESTUSDT")
                self.assertAlmostEqual(items[0]["trigger_level"],100.2)
        finally:
            lp.ANALYST_DB=old_db

    def test_unqualified_structure_still_cannot_confirm(self):
        row=_row()
        row["structure_gate_json"]=json.dumps({
            "version":STRUCTURE_GATE_VERSION,
            "qualified_precheck":False,
            "room_pct":0.8,
            "timeframe_confluence":1,
            "level_touches":1,
        })
        q=live_structure_confirmation(row,102.0,{
            "closed_5m_open":99.8,
            "closed_5m_high":102.3,
            "closed_5m_low":99.7,
            "closed_5m_close":102.0,
            "closed_5m_body_ratio":0.846,
            "closed_5m_close_location":0.885,
            "closed_5m_volume_mult":1.45,
        })
        self.assertFalse(q["qualified"])
        self.assertEqual(q["reason"],"structure_precheck_failed")


if __name__=="__main__":
    unittest.main()
