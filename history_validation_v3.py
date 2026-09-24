#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""History Miner V3 stress-test layer.

V3 does NOT modify V0/V2 labels, thresholds, matching, or results.
It asks a harder question: do V2 daily-feature effects survive when we reduce
same-coin overlap and require stability across multiple out-of-sample time folds?

This is research-only. It never trades and never feeds back into frozen V2.
"""
from __future__ import annotations

import math
import os
import sqlite3
import statistics
from datetime import datetime, timezone

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="history-v3-stress-v0.1-20260924"
SOURCE_VALIDATION_VERSION="history-validation-v0.2-20260924"
SOURCE_V2_VERSION="history-v2-outcomes-matched-v0.1-20260924"

HORIZONS=(7,14,30,60)
THRESHOLDS=(20,50,100)
FEATURES=(
    "ret_7d","ret_30d","ret_90d","drawdown_30d",
    "vol_ratio_7_30","vol_ratio_30_90","realized_vol_30d",
    "range_compression_7_30","green_ratio_14d","dist_high_90d",
)

# Fixed calendar folds. These are not tuned from V2 results.
FOLDS=(
    ("F1",None,"2025-03-01"),
    ("F2","2025-03-01","2025-09-01"),
    ("F3","2025-09-01","2026-03-01"),
    ("F4","2026-03-01",None),
)
MIN_FOLD_SIDE=20
MIN_EFFECT=0.50

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory=sqlite3.Row
    return c

def ts(date_s):
    if date_s is None:
        return None
    return int(datetime.fromisoformat(date_s+"T00:00:00+00:00").timestamp())

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

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS v3_fold_results(
      fold TEXT NOT NULL,
      horizon_days INTEGER NOT NULL,
      threshold_pct INTEGER NOT NULL,
      feature TEXT NOT NULL,
      rise_n INTEGER NOT NULL,
      control_n INTEGER NOT NULL,
      effect REAL,
      effect_pass INTEGER NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(fold,horizon_days,threshold_pct,feature,version)
    );
    CREATE TABLE IF NOT EXISTS v3_summary(
      horizon_days INTEGER NOT NULL,
      threshold_pct INTEGER NOT NULL,
      feature TEXT NOT NULL,
      raw_rise_n INTEGER NOT NULL,
      nonoverlap_rise_n INTEGER NOT NULL,
      overlap_reduction_pct REAL NOT NULL,
      folds_tested INTEGER NOT NULL,
      same_direction_folds INTEGER NOT NULL,
      effect_pass_folds INTEGER NOT NULL,
      median_fold_effect REAL,
      overall_effect REAL,
      version TEXT NOT NULL,
      PRIMARY KEY(horizon_days,threshold_pct,feature,version)
    );
    CREATE TABLE IF NOT EXISTS v3_runs(
      run_id TEXT PRIMARY KEY,
      started_utc TEXT NOT NULL,
      finished_utc TEXT,
      summary_rows INTEGER NOT NULL DEFAULT 0,
      notes TEXT,
      version TEXT NOT NULL
    );
    """)

def build_feature_cache(c):
    cols=",".join(FEATURES)
    rows=c.execute(f"""SELECT rowid,pair,event_ts,label,{cols}
      FROM event_features
      ORDER BY pair,event_ts,
               CASE label WHEN 'RISE' THEN 0 ELSE 1 END,
               rowid DESC""").fetchall()
    cache={}
    for r in rows:
        key=(r["pair"],int(r["event_ts"]))
        entry=cache.setdefault(key,{})
        for feature in FEATURES:
            if feature not in entry and r[feature] is not None:
                entry[feature]=float(r[feature])
    return cache

def build_match_cache(c):
    rows=c.execute("""SELECT split,rise_pair,rise_ts,control_pair,control_ts,distance
      FROM validation_control_matches
      WHERE version=?
      ORDER BY split,rise_pair,rise_ts,distance ASC""",
      (SOURCE_VALIDATION_VERSION,)).fetchall()
    cache={}
    for r in rows:
        key=(r["rise_pair"],int(r["rise_ts"]))
        arr=cache.setdefault(key,[])
        if len(arr)<3:
            arr.append((r["control_pair"],int(r["control_ts"])))
    return cache

def selected_raw_rises(c,horizon,threshold):
    hitcol=f"hit{threshold}"
    rows=c.execute(f"""SELECT DISTINCT v.pair,v.event_ts
      FROM validation_cases v
      JOIN v2_outcomes o ON o.pair=v.pair AND o.anchor_ts=v.event_ts
      WHERE v.version=? AND v.label='RISE'
        AND o.version=? AND o.horizon_days=? AND o.{hitcol}=1
      ORDER BY v.pair,v.event_ts""",
      (SOURCE_VALIDATION_VERSION,SOURCE_V2_VERSION,horizon)).fetchall()
    return [(r["pair"],int(r["event_ts"])) for r in rows]

