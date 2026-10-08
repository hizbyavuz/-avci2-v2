#!/usr/bin/env python3
"""Futures-native broad EARLY WATCH shadow pool.

Compares the freshest USD-M market-WEBSOCKET prices to EVERY analyst deep-scan
setup (up to 40), not only the few approved by the TRADE live pool. Logs exact
gate-failure counts and observations. NO Telegram and NO change to frozen rules.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sqlite3
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from binance_native_um_market_watch import restore

VERSION="WIDE_SHADOW_EARLY_WATCH_V1"
MAX_SCAN_AGE_MINUTES=20
MAX_PRICE_AGE_SECONDS=30
APPROACH_DISTANCE_PCT=0.25

def audit(analyst_db,state_path,market_report_path):
    raw=json.loads(Path(market_report_path).read_text(encoding="utf-8"))
    if raw.get("source")!="BINANCE_FUTURES_UM_WS":
        raise ValueError("NON_NATIVE_FUTURES_SOURCE")
    stamp=int(raw["timestamp_ms"])
    history=restore(Path(state_path))
    result={"version":VERSION,"as_of_ms":stamp,
            "source":"BINANCE_FUTURES_UM_WS",
            "read_only":True,"telegram_messages":0,"production_gates_changed":False,
            "scope":"All latest analyst deep-scan setups, even NO_TRADE, only if live native futures price exists",
            "issues":list(raw.get("issues") or []),"analyst_scan":None,
            "funnel":{},"shadow_approaching":[],"shadow_all_count":0}
    if not os.path.exists(analyst_db):
        result["issues"].append("ANALYST_DB_MISSING")
        return result
    try:
        with sqlite3.connect("file:"+os.path.abspath(analyst_db)+"?mode=ro",uri=True) as c:
            c.row_factory=sqlite3.Row
            at=c.execute("SELECT MAX(scan_time_utc) FROM analyses").fetchone()
            scan=at[0] if at else None
            if not scan:
                result["issues"].append("ANALYST_SCAN_MISSING")
                return result
            x=datetime.fromisoformat(scan.replace("Z","+00:00"))
            if x.tzinfo is None:x=x.replace(tzinfo=timezone.utc)
            diff=(stamp/1000-x.timestamp())/60
            if not 0<=diff<=MAX_SCAN_AGE_MINUTES:
                result["issues"].append("ANALYST_SCAN_TOO_OLD_OR_FUTURE")
                return result
            rows=c.execute("SELECT symbol,status,payload_json FROM analyses WHERE scan_time_utc=?",(scan,)).fetchall()
    except (sqlite3.Error,OSError,TypeError,ValueError) as exc:
        result["issues"].append("ANALYST_DB_INVALID_"+type(exc).__name__)
        return result
    result["analyst_scan"]=scan
    counts=Counter()
    near=[]
    watched=0
    for r in rows:
        counts["deep_scanned"]+=1
        status=str(r["status"] or "")
        counts["status_"+status]+=1
        try:p=json.loads(r["payload_json"] or "{}")
        except (ValueError,TypeError):
            counts["broken_payload"]+=1
            continue
        gate=p.get("structure_gate") or {}
        v3=p.get("v3") or {}
        plan=p.get("setup_plan") or {}
        if not v3.get("eligible"):counts["v3_ineligible"]+=1
        if not p.get("derivatives_ready"):counts["derivatives_missing"]+=1
        if not gate.get("room_ok"):counts["room_failed"]+=1
        if not gate.get("rr_ok"):counts["net_rr_failed"]+=1
        if not gate.get("level_ok"):counts["level_failed"]+=1
        if not gate.get("qualified_precheck"):counts["structure_precheck_failed"]+=1
        sym=r["symbol"]
        if not plan.get("direction") or not plan.get("trigger_level"):
            counts["no_setup_plan"]+=1
            continue
        seq=history.get(sym) or []
        if not seq:
            counts["no_native_stream_history"]+=1
            continue
        now,px=seq[-1]
        if not 0<=stamp-now<=MAX_PRICE_AGE_SECONDS*1000 or not math.isfinite(px) or px<=0:
            counts["native_stream_stale"]+=1
            continue
        trigger=float(plan["trigger_level"])
        if trigger<=0:continue
        counts["native_fresh_setup"]+=1
        direction=plan["direction"]
        if direction not in ("LONG","SHORT"):continue
        # From the directional perspective, positive means already beyond trigger.
        dist=(px/trigger-1)*100*(1 if direction=="LONG" else -1)
        classification=(
            "BEFORE_TRIGGER" if -APPROACH_DISTANCE_PCT<=dist<0 else
            "JUST_CROSSED_UNCONFIRMED" if 0<=dist<=APPROACH_DISTANCE_PCT else
            "FAR_FROM_TRIGGER")
        counts[classification.lower()]+=1
        watched+=1
        if classification=="FAR_FROM_TRIGGER":continue
        near.append({
            "symbol":sym,"direction_observed":direction,
            "price":px,"trigger_level":trigger,
            "distance_in_direction_pct":round(dist,4),
            "stage":classification,
            "analyst_status":status,
            "production_eligible":bool(v3.get("eligible")),
            "structure_room_ok":bool(gate.get("room_ok")),
            "net_t1_rr_ok":bool(gate.get("rr_ok")),
            "derivatives_ready":bool(p.get("derivatives_ready")),
            "source":"BINANCE_FUTURES_UM_WS",
            "source_age_seconds":round((stamp-now)/1000,1),
            "is_trade_signal":False,
            "note":"NO closed candle confirmation; cannot imply actionable entry"
        })
    counts["shadow_native_setups"]=watched
    near.sort(key=lambda x:(abs(x["distance_in_direction_pct"]),x["symbol"]))
    result["funnel"]=dict(sorted(counts.items()))
    result["shadow_all_count"]=len(near)
    result["shadow_approaching"]=near[:40]
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state",default="native_um_market_watch_state.json")
    p.add_argument("--market-report",default="native_um_market_watch_report.json")
    p.add_argument("--analyst-db",default="long_short_v31_analyst.db")
    p.add_argument("--output",default="long_short_wide_shadow_watch.json")
    args=p.parse_args()
    report=audit(args.analyst_db,args.state,args.market_report)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("LONG_SHORT_WIDE_SHADOW",json.dumps(report["funnel"],sort_keys=True),flush=True)
    print("SHADOW_NEAR_COUNT",report["shadow_all_count"],"ISSUES",report["issues"],flush=True)
    print("NO_LIVE_TRADE_OR_TELEGRAM_CHANGES",flush=True)

if __name__=="__main__":
    main()
