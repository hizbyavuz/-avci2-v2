#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""History Miner V5 Activation validation.

Purpose:
  Starting from the frozen V4 P2/P3 pattern-first candidate pool, test whether
  two *predeclared and outcome-blind* activation signals improve precision:
    1) trailing 3h quote-volume anomaly vs prior 24h median
    2) distance above the trailing 24h low ("dip departure")

Important:
- V0/V2/V3/V4/Cross-Venue are not modified.
- Cross-venue/Binance lead is deliberately NOT part of the primary V5 model.
- Hourly activation is aligned to the V4 daily signal close, never to a future
  +10% onset. This avoids the look-ahead alignment used by descriptive rewind.
- Activation thresholds are frozen from DISCOVERY candidate distributions only
  (75th percentile), without consulting future outcomes.
- Primary test family is fixed in advance:
    P2/P3 x {VOL,DIP,VOL_DIP} x {7,14,30d} x {+50,+100} = 36 tests.
- Each activation subset is tested against its non-activated remainder inside
  the same parent pattern using a one-sided Fisher exact enrichment test.
  Benjamini-Hochberg FDR is applied across the 36 validation tests.
- Research-only; no orders.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from urllib import parse, request, error

import history_validation_v4 as v4

DB=os.getenv("HISTORY_DB","history_miner.db")
BASE="https://api.gateio.ws/api/v4/spot/candlesticks"
VERSION="history-v5-activation-v0.2-20260925"
V4_VERSION="history-v4-pattern-first-v0.1-20260925"
V2_VERSION="history-v2-outcomes-matched-v0.1-20260924"
BUDGET=int(os.getenv("HISTORY_V5_BUDGET","120"))
SLEEP=float(os.getenv("HISTORY_V5_SLEEP","0.15"))
HORIZONS=(7,14,30)
TARGETS=(50,100)
PATTERNS=("P2_FAR_PLUS_VOL","P3_FAR_VOL_DRAWDOWN")
ACTIVATIONS=("BASE","VOL","DIP","VOL_DIP")
FDR_ALPHA=0.05

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.row_factory=sqlite3.Row
    return c

def f(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):
        return None

def pct(a,b):
    return 100.0*(b/a-1.0) if a and b and a>0 else None

