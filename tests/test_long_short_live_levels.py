import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


class LiveLifecycleTests(unittest.TestCase):
    def _item(self,symbol="TESTUSDT",direction="LONG",trigger=100.0,radar=False,setup_type="BREAKOUT"):
        if direction=="LONG":
            retest_low,retest_high=99.8,trigger
            invalidation,target1,target2=99.0,102.0,104.0
        else:
            retest_low,retest_high=trigger,trigger*1.002
            invalidation,target1,target2=trigger*1.01,trigger*0.98,trigger*0.96
        return {
            "symbol":symbol,
            "direction":direction,
            "reference_price":trigger,
            "trigger_level":trigger,
            "retest_low":retest_low,
            "retest_high":retest_high,
            "invalidation":invalidation,
            "target1":target1,
            "target2":target2,
            "confidence":60,
            "day_change_pct":6.0 if radar else 1.0,
            "radar_only":radar,
            "data_mode":"BINANCE_FUTURES",
            "data_cohort":"BINANCE_FUTURES_NATIVE",
            "derivatives_provider":"BINANCE",
            "derivatives_quality":"FULL",
            "htf_direction":direction,
            "htf_score":6,
            "htf_reasons":["test"],
            "structure_gate_version":live.STRUCTURE_GATE_VERSION,
            "structure_gate":{
                "version":live.STRUCTURE_GATE_VERSION,
                "_radar_only":radar,
                "_v3_setup_type":setup_type,
                "_v3_precheck_ok":not radar,
            },
            "setup_type":setup_type,
            "discovery_rank":10.0,
            "scan_time":"2026-10-07T18:00:00+00:00",
        }

    def _with_live_db(self):
        return tempfile.NamedTemporaryFile(suffix=".db")

    def test_same_direction_recalculation_does_not_move_locked_setup(self):
        old_db=live.LIVE_DB
        try:
            with self._with_live_db() as tmp:
                live.LIVE_DB=tmp.name
                live.init_db()
                first=self._item(trigger=100.0)
                live.sync_watchlist([first])
                second=self._item(trigger=101.0)
                second["target1"]=103.0
                second["scan_time"]="2026-10-07T18:05:00+00:00"
                live.sync_watchlist([second])
                with sqlite3.connect(tmp.name) as con:
                    con.row_factory=sqlite3.Row
                    row=con.execute("SELECT * FROM watch_state WHERE symbol='TESTUSDT'").fetchone()
                    n=con.execute("SELECT COUNT(*) FROM watch_episodes WHERE symbol='TESTUSDT'").fetchone()[0]
                self.assertAlmostEqual(row["trigger_level"],100.0)
                self.assertAlmostEqual(row["target1"],102.0)
                self.assertEqual(row["analyst_active"],1)
                self.assertEqual(n,1)
        finally:
            live.LIVE_DB=old_db

    def test_actionable_to_radar_pauses_without_overwriting_setup(self):
        old_db=live.LIVE_DB
        try:
            with self._with_live_db() as tmp:
                live.LIVE_DB=tmp.name
                live.init_db()
                live.sync_watchlist([self._item(trigger=100.0,radar=False)])
                radar=self._item(trigger=97.0,radar=True)
                radar["scan_time"]="2026-10-07T18:05:00+00:00"
                live.sync_watchlist([radar])
                with sqlite3.connect(tmp.name) as con:
                    con.row_factory=sqlite3.Row
                    row=con.execute("SELECT * FROM watch_state WHERE symbol='TESTUSDT'").fetchone()
                self.assertAlmostEqual(row["trigger_level"],100.0)
                self.assertEqual(row["analyst_active"],0)
                gate=json.loads(row["structure_gate_json"])
                self.assertFalse(gate.get("_radar_only"))
        finally:
            live.LIVE_DB=old_db

    def test_missing_one_cycle_is_paused_not_deleted(self):
        old_db=live.LIVE_DB
        try:
            with self._with_live_db() as tmp:
                live.LIVE_DB=tmp.name
                live.init_db()
                live.sync_watchlist([self._item()])
                live.sync_watchlist([])
                with sqlite3.connect(tmp.name) as con:
                    con.row_factory=sqlite3.Row
                    row=con.execute("SELECT * FROM watch_state WHERE symbol='TESTUSDT'").fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["analyst_active"],0)
        finally:
            live.LIVE_DB=old_db

    def test_stale_paused_setup_expires_after_grace(self):
        old_db=live.LIVE_DB
        try:
            with self._with_live_db() as tmp:
                live.LIVE_DB=tmp.name
                live.init_db()
                live.sync_watchlist([self._item()])
                with sqlite3.connect(tmp.name) as con:
                    con.execute("""UPDATE watch_state
                                   SET analyst_active=0,last_seen_watchlist_utc='2000-01-01T00:00:00+00:00'""")
                live.sync_watchlist([])
                with sqlite3.connect(tmp.name) as con:
                    row=con.execute("SELECT * FROM watch_state WHERE symbol='TESTUSDT'").fetchone()
                    reason=con.execute("""SELECT end_reason FROM watch_episodes
                                          WHERE symbol='TESTUSDT' ORDER BY id DESC LIMIT 1""").fetchone()[0]
                self.assertIsNone(row)
                self.assertEqual(reason,"WATCHLIST_GRACE_EXPIRED")
        finally:
            live.LIVE_DB=old_db

    def test_paused_setup_cannot_confirm(self):
        row={
            "direction":"LONG","trigger_level":100.0,"retest_low":99.8,"retest_high":100.0,
            "invalidation":99.0,"target1":102.0,"stage":"WATCH",
            "structure_gate_json":json.dumps({"_radar_only":False}),
            "analyst_active":0,"setup_locked_at_utc":live.now_iso(),
        }
        sq={"qualified":True,"setup_type":"BREAKOUT","acceptance_bars_beyond_last3":2}
        self.assertEqual(live.next_stage(row,101.0,101.0,sq),"WATCH")

    def test_fresh_actionable_direction_flip_starts_new_episode(self):
        old_db=live.LIVE_DB
        try:
            with self._with_live_db() as tmp:
                live.LIVE_DB=tmp.name
                live.init_db()
                live.sync_watchlist([self._item(direction="LONG",trigger=100.0)])
                short=self._item(direction="SHORT",trigger=99.0)
                short["scan_time"]="2026-10-07T18:05:00+00:00"
                live.sync_watchlist([short])
                with sqlite3.connect(tmp.name) as con:
                    con.row_factory=sqlite3.Row
                    row=con.execute("SELECT * FROM watch_state WHERE symbol='TESTUSDT'").fetchone()
                    eps=con.execute("""SELECT direction,end_reason FROM watch_episodes
                                       WHERE symbol='TESTUSDT' ORDER BY id""").fetchall()
                self.assertEqual(row["direction"],"SHORT")
                self.assertAlmostEqual(row["trigger_level"],99.0)
                self.assertEqual(len(eps),2)
                self.assertEqual(eps[0]["end_reason"],"RESET_ACTIONABLE_DIRECTION_FLIP")
        finally:
            live.LIVE_DB=old_db


if __name__=="__main__":
    unittest.main()
