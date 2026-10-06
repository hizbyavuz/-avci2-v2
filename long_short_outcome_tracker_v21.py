#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V2.1 user-executable outcome tracker.

Measures exactly what the user could act on:
- delivered CLOSE_CONFIRMED and TRIGGERED Telegram events,
- actual frozen invalidation / T1 / T2 from the live event,
- +30s human decision delay and first available 1m market proxy,
- minimum 5 bps fee + 10 bps slippage per side,
- stop-first same-bar ambiguity,
- separate fallback/native cohorts,
- WATCH episodes that leave the live pool without confirmation.

Research only. Never places orders.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests
from binance_notify import resolve_chat_id

DB=os.getenv("LS_LIVE_DB","long_short_live_pool.db")
NOTIFY_DB=os.getenv("LS_SIMPLE_NOTIFY_DB","long_short_simple_notify.db")
VERSION="LS_OUTCOME_V2_1_2026-10-06"
HORIZONS=(15,60,240)
FEE_BPS_PER_SIDE=float(os.getenv("LS_FEE_BPS_PER_SIDE","5"))
MIN_SLIPPAGE_BPS_PER_SIDE=float(os.getenv("LS_VALIDATION_MIN_SLIPPAGE_BPS_PER_SIDE","10"))
HUMAN_DELAY_SECONDS=float(os.getenv("LS_VALIDATION_HUMAN_DELAY_SECONDS","30"))
STALE_DELAY_SECONDS=float(os.getenv("LS_STALE_ALERT_DELAY_SECONDS","60"))
MISSED_CLOSE_DELAY_SECONDS=float(os.getenv("LS_MISSED_CLOSE_DELAY_SECONDS","90"))
BASES=("https://data-api.binance.vision","https://api.binance.com")
TELEGRAM_LIMIT=4096


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _dt(x):
    d=datetime.fromisoformat(x)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _get(path,params):
    last=None
    for base in BASES:
        try:
            r=requests.get(base+path,params=params,timeout=12,
                           headers={"User-Agent":"lsa-outcome-v2.1"})
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last=exc
    raise last or RuntimeError("spot data unavailable")


def _rows_1m(symbol,start,end):
    rows=[]
    cursor=int(start.timestamp()*1000)
    end_ms=int(end.timestamp()*1000)
    while cursor<end_ms and len(rows)<2000:
        chunk=_get("/api/v3/klines",{
            "symbol":symbol,"interval":"1m","startTime":cursor,"endTime":end_ms,
            "limit":1000,
        })
        if not chunk:
            break
        rows.extend(chunk)
        nxt=int(chunk[-1][6])+1
        if nxt<=cursor:
            break
        cursor=nxt
        if len(chunk)<1000:
            break
    return rows


def _payload(row):
    try:
        return json.loads(row["payload_json"] or "{}")
    except Exception:
        return {}


def _levels(payload):
    return (
        float(payload.get("invalidation") or 0.0),
        float(payload.get("target1") or 0.0),
        float(payload.get("target2") or 0.0),
    )


def _cohort(payload):
    return str(payload.get("data_cohort") or "UNKNOWN")


def _slippage_pair(payload,direction):
    proxy=payload.get("_trigger_execution_proxy") or {}
    costs=(proxy.get("costs") or {})
    size=str(int(float(proxy.get("notional_usdt") or 250)))
    c=costs.get(size) or {}
    buy=c.get("buy_bps")
    sell=c.get("sell_bps")
    def good(x):
        try:
            x=float(x)
            return x if math.isfinite(x) and x>=0 else None
        except Exception:
            return None
    buy=good(buy); sell=good(sell)
    if direction=="LONG":
        entry=max(MIN_SLIPPAGE_BPS_PER_SIDE,buy or 0.0)
        exit_=max(MIN_SLIPPAGE_BPS_PER_SIDE,sell or 0.0)
    else:
        entry=max(MIN_SLIPPAGE_BPS_PER_SIDE,sell or 0.0)
        exit_=max(MIN_SLIPPAGE_BPS_PER_SIDE,buy or 0.0)
    return entry,exit_


