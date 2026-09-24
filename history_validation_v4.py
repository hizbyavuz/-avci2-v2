#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""History Miner V4 Pattern-First validation.

Question:
  If a coin has the observed pre-move pattern BEFORE we look at the future,
  how often does it actually reach +20/+50/+100?

Safety / methodology:
- Separate layer; never modifies frozen V0/V2/V3/Cross-Venue tables.
- Pattern thresholds are learned ONLY from pre-cutoff DISCOVERY anchors.
- Thresholds use feature quantiles, never future outcomes.
- Thresholds are frozen in v4_pattern_spec and reused on later reruns.
- Signals are first-entry pattern events with a horizon-aware cooldown so
  consecutive pattern days do not become fake independent opportunities.
- Reports precision, false-positive rate, recall, unique coins and lift versus
  the raw same-split base rate.
- Research-only. Never sends orders.
"""
from __future__ import annotations

import math
import os
import sqlite3
import statistics
from datetime import datetime, timezone

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="history-v4-pattern-first-v0.1-20260925"
OUTCOME_VERSION="history-v2-outcomes-matched-v0.1-20260924"
CUTOFF=os.getenv("HISTORY_VALIDATION_CUTOFF","2025-09-01")
HORIZONS=(7,14,30,60)
THRESHOLDS=(20,50,100)

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory=sqlite3.Row
    return c

def cutoff_ts():
    return int(datetime.fromisoformat(CUTOFF+"T00:00:00+00:00").timestamp())

def pct(a,b):
    return 100.0*(b/a-1.0) if a and b and a>0 else None

def quantile(vals,q):
    xs=sorted(float(x) for x in vals if x is not None and math.isfinite(float(x)))
    if not xs:return None
    if len(xs)==1:return xs[0]
    pos=(len(xs)-1)*q
    lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi:return xs[lo]
    w=pos-lo
    return xs[lo]*(1-w)+xs[hi]*w

def realized_vol_30(closes):
    if len(closes)<31:return None
    rs=[]
    for a,b in zip(closes[-31:-1],closes[-30:]):
        if a>0 and b>0:
            rs.append(math.log(b/a))
    return statistics.pstdev(rs)*100 if len(rs)>=20 else None

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS v4_pattern_spec(
      pattern_name TEXT NOT NULL,
      feature TEXT NOT NULL,
      operator TEXT NOT NULL,
      threshold REAL NOT NULL,
      source_split TEXT NOT NULL,
      frozen_utc TEXT NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(pattern_name,feature,version)
    );
    CREATE TABLE IF NOT EXISTS v4_pattern_results(
      split TEXT NOT NULL,
      pattern_name TEXT NOT NULL,
      horizon_days INTEGER NOT NULL,
      threshold_pct INTEGER NOT NULL,
      signal_n INTEGER NOT NULL,
      hit_n INTEGER NOT NULL,
      miss_n INTEGER NOT NULL,
      unique_coin_n INTEGER NOT NULL,
      precision REAL,
      false_positive_rate REAL,
      recall REAL,
      raw_base_rate REAL,
      lift_vs_raw_base REAL,
      version TEXT NOT NULL,
      PRIMARY KEY(split,pattern_name,horizon_days,threshold_pct,version)
    );
    CREATE TABLE IF NOT EXISTS v4_pattern_counts(
      split TEXT NOT NULL,
      pattern_name TEXT NOT NULL,
      raw_matching_days INTEGER NOT NULL,
      first_entry_signals INTEGER NOT NULL,
      unique_coin_n INTEGER NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(split,pattern_name,version)
    );
    CREATE TABLE IF NOT EXISTS v4_runs(
      run_id TEXT PRIMARY KEY,
      started_utc TEXT NOT NULL,
      finished_utc TEXT,
      result_rows INTEGER NOT NULL DEFAULT 0,
      notes TEXT,
      version TEXT NOT NULL
    );
    """)