def med(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(xs) if xs else None

def quantile(vals,q):
    xs=sorted(float(x) for x in vals if x is not None and math.isfinite(float(x)))
    if not xs:return None
    if len(xs)==1:return xs[0]
    p=(len(xs)-1)*q
    lo=int(math.floor(p)); hi=int(math.ceil(p))
    if lo==hi:return xs[lo]
    w=p-lo
    return xs[lo]*(1-w)+xs[hi]*w

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS v5_activation_snapshots(
      pair TEXT NOT NULL,
      anchor_ts INTEGER NOT NULL,
      signal_ts INTEGER NOT NULL,
      vol_ratio_3_24 REAL,
      dist_low_24h REAL,
      status TEXT NOT NULL,
      last_error TEXT,
      version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,version)
    );
    CREATE TABLE IF NOT EXISTS v5_activation_spec(
      feature TEXT NOT NULL,
      operator TEXT NOT NULL,
      threshold REAL NOT NULL,
      quantile REAL NOT NULL,
      source_split TEXT NOT NULL,
      frozen_utc TEXT NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(feature,version)
    );
    CREATE TABLE IF NOT EXISTS v5_activation_results(
      split TEXT NOT NULL,
      pattern_name TEXT NOT NULL,
      activation_name TEXT NOT NULL,
      horizon_days INTEGER NOT NULL,
      threshold_pct INTEGER NOT NULL,
      signal_n INTEGER NOT NULL,
      hit_n INTEGER NOT NULL,
      miss_n INTEGER NOT NULL,
      unique_coin_n INTEGER NOT NULL,
      precision REAL,
      false_positive_rate REAL,
      parent_precision REAL,
      lift_vs_parent REAL,
      raw_base_rate REAL,
      lift_vs_raw_base REAL,
      p_value REAL,
      q_value REAL,
      fdr_pass INTEGER,
      version TEXT NOT NULL,
      PRIMARY KEY(split,pattern_name,activation_name,horizon_days,threshold_pct,version)
    );
    CREATE TABLE IF NOT EXISTS v5_runs(
      run_id TEXT PRIMARY KEY,
      started_utc TEXT NOT NULL,
      finished_utc TEXT,
      discovery_total INTEGER NOT NULL DEFAULT 0,
      discovery_done INTEGER NOT NULL DEFAULT 0,
      validation_total INTEGER NOT NULL DEFAULT 0,
      validation_done INTEGER NOT NULL DEFAULT 0,
      result_rows INTEGER NOT NULL DEFAULT 0,
      spec_frozen INTEGER NOT NULL DEFAULT 0,
      notes TEXT,
      version TEXT NOT NULL
    );
    """)

def load_v4_spec(c):
    rows=c.execute("""SELECT pattern_name,feature,operator,threshold
      FROM v4_pattern_spec WHERE version=?""",(V4_VERSION,)).fetchall()
    if not rows:
        raise RuntimeError("v4_pattern_spec_missing")
    spec={}
    for r in rows:
        spec.setdefault(r["pattern_name"],[]).append(
            (r["feature"],r["operator"],float(r["threshold"])))
    return {k:v for k,v in spec.items() if k in PATTERNS}

def candidate_rows(c):
    """Canonical V4 first-entry signals with 30d cooldown for P2/P3."""
    features=v4.build_anchor_features(c)
    spec=load_v4_spec(c)
    cut=v4.cutoff_ts()
    out={"DISCOVERY":{},"VALIDATION":{}}
    for split in out:
        raws=v4.raw_candidates(features,spec,split)
        for p in PATTERNS:
            sig=v4.first_entry_signals(raws.get(p,[]),30)
            out[split][p]=sig
    return out

def union_candidates(cands,split):
    seen={}
    for p,rows in cands[split].items():
        for pair,ts_ in rows:
            seen[(pair,int(ts_))]=1
    return sorted(seen)

def _gate_candles(params):
    q=parse.urlencode(params)
    url=BASE+"?"+q
    try:
        with request.urlopen(url,timeout=25) as r:
            return json.load(r)
    except error.HTTPError as e:
        body=""
        try:
            body=e.read().decode("utf-8","replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"gate_http_{e.code}:{body}") from e

def fetch_hourly(pair,start_ts,end_ts):
    # Keep 1h resolution. First try exact from/to. Some older windows return
    # HTTP 400, so retry with only 'to'; Gate documents that omitted 'from'
    # defaults to 100 intervals before 'to', enough for our 52h window.
    try:
        data=_gate_candles({
          "currency_pair":pair,"interval":"1h","from":int(start_ts),"to":int(end_ts)
        })
    except RuntimeError as e:
        if not str(e).startswith("gate_http_400:"):
            raise
        data=_gate_candles({
          "currency_pair":pair,"interval":"1h","to":int(end_ts)
        })
    if not isinstance(data,list):
        raise ValueError("invalid_hourly_payload")
    out=[]
    for row in data:
        if not isinstance(row,list) or len(row)<7:continue
        ts=int(float(row[0])); qv=f(row[1]); close=f(row[2]); high=f(row[3]); low=f(row[4])
        if None in (close,high,low) or min(close,high,low)<=0:continue
        if ts < int(start_ts) or ts >= int(end_ts):continue
        out.append({"ts":ts,"close":close,"high":high,"low":low,"qv":qv})
    out.sort(key=lambda x:x["ts"])
    return out

def make_snapshot(pair,anchor_ts):
    # Gate daily anchor is bucket start. The V4 daily feature becomes known at
    # the end of that UTC day; use the last closed hourly candle of that day.
    signal_end=int(anchor_ts)+86400
    hourly=fetch_hourly(pair,signal_end-52*3600,signal_end)
    usable=[x for x in hourly if x["ts"]<signal_end]
    if len(usable)<28:
        raise ValueError(f"short_hourly:{len(usable)}")
    cur=usable[-1]
    idx=len(usable)-1
    prev=usable[max(0,idx-24):idx]
    prev_q=[x["qv"] for x in prev if x["qv"] is not None]
    base_v=med(prev_q)
    last3=[x["qv"] for x in usable[max(0,idx-2):idx+1] if x["qv"] is not None]
    v3=(sum(last3)/len(last3)/base_v) if last3 and base_v and base_v>0 else None
    last24=usable[max(0,idx-23):idx+1]
    lo=min(x["low"] for x in last24)
    dist=pct(lo,cur["close"])
    if v3 is None or dist is None:
        raise ValueError("missing_activation_feature")
    return int(cur["ts"]),float(v3),float(dist)

def fill_snapshots(c,cands):
    all_disc=union_candidates(cands,"DISCOVERY")
    all_val=union_candidates(cands,"VALIDATION")
    rows=c.execute(
      "SELECT pair,anchor_ts,status FROM v5_activation_snapshots WHERE version=?",(VERSION,)
    ).fetchall()
    existing={(r["pair"],int(r["anchor_ts"])) for r in rows}
    retry_set={(r["pair"],int(r["anchor_ts"])) for r in rows if r["status"]=="ERROR"}
    due_disc=[x for x in all_disc if x not in existing]
    due_val=[x for x in all_val if x not in existing]
    retry_disc=[x for x in all_disc if x in retry_set]
    retry_val=[x for x in all_val if x in retry_set]
    # Unseen rows first; retries only after progress, avoiding starvation.
    due=(due_disc+due_val+retry_disc+retry_val)[:BUDGET]
    for i,(pair,ts_) in enumerate(due):
        try:
            sig,vr,dl=make_snapshot(pair,ts_)
            c.execute("""INSERT OR REPLACE INTO v5_activation_snapshots
              VALUES(?,?,?,?,?,?,?,?)""",
              (pair,ts_,sig,vr,dl,"DONE",None,VERSION))
        except Exception as e:
            c.execute("""INSERT OR REPLACE INTO v5_activation_snapshots
              VALUES(?,?,?,?,?,?,?,?)""",
              (pair,ts_,ts_+86400,None,None,"ERROR",str(e)[:180],VERSION))
        if (i+1)%20==0:
            c.commit()
        time.sleep(SLEEP)
    c.commit()
    return all_disc,all_val

def coverage(c,rows):
    done={(r["pair"],int(r["anchor_ts"])) for r in c.execute(
      "SELECT pair,anchor_ts FROM v5_activation_snapshots WHERE version=?",(VERSION,))}
    return len(rows),sum(1 for x in rows if x in done)

def freeze_activation_spec(c,disc_rows):
    rows=c.execute("""SELECT feature,operator,threshold FROM v5_activation_spec
      WHERE version=?""",(VERSION,)).fetchall()
    if rows:
        return {r["feature"]:(r["operator"],float(r["threshold"])) for r in rows},True

    total,done=coverage(c,disc_rows)
    if total==0 or done<total:
        return {},False

    keys=set(disc_rows)
    snaps=c.execute("""SELECT pair,anchor_ts,vol_ratio_3_24,dist_low_24h,status
      FROM v5_activation_snapshots WHERE version=?""",(VERSION,)).fetchall()
    good=[r for r in snaps if (r["pair"],int(r["anchor_ts"])) in keys and r["status"]=="DONE"]
    if len(good)<50:
        # Sparse hourly history is a data-health state, not a fatal workflow error.
        return {},False

    vr=quantile([r["vol_ratio_3_24"] for r in good],0.75)
    dl=quantile([r["dist_low_24h"] for r in good],0.75)
    if vr is None or dl is None:
        raise RuntimeError("cannot_freeze_activation_thresholds")
    now=datetime.now(timezone.utc).isoformat()
    for feat,thr in (("vol_ratio_3_24",vr),("dist_low_24h",dl)):
        c.execute("""INSERT INTO v5_activation_spec
          VALUES(?,?,?,?,?,?,?)""",(feat,">=",float(thr),0.75,"DISCOVERY_ONLY",now,VERSION))
    c.commit()
    return {
      "vol_ratio_3_24":(">=",float(vr)),
      "dist_low_24h":(">=",float(dl)),
    },True

def outcome_map(c,h,t):
    col=f"hit{t}"
    rows=c.execute(f"""SELECT pair,anchor_ts,{col} FROM v2_outcomes
      WHERE version=? AND horizon_days=?""",(V2_VERSION,h)).fetchall()
    return {(r["pair"],int(r["anchor_ts"])):int(r[col]) for r in rows}

def raw_base(c,split,h,t):
    cut=v4.cutoff_ts(); op="<" if split=="DISCOVERY" else ">="
    col=f"hit{t}"
    r=c.execute(f"""SELECT COUNT(*) n,SUM({col}) hits FROM v2_outcomes
      WHERE version=? AND horizon_days=? AND anchor_ts {op} ?""",
      (V2_VERSION,h,cut)).fetchone()
    n=int(r["n"] or 0); hits=int(r["hits"] or 0)
    return hits/n if n else None

def fisher_enrichment(a,b,c,d):
    """One-sided Fisher P[X>=a] for activated hit enrichment.

    Table: activated [a hits,b misses], nonactivated [c hits,d misses].
    """
    n1=a+b; n2=c+d; K=a+c; N=n1+n2
    if n1==0 or n2==0 or K==0:return 1.0
    lo=max(0,n1-(N-K)); hi=min(n1,K)
    den=math.comb(N,n1)
    p=0.0
    for x in range(max(a,lo),hi+1):
        p += (math.comb(K,x)*math.comb(N-K,n1-x))/den
    return min(1.0,p)

def bh_adjust(rows):
    """Mutates rows with q_value/fdr_pass using validation non-BASE tests."""
    tests=[(i,r["p_value"]) for i,r in enumerate(rows)
           if r["split"]=="VALIDATION" and r["activation_name"]!="BASE"
           and r["p_value"] is not None]
    m=len(tests)
    if not m:return
    ranked=sorted(tests,key=lambda x:x[1])
    qtmp={}
    prev=1.0
    for rank_idx in range(m-1,-1,-1):
        i,p=ranked[rank_idx]
        rank=rank_idx+1
        q=min(prev,p*m/rank)
        prev=q; qtmp[i]=q
    for i,q in qtmp.items():
        rows[i]["q_value"]=q
        rows[i]["fdr_pass"]=1 if q<=FDR_ALPHA else 0

def activated(name,snap,spec):
    if name=="BASE":return True
    vol=snap["vol_ratio_3_24"]>=spec["vol_ratio_3_24"][1]
    dip=snap["dist_low_24h"]>=spec["dist_low_24h"][1]
    if name=="VOL":return vol
    if name=="DIP":return dip
    if name=="VOL_DIP":return vol and dip
    return False

def compute_results(c,cands,spec):
    snap_rows=c.execute("""SELECT pair,anchor_ts,vol_ratio_3_24,dist_low_24h,status
      FROM v5_activation_snapshots WHERE version=?""",(VERSION,)).fetchall()
    snaps={(r["pair"],int(r["anchor_ts"])):r for r in snap_rows if r["status"]=="DONE"}
    rows=[]
    for split in ("DISCOVERY","VALIDATION"):
        for p in PATTERNS:
            parent=[x for x in cands[split].get(p,[]) if x in snaps]
            for h in HORIZONS:
                for t in TARGETS:
                    om=outcome_map(c,h,t)
                    eligible=[x for x in parent if x in om]
                    parent_hits=sum(om[x] for x in eligible)
                    parent_n=len(eligible)
                    parent_precision=parent_hits/parent_n if parent_n else None
                    base=raw_base(c,split,h,t)
                    for act in ACTIVATIONS:
                        sig=[x for x in eligible if activated(act,snaps[x],spec)]
                        hit=sum(om[x] for x in sig)
                        n=len(sig); miss=n-hit
                        precision=hit/n if n else None
                        fpr=miss/n if n else None
                        lift_parent=(precision/parent_precision) if precision is not None and parent_precision not in (None,0) else None
                        lift_raw=(precision/base) if precision is not None and base not in (None,0) else None
                        pval=None
                        if act!="BASE" and n and parent_n>n:
                            non_hits=parent_hits-hit
                            non_n=parent_n-n
                            pval=fisher_enrichment(hit,miss,non_hits,non_n-non_hits)
                        rows.append({
                          "split":split,"pattern_name":p,"activation_name":act,
                          "horizon_days":h,"threshold_pct":t,"signal_n":n,
                          "hit_n":hit,"miss_n":miss,"unique_coin_n":len({x[0] for x in sig}),
                          "precision":precision,"false_positive_rate":fpr,
                          "parent_precision":parent_precision,"lift_vs_parent":lift_parent,
                          "raw_base_rate":base,"lift_vs_raw_base":lift_raw,
                          "p_value":pval,"q_value":None,"fdr_pass":0,
                        })
    bh_adjust(rows)
    c.execute("DELETE FROM v5_activation_results WHERE version=?",(VERSION,))
    vals=[]
    for r in rows:
        vals.append((
          r["split"],r["pattern_name"],r["activation_name"],r["horizon_days"],r["threshold_pct"],
          r["signal_n"],r["hit_n"],r["miss_n"],r["unique_coin_n"],r["precision"],
          r["false_positive_rate"],r["parent_precision"],r["lift_vs_parent"],
          r["raw_base_rate"],r["lift_vs_raw_base"],r["p_value"],r["q_value"],
          r["fdr_pass"],VERSION
        ))
    c.executemany("""INSERT OR REPLACE INTO v5_activation_results
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",vals)
    c.commit()
    return rows

