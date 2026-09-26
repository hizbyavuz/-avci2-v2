#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Binance activation-math research.

Evaluates predeclared live-activation combinations on CLOSED outcomes.
Does not alter scanner thresholds. Validation results use BH-FDR.
"""

import json
import math
import os
import sqlite3
from datetime import datetime, timezone

DB=os.getenv("BINANCE_DB","binance_avci2.db")
VERSION="binance-activation-math-v1.1-frozen-oos-20260926"
TARGETS=(5,10,15)
DISCOVERY_CUTOFF_UTC="2026-09-25T21:00:00+00:00"
VALIDATION_N=100
COMBOS=(
    ("B1_WAKE_RET","Wake-up + retention"),
    ("B2_WAKE_RET_TAKER","Wake-up + retention + taker-buy"),
    ("B3_REIGNITION_RARITY","Re-ignition + cross-sectional rarity"),
    ("B4_TRIGGER_FLOW","Trigger + taker-buy + pozitif order-book"),
    ("B5_SILENT_ACCUM","Silent accumulation"),
)


def proportion_p(a,n,b,m):
    if not n or not m:
        return None
    pa=a/n; pb=b/m; p=(a+b)/(n+m)
    se=math.sqrt(max(0.0,p*(1-p)*(1/n+1/m)))
    if se==0:
        return 1.0
    z=abs(pa-pb)/se
    return math.erfc(z/math.sqrt(2))


def bh(rows):
    valid=[r for r in rows if r["p_value"] is not None]
    ordered=sorted(valid,key=lambda r:r["p_value"])
    m=len(ordered); prev=1.0
    for rank in range(m,0,-1):
        row=ordered[rank-1]
        q=min(prev,row["p_value"]*m/rank)
        row["q_value"]=q; prev=q
    for r in rows:
        r.setdefault("q_value",None)


def target_value(d,target):
    for k in (str(target), f"{float(target):.1f}"):
        if k in d:
            return d[k]
    return None

def reached(label,target):
    try:
        reach=json.loads(label["reach_json"] or "{}")
    except Exception:
        reach={}
    value=target_value(reach,target)
    if isinstance(value,bool):
        return value
    if isinstance(value,(int,float)):
        return bool(value)
    try:
        ft=json.loads(label["first_touch_json"] or "{}")
        return str(target_value(ft,target) or "").upper() in ("TARGET","TARGET_FIRST")
    except Exception:
        return False


def feature_for_event(c,e):
    f=c.execute("""SELECT * FROM features WHERE scan_time_utc=? AND symbol=?
        ORDER BY is_selected DESC LIMIT 1""",(e["signal_time_utc"],e["symbol"])).fetchone()
    if not f:
        return {}
    flow=c.execute("""SELECT * FROM flow_observations WHERE event_id=? LIMIT 1""",(e["event_id"],)).fetchone() if table(c,"flow_observations") else None
    opp=c.execute("""SELECT * FROM opportunity_observations WHERE scan_time_utc=? AND symbol=?
        ORDER BY version DESC LIMIT 1""",(e["signal_time_utc"],e["symbol"])).fetchone() if table(c,"opportunity_observations") else None
    book=None
    if flow and flow["book_imbalance"] is not None:
        book=float(flow["book_imbalance"])
    taker=float(f["taker_buy_ratio_15m"] or 0)
    rarity=float(f["cross_sectional_rarity_pct"] or 0)
    return {
        "B1_WAKE_RET":bool(f["wakeup"]) and bool(f["retention"]),
        "B2_WAKE_RET_TAKER":bool(f["wakeup"]) and bool(f["retention"]) and taker>=0.55,
        "B3_REIGNITION_RARITY":bool(f["reignition"]) and rarity>=75,
        "B4_TRIGGER_FLOW":bool(f["trigger"]) and taker>=0.55 and book is not None and book>=0.05,
        "B5_SILENT_ACCUM":bool(opp and opp["silent_accumulation"]),
    }


def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None


def main():
    if not os.path.exists(DB):
        print("Binance activation math: DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        c.execute("""CREATE TABLE IF NOT EXISTS binance_activation_math_results(
            version TEXT NOT NULL,split TEXT NOT NULL,combo TEXT NOT NULL,combo_label TEXT NOT NULL,
            target_pct INTEGER NOT NULL,selected_n INTEGER NOT NULL,selected_hits INTEGER NOT NULL,
            selected_rate REAL,baseline_n INTEGER NOT NULL,baseline_hits INTEGER NOT NULL,
            baseline_rate REAL,lift REAL,p_value REAL,q_value REAL,created_at_utc TEXT NOT NULL,
            PRIMARY KEY(version,split,combo,target_pct))""")
        events=c.execute("""SELECT s.event_id,s.symbol,s.signal_time_utc,s.event_class,
            o.reach_json,o.first_touch_json,o.label_status
            FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
            WHERE s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
              AND o.label_status='CLOSED'
            ORDER BY s.signal_time_utc,s.id""").fetchall()
        enriched=[]
        for e in events:
            d=dict(e); d["features"]=feature_for_event(c,e); enriched.append(d)
        if len(enriched)<10:
            print("Binance activation math: kapanmis event yetersiz",len(enriched)); c.commit(); return
        discovery=[r for r in enriched if r["signal_time_utc"] < DISCOVERY_CUTOFF_UTC]
        future=[r for r in enriched if r["signal_time_utc"] >= DISCOVERY_CUTOFF_UTC]
        validation=future[:VALIDATION_N]
        splits={"DISCOVERY":discovery,"VALIDATION":validation}
        valrows=[]
        for split,rows in splits.items():
            controls=[r for r in rows if r["event_class"] in ("NEAR_MISS","RANDOM_CONTROL")]
            for combo,label in COMBOS:
                sel=[r for r in rows if r["features"].get(combo)]
                base=[r for r in controls if r["features"].get(combo)]
                if len(base)<8: base=controls
                for target in TARGETS:
                    sh=sum(reached(r,target) for r in sel); bhit=sum(reached(r,target) for r in base)
                    sr=sh/len(sel) if sel else None; br=bhit/len(base) if base else None
                    lift=(sr/br) if sr is not None and br not in (None,0) else None
                    p=proportion_p(sh,len(sel),bhit,len(base)) if len(sel)>=8 and len(base)>=8 else None
                    if split=="VALIDATION":
                        valrows.append({"combo":combo,"target":target,"p_value":p,"lift":lift})
        bh(valrows)
        qmap={(r["combo"],r["target"]):r.get("q_value") for r in valrows}
        c.execute("DELETE FROM binance_activation_math_results WHERE version=?",(VERSION,))
        now=datetime.now(timezone.utc).isoformat()
        for split,rows in splits.items():
            controls=[r for r in rows if r["event_class"] in ("NEAR_MISS","RANDOM_CONTROL")]
            for combo,label in COMBOS:
                sel=[r for r in rows if r["features"].get(combo)]
                base=[r for r in controls if r["features"].get(combo)]
                if len(base)<8: base=controls
                for target in TARGETS:
                    sh=sum(reached(r,target) for r in sel); bhit=sum(reached(r,target) for r in base)
                    sr=sh/len(sel) if sel else None; br=bhit/len(base) if base else None
                    lift=(sr/br) if sr is not None and br not in (None,0) else None
                    p=proportion_p(sh,len(sel),bhit,len(base)) if len(sel)>=8 and len(base)>=8 else None
                    q=qmap.get((combo,target)) if split=="VALIDATION" else None
                    c.execute("""INSERT OR REPLACE INTO binance_activation_math_results VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (VERSION,split,combo,label,target,len(sel),sh,sr,len(base),bhit,br,lift,p,q,now))
        c.commit()
        best=c.execute("""SELECT combo,combo_label,target_pct,selected_n,selected_rate,baseline_n,
            baseline_rate,lift,p_value,q_value FROM binance_activation_math_results
            WHERE version=? AND split='VALIDATION' AND selected_n>=8
            ORDER BY CASE WHEN q_value IS NULL THEN 1 ELSE 0 END,q_value ASC,lift DESC LIMIT 5""",(VERSION,)).fetchall()
        print("BINANCE ACTIVATION MATH",VERSION,"closed",len(enriched),"discovery",len(discovery),"frozen_oos",len(validation),"/",VALIDATION_N)
        for r in best:
            print(dict(r))
        print("Kural: mevcut v1 degismez; cutoff sonrasi ilk",VALIDATION_N,"kapanmis event frozen OOS validation olur.")


if __name__=="__main__":
    main()
