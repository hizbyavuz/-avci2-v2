#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bias-aware validation layer for History Miner.

This module does NOT change frozen live Avci rules and does not relabel the raw
archive. It adds a deterministic research layer on top of existing event data:

- time-based DISCOVERY / VALIDATION split
- BTC UP / SIDEWAYS / DOWN regime tag using only information available at case time
- nearest matched controls using pre-event observables
- explicit survivorship and manipulation-data coverage flags
- separate EARLY (-72..-12h) vs ONSET_RISK (-6..-1h) hourly evidence
- discovery-direction replication checks on the untouched validation period

It is intentionally stdlib-only and safe to rerun.
"""
from __future__ import annotations

import math
import os
import sqlite3
import statistics
from datetime import datetime, timezone

DB = os.getenv("HISTORY_DB", "history_miner.db")
VERSION = "history-validation-v0.2-20260924"
MATCH_SPEC_VERSION = "match-spec-v1-frozen-20260924"
MIN_EFFECT = 0.50
STRONG_EFFECT = 1.00
MIN_VALIDATION_N = 40
DEFAULT_CUTOFF = os.getenv("HISTORY_VALIDATION_CUTOFF", "2025-09-01")
MATCH_WINDOW_DAYS = int(os.getenv("HISTORY_MATCH_WINDOW_DAYS", "45"))

DAILY_FEATURES = (
    "ret_7d","ret_30d","ret_90d","drawdown_30d",
    "vol_ratio_7_30","vol_ratio_30_90","realized_vol_30d",
    "range_compression_7_30","green_ratio_14d","dist_high_90d",
)
HOURLY_FEATURES = (
    "ret_1h","ret_3h","ret_6h","ret_12h","ret_24h",
    "vol_ratio_1_24","vol_ratio_3_24","range_ratio_1_24",
    "close_location","dist_low_24h","dist_high_24h",
)
OFFSETS = (-72,-48,-24,-12,-6,-3,-1)

def con():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory = sqlite3.Row
    return c

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def parse_cutoff(s):
    return int(datetime.fromisoformat(s + "T00:00:00+00:00").timestamp())

def med(xs):
    vals = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(vals) if vals else None

def mad(xs, center=None):
    vals = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    if not vals:
        return None
    center = med(vals) if center is None else center
    return med([abs(x-center) for x in vals])

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS validation_cases(
      pair TEXT NOT NULL,
      event_ts INTEGER NOT NULL,
      label TEXT NOT NULL,
      split TEXT NOT NULL,
      btc_regime TEXT NOT NULL,
      evidence_phase TEXT NOT NULL,
      survivorship_scope TEXT NOT NULL,
      manipulation_status TEXT NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(pair,event_ts,label,version)
    );
    CREATE TABLE IF NOT EXISTS validation_control_matches(
      rise_pair TEXT NOT NULL,
      rise_ts INTEGER NOT NULL,
      control_pair TEXT NOT NULL,
      control_ts INTEGER NOT NULL,
      split TEXT NOT NULL,
      btc_regime TEXT NOT NULL,
      distance REAL NOT NULL,
      quality TEXT NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(rise_pair,rise_ts,control_pair,control_ts,version)
    );
    CREATE TABLE IF NOT EXISTS validation_results(
      feature_scope TEXT NOT NULL,
      feature TEXT NOT NULL,
      offset_h INTEGER NOT NULL,
      regime TEXT NOT NULL,
      discovery_rise_n INTEGER NOT NULL,
      discovery_control_n INTEGER NOT NULL,
      validation_rise_n INTEGER NOT NULL,
      validation_control_n INTEGER NOT NULL,
      discovery_effect REAL,
      validation_effect REAL,
      direction_replicated INTEGER,
      validation_grade TEXT NOT NULL DEFAULT 'INSUFFICIENT',
      phase TEXT NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(feature_scope,feature,offset_h,regime,version)
    );
    CREATE TABLE IF NOT EXISTS validation_runs(
      run_id TEXT PRIMARY KEY,
      started_utc TEXT NOT NULL,
      finished_utc TEXT,
      cutoff_utc TEXT NOT NULL,
      cases INTEGER NOT NULL DEFAULT 0,
      matches INTEGER NOT NULL DEFAULT 0,
      replicated INTEGER NOT NULL DEFAULT 0,
      tested INTEGER NOT NULL DEFAULT 0,
      notes TEXT,
      version TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS validation_regime_counts(
      split TEXT NOT NULL,
      label TEXT NOT NULL,
      btc_regime TEXT NOT NULL,
      n INTEGER NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(split,label,btc_regime,version)
    );
    CREATE TABLE IF NOT EXISTS validation_spec(
      spec_key TEXT PRIMARY KEY,
      spec_value TEXT NOT NULL,
      frozen_utc TEXT NOT NULL,
      version TEXT NOT NULL
    );
    """)