def main():
    if not os.path.exists(DB):
        print("History V5: DB yok");return
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with con() as c:
        init_db(c)
        cands=candidate_rows(c)
        disc,val=fill_snapshots(c,cands)
        dtotal,ddone=coverage(c,disc)
        vtotal,vdone=coverage(c,val)
        spec,frozen=freeze_activation_spec(c,disc)
        result_rows=0
        if frozen:
            rows=compute_results(c,cands,spec)
            result_rows=len(rows)
        note=("Primary V5 excludes Cross-Venue lead. Fixed family: P2/P3 x VOL/DIP/VOL_DIP "
              "x 7/14/30d x +50/+100; Fisher enrichment + BH FDR. Hourly features aligned "
              "to V4 signal close, not future onset.")
        c.execute("""INSERT OR REPLACE INTO v5_runs
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(
          run_id,datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat(),
          dtotal,ddone,vtotal,vdone,result_rows,1 if frozen else 0,note,VERSION
        ))
        c.commit()
        good_n=c.execute("SELECT COUNT(*) FROM v5_activation_snapshots WHERE version=? AND status='DONE'",(VERSION,)).fetchone()[0]
        err_n=c.execute("SELECT COUNT(*) FROM v5_activation_snapshots WHERE version=? AND status='ERROR'",(VERSION,)).fetchone()[0]
        print(f"History V5: discovery {ddone}/{dtotal} | validation {vdone}/{vtotal} | spec_frozen={frozen}")
        print(f"History V5 hourly health: usable={good_n} errors={err_n}")
        if frozen:
            print("History V5 activation thresholds:",
                  f"vol3/24>={spec['vol_ratio_3_24'][1]:.4f}",
                  f"dip24>={spec['dist_low_24h'][1]:.4f}")
            for r in c.execute("""SELECT pattern_name,activation_name,horizon_days,threshold_pct,
                signal_n,hit_n,precision,parent_precision,lift_vs_parent,q_value,fdr_pass
              FROM v5_activation_results
              WHERE version=? AND split='VALIDATION' AND activation_name!='BASE'
              ORDER BY fdr_pass DESC, COALESCE(q_value,1), COALESCE(lift_vs_parent,0) DESC
              LIMIT 12""",(VERSION,)):
                print(dict(r))

if __name__=="__main__":
    main()
