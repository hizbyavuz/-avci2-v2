import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

from long_short_dual_v32 import (
    VERSION,init,sync,advance,versioned_plan,build_both_sides,
)

class DualV32Tests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,10,8,10,0,tzinfo=timezone.utc)
        self.longp=versioned_plan(
            "TESTUSDT","LONG",{"trigger_level":100.0,"invalidation":99.0,
                "target1":103.0,"target2":105.0,"retest_low":99.7,"retest_high":100.0},
            {"version":"LS_STRUCTURE_GATE_V3_1_2026-10-07","qualified_precheck":True,
             "direction":"LONG","room_pct":1.5},65,30,True,"AUTHORIZED_OBSERVATION","MULTI_VENUE_PUBLIC")
        self.longp["geometry_qualified"]=True
        self.shortp=versioned_plan(
            "TESTUSDT","SHORT",{"trigger_level":98.0,"invalidation":99.0,
                "target1":96.0,"target2":94.0,"retest_low":98.0,"retest_high":98.3},
            {"version":"LS_STRUCTURE_GATE_V3_1_2026-10-07","qualified_precheck":True,
             "direction":"SHORT","room_pct":1.5},40,65,False,"MODEL_DIRECTION_NOT_AUTHORIZED","MULTI_VENUE_PUBLIC")
        self.shortp["geometry_qualified"]=True

    def _db(self,path,scan,pair):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE analyses(scan_time_utc TEXT,symbol TEXT,payload_json TEXT)")
            db.execute("INSERT INTO analyses VALUES(?,?,?)",
                       (scan.isoformat(),"TESTUSDT",json.dumps({"v32_dual_sides":pair})))

    def _quality(self,row,closed,early):
        self.assertEqual(row["direction"],"LONG")
        return {"qualified":True,"acceptance_bars_beyond_last3":early["acceptance"],
                "quality_checks":{"volume":True,"wick":True}}

    def test_two_sides_have_independent_primary_keys_and_no_auto_flip(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"a.db"
            self._db(db,self.at-timedelta(seconds=20),
                    {"LONG":self.longp,"SHORT":self.shortp})
            with sqlite3.connect(":memory:") as con:
                con.row_factory=sqlite3.Row
                x=sync(con,str(db),self.at.isoformat())
                self.assertEqual(x["both_sides"],1)
                self.assertEqual(x["active"],2)
                self.assertEqual(x["authorized"],1)
                plans=con.execute("SELECT symbol,direction,authorized FROM dual_side_v32 ORDER BY direction").fetchall()
                self.assertEqual([(r["direction"],r["authorized"]) for r in plans],
                                 [("LONG",1),("SHORT",0)])
                candle=(self.at-timedelta(seconds=12)).isoformat()
                changes=advance(con,"TESTUSDT",(101.0,101.0,candle,{"acceptance":1}),
                                self.at.isoformat(),self._quality)
                self.assertIn(("LONG","CLOSE_CONFIRMED"),
                    [(x["direction"],x["to"]) for x in changes])
                self.assertIn(("SHORT","INVALIDATED"),
                    [(x["direction"],x["to"]) for x in changes])
                self.assertNotIn(("SHORT","TRIGGER_READY"),
                    [(x["direction"],x["to"]) for x in changes])
                events=con.execute("SELECT direction,to_stage,status FROM dual_side_v32_events").fetchall()
                self.assertTrue(all(x["status"]=="PAPER_ONLY" for x in events))

    def test_repeated_same_candle_cannot_create_trigger(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"a.db"
            self._db(db,self.at-timedelta(seconds=20),
                    {"LONG":self.longp,"SHORT":self.shortp})
            with sqlite3.connect(":memory:") as con:
                con.row_factory=sqlite3.Row
                sync(con,str(db),self.at.isoformat())
                c=(self.at-timedelta(seconds=12)).isoformat()
                advance(con,"TESTUSDT",(101.0,101.0,c,{"acceptance":1}),self.at.isoformat(),self._quality)
                later=self.at+timedelta(seconds=10)
                out=advance(con,"TESTUSDT",(101.1,101.0,c,{"acceptance":2}),
                             later.isoformat(),self._quality)
                self.assertFalse(any(x["to"]=="TRIGGER_READY" for x in out))
                second=later+timedelta(minutes=5)
                cc=(second-timedelta(seconds=5)).isoformat()
                advance(con,"TESTUSDT",(101.4,101.3,cc,{"acceptance":2}),
                        second.isoformat(),self._quality)
                result=con.execute("SELECT stage FROM dual_side_v32 WHERE symbol='TESTUSDT' AND direction='LONG'").fetchone()
                self.assertEqual(result["stage"],"TRIGGER_READY")

    def test_new_short_plan_can_start_after_old_short_invalidated(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"a.db";base=self.at-timedelta(minutes=1)
            self._db(db,base,{"LONG":self.longp,"SHORT":self.shortp})
            with sqlite3.connect(":memory:") as con:
                con.row_factory=sqlite3.Row
                sync(con,str(db),self.at.isoformat())
                c=(self.at-timedelta(seconds=10)).isoformat()
                advance(con,"TESTUSDT",(101.0,101.0,c,{"acceptance":1}),
                        self.at.isoformat(),self._quality)
                changed=dict(self.shortp)
                changed["plan_id"]="different-valid-short-hypothesis"
                changed["analyst_permit"]=True
                changed["nonpermit_reason"]="AUTHORIZED_OBSERVATION"
                longoff=dict(self.longp)
                longoff["analyst_permit"]=False
                later=self.at+timedelta(minutes=7)
                with sqlite3.connect(db) as a:
                    a.execute("INSERT INTO analyses VALUES(?,?,?)",
                        ((later-timedelta(seconds=15)).isoformat(),"TESTUSDT",
                         json.dumps({"v32_dual_sides":{"LONG":longoff,"SHORT":changed}})))
                x=sync(con,str(db),later.isoformat())
                self.assertEqual(x["authorized"],1)
                st=con.execute("SELECT direction,stage,authorized FROM dual_side_v32 ORDER BY direction").fetchall()
                self.assertEqual(st[1]["direction"],"SHORT")
                self.assertEqual(st[1]["stage"],"WATCH")
                self.assertEqual(st[1]["authorized"],1)
                self.assertEqual(st[0]["stage"],"CLOSE_CONFIRMED")
                self.assertEqual(st[0]["authorized"],0)

    def test_stale_analyst_snapshot_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"a.db"
            self._db(db,self.at-timedelta(hours=2),
                    {"LONG":self.longp,"SHORT":self.shortp})
            with sqlite3.connect(":memory:") as con:
                con.row_factory=sqlite3.Row
                res=sync(con,str(db),self.at.isoformat())
                self.assertIn("ANALYST_SCAN_STALE",res["issues"])
                self.assertEqual(res["active"],0)

    def test_both_direction_geometries_independently_computed(self):
        calls=[]
        def breakout(d,price,k5,t15,chart):
            calls.append(d)
            return {"trigger_level":101 if d=="LONG" else 99,
                    "invalidation":98 if d=="LONG" else 102,
                    "target1":105 if d=="LONG" else 95,
                    "target2":108 if d=="LONG" else 92,
                    "retest_low":100 if d=="LONG" else 99,
                    "retest_high":101 if d=="LONG" else 100}
        def struct(d,price,p,*args):
            return {"qualified_precheck":True,"room_pct":1.2,
                    "direction":d,"version":"LS_STRUCTURE_GATE_V3_1_2026-10-07"}
        result=build_both_sides(symbol="TESTUSDT",price=100,k5={},t15={},chart={},
            build_breakout=breakout,build_pullback=lambda *a:None,
            build_structure=struct,k15={},k30={},k1h={},k4h={},
            long_score=67,short_score=39,preferred_direction="LONG",
            preferred_setup_type="BREAKOUT",analyst_eligible=True,
            derivatives_ready=True,liquidity_ok=True,external_only=False,
            atr_pct=0.6,source="MULTI_VENUE_PUBLIC",required_room_pct=.9,
            minimum_cost_pct=.3,min_net_r=1)
        self.assertEqual(calls,["LONG","SHORT"])
        self.assertNotEqual(result["LONG"]["plan_id"],result["SHORT"]["plan_id"])
        self.assertTrue(result["LONG"]["analyst_permit"])
        self.assertFalse(result["SHORT"]["analyst_permit"])
        self.assertNotEqual(result["LONG"]["invalidation"],result["SHORT"]["invalidation"])

if __name__=="__main__":unittest.main()
