#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""History Miner V2 research layer.

Does NOT modify frozen v0.2/v0.3 labels or validation tables.

Adds:
- multi-horizon outcome matrix: 7/14/30/60 days
- thresholds: +20/+50/+100 percent
- descriptive base rates for every horizon/threshold
- matched-control effect estimates using ONLY controls selected by the frozen
  validation_control_matches table
- discovery/validation replication on daily pre-event features

This is research-only and never trades.
"""
from __future__ import annotations

import math
import os
import sqlite3
import statistics
from datetime import datetime, timezone

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="history-v2-outcomes-matched-v0.1-20260924"
SOURCE_VALIDATION_VERSION="history-validation-v0.2-20260924"
HORIZONS=(7,14,30,60)
THRESHOLDS=(20,50,100)
FEATURES=(
    "ret_7d","ret_30d","ret_90d","drawdown_30d",
    "vol_ratio_7_30","vol_ratio_30_90","realized_vol_30d",
    "range_compression_7_30","green_ratio_14d","dist_high_90d",
)
MIN_N=40
MIN_EFFECT=0.50
STRONG_EFFECT=1.00

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory=sqlite3.Row
    return c

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def med(xs):
    vals=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(vals) if vals else None

def mad(xs,center=None):
    vals=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    if not vals:return None
    center=med(vals) if center is None else center
    return med([abs(x-center) for x in vals])

def effect(rise,ctl):
    if len(rise)<8 or len(ctl)<8:return None
    rm,cm=med(rise),med(ctl)
    if rm is None or cm is None:return None
    scale=mad(ctl,cm)
    if not scale or scale<1e-9:
        scale=max(abs(cm),1.0)
    return (rm-cm)/scale

def grade(de,ve,nr,nc):
    if de is None or ve is None:return "INSUFFICIENT"
    if de*ve<=0:return "FAILED_DIRECTION"
    if nr<MIN_N or nc<MIN_N:return "DIRECTION_ONLY_LOW_N"
    if abs(de)<MIN_EFFECT or abs(ve)<MIN_EFFECT:return "DIRECTION_ONLY_WEAK"
    if abs(ve)>=STRONG_EFFECT:return "STRONG"
    return "CONSISTENT"

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS v2_outcomes(
      pair TEXT NOT NULL,
      anchor_ts INTEGER NOT NULL,
      horizon_days INTEGER NOT NULL,
      max_return_pct REAL NOT NULL,
      hit20 INTEGER NOT NULL,
      hit50 INTEGER NOT NULL,
      hit100 INTEGER NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,horizon_days,version)
    );
    CREATE TABLE IF NOT EXISTS v2_base_rates(
      horizon_days INTEGER NOT NULL,
      threshold_pct INTEGER NOT NULL,
      eligible_n INTEGER NOT NULL,
      hit_n INTEGER NOT NULL,
      base_rate REAL NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(horizon_days,threshold_pct,version)
    );
    CREATE TABLE IF NOT EXISTS v2_matched_results(
      horizon_days INTEGER NOT NULL,
      threshold_pct INTEGER NOT NULL,
      feature TEXT NOT NULL,
      discovery_rise_n INTEGER NOT NULL,
      discovery_control_n INTEGER NOT NULL,
      validation_rise_n INTEGER NOT NULL,
      validation_control_n INTEGER NOT NULL,
      discovery_effect REAL,
      validation_effect REAL,
      validation_grade TEXT NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(horizon_days,threshold_pct,feature,version)
    );
    CREATE TABLE IF NOT EXISTS v2_runs(
      run_id TEXT PRIMARY KEY,
      started_utc TEXT NOT NULL,
      finished_utc TEXT,
      outcome_rows INTEGER NOT NULL DEFAULT 0,
      result_rows INTEGER NOT NULL DEFAULT 0,
      notes TEXT,
      version TEXT NOT NULL
    );
    """)

