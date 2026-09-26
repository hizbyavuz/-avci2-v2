#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Whole-universe Binance research-integrity validation.

Research-only. No signal thresholds or live candidate membership are changed.
Implements point-in-time audit, survivorship/data-failure accounting,
dependence-aware clustering, confidence intervals, walk-forward stability,
candidate-vs-control significance, permutation testing and executable outcomes.
"""
import json, math, os, random, sqlite3, statistics
from datetime import datetime, timezone

DB=os.getenv("BINANCE_DB","binance_avci2.db")
VERSION="binance-research-integrity-v1-20260926"
PERMUTATIONS=1000
SEED=26092026

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None

def reached(r,target=10):
    try:
        d=json.loads(r["reach_json"] or "{}")
        v=d.get(str(target))
        return bool(v) if isinstance(v,(bool,int,float)) else False
    except Exception:return False

def ztest(a,n,b,m):
    if not n or not m:return None
    p=(a+b)/(n+m)
    se=math.sqrt(max(0,p*(1-p)*(1/n+1/m)))
    return 1.0 if se==0 else math.erfc(abs(a/n-b/m)/se/math.sqrt(2))

def ckey(r):
    x=dt(r["signal_time_utc"])
    iso=x.isocalendar() if x else (0,0,0)
    return (iso.year if x else 0,iso.week if x else 0,r["engine"] or "?",r["btc_regime"] or "?")

def cluster_bootstrap(rows,nboot=1000):
    clusters={}
    for r in rows:clusters.setdefault(ckey(r),[]).append(r)
    keys=list(clusters)
    if len(keys)<3:return None,None
    rng=random.Random(SEED); vals=[]
    for _ in range(nboot):
        sample=[]
        for _k in keys: sample.extend(clusters[rng.choice(keys)])
        ca=[x for x in sample if x["event_class"]=="CANDIDATE"]
        co=[x for x in sample if x["event_class"] in ("NEAR_MISS","RANDOM_CONTROL")]
        if ca and co:
            vals.append(sum(reached(x) for x in ca)/len(ca)-sum(reached(x) for x in co)/len(co))
    if len(vals)<50:return None,None
    vals.sort()
    return vals[int(.025*len(vals))],vals[min(len(vals)-1,int(.975*len(vals)))]

def permutation_p(rows,obs,nperm=1000):
    if obs is None:return None
    hits=[reached(r) for r in rows]
    ncan=sum(r["event_class"]=="CANDIDATE" for r in rows)
    if ncan==0 or ncan==len(rows):return None
    rng=random.Random(SEED); idx=list(range(len(rows))); ge=0
    for _ in range(nperm):
        rng.shuffle(idx); chosen=set(idx[:ncan])
        a=[hits[i] for i in range(len(rows)) if i in chosen]
        b=[hits[i] for i in range(len(rows)) if i not in chosen]
        d=sum(a)/len(a)-sum(b)/len(b)
        if d>=obs:ge+=1
    return (ge+1)/(nperm+1)

def walk_forward(rows):
    rows=sorted(rows,key=lambda r:r["signal_time_utc"])
    if len(rows)<40:return []
    fold=max(10,len(rows)//4); out=[]; start=fold
    while start<len(rows):
        test=rows[start:min(len(rows),start+fold)]
        ca=[r for r in test if r["event_class"]=="CANDIDATE"]
        co=[r for r in test if r["event_class"] in ("NEAR_MISS","RANDOM_CONTROL")]
        if len(ca)>=3 and len(co)>=3:
            cr=sum(reached(r) for r in ca)/len(ca); br=sum(reached(r) for r in co)/len(co)
            out.append({"start":test[0]["signal_time_utc"],"end":test[-1]["signal_time_utc"],
                        "candidate_n":len(ca),"control_n":len(co),
                        "candidate_rate":cr,"control_rate":br,"diff":cr-br,
                        "regimes":sorted({r["btc_regime"] or "UNKNOWN" for r in test})})
        start+=fold
    return out

def main():
    if not os.path.exists(DB):
        print("Binance integrity: DB yok");return
    with sqlite3.connect(DB,timeout=60) as c:
        c.row_factory=sqlite3.Row
        c.execute("""CREATE TABLE IF NOT EXISTS binance_research_integrity(
          version TEXT PRIMARY KEY,created_at_utc TEXT,total_closed INTEGER,
          candidate_n INTEGER,control_n INTEGER,independent_clusters INTEGER,
          candidate_hit10 REAL,control_hit10 REAL,diff_hit10 REAL,
          diff_ci_low REAL,diff_ci_high REAL,ztest_p REAL,permutation_p REAL,
          walkforward_json TEXT,walkforward_positive INTEGER,walkforward_total INTEGER,
          point_in_time_violations INTEGER,survivorship_data_failures INTEGER,
          dirty_high_risk_events INTEGER,net_mean REAL,net_median REAL,
          mae_median REAL,executable_mfe_median REAL,data_modes_json TEXT,
          regimes_json TEXT,notes_json TEXT)""")
        rows=[dict(r) for r in c.execute("""SELECT s.event_id,s.symbol,s.signal_time_utc,
            s.event_class,s.engine,s.stage,s.btc_regime,s.data_mode,s.entry_time_utc,
            s.entry_delay_seconds,s.manipulation_risk,o.reach_json,o.net_return_pct,
            o.mae_pct,o.executable_mfe_pct,o.label_status
            FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
            WHERE o.label_status='CLOSED'
              AND s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
            ORDER BY s.signal_time_utc""")]
        ca=[r for r in rows if r["event_class"]=="CANDIDATE"]
        co=[r for r in rows if r["event_class"] in ("NEAR_MISS","RANDOM_CONTROL")]
        ah=sum(reached(r) for r in ca); bh=sum(reached(r) for r in co)
        ar=ah/len(ca) if ca else None; br=bh/len(co) if co else None
        diff=(ar-br) if ar is not None and br is not None else None
        lo,hi=cluster_bootstrap(rows,PERMUTATIONS)
        p=ztest(ah,len(ca),bh,len(co)) if ca and co else None
        pp=permutation_p(rows,diff,PERMUTATIONS)
        wf=walk_forward(rows); wfpos=sum(x["diff"]>0 for x in wf)

        pit=0
        for r in rows:
            s=dt(r["signal_time_utc"]); e=dt(r["entry_time_utc"])
            if e and s:
                if e<s:pit+=1
                elif r["entry_delay_seconds"] is not None and (e-s).total_seconds()<float(r["entry_delay_seconds"]):pit+=1

        survivorship=0
        if table(c,"data_issues"):
            survivorship=c.execute("""SELECT COUNT(*) FROM data_issues WHERE issue_type IN
              ('OUTCOME_PATH_MISSING','OUTCOME_PATH_GAP','OUTCOME_PATH_STALE',
               'SYMBOL_STATUS_UNVERIFIED','OUTCOME_LABEL_FAILED')""").fetchone()[0]

        dirty=sum(1 for r in rows if int(r["manipulation_risk"] or 0))
        if table(c,"binance_deception_evidence"):
            dirty=max(dirty,c.execute("""SELECT COUNT(*) FROM binance_deception_evidence
              WHERE UPPER(deception_risk)='HIGH'""").fetchone()[0])

        net=[float(r["net_return_pct"]) for r in ca if r["net_return_pct"] is not None]
        mae=[float(r["mae_pct"]) for r in ca if r["mae_pct"] is not None]
        mfe=[float(r["executable_mfe_pct"]) for r in ca if r["executable_mfe_pct"] is not None]
        modes={}; regimes={}
        for r in rows:
            modes[r["data_mode"] or "UNKNOWN"]=modes.get(r["data_mode"] or "UNKNOWN",0)+1
            regimes[r["btc_regime"] or "UNKNOWN"]=regimes.get(r["btc_regime"] or "UNKNOWN",0)+1
        notes={
          "point_in_time":"signal-time snapshots + entry delay audit",
          "survivorship":"missing/delisted/stale outcomes remain explicit, never silently deleted",
          "independence_cluster":"ISO week + engine + BTC regime",
          "confidence_interval":"95% cluster bootstrap for candidate-control +10 difference",
          "permutation":f"{PERMUTATIONS} label permutations",
          "walk_forward":"chronological folds; no random train/test",
          "multiple_testing":"activation math applies BH-FDR separately",
          "paper_execution":"cost-aware net return, MAE and executable MFE"
        }
        c.execute("DELETE FROM binance_research_integrity WHERE version=?",(VERSION,))
        c.execute("""INSERT INTO binance_research_integrity VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
          VERSION,datetime.now(timezone.utc).isoformat(),len(rows),len(ca),len(co),
          len({ckey(r) for r in rows}),ar,br,diff,lo,hi,p,pp,json.dumps(wf,ensure_ascii=False),
          wfpos,len(wf),pit,survivorship,dirty,
          sum(net)/len(net) if net else None,statistics.median(net) if net else None,
          statistics.median(mae) if mae else None,statistics.median(mfe) if mfe else None,
          json.dumps(modes),json.dumps(regimes),json.dumps(notes,ensure_ascii=False)))
        c.commit()
        print("BINANCE RESEARCH INTEGRITY",{
          "closed":len(rows),"candidate":len(ca),"control":len(co),
          "independent_clusters":len({ckey(r) for r in rows}),
          "hit10_candidate":ar,"hit10_control":br,"ci95":[lo,hi],
          "ztest_p":p,"permutation_p":pp,"walkforward":f"{wfpos}/{len(wf)}",
          "point_in_time_violations":pit,"survivorship_failures":survivorship,
          "dirty_high_risk":dirty,"net_mean":sum(net)/len(net) if net else None})

if __name__=="__main__":main()
