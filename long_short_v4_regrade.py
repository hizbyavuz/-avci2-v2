#!/usr/bin/env python3
"""V4 read-only regrade of actual Telegram SENT signal events.
Uses recorded chart venue only, no price venue fallback or historical rewrites.
No live signal writes; outputs to the independent V4 research database.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
from datetime import datetime,timezone,timedelta

import requests
from long_short_outcome_prices import historical_1m,has_full_horizon
from long_short_v4_paper_math import path_result,VERSION as LABEL_VERSION
from long_short_v4_research import setup

HORIZONS=(15,60,180)
SOURCES=("BINANCE_SPOT","GATE_FUTURES","BYBIT_LINEAR")

def parse_ts(x):
    v=datetime.fromisoformat(str(x).replace("Z","+00:00"))
    if v.tzinfo is None:raise ValueError("naive timestamp")
    return v.astimezone(timezone.utc)

def _binance_spot(path,params):
    r=requests.get("https://data-api.binance.vision"+path,params=params,timeout=8)
    r.raise_for_status()
    return r.json()

def recorded_source(payload):
    """No guesswork. An absent live chart source can never be native-labeled."""
    val=str(payload.get("_live_price_source") or "")
    return val if val in SOURCES else None

def calculate_one(ev,horizon,now):
    payload=json.loads(ev["payload_json"] or "{}")
    source=recorded_source(payload)
    def fail(reason):
        return {"status":"DATA_FAILURE","reason":reason}
    if source is None:return fail("CHART_VENUE_UNVERIFIED")
    if ev["telegram_status"]!="SENT" or not ev["telegram_sent_time_utc"]:
        return fail("NOT_DELIVERED")
    direction=ev["direction"]
    if direction not in ("LONG","SHORT"):return fail("INVALID_DIRECTION")
    try:
        stop=float(payload["invalidation"])
        t1=float(payload["target1"])
        t2=float(payload["target2"])
        sent=parse_ts(ev["telegram_sent_time_utc"])
        condition=parse_ts(ev["condition_time_utc"] or ev["event_time_utc"])
    except (KeyError,TypeError,ValueError):
        return fail("MISSING_LEVEL_OR_TIMESTAMP")
    execution=max(sent,condition)+timedelta(seconds=30)
    until=execution+timedelta(minutes=horizon)
    if now<until+timedelta(minutes=2):return {"status":"NOT_MATURED"}
    try:
        series=historical_1m(ev["symbol"],execution,until,
          requested_source=source,allow_fallback=False,source_inferred=False,
          fetch_binance=_binance_spot if source=="BINANCE_SPOT" else None)
        if not getattr(series,"venue_matched",False):
            return fail("PRICE_VENUE_MISMATCH")
        if not has_full_horizon(series,execution,until):
            return fail("INCOMPLETE_1M_HISTORY")
        selected=[r for r in series if execution.timestamp()*1000-60000<int(r[0])<until.timestamp()*1000]
        if not selected:return fail("NO_1M_BARS")
        # For a 30s human delay, the first candle may partly predate entry.
        # Use the first *entire* 1m candle after execution, with no lookahead.
        first_full=int((execution.timestamp()*1000+59999)//60000)*60000
        selected=[r for r in selected if int(r[0])>=first_full]
        if not selected:return fail("NO_FULL_ENTRY_CANDLE")
        # No substitute entry fill: open of first executable complete 1m candle.
        entry=float(selected[0][1])
        bars=[{"open_ms":int(r[0]),"open":float(r[1]),
               "high":float(r[2]),"low":float(r[3]),"close":float(r[4]),
               "source":source,"closed":True} for r in selected]
        result=path_result(direction,entry,stop,t1,t2,bars,
            fee_bps_per_side=5,slippage_bps_per_side=10,funding_pct=None)
        result.update({"requested_source":source,"source_venue_matched":True,
            "entry_time_utc":datetime.fromtimestamp(first_full/1000,timezone.utc).isoformat(),
            "telegram_sent_utc":sent.isoformat(),
            "outcome_horizon_min":horizon,"event_id":ev["id"],
            "execution_policy":"first_full_1m_candle_after_human_delay",
            "funding_pnl_unverified":True})
        return result
    except (requests.RequestException,ValueError,KeyError,TypeError,RuntimeError) as exc:
        return fail("PRICE_FETCH_"+type(exc).__name__)

def regrade(live_db,research_db,limit=100):
    if not os.path.isfile(live_db):
        return {"status":"LIVE_DB_MISSING","processed":0}
    now=datetime.now(timezone.utc)
    done=failures=0
    with sqlite3.connect("file:"+os.path.abspath(live_db)+"?mode=ro",uri=True) as live, sqlite3.connect(research_db) as out:
        live.row_factory=sqlite3.Row
        setup(out)
        events=live.execute("""SELECT * FROM events
            WHERE telegram_status='SENT' AND stage_to IN ('CLOSE_CONFIRMED','TRIGGERED')
            ORDER BY id DESC LIMIT ?""",(limit,)).fetchall()[::-1]
        for ev in events:
            for h in HORIZONS:
                key=str(ev["id"])
                exists=out.execute("""SELECT 1 FROM paper_regrades
                   WHERE cohort=? AND event_key=? AND horizon_min=? AND label_version=?""",
                   ("TELEGRAM_SENT",key,h,LABEL_VERSION)).fetchone()
                if exists:continue
                result=calculate_one(ev,h,now)
                if result["status"]=="NOT_MATURED":continue
                # Keep unresolved data issues explicit, NEVER count as losses.
                source=recorded_source(json.loads(ev["payload_json"] or "{}"))
                out.execute("""INSERT OR IGNORE INTO paper_regrades
                  (cohort,event_key,horizon_min,label_version,direction,price_source,
                   source_venue_matched,status,result_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                   ("TELEGRAM_SENT",key,h,LABEL_VERSION,ev["direction"],source,
                    int(bool(result.get("source_venue_matched"))),
                    result["status"],json.dumps(result,separators=(",",":"))))
                done+=1
                failures+=result["status"]!="LABELED"
        out.commit()
    return {"status":"DONE","processed":done,"data_failures":failures,
            "report":"Strict source-matched V4 labels, never V3 retroactive changes"}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--live-db",default="long_short_v31_live_pool.db")
    p.add_argument("--db",default="long_short_v4_native.db")
    p.add_argument("--limit",type=int,default=60)
    p.add_argument("--out",default="long_short_v4_regrades.json")
    args=p.parse_args()
    r=regrade(args.live_db,args.db,args.limit)
    with open(args.out,"w",encoding="utf-8") as f:json.dump(r,f,indent=2)
    print(json.dumps(r))

if __name__=="__main__":main()
