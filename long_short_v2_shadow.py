#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long/Short V2 shadow overlay.

Purpose:
- simplify V1.9's many additive score weights into explicit gates;
- remain observational only;
- never send Telegram messages or change live V1.9 decisions;
- create a clean candidate set for out-of-sample comparison.

The overlay reads existing analyst snapshots and asks a narrower question:
Would this setup still qualify if trend, structure, micro trigger, derivatives
flow, crowding and cost-adjusted target room all had to agree?
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timezone

ANALYST_DB=os.getenv("LS_DB","long_short_analyst.db")
RESEARCH_DB=os.getenv("LS_RESEARCH_DB","long_short_research.db")
VERSION="LS_V2_SHADOW_GATE_BASED_2026-10-05"
MIN_DERIVATIVE_VOTES=2
MIN_ROOM_TO_COST=4.0
MIN_ROOM_TO_ATR=0.75
MIN_SLIPPAGE_BPS_PER_SIDE=10.0
FEE_BPS_PER_SIDE=5.0


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def f(x,default=0.0):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def trend_aligned(tf,direction):
    p=f(tf.get("price")); e20=f(tf.get("ema20")); e50=f(tf.get("ema50"))
    if direction=="LONG":
        return p>e20>e50
    return p<e20<e50


def structure_aligned(tf,direction):
    s=int(tf.get("structure") or 0)
    return s>0 if direction=="LONG" else s<0


def derivative_votes(payload,direction):
    t1h=payload.get("t1h") or {}
    price1h=f(t1h.get("change_1"))
    oi=payload.get("oi") or {}
    oic=f(oi.get("oi_change_1h"))
    taker=f(payload.get("taker_ratio"),1.0)
    depth=f(payload.get("depth_imbalance"))

    if direction=="LONG":
        votes={
            "oi": price1h>0 and oic>1.5,
            "taker": taker>=1.08,
            "depth": depth>=0.08,
        }
    else:
        votes={
            "oi": price1h<0 and oic>1.5,
            "taker": taker<=0.92,
            "depth": depth<=-0.08,
        }
    return votes


def crowding_ok(payload,direction):
    funding=f(payload.get("funding_pct"))
    ls=f(payload.get("long_short_ratio"),1.0)
    if direction=="LONG":
        return not (funding>=0.05 or ls>=2.0)
    return not (funding<=-0.05 or ls<=0.5)


def execution_cost_pct(payload,direction):
    proxy=payload.get("execution_proxy") or {}
    costs=(proxy.get("costs") or {}).get("250") or {}
    if direction=="LONG":
        ek="buy_bps"; xk="sell_bps"
    else:
        ek="sell_bps"; xk="buy_bps"
    entry=max(MIN_SLIPPAGE_BPS_PER_SIDE,f(costs.get(ek)))
    exit_=max(MIN_SLIPPAGE_BPS_PER_SIDE,f(costs.get(xk)))
    return (2.0*FEE_BPS_PER_SIDE+entry+exit_)/100.0


def room_metrics(payload,direction):
    plan=payload.get("setup_plan") or {}
    price=f((payload.get("t5") or {}).get("price"))
    target=f(plan.get("target1"))
    atr_pct=max(0.0001,f((payload.get("t15") or {}).get("atr_pct")))
    if not price or not target:
        return {"room_pct":0.0,"cost_pct":None,"room_to_cost":0.0,"room_to_atr":0.0}
    if direction=="LONG":
        room=max(0.0,(target/price-1.0)*100.0)
    else:
        room=max(0.0,(price/target-1.0)*100.0) if target>0 else 0.0
    cost=max(0.0001,execution_cost_pct(payload,direction))
    return {
        "room_pct":room,
        "cost_pct":cost,
        "room_to_cost":room/cost,
        "room_to_atr":room/atr_pct,
    }


def momentum_ok(payload,direction):
    t15=payload.get("t15") or {}
    t5=payload.get("t5") or {}
    r15=f(t15.get("rsi"),50.0)
    r5=f(t5.get("rsi"),50.0)
    if direction=="LONG":
        return 48.0<=r15<=72.0 and r5>=48.0
    return 28.0<=r15<=52.0 and r5<=52.0