def _cost_pct(payload,direction):
    e,x=_slippage_pair(payload,direction)
    return (2.0*FEE_BPS_PER_SIDE+e+x)/100.0


def _direction_return(direction,entry,price):
    if direction=="SHORT":
        return (entry/price-1.0)*100.0 if price else 0.0
    return (price/entry-1.0)*100.0 if entry else 0.0


def _barrier_path(direction,entry,stop,t1,t2,rows):
    first=None
    first_time=None
    tp2_hit=False
    high=max(float(r[2]) for r in rows) if rows else entry
    low=min(float(r[3]) for r in rows) if rows else entry
    for r in rows:
        h=float(r[2]); l=float(r[3])
        ts=datetime.fromtimestamp(int(r[0])/1000,tz=timezone.utc).isoformat()
        if direction=="LONG":
            # Pessimistic ambiguity: stop first.
            if l<=stop:
                first="STOP"; first_time=ts; break
            if h>=t1:
                first="TP1"; first_time=ts; break
        else:
            if h>=stop:
                first="STOP"; first_time=ts; break
            if l<=t1:
                first="TP1"; first_time=ts; break
    if direction=="LONG":
        tp2_hit=any(float(r[2])>=t2 for r in rows) if t2>0 else False
        mfe=(high/entry-1.0)*100.0
        mae=(low/entry-1.0)*100.0
    else:
        tp2_hit=any(float(r[3])<=t2 for r in rows) if t2>0 else False
        mfe=(entry/low-1.0)*100.0 if low else 0.0
        mae=(entry/high-1.0)*100.0 if high else 0.0
    return first,first_time,tp2_hit,mfe,mae


def init_db(con):
    con.execute("""CREATE TABLE IF NOT EXISTS delivered_signal_outcomes(
        event_id INTEGER NOT NULL,
        version TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        stage_name TEXT NOT NULL,
        data_cohort TEXT NOT NULL,
        condition_time_utc TEXT,
        telegram_sent_time_utc TEXT NOT NULL,
        execution_time_utc TEXT NOT NULL,
        delivery_delay_seconds REAL,
        stale_delivery INTEGER NOT NULL,
        missed_close_window INTEGER NOT NULL,
        entry_price REAL NOT NULL,
        invalidation REAL NOT NULL,
        target1 REAL NOT NULL,
        target2 REAL,
        fee_bps_per_side REAL NOT NULL,
        entry_slippage_bps REAL NOT NULL,
        exit_slippage_bps REAL NOT NULL,
        round_trip_cost_pct REAL NOT NULL,
        risk_pct REAL NOT NULL,
        horizon_min INTEGER NOT NULL,
        endpoint_price REAL,
        gross_return_pct REAL,
        net_return_pct REAL,
        r_multiple REAL,
        mfe_pct REAL,
        mae_pct REAL,
        mfe_r REAL,
        mae_r REAL,
        first_barrier TEXT,
        first_barrier_time_utc TEXT,
        tp2_hit INTEGER,
        reached_3 INTEGER,
        reached_5 INTEGER,
        reached_7 INTEGER,
        reached_10 INTEGER,
        reached_15 INTEGER,
        direction_correct INTEGER,
        evaluated_at_utc TEXT NOT NULL,
        PRIMARY KEY(event_id,horizon_min)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS ix_delivered_signal_perf
                   ON delivered_signal_outcomes(stage_name,data_cohort,horizon_min)""")
    con.execute("""CREATE TABLE IF NOT EXISTS watch_no_confirm_outcomes(
        watch_episode_id INTEGER NOT NULL,
        version TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        started_at_utc TEXT NOT NULL,
        ended_at_utc TEXT NOT NULL,
        data_cohort TEXT,
        start_price REAL NOT NULL,
        horizon_min INTEGER NOT NULL,
        endpoint_price REAL,
        gross_return_pct REAL,
        mfe_pct REAL,
        mae_pct REAL,
        direction_correct INTEGER,
        evaluated_at_utc TEXT NOT NULL,
        PRIMARY KEY(watch_episode_id,horizon_min)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS outcome_reports(
        report_time_utc TEXT NOT NULL,
        version TEXT NOT NULL,
        stage_name TEXT NOT NULL,
        data_cohort TEXT NOT NULL,
        horizon_min INTEGER NOT NULL,
        n INTEGER NOT NULL,
        correct INTEGER NOT NULL,
        wrong INTEGER NOT NULL,
        timeout INTEGER NOT NULL,
        win_rate REAL,
        expectancy_r REAL,
        avg_net_pct REAL,
        p50_delay_seconds REAL,
        p95_delay_seconds REAL,
        PRIMARY KEY(report_time_utc,stage_name,data_cohort,horizon_min)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS outcome_runtime(
        key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at_utc TEXT NOT NULL
    )""")


