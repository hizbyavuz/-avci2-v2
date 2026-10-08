import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import long_short_live_pool as lp
import long_short_outcome_tracker_v21 as ot


class V21IntegrationTests(unittest.TestCase):
    def test_watch_episode_persists_after_candidate_drops(self):
        old_live,old_analyst=lp.LIVE_DB,lp.ANALYST_DB
        try:
            with tempfile.TemporaryDirectory() as td:
                live=str(Path(td)/"live.db")
                analyst=str(Path(td)/"analyst.db")
                lp.LIVE_DB=live
                lp.ANALYST_DB=analyst
                lp.init_db()
                with sqlite3.connect(analyst) as con:
                    con.execute("""CREATE TABLE analyses(
                        scan_time_utc TEXT,symbol TEXT,status TEXT,long_score INTEGER,
                        short_score INTEGER,confidence INTEGER,price REAL,payload_json TEXT
                    )""")
                    payload={
                        "derivatives_ready":True,
                        "derivatives_source":"BINANCE_FUTURES",
                        "derivatives_quality":"NATIVE",
                        "setup_plan":{
                            "direction":"LONG","trigger_level":100.0,
                            "retest_low":99.6,"retest_high":100.0,
                            "invalidation":98.0,"target1":103.0,"target2":105.0,
                        },
                        "htf_gate":{"direction":"LONG","score":4,"reasons":[]},
                        "structure_gate":{
                            "version":lp.STRUCTURE_GATE_VERSION,
                            "direction":"LONG","qualified_precheck":True,
                            "trigger_zone":{"low":99.8,"high":100.2,"center":100.0},
                            "room_pct":2.0,"required_room_pct":0.9,
                        },
                    }
                    con.execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",
                                ("2026-10-06T00:00:00+00:00","TESTUSDT","WAIT",55,20,51,99.7,
                                 json.dumps(payload)))
                items=lp.load_watchlist()
                self.assertEqual(len(items),1)
                lp.sync_watchlist(items)
                with sqlite3.connect(live) as con:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0],1)
                    ep=con.execute("SELECT ended_at_utc,close_confirmed_time FROM watch_episodes").fetchone()
                    self.assertIsNone(ep[0])
                    self.assertIsNone(ep[1])
                lp.sync_watchlist([])
                with sqlite3.connect(live) as con:
                    con.row_factory=sqlite3.Row
                    # One-cycle disappearance pauses the setup instead of deleting it.
                    row=con.execute("SELECT * FROM watch_state").fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row["analyst_active"],0)
                    ep=con.execute("SELECT ended_at_utc,end_reason,close_confirmed_time FROM watch_episodes").fetchone()
                    self.assertIsNone(ep[0])
                    self.assertIsNone(ep[1])
                    self.assertIsNone(ep[2])

                    # Once the grace window is truly stale, retirement is explicit.
                    con.execute("""UPDATE watch_state
                                   SET last_seen_watchlist_utc='2000-01-01T00:00:00+00:00'""")
                    con.commit()
                lp.sync_watchlist([])
                with sqlite3.connect(live) as con:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0],0)
                    ep=con.execute("SELECT ended_at_utc,end_reason,close_confirmed_time FROM watch_episodes").fetchone()
                    self.assertIsNotNone(ep[0])
                    self.assertEqual(ep[1],"WATCHLIST_GRACE_EXPIRED")
                    self.assertIsNone(ep[2])
        finally:
            lp.LIVE_DB,lp.ANALYST_DB=old_live,old_analyst

    def test_delivered_trigger_is_evaluated_with_message_levels(self):
        old_db=ot.DB
        old_rows=ot._rows_1m
        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                ot.DB=tmp.name
                with sqlite3.connect(tmp.name) as con:
                    lp_old=lp.LIVE_DB
                    lp.LIVE_DB=tmp.name
                    try:
                        lp.init_db()
                    finally:
                        lp.LIVE_DB=lp_old
                    ot.init_db(con)
                    cond=datetime.now(timezone.utc)-timedelta(hours=5)
                    sent=cond+timedelta(seconds=10)
                    payload={
                        "invalidation":99.0,
                        "target1":101.5,
                        "target2":103.0,
                        "data_cohort":"BINANCE_FUTURES_NATIVE",
                    }
                    con.execute("""INSERT INTO events(
                        event_time_utc,symbol,direction,stage_from,stage_to,price,closed_5m,
                        condition_time_utc,telegram_sent_time_utc,telegram_status,telegram_error,
                        delay_seconds,payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sent.isoformat(),"TESTUSDT","LONG","RETESTING","TRIGGERED",100.0,100.0,
                     cond.isoformat(),sent.isoformat(),"SENT",None,10.0,json.dumps(payload)))
                    con.commit()

                execute=sent+timedelta(seconds=ot.HUMAN_DELAY_SECONDS)
                rows=[]
                for i in range(250):
                    ts=int((execute+timedelta(minutes=i)).timestamp()*1000)
                    if i==0:
                        o,h,l,c=100.0,100.4,99.8,100.2
                    elif i==1:
                        o,h,l,c=100.2,101.6,100.1,101.4
                    else:
                        o,h,l,c=101.4,102.2,101.0,102.0
                    rows.append([ts,str(o),str(h),str(l),str(c),"0",ts+59999])
                ot._rows_1m=lambda symbol,start,end,**_kwargs: rows

                with sqlite3.connect(tmp.name) as con:
                    ot.init_db(con)
                    added=ot.evaluate_delivered(con)
                    con.commit()
                    self.assertEqual(added,3)
                    out=con.execute("""SELECT horizon_min,first_barrier,direction_correct,
                                      round_trip_cost_pct
                                      FROM delivered_signal_outcomes ORDER BY horizon_min""").fetchall()
                    self.assertEqual([r[0] for r in out],[15,60,180])
                    self.assertTrue(all(r[1]=="TP1" for r in out))
                    self.assertTrue(all(r[2]==1 for r in out))
                    self.assertTrue(all(abs(r[3]-0.30)<1e-9 for r in out))
        finally:
            ot.DB=old_db
            ot._rows_1m=old_rows


if __name__=="__main__":
    unittest.main()
