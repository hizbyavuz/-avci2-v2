#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure real live-pool state transitions without changing trading rules.

Stages compared at fixed 15m / 1h / 4h horizons:
- EARLY
- CLOSE_CONFIRMED
- TRIGGERED

This lets us test whether waiting for confirmation/retest adds or destroys edge.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

import requests

DB=os.getenv("LS_LIVE_DB","long_short_live_pool.db")
HORIZONS=(15,60,240)
FEE_BPS_PER_SIDE=float(os.getenv("LS_FEE_BPS_PER_SIDE","5"))
SLIPPAGE_BPS_PER_SIDE=float(os.getenv("LS_SLIPPAGE_BPS_PER_SIDE","5"))
BASES=("https://data-api.binance.vision","https://api.binance.com")


def _dt(x):
    d=datetime.fromisoformat(x)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _get(path,params):
    last=None
    for base in BASES:
        try:
            r=requests.get(base+path,params=params,timeout=12,
                           headers={"User-Agent":"lsa-live-validation/1.0"})
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last=exc
    raise last or RuntimeError("spot data unavailable")


def _path(symbol,start,horizon):
    start_ms=int((start+timedelta(minutes=1)).timestamp()*1000)
    end_ms=int((start+timedelta(minutes=horizon)).timestamp()*1000)
    rows=_get("/api/v3/klines",{
        "symbol":symbol,"interval":"1m","startTime":start_ms,"endTime":end_ms,
        "limit":min(1000,horizon+5),
    })
    if not rows:
        return None
    return {
        "high":max(float(r[2]) for r in rows),
        "low":min(float(r[3]) for r in rows),
        "close":float(rows[-1][4]),
        "bars":len(rows),
    }


def _metrics(direction,entry,risk_pct,p):
    if direction=="SHORT":
        gross=(entry/p["close"]-1)*100 if p["close"] else 0.0
        mfe=(entry/p["low"]-1)*100 if p["low"] else 0.0
        mae=(entry/p["high"]-1)*100 if p["high"] else 0.0
    else:
        gross=(p["close"]/entry-1)*100 if entry else 0.0
        mfe=(p["high"]/entry-1)*100 if entry else 0.0
        mae=(p["low"]/entry-1)*100 if entry else 0.0
    cost=2*(FEE_BPS_PER_SIDE+SLIPPAGE_BPS_PER_SIDE)/100.0
    net=gross-cost
    return {
        "gross":gross,"net":net,
        "r":net/risk_pct if risk_pct>0 else None,
        "mfe":mfe,"mae":mae,
        "mfe_r":mfe/risk_pct if risk_pct>0 else None,
        "mae_r":mae/risk_pct if risk_pct>0 else None,
    }


def _stage_name(stage):
    if stage in ("EARLY:EARLY_LONG","EARLY:EARLY_SHORT"):
        return "EARLY"
    if stage=="CLOSE_CONFIRMED":
        return "CLOSE_CONFIRMED"
    if stage=="TRIGGERED":
        return "TRIGGERED"
    return None


def _setup_from_event(con,row):
    payload={}
    try:
        payload=json.loads(row["payload_json"] or "{}")
    except Exception:
        pass
    setup=payload.get("_setup") or {}
    if not setup and row["stage_to"] in ("CLOSE_CONFIRMED","TRIGGERED"):
        # Continuation event payload stores the watch_state row snapshot.
        setup=payload
    inv=float(setup.get("invalidation") or 0.0)
    trig=float(setup.get("trigger_level") or 0.0)
    if not inv:
        q=con.execute("SELECT invalidation,trigger_level FROM watch_state WHERE symbol=?",
                      (row["symbol"],)).fetchone()
        if q:
            inv=float(q[0] or 0.0)
            trig=float(q[1] or trig or 0.0)
    return inv,trig


