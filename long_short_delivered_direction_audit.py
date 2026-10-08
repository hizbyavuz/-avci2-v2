#!/usr/bin/env python3
"""Evidence-only analysis of Telegram LONG/SHORT direction failures.

No signal/threshold changes. Reports only messages recorded as SENT; separates
15m/60m/180m mature outcomes; never calls Spot proxy a Futures-backed outcome.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

VERSION="LS_DELIVERED_DIRECTION_AUDIT_V1_2026_10_08"
SENSITIVE_OVERBOUGHT_RSI=75
SENSITIVE_OVERSOLD_RSI=25
MAX_ALERT_CANDLE_AGE_SECONDS=75

def _parse(x):
    return datetime.fromisoformat(str(x).replace("Z","+00:00"))

def report(live_db,analyst_db):
    data={"version":VERSION,"read_only":True,
          "sample_rule":"only Telegram SENT CLOSE_CONFIRMED/TRIGGERED; each event counted once per horizon",
          "does_not_retrain_or_modify_v31":True,"issues":[],"messages":[],"by_horizon":{},"counts":{}}
    if not os.path.isfile(live_db):
        data["issues"].append("LIVE_DB_NOT_FOUND");return data
    try:
        live=sqlite3.connect("file:"+os.path.abspath(live_db)+"?mode=ro",uri=True)
        live.row_factory=sqlite3.Row
        events=live.execute("""SELECT * FROM events WHERE telegram_status='SENT'
                    AND stage_to IN ('CLOSE_CONFIRMED','TRIGGERED')
                    ORDER BY event_time_utc, id""").fetchall()
    except sqlite3.Error:
        data["issues"].append("LIVE_DB_INVALID");return data
    analyst=None
    if os.path.isfile(analyst_db):
        try:
            analyst=sqlite3.connect("file:"+os.path.abspath(analyst_db)+"?mode=ro",uri=True)
            analyst.row_factory=sqlite3.Row
            analyst.execute("SELECT 1 FROM analyses LIMIT 1").fetchone()
        except sqlite3.Error:
            analyst=None
            data["issues"].append("ANALYST_DB_INVALID")
    else:
        data["issues"].append("ANALYST_DB_NOT_FOUND")
    counts=Counter()
    for e in events:
        counts["delivered_confirmed_or_triggered"]+=1
        if e["stage_to"]=="CLOSE_CONFIRMED":counts["delivered_first_candle"]+=1
        if e["stage_to"]=="TRIGGERED":counts["delivered_final_triggered"]+=1
        age=None
        if e["stage_to"]=="CLOSE_CONFIRMED":
            try:
                age=round((_parse(e["telegram_sent_time_utc"])-_parse(e["condition_time_utc"])).total_seconds(),2)
            except (ValueError,TypeError):pass
            if age is not None and age>MAX_ALERT_CANDLE_AGE_SECONDS:
                counts["delivered_stale_5m_close"]+=1
        try:ep=json.loads(e["payload_json"] or "{}")
        except (TypeError,ValueError):ep={}
        rec={"event_id":e["id"],"symbol":e["symbol"],"direction":e["direction"],
             "stage":e["stage_to"],"sent_utc":e["telegram_sent_time_utc"],
             "time_after_confirmed_candle_seconds":age,
             "source_at_alert":ep.get("_live_price_source",ep.get("data_mode","UNKNOWN")),
             "data_cohort":ep.get("data_cohort","UNKNOWN"),
             "flags":[],"analysis_scan_at_utc":ep.get("analyst_scan_time"),
             "outcomes":{}}
        if age is not None and age>MAX_ALERT_CANDLE_AGE_SECONDS:rec["flags"].append("LATE_FIRST_CANDLE_DELIVERY")
        if rec["source_at_alert"] not in ("BINANCE_FUTURES","BINANCE_FUTURES_BOOK"):
            rec["flags"].append("LIVE_PRICE_NOT_PROVEN_BINANCE_FUTURES")
        if analyst and rec["analysis_scan_at_utc"]:
            try:
                a=analyst.execute("""SELECT payload_json FROM analyses
                     WHERE symbol=? AND scan_time_utc=? LIMIT 1""",
                     (e["symbol"],rec["analysis_scan_at_utc"])).fetchone()
                if a:
                    p=json.loads(a["payload_json"] or "{}")
                    for timeframe in ("t5","t15","t1h","t4h"):
                        x=p.get(timeframe) or {}
                        v=x.get("rsi")
                        if v is not None:
                            try:rsi=float(v)
                            except (ValueError,TypeError):continue
                            if timeframe=="t5" and (
                                (e["direction"]=="SHORT" and rsi<SENSITIVE_OVERSOLD_RSI)
                                or (e["direction"]=="LONG" and rsi>SENSITIVE_OVERBOUGHT_RSI)):
                                rec["flags"].append("RSI_EXHAUSTION_5M_OBSERVATION")
                    sf=p.get("spot_flow") or {}
                    delta=sf.get("delta_share")
                    if sf.get("available") and delta is not None:
                        if (e["direction"]=="LONG" and float(delta)<0) or (e["direction"]=="SHORT" and float(delta)>0):
                            rec["flags"].append("SPOT_FLOW_OPPOSITE_OBSERVATION")
                    regime=p.get("market_regime")
                    if (e["direction"]=="LONG" and regime=="DOWN") or (e["direction"]=="SHORT" and regime=="UP"):
                        rec["flags"].append("BTC_REGIME_OPPOSITE_OBSERVATION")
                    rec["analyst_data_mode"]=p.get("data_mode")
                    rec["analyst_deriv_source"]=p.get("derivatives_selected_provider")
                else:
                    counts["analyst_snapshot_missing"]+=1
                    rec["flags"].append("ANALYST_SNAPSHOT_NOT_RETAINED")
            except (sqlite3.Error,ValueError,TypeError):
                rec["flags"].append("ANALYST_SNAPSHOT_INVALID")
        # outcome rows can be incomplete; never label pending outcomes as failures.
        for h in (15,60,180):
            try:
                o=live.execute("""SELECT direction_correct,net_return_pct,
                        first_barrier,price_source,price_source_matched
                        FROM delivered_signal_outcomes
                        WHERE event_id=? AND horizon_min=? LIMIT 1""",(e["id"],h)).fetchone()
            except sqlite3.Error:
                data["issues"].append("OUTCOMES_NOT_AVAILABLE");o=None
            if not o:continue
            source=str(o["price_source"] or "UNKNOWN")
            rec["outcomes"][str(h)]={"direction_correct":bool(o["direction_correct"]),
                "net_pct":round(float(o["net_return_pct"]),4) if o["net_return_pct"] is not None else None,
                "first_barrier":o["first_barrier"],"price_source":source,
                "matched_to_original_chart":bool(o["price_source_matched"]),
                "binance_futures_native_outcome":source=="BINANCE_FUTURES"}
        data["messages"].append(rec)
    # Accuracy is per HORIZON, not conflated with first barrier or TP1.
    for h in ("15","60","180"):
        rows=[m["outcomes"][h] for m in data["messages"] if h in m["outcomes"]]
        trade=[m["outcomes"][h] for m in data["messages"] if m["stage"]=="TRIGGERED" and h in m["outcomes"]]
        data["by_horizon"][h]={"evaluated":len(rows),
            "direction_right":sum(bool(x["direction_correct"]) for x in rows),
            "direction_wrong":sum(not x["direction_correct"] for x in rows),
            "net_positive":sum(x["net_pct"] is not None and x["net_pct"]>0 for x in rows),
            "futures_native_outcomes":sum(bool(x["binance_futures_native_outcome"]) for x in rows),
            "final_trade_evaluated":len(trade)}
    counts["price_not_proven_futures"]=sum("LIVE_PRICE_NOT_PROVEN_BINANCE_FUTURES" in m["flags"] for m in data["messages"])
    data["counts"]=dict(sorted(counts.items()))
    live.close()
    if analyst:analyst.close()
    return data

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--live-db",default="long_short_v31_live_pool.db")
    ap.add_argument("--analyst-db",default="long_short_v31_analyst.db")
    ap.add_argument("--out",default="long_short_delivered_direction_audit.json")
    a=ap.parse_args()
    result=report(a.live_db,a.analyst_db)
    Path(a.out).write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print("DIRECTION_AUDIT",result["counts"],"BY_HORIZON",result["by_horizon"],"ISSUES",result["issues"])
    print("NO_STRATEGY_PARAMETER_CHANGES")
if __name__=="__main__":main()