def build_anchor_features(c):
    """Return {(pair,ts): feature dict} for all anchors with >=90d history."""
    out={}
    pairs=[r[0] for r in c.execute("SELECT DISTINCT pair FROM daily_bars")]
    for pair in pairs:
        rows=c.execute("""SELECT ts,high,close FROM daily_bars
          WHERE pair=? ORDER BY ts""",(pair,)).fetchall()
        highs=[]; closes=[]
        for i,r in enumerate(rows):
            highs.append(float(r["high"]))
            closes.append(float(r["close"]))
            if i<90:continue
            cur=closes[i]
            hi90=max(highs[i-89:i+1])
            hi30=max(highs[i-29:i+1])
            dist_high=pct(hi90,cur)
            drawdown=pct(hi30,cur)
            rv=realized_vol_30(closes[:i+1])
            if None in (dist_high,drawdown,rv):
                continue
            out[(pair,int(r["ts"]))]={
              "dist_high_90d":dist_high,
              "drawdown_30d":drawdown,
              "realized_vol_30d":rv,
            }
    return out

def freeze_spec(c,features):
    """Discovery-only, outcome-blind quantile thresholds."""
    existing=c.execute("""SELECT pattern_name,feature,operator,threshold
      FROM v4_pattern_spec WHERE version=?""",(VERSION,)).fetchall()
    if existing:
        spec={}
        for r in existing:
            spec.setdefault(r["pattern_name"],[]).append(
              (r["feature"],r["operator"],float(r["threshold"])))
        return spec

    cut=cutoff_ts()
    disc=[v for (pair,ts_),v in features.items() if ts_<cut]
    if not disc:
        raise RuntimeError("no_discovery_features")

    dh_q25=quantile([x["dist_high_90d"] for x in disc],0.25)
    rv_q75=quantile([x["realized_vol_30d"] for x in disc],0.75)
    dd_q25=quantile([x["drawdown_30d"] for x in disc],0.25)
    if None in (dh_q25,rv_q75,dd_q25):
        raise RuntimeError("cannot_freeze_v4_thresholds")

    # Nested patterns. No future return was used to choose these thresholds.
    spec={
      "P1_FAR_FROM_HIGH":[("dist_high_90d","<=",dh_q25)],
      "P2_FAR_PLUS_VOL":[
          ("dist_high_90d","<=",dh_q25),
          ("realized_vol_30d",">=",rv_q75),
      ],
      "P3_FAR_VOL_DRAWDOWN":[
          ("dist_high_90d","<=",dh_q25),
          ("realized_vol_30d",">=",rv_q75),
          ("drawdown_30d","<=",dd_q25),
      ],
    }
    now=datetime.now(timezone.utc).isoformat()
    for name,conds in spec.items():
        for feature,op,thr in conds:
            c.execute("""INSERT INTO v4_pattern_spec
              VALUES(?,?,?,?,?,?,?)""",
              (name,feature,op,float(thr),"DISCOVERY_ONLY",now,VERSION))
    return spec

def match_pattern(v,conds):
    for feature,op,thr in conds:
        x=v.get(feature)
        if x is None:return False
        if op=="<=" and not (x<=thr):return False
        if op==">=" and not (x>=thr):return False
    return True

def raw_candidates(features,spec,split):
    cut=cutoff_ts()
    by_pattern={name:[] for name in spec}
    for (pair,ts_),vals in features.items():
        this_split="DISCOVERY" if ts_<cut else "VALIDATION"
        if this_split!=split:continue
        for name,conds in spec.items():
            if match_pattern(vals,conds):
                by_pattern[name].append((pair,ts_))
    for name in by_pattern:
        by_pattern[name].sort(key=lambda x:(x[0],x[1]))
    return by_pattern

def first_entry_signals(rows,horizon):
    """First pattern entry after a non-pattern day, plus cooldown per coin.

    Since raw rows contain only matching days, a >1-day gap defines a new entry.
    Cooldown is max(30d,horizon) from the prior accepted signal.
    """
    cooldown=max(30,horizon)*86400
    accepted=[]
    prev_match={}
    last_signal={}
    for pair,ts_ in rows:
        prev=prev_match.get(pair)
        is_entry=(prev is None or ts_-prev>36*3600)
        prev_match[pair]=ts_
        if not is_entry:
            continue
        last=last_signal.get(pair)
        if last is None or ts_-last>=cooldown:
            accepted.append((pair,ts_))
            last_signal[pair]=ts_
    return accepted

def outcome_map(c,horizon,threshold):
    hitcol=f"hit{threshold}"
    rows=c.execute(f"""SELECT pair,anchor_ts,{hitcol}
      FROM v2_outcomes WHERE version=? AND horizon_days=?""",
      (OUTCOME_VERSION,horizon)).fetchall()
    return {(r["pair"],int(r["anchor_ts"])):int(r[hitcol]) for r in rows}