def evaluate_delivered(con):
    con.row_factory=sqlite3.Row
    events=con.execute("""SELECT * FROM events
        WHERE stage_to IN ('CLOSE_CONFIRMED','TRIGGERED')
          AND telegram_status='SENT'
          AND telegram_sent_time_utc IS NOT NULL
        ORDER BY id""").fetchall()
    now=datetime.now(timezone.utc)
    added=0
    for ev in events:
        payload=_payload(ev)
        inv,t1,t2=_levels(payload)
        if inv<=0 or t1<=0:
            continue
        sent=_dt(ev["telegram_sent_time_utc"])
        cond=_dt(ev["condition_time_utc"] or ev["event_time_utc"])
        execute=max(sent,cond)+timedelta(seconds=HUMAN_DELAY_SECONDS)
        delay=(sent-cond).total_seconds()
        cohort=_cohort(payload)
        direction=ev["direction"]
        entry_slip,exit_slip=_slippage_pair(payload,direction)
        cost_pct=(2.0*FEE_BPS_PER_SIDE+entry_slip+exit_slip)/100.0
        max_end=execute+timedelta(minutes=max(HORIZONS)+2)
        if now<execute+timedelta(minutes=min(HORIZONS)+2):
            continue
        try:
            all_rows=_rows_1m(ev["symbol"],execute,max_end)
        except Exception as exc:
            print("outcome fetch error",ev["symbol"],type(exc).__name__,str(exc)[:120])
            continue
        if not all_rows:
            continue
        entry=float(all_rows[0][1])
        if direction=="LONG" and not (inv<entry<t1):
            print("outcome invalid levels",ev["symbol"],direction,entry,inv,t1)
            continue
        if direction=="SHORT" and not (t1<entry<inv):
            print("outcome invalid levels",ev["symbol"],direction,entry,inv,t1)
            continue
        risk_pct=abs(entry-inv)/entry*100.0
        for h in HORIZONS:
            if now<execute+timedelta(minutes=h+2):
                continue
            if con.execute("SELECT 1 FROM delivered_signal_outcomes WHERE event_id=? AND horizon_min=?",
                           (ev["id"],h)).fetchone():
                continue
            cutoff=execute+timedelta(minutes=h)
            rows=[r for r in all_rows if datetime.fromtimestamp(int(r[0])/1000,tz=timezone.utc)<cutoff]
            if not rows:
                continue
            endpoint=float(rows[-1][4])
            gross=_direction_return(direction,entry,endpoint)
            net=gross-cost_pct
            first,first_time,tp2_hit,mfe,mae=_barrier_path(direction,entry,inv,t1,t2,rows)
            rm=net/risk_pct if risk_pct>0 else None
            mfe_r=mfe/risk_pct if risk_pct>0 else None
            mae_r=mae/risk_pct if risk_pct>0 else None
            correct=1 if net>0 else 0
            con.execute("""INSERT INTO delivered_signal_outcomes(
                event_id,version,symbol,direction,stage_name,data_cohort,
                condition_time_utc,telegram_sent_time_utc,execution_time_utc,
                delivery_delay_seconds,stale_delivery,missed_close_window,
                entry_price,invalidation,target1,target2,fee_bps_per_side,
                entry_slippage_bps,exit_slippage_bps,round_trip_cost_pct,risk_pct,
                horizon_min,endpoint_price,gross_return_pct,net_return_pct,r_multiple,
                mfe_pct,mae_pct,mfe_r,mae_r,first_barrier,first_barrier_time_utc,tp2_hit,
                reached_3,reached_5,reached_7,reached_10,reached_15,direction_correct,
                evaluated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ev["id"],VERSION,ev["symbol"],direction,ev["stage_to"],cohort,
             cond.isoformat(),sent.isoformat(),execute.isoformat(),delay,
             1 if delay>STALE_DELAY_SECONDS else 0,
             1 if ev["stage_to"]=="CLOSE_CONFIRMED" and delay>MISSED_CLOSE_DELAY_SECONDS else 0,
             entry,inv,t1,t2,FEE_BPS_PER_SIDE,entry_slip,exit_slip,cost_pct,risk_pct,
             h,endpoint,gross,net,rm,mfe,mae,mfe_r,mae_r,first,first_time,
             1 if tp2_hit else 0,
             1 if mfe>=3 else 0,1 if mfe>=5 else 0,1 if mfe>=7 else 0,
             1 if mfe>=10 else 0,1 if mfe>=15 else 0,correct,now_iso()))
            added+=1
    return added


def evaluate_no_confirm(con):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watch_episodes'").fetchone():
        return 0
    con.row_factory=sqlite3.Row
    eps=con.execute("""SELECT * FROM watch_episodes
        WHERE ended_at_utc IS NOT NULL
          AND close_confirmed_time IS NULL
          AND start_price IS NOT NULL
          AND start_price>0
        ORDER BY id""").fetchall()
    now=datetime.now(timezone.utc)
    added=0
    for ep in eps:
        start=_dt(ep["started_at_utc"])
        for h in HORIZONS:
            if now<start+timedelta(minutes=h+2):
                continue
            if con.execute("SELECT 1 FROM watch_no_confirm_outcomes WHERE watch_episode_id=? AND horizon_min=?",
                           (ep["id"],h)).fetchone():
                continue
            try:
                rows=_rows_1m(ep["symbol"],start,start+timedelta(minutes=h))
            except Exception as exc:
                print("watch control fetch error",ep["symbol"],type(exc).__name__,str(exc)[:120])
                continue
            if not rows:
                continue
            entry=float(ep["start_price"])
            endpoint=float(rows[-1][4])
            direction=ep["direction"]
            gross=_direction_return(direction,entry,endpoint)
            high=max(float(r[2]) for r in rows); low=min(float(r[3]) for r in rows)
            if direction=="LONG":
                mfe=(high/entry-1)*100; mae=(low/entry-1)*100
            else:
                mfe=(entry/low-1)*100 if low else 0; mae=(entry/high-1)*100 if high else 0
            con.execute("""INSERT INTO watch_no_confirm_outcomes(
                watch_episode_id,version,symbol,direction,started_at_utc,ended_at_utc,
                data_cohort,start_price,horizon_min,endpoint_price,gross_return_pct,
                mfe_pct,mae_pct,direction_correct,evaluated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ep["id"],VERSION,ep["symbol"],direction,ep["started_at_utc"],ep["ended_at_utc"],
             ep["data_cohort"],entry,h,endpoint,gross,mfe,mae,1 if gross>0 else 0,now_iso()))
            added+=1
    return added