def init_db(con):
    con.execute("""CREATE TABLE IF NOT EXISTS live_forward_validation(
        event_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        stage_name TEXT NOT NULL,
        signal_time_utc TEXT NOT NULL,
        signal_price REAL NOT NULL,
        risk_pct REAL NOT NULL,
        horizon_min INTEGER NOT NULL,
        gross_return_pct REAL,
        net_return_pct REAL,
        r_multiple REAL,
        mfe_pct REAL,
        mae_pct REAL,
        mfe_r REAL,
        mae_r REAL,
        bars INTEGER,
        evaluated_at_utc TEXT NOT NULL,
        PRIMARY KEY(event_id,horizon_min)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS live_validation_reports(
        report_time_utc TEXT NOT NULL,
        horizon_min INTEGER NOT NULL,
        stage_name TEXT NOT NULL,
        n INTEGER NOT NULL,
        expectancy_r REAL,
        avg_net_pct REAL,
        win_rate REAL,
        PRIMARY KEY(report_time_utc,horizon_min,stage_name)
    )""")


def evaluate(con):
    con.row_factory=sqlite3.Row
    rows=con.execute("""SELECT * FROM events
                        WHERE stage_to IN (
                          'EARLY:EARLY_LONG','EARLY:EARLY_SHORT',
                          'CLOSE_CONFIRMED','TRIGGERED'
                        )
                        ORDER BY id""").fetchall()
    now=datetime.now(timezone.utc)
    inserted=0
    for row in rows:
        stage=_stage_name(row["stage_to"])
        if not stage:
            continue
        signal_time=row["condition_time_utc"] or row["event_time_utc"]
        start=_dt(signal_time)
        entry=float(row["closed_5m"] if stage=="CLOSE_CONFIRMED" and row["closed_5m"] else row["price"])
        inv,_=_setup_from_event(con,row)
        if not entry or not inv:
            continue
        risk_pct=abs(entry-inv)/entry*100.0
        if risk_pct<=0:
            continue
        for h in HORIZONS:
            if now < start+timedelta(minutes=h+2):
                continue
            if con.execute("SELECT 1 FROM live_forward_validation WHERE event_id=? AND horizon_min=?",
                           (row["id"],h)).fetchone():
                continue
            try:
                p=_path(row["symbol"],start,h)
                if not p:
                    continue
                m=_metrics(row["direction"],entry,risk_pct,p)
                con.execute("""INSERT INTO live_forward_validation(
                    event_id,symbol,direction,stage_name,signal_time_utc,signal_price,
                    risk_pct,horizon_min,gross_return_pct,net_return_pct,r_multiple,
                    mfe_pct,mae_pct,mfe_r,mae_r,bars,evaluated_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["id"],row["symbol"],row["direction"],stage,signal_time,entry,
                 risk_pct,h,m["gross"],m["net"],m["r"],m["mfe"],m["mae"],
                 m["mfe_r"],m["mae_r"],p["bars"],now.isoformat()))
                inserted+=1
            except Exception as exc:
                print("live validation error",row["symbol"],stage,h,type(exc).__name__,str(exc)[:120])
    return inserted


def _mean(xs):
    vals=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.fmean(vals) if vals else None


def report(con):
    now=datetime.now(timezone.utc).isoformat()
    out=[]
    for h in HORIZONS:
        for stage in ("EARLY","CLOSE_CONFIRMED","TRIGGERED"):
            rows=con.execute("""SELECT net_return_pct,r_multiple FROM live_forward_validation
                                WHERE horizon_min=? AND stage_name=?""",(h,stage)).fetchall()
            nets=[r[0] for r in rows if r[0] is not None]
            rs=[r[1] for r in rows if r[1] is not None]
            exp=_mean(rs); avg=_mean(nets)
            win=(100.0*sum(1 for x in nets if x>0)/len(nets)) if nets else None
            con.execute("""INSERT INTO live_validation_reports(
                report_time_utc,horizon_min,stage_name,n,expectancy_r,avg_net_pct,win_rate
            ) VALUES(?,?,?,?,?,?,?)""",(now,h,stage,len(rows),exp,avg,win))
            out.append((h,stage,len(rows),exp,avg,win))
    return out


def main():
    if not os.path.exists(DB):
        print("live validation: DB missing")
        return
    with sqlite3.connect(DB) as con:
        init_db(con)
        n=evaluate(con)
        rows=report(con)
        con.commit()
    print("LIVE_FORWARD_VALIDATION inserted=",n)
    for h,stage,n,exp,avg,win in rows:
        es="-" if exp is None else f"{exp:+.3f}R"
        ns="-" if avg is None else f"{avg:+.3f}%"
        ws="-" if win is None else f"{win:.1f}%"
        print(f"{h}m {stage}: n={n} exp={es} net={ns} win={ws}")


if __name__=="__main__":
    main()
