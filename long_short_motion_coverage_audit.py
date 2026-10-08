#!/usr/bin/env python3
"""Read-only audit of short moves in the existing V3.1 prefilter archive.
Not a full Binance Futures universe scanner; never creates trading signals.
"""
import argparse
import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import datetime,timezone,timedelta
from pathlib import Path

PERIODS={"5m":"t5","15m":"t15"}
LEVELS=(3,5,10)

def dt(s):
    x=datetime.fromisoformat(str(s).replace("Z","+00:00"))
    return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)

def audit(db,minutes=60,now=None):
    end=dt(now) if now else datetime.now(timezone.utc)
    start=end-timedelta(minutes=minutes)
    report={"version":"LS_PREFILTER_MOTION_AUDIT_V1","as_of_utc":end.isoformat(),
        "scope":"RECORDED_PREFILTER_ONLY","full_futures_universe":False,
        "signals_or_frozen_rules_changed":False,"lookback_minutes":minutes,
        "scans":0,"coins":0,"source_counts":{},"movements":{},
        "unavailable":{"30m":"30m closed-bar change not in archived features"},
        "issues":[]}
    if not os.path.exists(db):
        report["issues"].append("MISSING_DB")
        return report
    with sqlite3.connect("file:"+os.path.abspath(db)+"?mode=ro",uri=True) as c:
        c.row_factory=sqlite3.Row
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='universe_observations'").fetchone()
        if not exists:
            report["issues"].append("MISSING_UNIVERSE_OBSERVATIONS")
            return report
        rows=c.execute("""SELECT scan_time_utc,symbol,shortlisted,quote_volume,payload_json
            FROM universe_observations WHERE scan_time_utc>=? AND scan_time_utc<=?
            ORDER BY scan_time_utc,symbol""",(start.isoformat(),end.isoformat())).fetchall()
    scans=set()
    symbols=set()
    sources=defaultdict(set)
    moves=defaultdict(lambda:defaultdict(list))
    bad=0
    for r in rows:
        try:
            stamp=dt(r["scan_time_utc"])
            if not(start<=stamp<=end): continue
            payload=json.loads(r["payload_json"] or "{}")
            sym=str(r["symbol"])
            source=str((payload.get("discovery_meta") or {}).get("source") or "UNKNOWN")
            scans.add(r["scan_time_utc"])
            symbols.add(sym)
            sources[source].add(sym)
            for label,key in PERIODS.items():
                ch=(payload.get(key) or {}).get("change_1")
                if not isinstance(ch,(int,float)) or not math.isfinite(ch):continue
                direction="LONG" if ch>0 else "SHORT"
                for level in LEVELS:
                    if abs(ch)>=level:
                        moves[label,direction,level][sym].append({
                            "shortlisted":bool(r["shortlisted"]),
                            "volume":float(r["quote_volume"] or 0),
                            "abs_change":abs(ch),"source":source})
        except (ValueError,TypeError,AttributeError):
            bad+=1
    report["scans"]=len(scans)
    report["coins"]=len(symbols)
    report["source_counts"]={k:len(v) for k,v in sorted(sources.items())}
    if not rows:report["issues"].append("NO_RECENT_SCANS")
    if bad:report["issues"].append("BAD_ROWS:"+str(bad))
    for label in PERIODS:
        report["movements"][label]={}
        for direction in ("LONG","SHORT"):
            report["movements"][label][direction]={}
            for level in LEVELS:
                groups=moves[label,direction,level]
                excluded=[(sym,ss) for sym,ss in groups.items() if not any(s["shortlisted"] for s in ss)]
                report["movements"][label][direction][str(level)]={
                    "symbols_with_move":len(groups),
                    "snapshots_with_move":sum(len(ss) for ss in groups.values()),
                    "symbols_shortlisted_during_move":len(groups)-len(excluded),
                    "symbols_not_shortlisted_during_move":len(excluded),
                    "below_25m_volume_and_not_shortlisted":sum(
                        all(s["volume"]<25_000_000 for s in ss) for _,ss in excluded),
                    "diagnostics":[{"symbol":sym,
                        "largest_movement_pct":round(max(s["abs_change"] for s in ss),3),
                        "source":ss[0]["source"]} for sym,ss in sorted(
                            excluded,key=lambda x:-max(s["abs_change"] for s in x[1]))[:20]]
                }
    return report

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="long_short_v31_analyst.db")
    p.add_argument("--minutes",type=int,default=60)
    p.add_argument("--out",default="long_short_motion_coverage_audit.json")
    a=p.parse_args()
    if a.minutes<1:p.error("minutes must be positive")
    result=audit(a.db,a.minutes)
    Path(a.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("MOTION_COVERAGE", "scans",result["scans"],"coins",result["coins"],"issues",result["issues"])
    for period,data in result["movements"].items():
        for direction,levels in data.items():
            for threshold,counts in levels.items():
                print(period,direction,">="+threshold+"%", "moving",counts["symbols_with_move"],
                    "not_shortlisted",counts["symbols_not_shortlisted_during_move"])
    print("NO_LIVE_SIGNAL_CHANGES")
