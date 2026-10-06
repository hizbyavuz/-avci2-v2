import json
import sqlite3
import tempfile
import unittest

import long_short_live_pool as live


class LiveLevelConsistencyTests(unittest.TestCase):
    def test_nearby_zone_cannot_replace_harder_raw_long_trigger(self):
        old_db=live.ANALYST_DB
        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                live.ANALYST_DB=tmp.name
                with sqlite3.connect(tmp.name) as con:
                    con.execute("""CREATE TABLE analyses(
                        scan_time_utc TEXT,symbol TEXT,status TEXT,long_score INTEGER,
                        short_score INTEGER,confidence INTEGER,price REAL,payload_json TEXT
                    )""")
                    payload={
                        "derivatives_ready":True,
                        "derivatives_source":"MULTI_VENUE_PUBLIC",
                        "derivatives_selected_provider":"GATE_FUTURES",
                        "derivatives_quality":"FULL",
                        "day_change_pct":1.0,
                        "setup_plan":{
                            "direction":"LONG",
                            "trigger_level":11.54,
                            "retest_low":11.5285,
                            "retest_high":11.54,
                            "invalidation":11.457842857142856,
                            "target1":11.691,
                            "target2":11.768214285714286,
                        },
                        "htf_gate":{"direction":"LONG","score":6,"reasons":[]},
                        "structure_gate":{
                            "version":live.STRUCTURE_GATE_VERSION,
                            "direction":"LONG",
                            "qualified_precheck":True,
                            "minimum_round_trip_cost_pct":0.30,
                            "required_room_pct":0.90,
                            "thresholds":{"min_net_t1_r":1.0},
                            "trigger_zone":{
                                "center":11.453,
                                "low":11.4441006,
                                "high":11.4618994,
                            },
                        },
                    }
                    con.execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",(
                        "2026-10-06T14:15:55+00:00","AVAXUSDT","WAIT",
                        63,20,63,11.465,json.dumps(payload),
                    ))
                items=live.load_watchlist()
                self.assertEqual(len(items),1)
                item=items[0]
                self.assertAlmostEqual(item["trigger_level"],11.54,places=8)
                self.assertLessEqual(item["retest_low"],item["retest_high"])
                risk=(item["trigger_level"]-item["invalidation"])/item["trigger_level"]*100.0
                self.assertGreater(risk,0.50)
        finally:
            live.ANALYST_DB=old_db

    def test_actionable_item_fails_closed_if_effective_tp_sl_geometry_is_bad(self):
        old_db=live.ANALYST_DB
        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                live.ANALYST_DB=tmp.name
                with sqlite3.connect(tmp.name) as con:
                    con.execute("""CREATE TABLE analyses(
                        scan_time_utc TEXT,symbol TEXT,status TEXT,long_score INTEGER,
                        short_score INTEGER,confidence INTEGER,price REAL,payload_json TEXT
                    )""")
                    payload={
                        "derivatives_ready":True,
                        "derivatives_source":"BINANCE_FUTURES",
                        "derivatives_quality":"NATIVE",
                        "day_change_pct":2.0,
                        "setup_plan":{
                            "direction":"LONG",
                            "trigger_level":100.0,
                            "retest_low":99.7,
                            "retest_high":100.0,
                            "invalidation":99.5,
                            "target1":100.6,
                            "target2":102.0,
                        },
                        "htf_gate":{"direction":"LONG","score":5,"reasons":[]},
                        "structure_gate":{
                            "version":live.STRUCTURE_GATE_VERSION,
                            "direction":"LONG",
                            "qualified_precheck":True,
                            "minimum_round_trip_cost_pct":0.30,
                            "required_room_pct":0.90,
                            "thresholds":{"min_net_t1_r":1.0},
                            "trigger_zone":{"low":99.8,"high":100.2,"center":100.0},
                        },
                    }
                    con.execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",(
                        "2026-10-06T00:00:00+00:00","TESTUSDT","WAIT",
                        60,20,60,100.1,json.dumps(payload),
                    ))
                self.assertEqual(live.load_watchlist(),[])
        finally:
            live.ANALYST_DB=old_db


if __name__=="__main__":
    unittest.main()
