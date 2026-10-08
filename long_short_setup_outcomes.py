#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post-ACTIVE paper outcome labeler for locked Long/Short setup ledger.

Pure path logic plus optional explicit venue fetch. No signal scoring, no
threshold tweaks, no Telegram and NO order execution. Every primary result
requires a final delivered Telegram event and a retest-band ACTIVE entry.
All path features use only closed, time-ordered 1m candles after the entry.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime,timedelta,timezone
from long_short_setup_lifecycle import (
    init_schema,utc,iso,_query_one,independence_key,market_cluster,
)

HORIZONS=(15,60,180)
FEE_BPS_PER_SIDE=5.0
SLIPPAGE_BPS_PER_SIDE=10.0
VERSION="SETUP_OUTCOME_V1_2026_10_08"


def _gross(side,entry,exit_price):
    if side=="LONG":
        return 100.0*(exit_price/entry-1.0)
    return 100.0*(entry/exit_price-1.0)


def _favorable(side,entry,high,low):
    if side=="LONG":
        return 100.0*(high/entry-1.0),100.0*(low/entry-1.0)
    return 100.0*(entry/low-1.0),100.0*(entry/high-1.0)


def _msec(value):
    return int(utc(value).timestamp()*1000)


def _closed_candles(bars,start,end):
    """One-minute candle is usable only if BOTH its open and close follow entry
    and its close is at/before the exact fixed-horizon timeout. No hindsight
    from the forming entry/timeout candle.
    """
    st=_msec(start); en=_msec(end)
    first=((st+59999)//60000)*60000
    last_exclusive=(en//60000)*60000
    if first>=last_exclusive:
        return [],"NO_FULL_CANDLE"
    unique={}
    try:
        for r in bars:
            ts=int(r[0])
            if ts<first or ts>=last_exclusive:
                continue
            prices=tuple(float(r[i]) for i in (1,2,3,4))
            if not all(math.isfinite(x) and x>0 for x in prices):
                return [],"BAD_PRICE"
            op,high,low,close=prices
            if low>min(op,close) or high<max(op,close):
                return [],"BAD_OHLC"
            if ts in unique:
                return [],"DUPLICATE_CANDLE"
            unique[ts]=(ts,op,high,low,close)
    except (IndexError,ValueError,TypeError,OverflowError):
        return [],"BAD_CANDLE_FORMAT"
    expected=list(range(first,last_exclusive,60000))
    if sorted(unique)!=expected:
        return [],"MISSING_1M_CANDLE"
    return [unique[t] for t in expected],None


def label_path(setup,bars,horizon_min,*,btc_bars=None,price_source=None,
               same_chart_venue=False,now=None,
               fee_bps_per_side=FEE_BPS_PER_SIDE,
               slippage_bps_per_side=SLIPPAGE_BPS_PER_SIDE):
    """Deterministic first-barrier label. Missing bars -> DATA_GAP, NULL returns."""
    if horizon_min not in HORIZONS:
        raise ValueError("unsupported fixed horizon")
    if setup.get("active_at") is None or setup.get("final_telegram_event_id") is None:
        raise ValueError("only delivered ACTIVE setups can be measured")
    if setup.get("final_telegram_sent_at") is None:
        raise ValueError("Telegram send time required")
    entry_time=utc(setup["active_at"])
    if utc(setup["final_telegram_sent_at"])>entry_time:
        raise ValueError("trade starts before final Telegram")
    end=entry_time+timedelta(minutes=horizon_min)
    now=utc(now or datetime.now(timezone.utc))
    if now<end:
        raise ValueError("horizon has not matured")
    source=str(price_source or setup.get("price_source") or "UNKNOWN")
    entry=float(setup["entry_price"])
    side=setup["direction"]
    sl=float(setup["invalidation"])
    tp1=float(setup["tp1"])
    costs=(2.0*(float(fee_bps_per_side)+float(slippage_bps_per_side)))/100.0
    if costs<0:raise ValueError("negative fee/slippage")
    base={
        "outcome_status":"DATA_GAP","first_barrier":None,"first_barrier_time":None,
        "start_at":iso(entry_time),"horizon_end_at":iso(end),"exit_at":None,
        "entry_price":entry,"exit_price":None,"gross_return_pct":None,
        "net_return_pct":None,"net_return_2x_cost_pct":None,"cost_pct":costs,
        "mfe_pct":None,"mae_pct":None,"btc_excess_return_pct":None,
        "btc_return_pct":None,"time_in_trade_seconds":None,
        "price_source":source,"same_chart_venue":int(bool(same_chart_venue)),
        "data_gap_reason":None,
    }
    records,issue=_closed_candles(bars or [],entry_time,end)
    if issue:
        base["data_gap_reason"]=issue
        return base

    first_barrier=None
    first_time=None
    exit_price=None
    exit_at=None
    highs=[];lows=[]
    for t,opening,high,low,close in records:
        highs.append(high);lows.append(low)
        # Opening gap is worse than theoretical stop; never fill at imaginary SL.
        if side=="LONG":
            stop_hit=(opening<=sl or low<=sl)
            tp_hit=(high>=tp1)
            gap_exit=min(sl,opening)
        else:
            stop_hit=(opening>=sl or high>=sl)
            tp_hit=(low<=tp1)
            gap_exit=max(sl,opening)
        # STOP FIRST if both barriers are inside the same 1m candle.
        if stop_hit:
            first_barrier="STOP";exit_price=gap_exit
        elif tp_hit:
            first_barrier="TP1";exit_price=tp1
        if first_barrier:
            first_time=iso(datetime.fromtimestamp(t/1000,tz=timezone.utc))
            exit_at=first_time
            break

    if first_barrier is None:
        last=records[-1]
        exit_price=last[4]
        exit_at=iso(datetime.fromtimestamp((last[0]+60000)/1000,tz=timezone.utc))
        first_barrier="TIMEOUT"
        first_time=iso(end)

    gross=_gross(side,entry,exit_price)
    net=gross-costs
    mfe,mae=_favorable(side,entry,max(highs),min(lows))
    base.update({
        "outcome_status":first_barrier,
        "first_barrier":first_barrier,
        "first_barrier_time":first_time,
        "exit_at":exit_at,
        "exit_price":exit_price,
        "gross_return_pct":gross,
        "net_return_pct":net,
        "net_return_2x_cost_pct":gross-2.0*costs,
        "mfe_pct":mfe,
        "mae_pct":mae,
        "time_in_trade_seconds":(utc(exit_at)-entry_time).total_seconds(),
        "data_gap_reason":None,
    })
    if btc_bars is not None:
        btc,btc_issue=_closed_candles(btc_bars,entry_time,end)
        if not btc_issue and btc:
            # Compare until trade actually exited, not at the full horizon when
            # the coin had already hit its stop or target.
            exit_boundary=utc(exit_at)
            priced=[r for r in btc if r[0]+60000<=_msec(exit_boundary)]
            if priced:
                btc_return=100.0*(priced[-1][4]/priced[0][1]-1.0)
                base["btc_return_pct"]=btc_return
                base["btc_excess_return_pct"]=net-(btc_return if side=="LONG" else -btc_return)
    return base


def write_outcome(con,setup_id,horizon_min,bars,*,btc_bars=None,
                  price_source=None,same_chart_venue=False,now=None,
                  fee_bps_per_side=FEE_BPS_PER_SIDE,
                  slippage_bps_per_side=SLIPPAGE_BPS_PER_SIDE):
    init_schema(con)
    if con.execute("""SELECT 1 FROM setup_outcomes
        WHERE setup_id=? AND horizon_min=?""",(setup_id,horizon_min)).fetchone():
        return False
    setup=_query_one(con,"SELECT * FROM setups WHERE setup_id=?",(setup_id,))
    if setup is None:
        raise KeyError(setup_id)
    label=label_path(setup,bars,horizon_min,btc_bars=btc_bars,
        price_source=price_source,same_chart_venue=same_chart_venue,now=now,
        fee_bps_per_side=fee_bps_per_side,
        slippage_bps_per_side=slippage_bps_per_side)
    label["market_cluster"]=market_cluster(setup["active_at"])
    label["independence_key"]=independence_key(con,setup_id)
    label["measured_at"]=iso(now or datetime.now(timezone.utc))
    columns=("setup_id","horizon_min")+tuple(label)
    values=(setup_id,horizon_min)+tuple(label.values())
    con.execute("INSERT INTO setup_outcomes("+",".join(columns)+") VALUES("+",".join("?" for _ in columns)+")",values)
    return True


def primary_report(con):
    """No WATCH, SHADOW, unentered or undelivered setups. Distinct waves reported."""
    init_schema(con)
    cur=con.execute("""SELECT s.direction,s.regime_at_create,o.horizon_min,
        o.market_cluster,o.independence_key,o.outcome_status,
        o.net_return_pct,o.net_return_2x_cost_pct
        FROM setup_outcomes o
        JOIN setups s ON s.setup_id=o.setup_id
        WHERE s.active_at IS NOT NULL AND s.final_telegram_event_id IS NOT NULL
        ORDER BY s.direction,s.regime_at_create,o.horizon_min,o.market_cluster""")
    buckets={}
    for side,regime,h,cluster,ikey,status,net,stress in cur:
        key=(side,regime,h)
        buckets.setdefault(key,[]).append((cluster,ikey,status,net,stress))
    result=[]
    for (side,regime,h),rows in sorted(buckets.items()):
        independent={}
        for cl,ik,status,net,stress in rows:
            independent.setdefault(cl,[]).append((ik,status,net,stress))
        # One coin+direction event per 2h, then one equal-weight independent
        # 15m market wave. Correlated multi-coin bursts never dominate an edge
        # estimate merely because they produced more alerts.
        cluster_nets=[]
        cluster_stress=[]
        for wave in independent.values():
            unique={}
            for ik,status,net,stress in wave:
                if ik not in unique:
                    unique[ik]=(status,net,stress)
            n=[x[1] for x in unique.values() if x[1] is not None]
            s=[x[2] for x in unique.values() if x[2] is not None]
            if n:
                cluster_nets.append(sum(n)/len(n))
            if s:
                cluster_stress.append(sum(s)/len(s))
        result.append({
            "direction":side,"regime":regime,"horizon_min":h,
            "raw_final_events":len(rows),
            "unique_market_clusters":len(independent),
            "unique_coin_direction_events":len({v[0] for arr in independent.values() for v in arr}),
            "data_gap":sum(x[2]=="DATA_GAP" for x in rows),
            "average_net_pct":(sum(cluster_nets)/len(cluster_nets)) if cluster_nets else None,
            "average_2x_cost_net_pct":(sum(cluster_stress)/len(cluster_stress)) if cluster_stress else None,
            "conclusion":"INCONCLUSIVE" if len(independent)<100 else "REQUIRES_CLUSTER_CI_REVIEW",
        })
    return result


def evaluate_due(con,as_of=None,*,max_setups=20,history_fetch=None):
    """Append matured ACTIVE-only labels; never manufacture a final trade.

    Historical 1m candles must come from the setup's declared chart venue.
    No silent provider swaps. If the provider is unavailable or has gaps, a
    DATA_GAP row with NULL return is retained. Existing labels are immutable.
    """
    from long_short_outcome_prices import historical_1m
    init_schema(con)
    at=utc(as_of or datetime.now(timezone.utc))
    fetch=history_fetch or historical_1m
    rows=con.execute("""SELECT s.setup_id,s.active_at,s.symbol,s.price_source
        FROM setups s WHERE s.active_at IS NOT NULL
          AND s.final_telegram_event_id IS NOT NULL
          AND s.final_telegram_sent_at IS NOT NULL
          AND (SELECT COUNT(*) FROM setup_outcomes o WHERE o.setup_id=s.setup_id)<3
        ORDER BY s.active_at LIMIT ?""",(max(1,int(max_setups)),)).fetchall()
    written=0
    for sid,entered,symbol,source in rows:
        started=utc(entered)
        for h in HORIZONS:
            cutoff=started+timedelta(minutes=h)
            if at<cutoff+timedelta(minutes=2):
                continue
            if con.execute("SELECT 1 FROM setup_outcomes WHERE setup_id=? AND horizon_min=?",
                           (sid,h)).fetchone():
                continue
            market=[]
            btc=None
            chart_venue_ok=False
            if source in ("GATE_FUTURES","BYBIT_LINEAR","BINANCE_SPOT"):
                try:
                    market=fetch(symbol,started,cutoff,
                                 requested_source=source,allow_fallback=False)
                    chart_venue_ok=(getattr(market,"source",None)==source and
                                    getattr(market,"venue_matched",False))
                    if symbol=="BTCUSDT":
                        btc=market
                    elif chart_venue_ok:
                        try:
                            btc=fetch("BTCUSDT",started,cutoff,
                                      requested_source=source,allow_fallback=False)
                        except Exception:
                            btc=None
                except Exception as exc:
                    print("SETUP_DATA_GAP",symbol,source,h,type(exc).__name__,
                          str(exc)[:120],flush=True)
            if write_outcome(con,sid,h,market,btc_bars=btc,
                             price_source=source,same_chart_venue=chart_venue_ok,
                             now=at):
                written+=1
    return written