def raw_base(c,split,horizon,threshold):
    cut=cutoff_ts(); hitcol=f"hit{threshold}"
    op="<" if split=="DISCOVERY" else ">="
    row=c.execute(f"""SELECT COUNT(*) n,SUM({hitcol}) hits
      FROM v2_outcomes
      WHERE version=? AND horizon_days=? AND anchor_ts {op} ?""",
      (OUTCOME_VERSION,horizon,cut)).fetchone()
    n=int(row["n"] or 0); hits=int(row["hits"] or 0)
    return (hits/n if n else None),hits,n

def main():
    if not os.path.exists(DB):
        print("History V4: DB yok");return
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with con() as c:
        init_db(c)
        c.execute("""INSERT OR REPLACE INTO v4_runs
          (run_id,started_utc,notes,version) VALUES(?,?,?,?)""",
          (run_id,datetime.now(timezone.utc).isoformat(),
           "Pattern-first OOS validation. Discovery-only outcome-blind quantile thresholds; first-entry signals; horizon-aware cooldown; frozen V0/V2/V3/Cross-Venue untouched.",
           VERSION))

        features=build_anchor_features(c)
        spec=freeze_spec(c,features)
        c.execute("DELETE FROM v4_pattern_results WHERE version=?",(VERSION,))
        c.execute("DELETE FROM v4_pattern_counts WHERE version=?",(VERSION,))

        results=[]
        count_rows=[]
        for split in ("DISCOVERY","VALIDATION"):
            raws=raw_candidates(features,spec,split)
            # Store a canonical 30d de-overlapped count for readability.
            for name,rows in raws.items():
                sig30=first_entry_signals(rows,30)
                count_rows.append((
                  split,name,len(rows),len(sig30),len({p for p,_ in sig30}),VERSION
                ))

            for h in HORIZONS:
                for t in THRESHOLDS:
                    om=outcome_map(c,h,t)
                    base,total_hits,total_n=raw_base(c,split,h,t)
                    for name,rows in raws.items():
                        signals=first_entry_signals(rows,h)
                        eligible=[x for x in signals if x in om]
                        hits=sum(om[x] for x in eligible)
                        n=len(eligible)
                        misses=n-hits
                        precision=hits/n if n else None
                        fpr=misses/n if n else None
                        recall=hits/total_hits if total_hits else None
                        lift=(precision/base) if precision is not None and base not in (None,0) else None
                        results.append((
                          split,name,h,t,n,hits,misses,len({p for p,_ in eligible}),
                          precision,fpr,recall,base,lift,VERSION
                        ))

        c.executemany("""INSERT OR REPLACE INTO v4_pattern_counts
          VALUES(?,?,?,?,?,?)""",count_rows)
        c.executemany("""INSERT OR REPLACE INTO v4_pattern_results
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",results)
        c.execute("""UPDATE v4_runs SET finished_utc=?,result_rows=? WHERE run_id=?""",
          (datetime.now(timezone.utc).isoformat(),len(results),run_id))
        c.commit()

        print("History V4 frozen pattern thresholds:")
        for name,conds in spec.items():
            print(" ",name," | ".join(f"{f} {op} {thr:.4f}" for f,op,thr in conds))
        print(f"History V4: features={len(features)} result_rows={len(results)}")
        for r in c.execute("""SELECT pattern_name,horizon_days,threshold_pct,signal_n,hit_n,
              precision,raw_base_rate,lift_vs_raw_base,unique_coin_n
          FROM v4_pattern_results
          WHERE version=? AND split='VALIDATION' AND threshold_pct IN (50,100)
          ORDER BY threshold_pct DESC,horizon_days,pattern_name""",(VERSION,)):
            p=100*float(r["precision"]) if r["precision"] is not None else 0
            b=100*float(r["raw_base_rate"]) if r["raw_base_rate"] is not None else 0
            lift=float(r["lift_vs_raw_base"]) if r["lift_vs_raw_base"] is not None else 0
            print(f"  {r['pattern_name']} {r['horizon_days']}d +{r['threshold_pct']} "
                  f"precision={p:.2f}% base={b:.2f}% lift={lift:.2f}x "
                  f"n={r['signal_n']} hits={r['hit_n']} coins={r['unique_coin_n']}")

if __name__=="__main__":
    main()