def _percentile(xs,p):
    vals=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not vals:
        return None
    if len(vals)==1:
        return vals[0]
    pos=(len(vals)-1)*p
    lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi:
        return vals[lo]
    return vals[lo]*(hi-pos)+vals[hi]*(pos-lo)


def report(con):
    now=now_iso()
    cohorts=[r[0] for r in con.execute("SELECT DISTINCT data_cohort FROM delivered_signal_outcomes").fetchall()]
    if not cohorts:
        cohorts=[]
    out=[]
    for stage in ("CLOSE_CONFIRMED","TRIGGERED"):
        for cohort in cohorts:
            for h in HORIZONS:
                rows=con.execute("""SELECT direction_correct,r_multiple,net_return_pct,
                    first_barrier,delivery_delay_seconds
                    FROM delivered_signal_outcomes
                    WHERE stage_name=? AND data_cohort=? AND horizon_min=?""",
                    (stage,cohort,h)).fetchall()
                if not rows:
                    continue
                correct=sum(int(r[0] or 0) for r in rows)
                rs=[r[1] for r in rows if r[1] is not None]
                nets=[r[2] for r in rows if r[2] is not None]
                wrong=sum(1 for r in rows if r[3]=="STOP")
                wins=sum(1 for r in rows if r[3]=="TP1")
                timeout=sum(1 for r in rows if r[3] is None)
                delays=[r[4] for r in rows if r[4] is not None]
                exp=statistics.fmean(rs) if rs else None
                avg=statistics.fmean(nets) if nets else None
                wr=100.0*correct/len(rows)
                p50=_percentile(delays,0.50); p95=_percentile(delays,0.95)
                con.execute("""INSERT INTO outcome_reports(
                    report_time_utc,version,stage_name,data_cohort,horizon_min,n,
                    correct,wrong,timeout,win_rate,expectancy_r,avg_net_pct,
                    p50_delay_seconds,p95_delay_seconds
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now,VERSION,stage,cohort,h,len(rows),correct,wrong,timeout,wr,exp,avg,p50,p95))
                out.append((stage,cohort,h,len(rows),correct,wins,wrong,timeout,wr,exp,avg,p50,p95))
    return out


def _send_daily_summary(rows):
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or not rows:
        return
    day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key="daily_summary|"+VERSION+"|"+day
    with sqlite3.connect(DB) as con:
        if con.execute("SELECT 1 FROM outcome_runtime WHERE key=?",(key,)).fetchone():
            return
    # Prefer TRIGGERED 60m, separated by cohort.
    selected=[r for r in rows if r[0]=="TRIGGERED" and r[2]==60]
    if not selected:
        return
    lines=["📊 LONG/SHORT V2.1 — GÜNLÜK SONUÇ",
           "Gerçek Telegram seviyeleriyle ölçüm (maliyet sonrası)."]
    for stage,cohort,h,n,correct,tp1,stop,timeout,wr,exp,avg,p50,p95 in selected:
        es="-" if exp is None else f"{exp:+.2f}R"
        av="-" if avg is None else f"%{avg:+.2f}"
        ds="-" if p95 is None else f"{p95:.0f}s"
        lines.append(f"{cohort}: n={n} | doğru {correct}/{n} (%{wr:.1f}) | TP1 {tp1} | stop {stop} | net {av} | exp {es} | p95 gecikme {ds}")
    lines.append("Not: 100 bağımsız TRIGGERED episode öncesi edge kanıtlanmış sayılmaz.")
    msg="\n".join(lines)[:TELEGRAM_LIMIT]
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chat=resolve_chat_id(token,configured,NOTIFY_DB,"Long/Short V2.1 Outcome")
    r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id":chat,"text":msg,"disable_web_page_preview":True},
                    timeout=10)
    r.raise_for_status()
    with sqlite3.connect(DB) as con:
        con.execute("INSERT OR REPLACE INTO outcome_runtime(key,value,updated_at_utc) VALUES(?,?,?)",
                    (key,"sent",now_iso()))


def main():
    if not os.path.exists(DB):
        print("outcome tracker: live DB missing")
        return
    with sqlite3.connect(DB) as con:
        init_db(con)
        a=evaluate_delivered(con)
        b=evaluate_no_confirm(con)
        rows=report(con)
        con.commit()
    print("V2.1_OUTCOMES delivered_added=",a,"watch_controls_added=",b)
    for r in rows:
        stage,cohort,h,n,correct,tp1,stop,timeout,wr,exp,avg,p50,p95=r
        es="-" if exp is None else f"{exp:+.3f}R"
        ns="-" if avg is None else f"{avg:+.3f}%"
        d95="-" if p95 is None else f"{p95:.1f}s"
        print(f"{stage} {cohort} {h}m n={n} correct={correct}/{n} TP1={tp1} STOP={stop} timeout={timeout} win={wr:.1f}% exp={es} net={ns} p95={d95}")
    try:
        _send_daily_summary(rows)
    except Exception as exc:
        print("daily summary error",type(exc).__name__,str(exc)[:160])


if __name__=="__main__":
    main()
