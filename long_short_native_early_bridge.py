#!/usr/bin/env python3
"""Read-only bridge: native Binance Futures market data -> Long/Short early OBSERVATION.

Never executes orders, sends Telegram, or modifies the frozen V3.1 models.
No movement is a trade recommendation. No future prices enter entry decisions.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime,timezone
from pathlib import Path

from binance_native_um_market_watch import WINDOWS,restore

VERSION="NATIVE_UM_TO_LS_OBSERVATION_V1"

def analyst_scan(db,asof_ms,max_age_minutes=20):
    if not os.path.isfile(db):
        return {},None,"ANALYST_DB_NOT_FOUND"
    try:
        with sqlite3.connect("file:"+os.path.abspath(db)+"?mode=ro",uri=True) as con:
            con.row_factory=sqlite3.Row
            row=con.execute("SELECT MAX(scan_time_utc) FROM universe_observations").fetchone()
            if row is None or row[0] is None:
                return {},None,"ANALYST_SCAN_NOT_FOUND"
            stamp=str(row[0])
            dt=datetime.fromisoformat(stamp.replace("Z","+00:00"))
            if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
            delta=(asof_ms/1000-dt.timestamp())/60
            if delta<0 or delta>max_age_minutes:
                return {},stamp,"ANALYST_SCAN_NOT_CURRENT"
            records=con.execute("""SELECT symbol,shortlisted,quote_volume,day_change_pct,
                     direction_hint,prefilter_rank FROM universe_observations
                     WHERE scan_time_utc=?""",(stamp,)).fetchall()
            return {r["symbol"]:dict(r) for r in records},stamp,None
    except (OSError,sqlite3.Error,ValueError,TypeError) as exc:
        return {},None,"ANALYST_DB_INVALID:"+type(exc).__name__

def bridge(state,report,db):
    raw=json.loads(Path(report).read_text(encoding="utf-8"))
    stamp=int(raw["timestamp_ms"])
    if raw.get("source")!="BINANCE_FUTURES_UM_WS":
        raise ValueError("NATIVE_FUTURES_SOURCE_NOT_VALIDATED")
    if raw.get("issues"):
        issues=list(raw["issues"])
    else:
        issues=[]
    history=restore(Path(state))
    analyst,analyst_time,err=analyst_scan(db,stamp)
    if err:issues.append(err)
    results=[]
    stats={}
    fresh=0
    for symbol,seq in history.items():
        if not seq:continue
        last,px=seq[-1]
        if not 0<=stamp-last<30000:continue
        fresh+=1
        # Only compare using prior observed prices near an actual elapsed horizon.
        matching=analyst.get(symbol)
        status=("NOT_IN_ANALYST_PREFILTER" if not matching else
                "IN_ANALYST_DEEP_SCAN" if matching["shortlisted"] else
                "PREFILTER_ONLY")
        for mins,tolerance in WINDOWS.items():
            target=last-mins*60000
            ref=next((x for x in reversed(seq)
                      if x[0]<=target and target-x[0]<=tolerance),None)
            if not ref or ref[1]<=0:continue
            base_time,base_price=ref
            change=100*(px/base_price-1)
            direction="LONG" if change>0 else "SHORT"
            # Observation bands are NOT production triggers.
            mag=abs(change)
            if mag<1.0:continue
            band="1_TO_3_OBSERVATION" if mag<3 else (
                "3_TO_5_OBSERVATION" if mag<5 else "ALREADY_EXTENDED_NOT_EARLY")
            results.append({"symbol":symbol,"direction_observed":direction,
                "window_minutes":mins,"change_pct":round(change,4),
                "current_price":px,"source":"BINANCE_FUTURES_UM_WS",
                "observed_at_ms":last,"reference_at_ms":base_time,
                "actual_elapsed_seconds":round((last-base_time)/1000,1),
                "observation_band":band,"analyst_coverage":status,
                "analyst_quote_volume_24h":matching["quote_volume"] if matching else None,
                "analyst_prefilter_rank":matching["prefilter_rank"] if matching else None,
                "trade_signal":False,"telegram":False})
    for mins in WINDOWS:
        rows=[r for r in results if r["window_minutes"]==mins]
        stats[str(mins)]={"watch_over_1pct":len(rows),
            "long":sum(r["direction_observed"]=="LONG" for r in rows),
            "short":sum(r["direction_observed"]=="SHORT" for r in rows),
            "not_in_analyst_prefilter":sum(r["analyst_coverage"]=="NOT_IN_ANALYST_PREFILTER" for r in rows),
            "prefilter_only":sum(r["analyst_coverage"]=="PREFILTER_ONLY" for r in rows),
            "in_deep_scan":sum(r["analyst_coverage"]=="IN_ANALYST_DEEP_SCAN" for r in rows),
            "already_extended":sum(r["observation_band"]=="ALREADY_EXTENDED_NOT_EARLY" for r in rows)}
    results.sort(key=lambda r:(r["window_minutes"],-abs(r["change_pct"]),r["symbol"]))
    return {"version":VERSION,"timestamp_ms":stamp,
       "source":"BINANCE_FUTURES_UM_WS",
       "measurement":"retrospective price observations; never prospective trade signals",
       "is_read_only":True,"changes_frozen_rules":False,
       "telegram_alerts":False,"automatic_execution":False,
       "active_futures_symbols_with_price":fresh,
       "analyst_scan_time_utc":analyst_time,
       "analyst_symbols":len(analyst),
       "issues":sorted(set(issues)),
       "counts":stats,"observations":results}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state",default="native_um_market_watch_state.json")
    p.add_argument("--market-report",default="native_um_market_watch_report.json")
    p.add_argument("--analyst-db",default="long_short_v31_analyst.db")
    p.add_argument("--out",default="long_short_native_early_observations.json")
    args=p.parse_args()
    data=bridge(args.state,args.market_report,args.analyst_db)
    Path(args.out).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding="utf-8")
    print("LS_NATIVE_EARLY_BRIDGE", "futures_symbols",data["active_futures_symbols_with_price"],
          "analyst_symbols",data["analyst_symbols"],"issues",data["issues"])
    for window,counts in data["counts"].items():
        print("EARLY_OBSERVATION",window,"long",counts["long"],"short",counts["short"],
              "not_in_analyst",counts["not_in_analyst_prefilter"],"prefilter_only",counts["prefilter_only"],
              "extended",counts["already_extended"])
    print("NO_TRADE_SIGNALS_SENT")

if __name__=="__main__":
    main()