def btc_regime(c, ts):
    rows = c.execute("""SELECT ts,close FROM daily_bars
        WHERE pair='BTC_USDT' AND ts<=? ORDER BY ts DESC LIMIT 31""",(int(ts),)).fetchall()
    if len(rows) < 31:
        return "UNKNOWN"
    now = float(rows[0]["close"])
    old = float(rows[-1]["close"])
    if old <= 0:
        return "UNKNOWN"
    r = 100.0*(now/old-1.0)
    if r >= 10.0:
        return "UP"
    if r <= -10.0:
        return "DOWN"
    return "SIDEWAYS"

def freeze_spec(c):
    spec={
      "match_spec_version": MATCH_SPEC_VERSION,
      "match_features": "ret_30d,drawdown_30d,realized_vol_30d,log1p(pre_volume30)",
      "match_scales": "20,20,5,2",
      "match_time_window_days": str(MATCH_WINDOW_DAYS),
      "match_same_split": "required",
      "match_same_btc_regime": "required_when_known",
      "max_controls_per_rise": "3",
      "good_distance_max": "0.75",
      "ok_distance_max": "1.50",
      "min_effect": str(MIN_EFFECT),
      "strong_effect": str(STRONG_EFFECT),
      "min_validation_n_per_side": str(MIN_VALIDATION_N),
      "discovery_validation_cutoff": DEFAULT_CUTOFF,
      "validation_tuning_rule": "validation set may not be used to tune this spec; changes require a new version"
    }
    now=utcnow()
    for k,v in spec.items():
        old=c.execute("SELECT spec_value,version FROM validation_spec WHERE spec_key=?",(k,)).fetchone()
        if old and (old["spec_value"]!=v or old["version"]!=VERSION):
            # Do not silently rewrite a frozen spec for the same key.
            continue
        c.execute("""INSERT OR IGNORE INTO validation_spec(spec_key,spec_value,frozen_utc,version)
          VALUES(?,?,?,?)""",(k,v,now,VERSION))

def populate_cases(c, cutoff_ts):
    c.execute("DELETE FROM validation_cases WHERE version=?", (VERSION,))
    rows = c.execute("""SELECT pair,event_ts,label FROM event_features
        WHERE label IN ('RISE','CONTROL')""").fetchall()
    for r in rows:
        ts = int(r["event_ts"])
        split = "DISCOVERY" if ts < cutoff_ts else "VALIDATION"
        regime = btc_regime(c, ts)
        c.execute("""INSERT OR REPLACE INTO validation_cases
          (pair,event_ts,label,split,btc_regime,evidence_phase,
           survivorship_scope,manipulation_status,version)
          VALUES(?,?,?,?,?,?,?,?,?)""",
          (r["pair"],ts,r["label"],split,regime,"DAILY_PRE_EVENT",
           "ACTIVE_PAIR_ARCHIVE_ONLY","UNKNOWN_NO_HOLDER_WASH_HISTORY",VERSION))

def feature_row(c, pair, ts, label):
    return c.execute("""SELECT ret_30d,drawdown_30d,realized_vol_30d,pre_volume30
        FROM event_features WHERE pair=? AND event_ts=? AND label=?
        ORDER BY rowid DESC LIMIT 1""",(pair,ts,label)).fetchone()

def logv(x):
    if x is None or float(x) <= 0:
        return None
    return math.log1p(float(x))

def dist(a,b):
    # Only pre-event observables. Missing dimensions are ignored.
    av = [a["ret_30d"],a["drawdown_30d"],a["realized_vol_30d"],logv(a["pre_volume30"])]
    bv = [b["ret_30d"],b["drawdown_30d"],b["realized_vol_30d"],logv(b["pre_volume30"])]
    scales = (20.0,20.0,5.0,2.0)
    ds=[]
    for x,y,s in zip(av,bv,scales):
        if x is not None and y is not None:
            ds.append(((float(x)-float(y))/s)**2)
    return math.sqrt(sum(ds)/len(ds)) if ds else 999.0

