#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Whole-universe Gate research-integrity validation.

Research-only. Keeps frozen V5 untouched. Audits point-in-time alignment,
survivorship/data failures, dependence clusters, confidence intervals,
walk-forward stability, candidate-vs-control significance, permutation
robustness and cost-aware outcomes.
"""
import json, math, os, random, sqlite3, statistics
from datetime import datetime, timezone

OBS_DB=os.getenv("AVCI_DB","avci2.db")
VAL_DB=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
VERSION="gate-research-integrity-v1-20260926"
PERMUTATIONS=1000
SEED=26092026

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def reached(r):
    return r["result_10"]=="TARGET_FIRST"

def ztest(a,n,b,m):
    if not n or not m:return None
    p=(a+b)/(n+m)
    se=math.sqrt(max(0,p*(1-p)*(1/n+1/m)))
    return 1.0 if se==0 else math.erfc(abs(a/n-b/m)/se/math.sqrt(2))

def ckey(r):
    dt=datetime.fromtimestamp(int(r["signal_ts"]),timezone.utc)
    iso=dt.isocalendar()
    return (iso.year,iso.week,r["network_id"] or "?",r["btc_regime"] or "?")

def cluster_bootstrap(rows,nboot=1000):
    clusters={}
    for r in rows:clusters.setdefault(ckey(r),[]).append(r)
    keys=list(clusters)
    if len(keys)<3:return None,None
    rng=random.Random(SEED); vals=[]
    for _ in range(nboot):
        sample=[]
        for _k in keys: sample.extend(clusters[rng.choice(keys)])
        ca=[x for x in sample if x["group_type"] in ("CANDIDATE","EXPANDED_CANDIDATE")]
        co=[x for x in sample if x["group_type"] in ("NEAR_MISS","RANDOM_CONTROL")]
        if ca and co:
            vals.append(sum(reached(x) for x in ca)/len(ca)-sum(reached(x) for x in co)/len(co))
    if len(vals)<50:return None,None
    vals.sort()
    return vals[int(.025*len(vals))],vals[min(len(vals)-1,int(.975*len(vals)))]

def permutation_p(rows,obs,nperm=1000):
    if obs is None:return None
    hits=[reached(r) for r in rows]
    ncan=sum(r["group_type"] in ("CANDIDATE","EXPANDED_CANDIDATE") for r in rows)
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
    rows=sorted(rows,key=lambda r:r["signal_ts"])
    if len(rows)<40:return []
    fold=max(10,len(rows)//4); out=[]; start=fold
    while start<len(rows):
        test=rows[start:min(len(rows),start+fold)]
        ca=[r for r in test if r["group_type"] in ("CANDIDATE","EXPANDED_CANDIDATE")]
        co=[r for r in test if r["group_type"] in ("NEAR_MISS","RANDOM_CONTROL")]
        if len(ca)>=3 and len(co)>=3:
            cr=sum(reached(r) for r in ca)/len(ca); br=sum(reached(r) for r in co)/len(co)
            out.append({"start_ts":test[0]["signal_ts"],"end_ts":test[-1]["signal_ts"],
                        "candidate_n":len(ca),"control_n":len(co),"candidate_rate":cr,
                        "control_rate":br,"diff":cr-br,
                        "regimes":sorted({r["btc_regime"] or "UNKNOWN" for r in test})})
        start+=fold
    return out

def main():
    if not os.path.exists(OBS_DB) or not os.path.exists(VAL_DB):
        print("Gate integrity: DB eksik");return
    with sqlite3.connect(OBS_DB,timeout=60) as o, sqlite3.connect(VAL_DB,timeout=60) as v:
        o.row_factory=v.row_factory=sqlite3.Row
        o.execute("""CREATE TABLE IF NOT EXISTS gate_research_integrity(
          version TEXT PRIMARY KEY,created_at_utc TEXT,total_closed INTEGER,
          candidate_n INTEGER,control_n INTEGER,independent_clusters INTEGER,
          candidate_hit10 REAL,control_hit10 REAL,diff_hit10 REAL,
          diff_ci_low REAL,diff_ci_high REAL,ztest_p REAL,permutation_p REAL,
          walkforward_json TEXT,walkforward_positive INTEGER,walkforward_total INTEGER,
          point_in_time_violations INTEGER,survivorship_data_failures INTEGER,
          dirty_high_risk_events INTEGER,net_mean REAL,net_median REAL,
          regimes_json TEXT,notes_json TEXT)""")
        rows=[dict(r) for r in v.execute("""SELECT id,batch_id,network_id,token_contract,
          group_type,rulesets,btc_regime,result_10,status,signal_ts,net_final_pct,cost_status
          FROM validation_events
          WHERE status='CLOSED_72H'
            AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
          ORDER BY signal_ts,id""")]
        ca=[r for r in rows if r["group_type"] in ("CANDIDATE","EXPANDED_CANDIDATE")]
        co=[r for r in rows if r["group_type"] in ("NEAR_MISS","RANDOM_CONTROL")]
        ah=sum(reached(r) for r in ca); bh=sum(reached(r) for r in co)
        ar=ah/len(ca) if ca else None; br=bh/len(co) if co else None
        diff=(ar-br) if ar is not None and br is not None else None
        lo,hi=cluster_bootstrap(rows,PERMUTATIONS)
        p=ztest(ah,len(ca),bh,len(co)) if ca and co else None
        pp=permutation_p(rows,diff,PERMUTATIONS)
        wf=walk_forward(rows); wfpos=sum(x["diff"]>0 for x in wf)

        health={}
        if table(o,"gate_scan_health"):
            health={str(r["batch_id"]):int(r["scan_ts"]) for r in o.execute(
                "SELECT batch_id,scan_ts FROM gate_scan_health")}
        pit=0
        for r in rows:
            h=health.get(str(r["batch_id"]))
            if h is None or abs(int(r["signal_ts"])-h)>1800:pit+=1

        unresolved=v.execute("""SELECT COUNT(*) FROM validation_events
          WHERE group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
            AND status<>'CLOSED_72H'""").fetchone()[0]

        dirty=0
        if table(o,"gate_deception_evidence"):
            dirty+=o.execute("""SELECT COUNT(*) FROM gate_deception_evidence
              WHERE UPPER(deception_risk)='HIGH'""").fetchone()[0]
        if table(o,"gate_security_confidence_history"):
            dirty=max(dirty,o.execute("""SELECT COUNT(*) FROM gate_security_confidence_history
              WHERE hard_veto=1""").fetchone()[0])

        net=[float(r["net_final_pct"]) for r in ca
             if r["cost_status"]=="QUOTE_PLUS_ASSUMPTION" and r["net_final_pct"] is not None]
        regimes={}
        for r in rows:regimes[r["btc_regime"] or "UNKNOWN"]=regimes.get(r["btc_regime"] or "UNKNOWN",0)+1
        notes={
          "point_in_time":"event batch scan timestamp must align with signal timestamp",
          "survivorship":"unresolved/delisted/rug/data-failure events remain explicit, not silent drops",
          "independence_cluster":"ISO week + network + BTC regime",
          "confidence_interval":"95% cluster bootstrap for candidate-control +10 difference",
          "permutation":f"{PERMUTATIONS} label permutations",
          "walk_forward":"chronological folds across future periods",
          "multiple_testing":"activation math separately applies BH-FDR",
          "paper_execution":"net_final_pct only when quote+cost assumption is available"
        }
        o.execute("DELETE FROM gate_research_integrity WHERE version=?",(VERSION,))
        o.execute("""INSERT INTO gate_research_integrity VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
          VERSION,datetime.now(timezone.utc).isoformat(),len(rows),len(ca),len(co),
          len({ckey(r) for r in rows}),ar,br,diff,lo,hi,p,pp,json.dumps(wf,ensure_ascii=False),
          wfpos,len(wf),pit,unresolved,dirty,
          sum(net)/len(net) if net else None,statistics.median(net) if net else None,
          json.dumps(regimes),json.dumps(notes,ensure_ascii=False)))
        o.commit()
        print("GATE RESEARCH INTEGRITY",{
          "closed":len(rows),"candidate":len(ca),"control":len(co),
          "independent_clusters":len({ckey(r) for r in rows}),
          "hit10_candidate":ar,"hit10_control":br,"ci95":[lo,hi],
          "ztest_p":p,"permutation_p":pp,"walkforward":f"{wfpos}/{len(wf)}",
          "point_in_time_violations":pit,"unresolved":unresolved,
          "dirty_high_risk":dirty,"net_mean":sum(net)/len(net) if net else None})

if __name__=="__main__":main()
