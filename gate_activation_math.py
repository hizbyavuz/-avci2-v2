#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate activation-math research.

Predeclared activation combinations are evaluated on CLOSED_72H events only.
This module never changes frozen V5 rules and never places trades.
It compares target-first rates and applies BH-FDR on the validation split.
"""

import json
import math
import os
import sqlite3
from datetime import datetime, timezone

OBS_DB=os.getenv("AVCI_DB","avci2.db")
VAL_DB=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
VERSION="gate-activation-math-v1.1-frozen-oos-20260926"
TARGETS=(5,10,15)
DISCOVERY_CUTOFF_TS=1790370000
VALIDATION_N=100
COMBOS=(
    ("G1_VOL_BUY","Hacim uyanisi + alis baskisi"),
    ("G2_VOL_BUYER","Hacim + unique buyer hizlanmasi"),
    ("G3_EARLY_IGNITION","Hacim + buyer hizlanmasi + fiyat henuz kacmamis"),
    ("G4_LIQUIDITY_IGNITION","Likidite genislemesi + hacim + alis baskisi"),
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
    ordered=sorted(enumerate(valid),key=lambda x:x[1]["p_value"])
    m=len(ordered); prev=1.0
    for rank in range(m,0,-1):
        idx,row=ordered[rank-1]
        q=min(prev,row["p_value"]*m/rank)
        row["q_value"]=q; prev=q
    for r in rows:
        r.setdefault("q_value",None)


def buyer_ratio(con, network, contract, ts):
    rows=con.execute("""SELECT scan_ts,buyers_5m FROM gate_buyer_observations
        WHERE network_id=? AND token_contract=? AND scan_ts<=? AND scan_ts>=?
          AND buyers_5m IS NOT NULL AND buyers_5m>0
        ORDER BY scan_ts DESC LIMIT 25""",(network,contract,ts,ts-6*3600)).fetchall()
    if not rows:
        return None
    current=rows[0][1]
    prior=[r[1] for r in rows[1:]]
    if current is None or len(prior)<3:
        return None
    prior=sorted(float(x) for x in prior)
    med=prior[len(prior)//2] if len(prior)%2 else (prior[len(prior)//2-1]+prior[len(prior)//2])/2
    return current/med if med>0 else None


def liquidity_ratio(con, network, contract, ts, current):
    row=con.execute("""SELECT liquidity FROM gate_early_observations
        WHERE network_id=? AND token_contract=? AND scan_ts<=? AND scan_ts>=?
          AND liquidity>0 ORDER BY scan_ts DESC LIMIT 1""",
        (network,contract,ts-20*60,ts-120*60)).fetchone()
    return (current/row[0]) if row and row[0] else None


def feature_snapshot(con,e):
    o=con.execute("""SELECT own_volume_ratio,buys_5m,sells_5m,change_24h,liquidity
        FROM gate_early_observations WHERE batch_id=? AND network_id=? AND token_contract=?
        LIMIT 1""",(e["batch_id"],e["network_id"],e["token_contract"])).fetchone()
    if not o:
        return {}
    vol=o[0]; buys=float(o[1] or 0); sells=float(o[2] or 0); day=float(o[3] or 0)
    br=buyer_ratio(con,e["network_id"],e["token_contract"],e["signal_ts"])
    lr=liquidity_ratio(con,e["network_id"],e["token_contract"],e["signal_ts"],float(o[4] or 0))
    buy_pressure=buys>=1.2*max(sells,1) and buys+sells>=5
    return {
        "vol":vol,"buyer_ratio":br,"liq_ratio":lr,"day":day,"buy_pressure":buy_pressure,
        "G1_VOL_BUY":vol is not None and vol>=2.5 and buy_pressure,
        "G2_VOL_BUYER":vol is not None and vol>=1.5 and br is not None and br>=2.0,
        "G3_EARLY_IGNITION":vol is not None and vol>=1.5 and br is not None and br>=2.0 and -2<=day<=20 and buy_pressure,
        "G4_LIQUIDITY_IGNITION":lr is not None and lr>=1.25 and vol is not None and vol>=1.5 and buy_pressure,
    }


def main():
    if not os.path.exists(OBS_DB) or not os.path.exists(VAL_DB):
        print("Gate activation math: DB eksik"); return
    with sqlite3.connect(OBS_DB,timeout=30) as obs, sqlite3.connect(VAL_DB,timeout=30) as val:
        obs.row_factory=val.row_factory=sqlite3.Row
        obs.execute("""CREATE TABLE IF NOT EXISTS gate_activation_math_results(
            version TEXT NOT NULL,split TEXT NOT NULL,combo TEXT NOT NULL,combo_label TEXT NOT NULL,
            target_pct INTEGER NOT NULL,selected_n INTEGER NOT NULL,selected_hits INTEGER NOT NULL,
            selected_rate REAL,baseline_n INTEGER NOT NULL,baseline_hits INTEGER NOT NULL,
            baseline_rate REAL,lift REAL,p_value REAL,q_value REAL,created_at_utc TEXT NOT NULL,
            PRIMARY KEY(version,split,combo,target_pct))""")
        events=val.execute("""SELECT id,batch_id,group_type,network_id,token_contract,signal_ts,
            result_5,result_10,result_15,status FROM validation_events
            WHERE status='CLOSED_72H' AND group_type IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
            ORDER BY signal_ts,id""").fetchall()
        enriched=[]
        for e in events:
            d=dict(e); d["features"]=feature_snapshot(obs,e); enriched.append(d)
        if len(enriched)<10:
            print("Gate activation math: kapanmis event yetersiz",len(enriched)); obs.commit(); return
        discovery=[r for r in enriched if int(r["signal_ts"]) < DISCOVERY_CUTOFF_TS]
        future=[r for r in enriched if int(r["signal_ts"]) >= DISCOVERY_CUTOFF_TS]
        validation=future[:VALIDATION_N]
        splits={"DISCOVERY":discovery,"VALIDATION":validation}
        now=datetime.now(timezone.utc).isoformat()
        validation_rows=[]
        for split,rows in splits.items():
            controls=[r for r in rows if r["group_type"] in ("NEAR_MISS","RANDOM_CONTROL")]
            for combo,label in COMBOS:
                selected=[r for r in rows if r["features"].get(combo)]
                base=[r for r in controls if r["features"].get(combo)]
                # If combo is rare in controls, use all contemporaneous controls as conservative baseline.
                if len(base)<8:
                    base=controls
                for target in TARGETS:
                    col=f"result_{target}"
                    s=[r for r in selected if r.get(col) in ("TARGET_FIRST","STOP_FIRST","TIMEOUT")]
                    b=[r for r in base if r.get(col) in ("TARGET_FIRST","STOP_FIRST","TIMEOUT")]
                    sh=sum(r[col]=="TARGET_FIRST" for r in s); bhit=sum(r[col]=="TARGET_FIRST" for r in b)
                    sr=sh/len(s) if s else None; brate=bhit/len(b) if b else None
                    lift=(sr/brate) if sr is not None and brate not in (None,0) else None
                    p=proportion_p(sh,len(s),bhit,len(b)) if len(s)>=8 and len(b)>=8 else None
                    out={"split":split,"combo":combo,"target":target,"selected_n":len(s),"selected_hits":sh,
                         "selected_rate":sr,"baseline_n":len(b),"baseline_hits":bhit,"baseline_rate":brate,
                         "lift":lift,"p_value":p}
                    if split=="VALIDATION": validation_rows.append(out)
        bh(validation_rows)
        qmap={(r["combo"],r["target"]):r.get("q_value") for r in validation_rows}
        obs.execute("DELETE FROM gate_activation_math_results WHERE version=?",(VERSION,))
        for split,rows in splits.items():
            controls=[r for r in rows if r["group_type"] in ("NEAR_MISS","RANDOM_CONTROL")]
            for combo,label in COMBOS:
                selected=[r for r in rows if r["features"].get(combo)]
                base=[r for r in controls if r["features"].get(combo)]
                if len(base)<8: base=controls
                for target in TARGETS:
                    col=f"result_{target}"
                    s=[r for r in selected if r.get(col) in ("TARGET_FIRST","STOP_FIRST","TIMEOUT")]
                    b=[r for r in base if r.get(col) in ("TARGET_FIRST","STOP_FIRST","TIMEOUT")]
                    sh=sum(r[col]=="TARGET_FIRST" for r in s); bhit=sum(r[col]=="TARGET_FIRST" for r in b)
                    sr=sh/len(s) if s else None; brate=bhit/len(b) if b else None
                    lift=(sr/brate) if sr is not None and brate not in (None,0) else None
                    p=proportion_p(sh,len(s),bhit,len(b)) if len(s)>=8 and len(b)>=8 else None
                    q=qmap.get((combo,target)) if split=="VALIDATION" else None
                    obs.execute("""INSERT OR REPLACE INTO gate_activation_math_results VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (VERSION,split,combo,label,target,len(s),sh,sr,len(b),bhit,brate,lift,p,q,now))
        obs.commit()
        best=obs.execute("""SELECT combo,combo_label,target_pct,selected_n,selected_rate,baseline_n,
            baseline_rate,lift,p_value,q_value FROM gate_activation_math_results
            WHERE version=? AND split='VALIDATION' AND selected_n>=8
            ORDER BY CASE WHEN q_value IS NULL THEN 1 ELSE 0 END,q_value ASC,lift DESC LIMIT 5""",(VERSION,)).fetchall()
        print("GATE ACTIVATION MATH",VERSION,"closed",len(enriched),"discovery",len(discovery),"frozen_oos",len(validation),"/",VALIDATION_N)
        for r in best:
            print(dict(r))
        print("Kural: mevcut V5 degismez; cutoff sonrasi ilk",VALIDATION_N,"kapanmis event frozen OOS validation olur.")


if __name__=="__main__":
    main()