def nonoverlap_rises(rows,horizon):
    """Greedy same-coin de-overlap. One event per max(30d,horizon) window."""
    cooldown=max(30,horizon)*86400
    out=[]
    last_by_pair={}
    for pair,event_ts in rows:
        last=last_by_pair.get(pair)
        if last is None or event_ts-last>=cooldown:
            out.append((pair,event_ts))
            last_by_pair[pair]=event_ts
    return out

def in_fold(event_ts,start_s,end_s):
    lo=ts(start_s); hi=ts(end_s)
    if lo is not None and event_ts<lo:return False
    if hi is not None and event_ts>=hi:return False
    return True

def values_for(cases,feature,feature_cache,match_cache):
    rises=[]
    controls=[]
    seen_ctl=set()
    for key in cases:
        rv=feature_cache.get(key,{}).get(feature)
        if rv is not None:
            rises.append(rv)
        for ctl in match_cache.get(key,()):
            if ctl in seen_ctl:continue
            cv=feature_cache.get(ctl,{}).get(feature)
            if cv is not None:
                seen_ctl.add(ctl)
                controls.append(cv)
    return rises,controls

def main():
    if not os.path.exists(DB):
        print("History V3: DB yok");return
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with con() as c:
        init_db(c)
        c.execute("DELETE FROM v3_fold_results WHERE version=?",(VERSION,))
        c.execute("DELETE FROM v3_summary WHERE version=?",(VERSION,))
        c.execute("""INSERT OR REPLACE INTO v3_runs
          (run_id,started_utc,notes,version) VALUES(?,?,?,?)""",
          (run_id,datetime.now(timezone.utc).isoformat(),
           "Separate stress test only. V2 frozen. Same-coin overlap reduced with max(30d,horizon) cooldown; four fixed calendar folds; regime findings remain descriptive hypotheses; survivorship remains unresolved until delist backfill.",
           VERSION))

        feature_cache=build_feature_cache(c)
        match_cache=build_match_cache(c)
        summaries=[]

        for h in HORIZONS:
            for t in THRESHOLDS:
                raw=selected_raw_rises(c,h,t)
                indep=nonoverlap_rises(raw,h)
                reduction=(1.0-len(indep)/len(raw))*100.0 if raw else 0.0

                for feature in FEATURES:
                    fold_effects=[]
                    tested=0
                    for fold,start_s,end_s in FOLDS:
                        fold_cases=[x for x in indep if in_fold(x[1],start_s,end_s)]
                        rv,cv=values_for(fold_cases,feature,feature_cache,match_cache)
                        eff=effect(rv,cv)
                        pass_effect=int(
                            eff is not None and len(rv)>=MIN_FOLD_SIDE and
                            len(cv)>=MIN_FOLD_SIDE and abs(eff)>=MIN_EFFECT
                        )
                        if eff is not None:
                            tested+=1
                            fold_effects.append(eff)
                        c.execute("""INSERT OR REPLACE INTO v3_fold_results
                          VALUES(?,?,?,?,?,?,?,?,?)""",
                          (fold,h,t,feature,len(rv),len(cv),eff,pass_effect,VERSION))

                    all_r,all_c=values_for(indep,feature,feature_cache,match_cache)
                    overall=effect(all_r,all_c)
                    nonzero=[e for e in fold_effects if e is not None and e!=0]
                    if nonzero:
                        overall_sign=1 if (overall or 0)>0 else (-1 if (overall or 0)<0 else 0)
                        same=sum(1 for e in nonzero if (1 if e>0 else -1)==overall_sign) if overall_sign else 0
                        passed=sum(1 for e in nonzero if abs(e)>=MIN_EFFECT)
                        med_eff=statistics.median(nonzero)
                    else:
                        same=passed=0
                        med_eff=None
                    summaries.append(
                        (h,t,feature,len(raw),len(indep),reduction,tested,same,passed,
                         med_eff,overall,VERSION)
                    )

        c.executemany("""INSERT OR REPLACE INTO v3_summary
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",summaries)
        c.execute("""UPDATE v3_runs SET finished_utc=?,summary_rows=? WHERE run_id=?""",
                  (datetime.now(timezone.utc).isoformat(),len(summaries),run_id))
        c.commit()

        stable=c.execute("""SELECT COUNT(*) FROM v3_summary
          WHERE version=? AND folds_tested>=3
            AND same_direction_folds>=3 AND effect_pass_folds>=2""",(VERSION,)).fetchone()[0]
        avg_reduction=c.execute("""SELECT AVG(overlap_reduction_pct) FROM v3_summary
          WHERE version=?""",(VERSION,)).fetchone()[0] or 0.0

    print(f"History V3: summary_rows={len(summaries)} stable_rows={stable} avg_overlap_reduction={avg_reduction:.1f}%")
    print("History V3: regime-specific findings are hypotheses only; no regime ranking is treated as independently validated.")
    print("History V3: survivorship remains unresolved until delisted/suspended history is backfilled.")

if __name__=="__main__":
    main()