def build_outcomes(c):
    c.execute("DELETE FROM v2_outcomes WHERE version=?",(VERSION,))
    c.execute("DELETE FROM v2_base_rates WHERE version=?",(VERSION,))
    totals={(h,t):[0,0] for h in HORIZONS for t in THRESHOLDS}
    pairs=c.execute("SELECT DISTINCT pair FROM daily_bars").fetchall()
    for pr in pairs:
        pair=pr[0]
        bars=c.execute("""SELECT ts,high,close FROM daily_bars
            WHERE pair=? ORDER BY ts""",(pair,)).fetchall()
        n=len(bars)
        for i in range(90,n-7):
            base=float(bars[i]["close"])
            if base<=0:continue
            for h in HORIZONS:
                if i+h>=n:continue
                future=bars[i+1:i+h+1]
                if not future:continue
                mx=max(float(r["high"]) for r in future)
                ret=100.0*(mx/base-1.0)
                hits={t:int(ret>=t) for t in THRESHOLDS}
                c.execute("""INSERT OR REPLACE INTO v2_outcomes
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (pair,int(bars[i]["ts"]),h,ret,hits[20],hits[50],hits[100],VERSION))
                for t in THRESHOLDS:
                    totals[(h,t)][0]+=1
                    totals[(h,t)][1]+=hits[t]
    for (h,t),(eligible,hit) in totals.items():
        if eligible:
            c.execute("""INSERT OR REPLACE INTO v2_base_rates
                VALUES(?,?,?,?,?,?)""",(h,t,eligible,hit,hit/eligible,VERSION))

def feature_value(c,pair,ts,feature):
    row=c.execute(f"""SELECT {feature} FROM event_features
        WHERE pair=? AND event_ts=? AND {feature} IS NOT NULL
        ORDER BY CASE label WHEN 'RISE' THEN 0 ELSE 1 END,rowid DESC LIMIT 1""",
        (pair,ts)).fetchone()
    return row[0] if row else None

def selected_rises(c,split,horizon,threshold):
    hitcol=f"hit{threshold}"
    return c.execute(f"""SELECT DISTINCT v.pair,v.event_ts
      FROM validation_cases v
      JOIN v2_outcomes o ON o.pair=v.pair AND o.anchor_ts=v.event_ts
      WHERE v.version=? AND v.split=? AND v.label='RISE'
        AND o.version=? AND o.horizon_days=? AND o.{hitcol}=1""",
      (SOURCE_VALIDATION_VERSION,split,VERSION,horizon)).fetchall()

def matched_values(c,split,horizon,threshold,feature):
    rises=selected_rises(c,split,horizon,threshold)
    rise_vals=[]
    control_vals=[]
    seen_ctl=set()
    for r in rises:
        rv=feature_value(c,r["pair"],r["event_ts"],feature)
        if rv is not None:
            rise_vals.append(rv)
        matches=c.execute("""SELECT control_pair,control_ts
          FROM validation_control_matches
          WHERE version=? AND split=? AND rise_pair=? AND rise_ts=?
          ORDER BY distance ASC LIMIT 3""",
          (SOURCE_VALIDATION_VERSION,split,r["pair"],r["event_ts"])).fetchall()
        for m in matches:
            key=(m["control_pair"],int(m["control_ts"]))
            if key in seen_ctl:continue
            cv=feature_value(c,key[0],key[1],feature)
            if cv is not None:
                seen_ctl.add(key)
                control_vals.append(cv)
    return rise_vals,control_vals

def build_matched_results(c):
    c.execute("DELETE FROM v2_matched_results WHERE version=?",(VERSION,))
    for h in HORIZONS:
        for t in THRESHOLDS:
            for feature in FEATURES:
                dr,dc=matched_values(c,"DISCOVERY",h,t,feature)
                vr,vc=matched_values(c,"VALIDATION",h,t,feature)
                de=effect(dr,dc); ve=effect(vr,vc)
                g=grade(de,ve,len(vr),len(vc))
                c.execute("""INSERT OR REPLACE INTO v2_matched_results
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (h,t,feature,len(dr),len(dc),len(vr),len(vc),de,ve,g,VERSION))

def main():
    if not os.path.exists(DB):
        print("History V2: DB yok");return
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with con() as c:
        init_db(c)
        c.execute("""INSERT OR REPLACE INTO v2_runs
          (run_id,started_utc,notes,version) VALUES(?,?,?,?)""",
          (run_id,utcnow(),
           "Separate observational V2. Frozen V0 labels/matching unchanged; effects use frozen matched controls only.",
           VERSION))
        build_outcomes(c)
        build_matched_results(c)
        outn=c.execute("SELECT COUNT(*) FROM v2_outcomes WHERE version=?",(VERSION,)).fetchone()[0]
        resn=c.execute("SELECT COUNT(*) FROM v2_matched_results WHERE version=?",(VERSION,)).fetchone()[0]
        c.execute("UPDATE v2_runs SET finished_utc=?,outcome_rows=?,result_rows=? WHERE run_id=?",
                  (utcnow(),outn,resn,run_id))
        rates=c.execute("""SELECT horizon_days,threshold_pct,base_rate
          FROM v2_base_rates WHERE version=? ORDER BY horizon_days,threshold_pct""",(VERSION,)).fetchall()
        strong=c.execute("""SELECT COUNT(*) FROM v2_matched_results
          WHERE version=? AND validation_grade IN ('STRONG','CONSISTENT')""",(VERSION,)).fetchone()[0]
        c.commit()
    print(f"History V2: outcomes={outn} matched_results={resn} strong_or_consistent={strong}")
    for r in rates:
        print(f"  base_rate {r['horizon_days']}d +{r['threshold_pct']}% = {100*r['base_rate']:.1f}%")

if __name__=="__main__":
    main()