def classify(payload):
    plan=payload.get("setup_plan") or {}
    direction=str(plan.get("direction") or "")
    if direction not in ("LONG","SHORT"):
        return "REJECT",{"reason":"NO_DIRECTION"}

    htf=payload.get("htf_gate") or {}
    chart=payload.get("chart") or {}
    t4h=payload.get("t4h") or {}
    t1h=payload.get("t1h") or {}
    t15=payload.get("t15") or {}
    t5=payload.get("t5") or {}

    votes=derivative_votes(payload,direction)
    room=room_metrics(payload,direction)
    gates={
        "data_ready":bool(payload.get("derivatives_ready")),
        "htf_aligned":bool(htf.get("qualified")) and str(htf.get("direction") or "")==direction,
        "trend_aligned":sum([trend_aligned(t4h,direction),trend_aligned(t1h,direction),trend_aligned(t15,direction)])>=2,
        "structure_aligned":structure_aligned(t15,direction) and structure_aligned(t5,direction),
        "micro_trigger":bool(chart.get("long_trigger") if direction=="LONG" else chart.get("short_trigger")),
        "derivatives_confirmed":sum(bool(v) for v in votes.values())>=MIN_DERIVATIVE_VOTES,
        "momentum_ok":momentum_ok(payload,direction),
        "not_overextended":not bool(chart.get("overextended_up") if direction=="LONG" else chart.get("overextended_down")),
        "crowding_ok":crowding_ok(payload,direction),
        "cost_room_ok":room["room_to_cost"]>=MIN_ROOM_TO_COST and room["room_to_atr"]>=MIN_ROOM_TO_ATR,
    }

    mandatory=("data_ready","htf_aligned","trend_aligned","structure_aligned","micro_trigger","derivatives_confirmed","cost_room_ok")
    mandatory_pass=all(gates[k] for k in mandatory)
    soft_pass=sum(bool(v) for k,v in gates.items() if k not in mandatory)
    if mandatory_pass and soft_pass>=2:
        status="QUALIFIED"
    elif gates["data_ready"] and gates["htf_aligned"] and sum(bool(v) for v in gates.values())>=7:
        status="NEAR_MISS"
    else:
        status="REJECT"

    return status,{
        "version":VERSION,
        "direction":direction,
        "gates":gates,
        "derivative_votes":votes,
        "room":room,
        "gate_pass_count":sum(bool(v) for v in gates.values()),
        "gate_total":len(gates),
    }


def init_db(con):
    con.execute("""CREATE TABLE IF NOT EXISTS v2_shadow_decisions(
        scan_time_utc TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT,
        status TEXT NOT NULL,
        v1_status TEXT,
        v1_long_score INTEGER,
        v1_short_score INTEGER,
        v1_confidence INTEGER,
        data_cohort TEXT,
        model_config_hash TEXT,
        payload_json TEXT NOT NULL,
        evaluated_at_utc TEXT NOT NULL,
        PRIMARY KEY(scan_time_utc,symbol)
    )""")


def cohort(payload):
    source=str(payload.get("derivatives_source") or payload.get("data_mode") or "")
    provider=str(payload.get("derivatives_selected_provider") or "")
    if source=="BINANCE_FUTURES":
        return "BINANCE_FUTURES_NATIVE"
    if provider=="BYBIT_LINEAR":
        return "SPOT_PLUS_BYBIT"
    if provider=="GATE_FUTURES":
        return "SPOT_PLUS_GATE"
    if source=="MULTI_VENUE_PUBLIC":
        return "SPOT_PLUS_OTHER_OR_PARTIAL"
    return "UNKNOWN"


def main():
    if not os.path.exists(ANALYST_DB):
        print("V2 shadow skipped: analyst DB missing")
        return
    acon=sqlite3.connect(f"file:{os.path.abspath(ANALYST_DB)}?mode=ro",uri=True)
    acon.row_factory=sqlite3.Row
    rcon=sqlite3.connect(RESEARCH_DB)
    try:
        init_db(rcon)
        rows=acon.execute("""SELECT scan_time_utc,symbol,status,long_score,short_score,
                                   confidence,payload_json
                            FROM analyses
                            ORDER BY scan_time_utc,symbol""").fetchall()
        inserted=0
        counts={"QUALIFIED":0,"NEAR_MISS":0,"REJECT":0}
        for row in rows:
            if rcon.execute("SELECT 1 FROM v2_shadow_decisions WHERE scan_time_utc=? AND symbol=?",
                            (row["scan_time_utc"],row["symbol"])).fetchone():
                continue
            try:
                p=json.loads(row["payload_json"] or "{}")
            except Exception:
                p={}
            status,meta=classify(p)
            counts[status]=counts.get(status,0)+1
            rcon.execute("""INSERT INTO v2_shadow_decisions(
                scan_time_utc,symbol,direction,status,v1_status,v1_long_score,
                v1_short_score,v1_confidence,data_cohort,model_config_hash,
                payload_json,evaluated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(
                row["scan_time_utc"],row["symbol"],meta.get("direction"),status,row["status"],
                row["long_score"],row["short_score"],row["confidence"],cohort(p),
                str(p.get("frozen_config_hash") or ""),
                json.dumps(meta,ensure_ascii=False,separators=(",",":")),now_iso()
            ))
            inserted+=1
        rcon.commit()
        print("V2_SHADOW inserted=",inserted,"counts=",json.dumps(counts,sort_keys=True))
    finally:
        acon.close(); rcon.close()


if __name__=="__main__":
    main()