def build_matches(c):
    c.execute("DELETE FROM validation_control_matches WHERE version=?", (VERSION,))
    rises = c.execute("""SELECT * FROM validation_cases
        WHERE version=? AND label='RISE'""",(VERSION,)).fetchall()
    max_dt = MATCH_WINDOW_DAYS*86400
    for r in rises:
        rf = feature_row(c,r["pair"],r["event_ts"],"RISE")
        if not rf:
            continue
        controls = c.execute("""SELECT v.pair,v.event_ts,v.split,v.btc_regime
          FROM validation_cases v
          WHERE v.version=? AND v.label='CONTROL' AND v.split=?
            AND (v.btc_regime=? OR ?='UNKNOWN' OR v.btc_regime='UNKNOWN')
            AND ABS(v.event_ts-?)<=?
          ORDER BY ABS(v.event_ts-?) LIMIT 60""",
          (VERSION,r["split"],r["btc_regime"],r["btc_regime"],r["event_ts"],max_dt,r["event_ts"])).fetchall()
        ranked=[]
        for ctl in controls:
            cf=feature_row(c,ctl["pair"],ctl["event_ts"],"CONTROL")
            if not cf: continue
            d=dist(rf,cf)
            ranked.append((d,ctl))
        ranked.sort(key=lambda x:x[0])
        for d,ctl in ranked[:3]:
            quality = "GOOD" if d<=0.75 else ("OK" if d<=1.5 else "WEAK")
            c.execute("""INSERT OR REPLACE INTO validation_control_matches
              VALUES(?,?,?,?,?,?,?,?,?)""",
              (r["pair"],r["event_ts"],ctl["pair"],ctl["event_ts"],
               r["split"],r["btc_regime"],d,quality,VERSION))

def values_daily(c, feature, split, label, regime):
    q=f"""SELECT e.{feature}
      FROM event_features e JOIN validation_cases v
        ON v.pair=e.pair AND v.event_ts=e.event_ts AND v.label=e.label
      WHERE v.version=? AND v.split=? AND e.label=? AND e.{feature} IS NOT NULL"""
    args=[VERSION,split,label]
    if regime!="ALL":
        q+=" AND v.btc_regime=?"; args.append(regime)
    return [r[0] for r in c.execute(q,args)]

def values_hourly(c, feature, offset, split, label, regime):
    q=f"""SELECT h.{feature}
      FROM hourly_path_snapshots h JOIN validation_cases v
        ON v.pair=h.pair AND v.event_ts=h.anchor_ts AND v.label=h.label
      WHERE v.version=? AND v.split=? AND h.label=? AND h.offset_h=?
        AND h.{feature} IS NOT NULL"""
    args=[VERSION,split,label,offset]
    if regime!="ALL":
        q+=" AND v.btc_regime=?"; args.append(regime)
    return [r[0] for r in c.execute(q,args)]

def effect(rise, ctl):
    if len(rise)<8 or len(ctl)<8:
        return None
    rm,cm=med(rise),med(ctl)
    scale=mad(ctl,cm)
    if rm is None or cm is None:
        return None
    if not scale or scale<1e-9:
        scale=max(abs(cm),1.0)
    return (rm-cm)/scale

def grade_result(de,ve,vr,vc):
    if de is None or ve is None:
        return "INSUFFICIENT"
    same = de*ve>0
    if not same:
        return "FAILED_DIRECTION"
    if len(vr)<MIN_VALIDATION_N or len(vc)<MIN_VALIDATION_N:
        return "DIRECTION_ONLY_LOW_N"
    if abs(de)<MIN_EFFECT or abs(ve)<MIN_EFFECT:
        return "DIRECTION_ONLY_WEAK"
    if abs(ve)>=STRONG_EFFECT and abs(de)>=MIN_EFFECT:
        return "STRONG"
    return "CONSISTENT"

def store_result(c, scope, feature, off, regime, phase, dr,dc,vr,vc):
    de=effect(dr,dc); ve=effect(vr,vc)
    repl = int(de is not None and ve is not None and de*ve>0)
    grade=grade_result(de,ve,vr,vc)
    c.execute("""INSERT OR REPLACE INTO validation_results
      (feature_scope,feature,offset_h,regime,discovery_rise_n,discovery_control_n,
       validation_rise_n,validation_control_n,discovery_effect,validation_effect,
       direction_replicated,validation_grade,phase,version)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (scope,feature,off,regime,len(dr),len(dc),len(vr),len(vc),
       de,ve,repl,grade,phase,VERSION))

def compute_results(c):
    c.execute("DELETE FROM validation_results WHERE version=?", (VERSION,))
    regimes=("ALL","UP","SIDEWAYS","DOWN")
    for regime in regimes:
        for feature in DAILY_FEATURES:
            dr=values_daily(c,feature,"DISCOVERY","RISE",regime)
            dc=values_daily(c,feature,"DISCOVERY","CONTROL",regime)
            vr=values_daily(c,feature,"VALIDATION","RISE",regime)
            vc=values_daily(c,feature,"VALIDATION","CONTROL",regime)
            store_result(c,"DAILY",feature,0,regime,"PRE_EVENT",dr,dc,vr,vc)
        has_hourly=c.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='hourly_path_snapshots'""").fetchone()
        if has_hourly:
            for off in OFFSETS:
                phase="EARLY" if off<=-12 else "ONSET_RISK"
                for feature in HOURLY_FEATURES:
                    dr=values_hourly(c,feature,off,"DISCOVERY","RISE",regime)
                    dc=values_hourly(c,feature,off,"DISCOVERY","CONTROL",regime)
                    vr=values_hourly(c,feature,off,"VALIDATION","RISE",regime)
                    vc=values_hourly(c,feature,off,"VALIDATION","CONTROL",regime)
                    store_result(c,"HOURLY",feature,off,regime,phase,dr,dc,vr,vc)

def save_regime_counts(c):
    c.execute("DELETE FROM validation_regime_counts WHERE version=?",(VERSION,))
    rows=c.execute("""SELECT split,label,btc_regime,COUNT(*) n
      FROM validation_cases WHERE version=?
      GROUP BY split,label,btc_regime""",(VERSION,)).fetchall()
    for r in rows:
        c.execute("""INSERT OR REPLACE INTO validation_regime_counts
          VALUES(?,?,?,?,?)""",(r["split"],r["label"],r["btc_regime"],r["n"],VERSION))

def main():
    if not os.path.exists(DB):
        print("History Validation: DB yok"); return
    cutoff_ts=parse_cutoff(DEFAULT_CUTOFF)
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with con() as c:
        init_db(c)
        c.execute("""INSERT OR REPLACE INTO validation_runs
          (run_id,started_utc,cutoff_utc,notes,version) VALUES(?,?,?,?,?)""",
          (run_id,utcnow(),DEFAULT_CUTOFF,
           "Raw archive preserved; active-pair survivorship coverage is still incomplete.",VERSION))
        freeze_spec(c)
        populate_cases(c,cutoff_ts)
        save_regime_counts(c)
        build_matches(c)
        compute_results(c)
        cases=c.execute("SELECT COUNT(*) FROM validation_cases WHERE version=?",(VERSION,)).fetchone()[0]
        matches=c.execute("SELECT COUNT(*) FROM validation_control_matches WHERE version=?",(VERSION,)).fetchone()[0]
        tested=c.execute("""SELECT COUNT(*) FROM validation_results
          WHERE version=? AND discovery_effect IS NOT NULL AND validation_effect IS NOT NULL""",(VERSION,)).fetchone()[0]
        replicated=c.execute("""SELECT COUNT(*) FROM validation_results
          WHERE version=? AND validation_grade IN ('CONSISTENT','STRONG')""",(VERSION,)).fetchone()[0]
        c.execute("""UPDATE validation_runs SET finished_utc=?,cases=?,matches=?,
          replicated=?,tested=? WHERE run_id=?""",
          (utcnow(),cases,matches,replicated,tested,run_id))
        c.commit()
    print(f"History Validation: cutoff={DEFAULT_CUTOFF} cases={cases} matches={matches} validated={replicated}/{tested}")
    print("Coverage: survivorship=ACTIVE_PAIR_ARCHIVE_ONLY manipulation_history=UNKNOWN")

if __name__=="__main__":
    main()
