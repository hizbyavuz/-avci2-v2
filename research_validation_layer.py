#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified statistical validation layer for Binance Avci and Gate Avci 2.

Research-only. It NEVER changes scanner thresholds, candidate membership, scores,
or execution rules. It measures the frozen systems.

Covers:
1) discovery/calibration/final-test separation
2) univariate feature attribution
3) multiple-testing registry + BH-FDR
4) day/regime clustered bootstrap
5) portfolio simulation / equity metrics
6) simple baselines
7) provider latency/staleness summaries
8) final-test no-touch governance
9) candidate-control significance tests
10) feature redundancy / Spearman clustering
11) fractional-Kelly research sizing
12) regime sample sizes + confidence intervals
13) PSI + CUSUM drift
14) precision/recall and PR curve
"""
from __future__ import annotations

import hashlib, json, math, os, random, sqlite3, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

VERSION="research-validation-v1-20260926"
DISCOVERY_END_UTC="2026-09-25T21:00:00+00:00"
CALIBRATION_END_UTC="2026-10-03T00:00:00+00:00"\nPURGE_HOURS=72\nEMBARGO_HOURS=24\nLABEL_HORIZON_HOURS=72
PRIMARY_TARGET=10.0
BOOTSTRAPS=1500
RNG_SEED=20260926
CORR_THRESHOLD=0.70
PSI_WARN=0.10
PSI_HIGH=0.25
CUSUM_K=0.25
CUSUM_H=5.0
PORTFOLIO_MAX_POSITIONS=5
PORTFOLIO_START_EQUITY=100.0
PORTFOLIO_RISK_FRACTION=0.02
KELLY_CAP=0.25

BINANCE_FEATURES=(
    "volume_z_15m","return_z_15m","trade_z_15m","volume_mult_1h",
    "retention_proxy","reignition_ratio","taker_buy_ratio_15m",
    "cross_sectional_rarity_pct","btc_relative_24h","change_15m","change_1h",
    "change_24h","oi_change_1h_pct","funding_rate","wakeup","persistence",
    "retention","reignition","trigger","climax_risk","data_staleness_minutes",
)
GATE_FEATURES=(
    "own_volume_ratio","buys_5m","sells_5m","change_5m","change_1h",
    "change_24h","liquidity","unique_buyers_5m","unique_buyers_1h",
)

def now(): return datetime.now(timezone.utc).isoformat()
def dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None
def ts_iso(x):
    try:return datetime.fromtimestamp(int(x),timezone.utc).isoformat()
    except Exception:return None
def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def cols(c,t):
    return {r[1] for r in c.execute(f"PRAGMA table_info({t})")} if table(c,t) else set()
def f(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except Exception:return None
def mean(xs): return sum(xs)/len(xs) if xs else None
def median(xs): return statistics.median(xs) if xs else None
def stdev(xs): return statistics.pstdev(xs) if len(xs)>=2 else None
def quantile(xs,q):
    ys=sorted(x for x in xs if x is not None and math.isfinite(x))
    if not ys:return None
    if len(ys)==1:return ys[0]
    p=(len(ys)-1)*q; lo=int(math.floor(p)); hi=int(math.ceil(p))
    if lo==hi:return ys[lo]
    w=p-lo; return ys[lo]*(1-w)+ys[hi]*w

def wilson(h,n,z=1.96):
    if not n:return (None,None)
    p=h/n; d=1+z*z/n
    center=(p+z*z/(2*n))/d
    margin=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/d
    return center-margin,center+margin

def split_of(t):
    """Chronological split with purge/embargo around boundaries.

    The 72h purge prevents outcome windows from crossing into the next split.
    The 24h embargo prevents immediate post-boundary microstructure carry-over.
    """
    x=dt(t)
    if not x:return "UNKNOWN"
    d=dt(DISCOVERY_END_UTC); k=dt(CALIBRATION_END_UTC)
    if x < d-timedelta(hours=PURGE_HOURS):
        return "DISCOVERY"
    if x < d+timedelta(hours=EMBARGO_HOURS):
        return "PURGED_EMBARGO"
    if x < k-timedelta(hours=PURGE_HOURS):
        return "CALIBRATION"
    if x < k+timedelta(hours=EMBARGO_HOURS):
        return "PURGED_EMBARGO"
    return "FINAL_TEST"

def bh_adjust(rows):
    valid=[r for r in rows if r.get("p_value") is not None]
    ordered=sorted(valid,key=lambda r:r["p_value"])
    m=len(ordered); prev=1.0
    for rank in range(m,0,-1):
        r=ordered[rank-1]
        q=min(prev,r["p_value"]*m/rank)
        r["q_value"]=q; prev=q
    for r in rows:r.setdefault("q_value",None)

def proportion_p(a,n,b,m):
    if not n or not m:return None
    pa=a/n; pb=b/m; pooled=(a+b)/(n+m)
    se=math.sqrt(max(0.0,pooled*(1-pooled)*(1/n+1/m)))
    if se==0:return 1.0
    z=abs(pa-pb)/se
    return math.erfc(z/math.sqrt(2))

def bootstrap_diff(a,b,blocks=False):
    if not a or not b:return (None,None,None)
    rng=random.Random(RNG_SEED)
    vals=[]
    if not blocks:
        for _ in range(BOOTSTRAPS):
            aa=[rng.choice(a) for _ in range(len(a))]
            bb=[rng.choice(b) for _ in range(len(b))]
            vals.append(mean(aa)-mean(bb))
    else:
        # a/b are dict cluster->[0/1]
        keys=sorted(set(a)|set(b))
        if len(keys)<2:return (None,None,None)
        for _ in range(BOOTSTRAPS):
            chosen=[rng.choice(keys) for _ in range(len(keys))]
            av=[]; bv=[]
            for k in chosen:
                av.extend(a.get(k,[])); bv.extend(b.get(k,[]))
            if av and bv: vals.append(mean(av)-mean(bv))
    if not vals:return (None,None,None)
    vals.sort()
    lo=vals[int(.025*(len(vals)-1))]; hi=vals[int(.975*(len(vals)-1))]
    p=2*min(sum(v<=0 for v in vals)/len(vals),sum(v>=0 for v in vals)/len(vals))
    return lo,hi,min(1.0,p)

def ranks(xs):
    order=sorted(range(len(xs)),key=lambda i:xs[i])
    out=[0.0]*len(xs); i=0
    while i<len(order):
        j=i
        while j+1<len(order) and xs[order[j+1]]==xs[order[i]]:j+=1
        r=(i+j+2)/2.0
        for k in range(i,j+1):out[order[k]]=r
        i=j+1
    return out

def pearson(a,b):
    if len(a)<3:return None
    ma=mean(a); mb=mean(b)
    va=sum((x-ma)**2 for x in a); vb=sum((y-mb)**2 for y in b)
    if va<=0 or vb<=0:return None
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb)

def spearman(a,b): return pearson(ranks(a),ranks(b))

def psi(base,new,bins=10):
    base=[x for x in base if x is not None]; new=[x for x in new if x is not None]
    if len(base)<20 or len(new)<10:return None
    cuts=sorted(set(quantile(base,i/bins) for i in range(1,bins)))
    def idx(x):
        i=0
        while i<len(cuts) and x>cuts[i]:i+=1
        return i
    eps=1e-6; score=0.0
    for k in range(len(cuts)+1):
        p=max(eps,sum(idx(x)==k for x in base)/len(base))
        q=max(eps,sum(idx(x)==k for x in new)/len(new))
        score+=(q-p)*math.log(q/p)
    return score

def cusum(values):
    if len(values)<10:return None
    mu=mean(values[:max(5,len(values)//3)])
    sd=stdev(values[:max(5,len(values)//3)]) or 1.0
    pos=neg=0.0; alarms=0
    for x in values:
        z=(x-mu)/sd
        pos=max(0.0,pos+z-CUSUM_K)
        neg=min(0.0,neg+z+CUSUM_K)
        if pos>CUSUM_H or neg<-CUSUM_H:
            alarms+=1; pos=neg=0.0
    return alarms

def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS research_validation_runs(
      source TEXT,version TEXT,run_utc TEXT,discovery_end TEXT,calibration_end TEXT,
      final_test_locked INTEGER,notes_json TEXT,PRIMARY KEY(source,version));
    CREATE TABLE IF NOT EXISTS research_split_registry(
      source TEXT,event_key TEXT,event_time TEXT,split TEXT,group_type TEXT,asset TEXT,
      regime TEXT,cluster_day TEXT,cluster_key TEXT,PRIMARY KEY(source,event_key));
    CREATE TABLE IF NOT EXISTS research_feature_attribution(
      source TEXT,version TEXT,feature TEXT,split TEXT,threshold REAL,threshold_source TEXT,
      high_n INTEGER,high_hits INTEGER,high_rate REAL,low_n INTEGER,low_hits INTEGER,low_rate REAL,
      lift REAL,diff REAL,ci_low REAL,ci_high REAL,p_value REAL,q_value REAL,
      PRIMARY KEY(source,version,feature,split));
    CREATE TABLE IF NOT EXISTS research_feature_correlation(
      source TEXT,version TEXT,feature_a TEXT,feature_b TEXT,n INTEGER,rho REAL,
      redundant INTEGER,PRIMARY KEY(source,version,feature_a,feature_b));
    CREATE TABLE IF NOT EXISTS research_significance(
      source TEXT,version TEXT,split TEXT,test_name TEXT,candidate_n INTEGER,candidate_hits INTEGER,
      control_n INTEGER,control_hits INTEGER,diff REAL,ci_low REAL,ci_high REAL,
      p_value REAL,cluster_ci_low REAL,cluster_ci_high REAL,cluster_p_value REAL,
      PRIMARY KEY(source,version,split,test_name));
    CREATE TABLE IF NOT EXISTS research_dependence_stats(
      source TEXT,version TEXT,split TEXT,group_type TEXT,raw_n INTEGER,cluster_n INTEGER,
      effective_n REAL,max_cluster_size INTEGER,
      PRIMARY KEY(source,version,split,group_type));
    CREATE TABLE IF NOT EXISTS research_baselines(
      source TEXT,version TEXT,split TEXT,baseline TEXT,n INTEGER,hits INTEGER,rate REAL,
      expectancy REAL,PRIMARY KEY(source,version,split,baseline));
    CREATE TABLE IF NOT EXISTS research_portfolio_metrics(
      source TEXT,version TEXT,split TEXT,strategy TEXT,trades INTEGER,final_equity REAL,total_return REAL,
      max_drawdown REAL,sharpe REAL,sortino REAL,profit_factor REAL,
      PRIMARY KEY(source,version,split,strategy));
    CREATE TABLE IF NOT EXISTS research_regime_stats(
      source TEXT,version TEXT,split TEXT,regime TEXT,group_type TEXT,n INTEGER,hits INTEGER,
      rate REAL,ci_low REAL,ci_high REAL,PRIMARY KEY(source,version,split,regime,group_type));
    CREATE TABLE IF NOT EXISTS research_drift_metrics(
      source TEXT,version TEXT,feature TEXT,psi REAL,psi_status TEXT,cusum_alarms INTEGER,
      discovery_n INTEGER,recent_n INTEGER,PRIMARY KEY(source,version,feature));
    CREATE TABLE IF NOT EXISTS research_precision_recall(
      source TEXT,version TEXT,split TEXT,score_threshold REAL,tp INTEGER,fp INTEGER,fn INTEGER,
      precision REAL,recall REAL,f1 REAL,PRIMARY KEY(source,version,split,score_threshold));
    CREATE TABLE IF NOT EXISTS research_kelly(
      source TEXT,version TEXT,split TEXT,n INTEGER,win_rate REAL,avg_win REAL,avg_loss REAL,
      raw_kelly REAL,half_kelly REAL,quarter_kelly REAL,capped_quarter_kelly REAL,
      PRIMARY KEY(source,version,split));
    CREATE TABLE IF NOT EXISTS research_mover_recall(
      source TEXT,version TEXT,split TEXT,definition TEXT,opportunity_n INTEGER,caught_n INTEGER,
      recall REAL,miss_rate REAL,median_lead_minutes REAL,
      PRIMARY KEY(source,version,split,definition));
    CREATE TABLE IF NOT EXISTS research_latency_summary(
      source TEXT,version TEXT,provider TEXT,n INTEGER,p50_ms REAL,p95_ms REAL,max_ms REAL,
      error_rate REAL,age_p50_ms REAL,age_p95_ms REAL,
      PRIMARY KEY(source,version,provider));
    CREATE TABLE IF NOT EXISTS research_experiment_registry(
      source TEXT,version TEXT,family TEXT,hypothesis TEXT,split TEXT,created_utc TEXT,
      selection_eligible INTEGER,notes TEXT,
      PRIMARY KEY(source,version,family,hypothesis,split));
    CREATE TABLE IF NOT EXISTS research_purged_folds(
      source TEXT,version TEXT,fold INTEGER,event_key TEXT,role TEXT,event_time TEXT,
      PRIMARY KEY(source,version,fold,event_key));
    """)
    c.commit()

def target_from_json(text,target=PRIMARY_TARGET):
    try:d=json.loads(text or "{}")
    except Exception:return None
    for k in (str(target),f"{target:.1f}"):
        v=d.get(k)
        if isinstance(v,bool):return int(v)
        if isinstance(v,(int,float)):return int(bool(v))
        if isinstance(v,str):return int(v.upper() in ("TARGET","TARGET_FIRST","TRUE","1"))
    return None

def load_binance(c):
    if not table(c,"signal_events") or not table(c,"outcome_labels"):return []
    sf=cols(c,"signal_events"); of=cols(c,"outcome_labels")
    reachcol="reach_json" if "reach_json" in of else None
    raw=c.execute("""SELECT s.*,o.* FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
      WHERE o.label_status='CLOSED' AND s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
      ORDER BY s.signal_time_utc""").fetchall()
    out=[]
    for r in raw:
        d=dict(r); feats={}
        fr=c.execute("SELECT * FROM features WHERE scan_time_utc=? AND symbol=? ORDER BY is_selected DESC LIMIT 1",
                    (d.get("signal_time_utc"),d.get("symbol"))).fetchone() if table(c,"features") else None
        if fr:
            fd=dict(fr)
            for k in BINANCE_FEATURES:
                if k in fd:feats[k]=f(fd.get(k))
        hit=target_from_json(d.get(reachcol)) if reachcol else None
        if hit is None:
            try:
                bj=json.loads(d.get("barrier_results_json") or "{}")
                hit=int(str(bj.get("72",{}).get("10.0",{}).get("result","")).upper()=="TARGET")
            except Exception: hit=0
        out.append({
          "key":str(d.get("event_id")),"time":d.get("signal_time_utc"),"asset":d.get("symbol"),
          "group":d.get("event_class"),"regime":d.get("btc_regime") or "UNKNOWN",
          "net":f(d.get("net_return_pct")),"hit":int(hit or 0),"score":f(d.get("score")),
          "features":feats,
        })
    return out

def load_gate(obs,val):
    if not table(val,"validation_events"):return []
    vc=cols(val,"validation_events")
    rows=val.execute("""SELECT * FROM validation_events WHERE status='CLOSED_72H'
      AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
      ORDER BY signal_ts,id""").fetchall()
    out=[]
    for r in rows:
        d=dict(r); feats={}
        er=None
        if table(obs,"gate_early_observations"):
            qcols=cols(obs,"gate_early_observations")
            er=obs.execute("""SELECT * FROM gate_early_observations
              WHERE batch_id=? AND network_id=? AND token_contract=? LIMIT 1""",
              (d.get("batch_id"),d.get("network_id"),d.get("token_contract"))).fetchone()
            if er:
                ed=dict(er)
                for k in GATE_FEATURES:
                    if k in ed:feats[k]=f(ed.get(k))
                buys=f(ed.get("buys_5m")); sells=f(ed.get("sells_5m"))
                if buys is not None and sells is not None:
                    feats["buy_sell_ratio_5m"]=buys/max(1.0,sells)
        hit=int(str(d.get("result_10") or "").upper()=="TARGET_FIRST")
        net=f(d.get("net_final_pct"))
        if d.get("cost_status") not in (None,"QUOTE_PLUS_ASSUMPTION","EXECUTABLE","OK"):
            # Keep hit outcome, but do not pretend cost-aware return is known.
            net=None
        out.append({
          "key":str(d.get("id")),"time":ts_iso(d.get("signal_ts")),
          "asset":f"{d.get('network_id')}:{d.get('token_contract')}",
          "group":d.get("group_type"),"regime":d.get("btc_regime") or "UNKNOWN",
          "net":net,"hit":hit,"score":f(d.get("score")),"features":feats,
        })
    return out

def store_splits(c,source,rows):
    for r in rows:
        t=dt(r["time"]); day=t.date().isoformat() if t else "UNKNOWN"
        sp=split_of(r["time"]); cluster=f"{day}|{r['regime']}"
        c.execute("""INSERT OR REPLACE INTO research_split_registry VALUES(?,?,?,?,?,?,?,?,?)""",
          (source,r["key"],r["time"],sp,r["group"],r["asset"],r["regime"],day,cluster))
    c.commit()

def is_candidate(source,g):
    return g=="CANDIDATE" if source=="BINANCE" else g in ("CANDIDATE","EXPANDED_CANDIDATE")

def build_purged_folds(c,source,rows,k=5):
    """Create reusable chronological purged folds from non-final research data."""
    c.execute("DELETE FROM research_purged_folds WHERE source=? AND version=?",(source,VERSION))
    eligible=sorted([r for r in rows if split_of(r["time"]) in ("DISCOVERY","CALIBRATION") and dt(r["time"])],
                    key=lambda r:dt(r["time"]))
    if len(eligible)<k:
        c.commit(); return
    n=len(eligible)
    for fold in range(k):
        lo=fold*n//k; hi=(fold+1)*n//k
        test=eligible[lo:hi]
        if not test:continue
        t0=dt(test[0]["time"]); t1=dt(test[-1]["time"])
        purge0=t0-timedelta(hours=PURGE_HOURS)
        embargo1=t1+timedelta(hours=EMBARGO_HOURS)
        testkeys={r["key"] for r in test}
        for r in eligible:
            t=dt(r["time"])
            if r["key"] in testkeys:
                role="TEST"
            elif purge0<=t<=embargo1:
                role="PURGED_EMBARGO"
            else:
                role="TRAIN"
            c.execute("INSERT INTO research_purged_folds VALUES(?,?,?,?,?,?)",
                      (source,VERSION,fold,r["key"],role,r["time"]))
    c.commit()


def feature_thresholds(rows):
    disc=[r for r in rows if split_of(r["time"])=="DISCOVERY"]
    names=sorted({k for r in disc for k in r["features"]})
    out={}
    for k in names:
        vals=[r["features"].get(k) for r in disc if r["features"].get(k) is not None]
        if len(vals)>=8:out[k]=median(vals)
    return out

def attribution(c,source,rows):
    thresholds=feature_thresholds(rows)
    all_rows=[]
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        sr=[r for r in rows if split_of(r["time"])==sp]
        for feat,thr in thresholds.items():
            hi=[r for r in sr if r["features"].get(feat) is not None and r["features"][feat]>=thr]
            lo=[r for r in sr if r["features"].get(feat) is not None and r["features"][feat]<thr]
            hh=sum(r["hit"] for r in hi); lh=sum(r["hit"] for r in lo)
            hr=hh/len(hi) if hi else None; lr=lh/len(lo) if lo else None
            diff=(hr-lr) if hr is not None and lr is not None else None
            ci1=ci2=bp=None
            if hi and lo: ci1,ci2,bp=bootstrap_diff([r["hit"] for r in hi],[r["hit"] for r in lo])
            p=proportion_p(hh,len(hi),lh,len(lo)) if len(hi)>=8 and len(lo)>=8 else None
            rec={"feature":feat,"split":sp,"threshold":thr,"high_n":len(hi),"high_hits":hh,
                 "high_rate":hr,"low_n":len(lo),"low_hits":lh,"low_rate":lr,
                 "lift":(hr/lr if hr is not None and lr not in (None,0) else None),
                 "diff":diff,"ci_low":ci1,"ci_high":ci2,"p_value":p}
            all_rows.append(rec)
    for sp in ("CALIBRATION","FINAL_TEST"):
        bh_adjust([r for r in all_rows if r["split"]==sp])
    c.execute("DELETE FROM research_feature_attribution WHERE source=? AND version=?",(source,VERSION))
    for r in all_rows:
        c.execute("""INSERT INTO research_feature_attribution VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (source,VERSION,r["feature"],r["split"],r["threshold"],"DISCOVERY_MEDIAN",
           r["high_n"],r["high_hits"],r["high_rate"],r["low_n"],r["low_hits"],r["low_rate"],
           r["lift"],r["diff"],r["ci_low"],r["ci_high"],r["p_value"],r.get("q_value")))
        c.execute("""INSERT OR REPLACE INTO research_experiment_registry VALUES(?,?,?,?,?,?,?,?)""",
          (source,VERSION,"UNIVARIATE_FEATURE",r["feature"],r["split"],now(),
           1 if r["split"]=="CALIBRATION" else 0,
           "Threshold frozen from DISCOVERY median; FINAL_TEST is report-only."))
    c.commit()
    return thresholds

def correlations(c,source,rows,thresholds):
    disc=[r for r in rows if split_of(r["time"])=="DISCOVERY"]
    feats=sorted(thresholds)
    c.execute("DELETE FROM research_feature_correlation WHERE source=? AND version=?",(source,VERSION))
    edges=[]
    for i,a in enumerate(feats):
        for b in feats[i+1:]:
            pairs=[(r["features"].get(a),r["features"].get(b)) for r in disc
                   if r["features"].get(a) is not None and r["features"].get(b) is not None]
            if len(pairs)<8:continue
            rho=spearman([x for x,_ in pairs],[y for _,y in pairs])
            red=int(rho is not None and abs(rho)>=CORR_THRESHOLD)
            if red:edges.append((a,b))
            c.execute("INSERT INTO research_feature_correlation VALUES(?,?,?,?,?,?,?)",
                      (source,VERSION,a,b,len(pairs),rho,red))
    c.commit()
    # connected components = approximate independent information families
    parent={x:x for x in feats}
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]]; x=parent[x]
        return x
    def union(a,b):
        a=find(a); b=find(b)
        if a!=b:parent[b]=a
    for a,b in edges:union(a,b)
    return len({find(x) for x in feats}) if feats else 0

def significance(c,source,rows):
    c.execute("DELETE FROM research_significance WHERE source=? AND version=?",(source,VERSION))
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        sr=[r for r in rows if split_of(r["time"])==sp]
        ca=[r for r in sr if is_candidate(source,r["group"])]
        co=[r for r in sr if not is_candidate(source,r["group"])]
        ch=sum(r["hit"] for r in ca); oh=sum(r["hit"] for r in co)
        cr=ch/len(ca) if ca else None; rr=oh/len(co) if co else None
        diff=(cr-rr) if cr is not None and rr is not None else None
        lo=hi=bp=None
        if ca and co:lo,hi,bp=bootstrap_diff([r["hit"] for r in ca],[r["hit"] for r in co])
        p=proportion_p(ch,len(ca),oh,len(co)) if len(ca)>=8 and len(co)>=8 else None
        ac=defaultdict(list); bc=defaultdict(list)
        for r in ca:
            t=dt(r["time"]); ac[f"{t.date().isoformat() if t else 'UNKNOWN'}|{r['regime']}"].append(r["hit"])
        for r in co:
            t=dt(r["time"]); bc[f"{t.date().isoformat() if t else 'UNKNOWN'}|{r['regime']}"].append(r["hit"])
        clo,chi,cp=bootstrap_diff(ac,bc,blocks=True)
        c.execute("""INSERT INTO research_significance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (source,VERSION,sp,"CANDIDATE_VS_CONTROL_10",len(ca),ch,len(co),oh,diff,lo,hi,
           p,clo,chi,cp))
    c.commit()

def dependence(c,source,rows):
    c.execute("DELETE FROM research_dependence_stats WHERE source=? AND version=?",(source,VERSION))
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        sr=[r for r in rows if split_of(r["time"])==sp]
        for name,pred in (("CANDIDATE",lambda r:is_candidate(source,r["group"])),
                          ("CONTROL",lambda r:not is_candidate(source,r["group"]))):
            g=[r for r in sr if pred(r)]
            clusters=defaultdict(int)
            for r in g:
                t=dt(r["time"])
                key=f"{t.date().isoformat() if t else 'UNKNOWN'}|{r['regime']}"
                clusters[key]+=1
            sizes=list(clusters.values())
            raw=len(g)
            eff=(raw*raw/sum(x*x for x in sizes)) if sizes else 0.0
            c.execute("INSERT INTO research_dependence_stats VALUES(?,?,?,?,?,?,?,?)",
                      (source,VERSION,sp,name,raw,len(sizes),eff,max(sizes) if sizes else 0))
    c.commit()

def baselines(c,source,rows):
    c.execute("DELETE FROM research_baselines WHERE source=? AND version=?",(source,VERSION))
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        sr=[r for r in rows if split_of(r["time"])==sp]
        choices={"AVCI":[r for r in sr if is_candidate(source,r["group"])]}
        # Simple contemporaneous baselines using only one raw feature.
        def top_by(feature):
            good=[r for r in sr if r["features"].get(feature) is not None]
            if not good:return []
            byday=defaultdict(list)
            for r in good:
                t=dt(r["time"]); byday[t.date().isoformat() if t else "UNKNOWN"].append(r)
            out=[]
            for g in byday.values():
                out.extend(sorted(g,key=lambda x:x["features"][feature],reverse=True)[:10])
            return out
        if source=="BINANCE":
            choices["VOLUME_ONLY"]=top_by("volume_z_15m")
            choices["MOMENTUM_ONLY"]=top_by("change_1h")
            choices["BTC_REL_STRENGTH_ONLY"]=top_by("btc_relative_24h")
        else:
            choices["VOLUME_ONLY"]=top_by("own_volume_ratio")
            choices["MOMENTUM_ONLY"]=top_by("change_1h")
            choices["LIQUIDITY_ONLY"]=top_by("liquidity")
        choices["RANDOM_CONTROL"]=[r for r in sr if r["group"]=="RANDOM_CONTROL"]
        choices["ALL_CONTROLS"]=[r for r in sr if not is_candidate(source,r["group"])]
        for name,g in choices.items():
            vals=[r["net"] for r in g if r["net"] is not None]
            c.execute("INSERT INTO research_baselines VALUES(?,?,?,?,?,?,?,?)",
                      (source,VERSION,sp,name,len(g),sum(r["hit"] for r in g),
                       mean([r["hit"] for r in g]) if g else None,mean(vals)))
    c.commit()

def portfolio(c,source,rows):
    """Slot-based paper portfolio with 72h capital lock per opened signal."""
    c.execute("DELETE FROM research_portfolio_metrics WHERE source=? AND version=?",(source,VERSION))
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        for strat in ("AVCI","VOLUME_ONLY"):
            sr=[r for r in rows if split_of(r["time"])==sp]
            if strat=="AVCI":
                trades=[r for r in sr if is_candidate(source,r["group"]) and r["net"] is not None]
            else:
                feature="volume_z_15m" if source=="BINANCE" else "own_volume_ratio"
                good=[r for r in sr if r["net"] is not None and r["features"].get(feature) is not None]
                byday=defaultdict(list)
                for r in good:
                    t=dt(r["time"]); byday[t.date().isoformat() if t else "UNKNOWN"].append(r)
                trades=[]
                for g in byday.values():
                    trades.extend(sorted(g,key=lambda x:x["features"][feature],reverse=True)[:10])
            trades=sorted([r for r in trades if dt(r["time"])],key=lambda r:dt(r["time"]))
            eq=PORTFOLIO_START_EQUITY; peak=eq; mdd=0.0; pos=[]; neg=[]; count=0
            active=[]; realized_by_day=defaultdict(float)
            def close_due(t):
                nonlocal eq,peak,mdd
                remain=[]
                for item in active:
                    if item["close"]<=t:
                        pnl=item["allocation"]*(item["net"]/100.0)
                        eq+=pnl
                        realized_by_day[item["close"].date().isoformat()]+=pnl/max(PORTFOLIO_START_EQUITY,1e-9)
                        peak=max(peak,eq); mdd=max(mdd,(peak-eq)/peak if peak else 0)
                    else:
                        remain.append(item)
                active[:]=remain
            for r in trades:
                t=dt(r["time"]); close_due(t)
                if len(active)>=PORTFOLIO_MAX_POSITIONS:
                    continue
                allocation=eq*min(0.10,PORTFOLIO_RISK_FRACTION*5)
                active.append({"close":t+timedelta(hours=LABEL_HORIZON_HOURS),
                               "allocation":allocation,"net":r["net"]})
                count+=1
                if r["net"]>0:pos.append(r["net"])
                elif r["net"]<0:neg.append(r["net"])
            close_due(datetime.max.replace(tzinfo=timezone.utc))
            daily=[realized_by_day[k] for k in sorted(realized_by_day)]
            mu=mean(daily); sd=stdev(daily)
            downside=math.sqrt(mean([min(0,x)**2 for x in daily])) if daily else None
            sharpe=(mu/sd*math.sqrt(365)) if mu is not None and sd not in (None,0) else None
            sortino=(mu/downside*math.sqrt(365)) if mu is not None and downside not in (None,0) else None
            pf=(sum(pos)/(-sum(neg))) if neg else (999.0 if pos else None)
            c.execute("""INSERT INTO research_portfolio_metrics VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (source,VERSION,sp,strat,count,eq,eq/PORTFOLIO_START_EQUITY-1,mdd,sharpe,sortino,pf))
    c.commit()

def regimes(c,source,rows):
    c.execute("DELETE FROM research_regime_stats WHERE source=? AND version=?",(source,VERSION))
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        sr=[r for r in rows if split_of(r["time"])==sp]
        for reg in sorted({r["regime"] for r in sr}):
            for gname,fn in (("CANDIDATE",lambda r:is_candidate(source,r["group"])),
                             ("CONTROL",lambda r:not is_candidate(source,r["group"]))):
                g=[r for r in sr if r["regime"]==reg and fn(r)]
                h=sum(r["hit"] for r in g); lo,hi=wilson(h,len(g))
                c.execute("INSERT INTO research_regime_stats VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (source,VERSION,sp,reg,gname,len(g),h,h/len(g) if g else None,lo,hi))
    c.commit()

def drift(c,source,rows,thresholds):
    c.execute("DELETE FROM research_drift_metrics WHERE source=? AND version=?",(source,VERSION))
    disc=[r for r in rows if split_of(r["time"])=="DISCOVERY"]
    recent=[r for r in rows if split_of(r["time"]) in ("CALIBRATION","FINAL_TEST")]
    for feat in thresholds:
        a=[r["features"].get(feat) for r in disc if r["features"].get(feat) is not None]
        b=[r["features"].get(feat) for r in recent if r["features"].get(feat) is not None]
        pv=psi(a,b)
        status="INSUFFICIENT" if pv is None else ("HIGH" if pv>=PSI_HIGH else ("WARN" if pv>=PSI_WARN else "OK"))
        ordered=[r["features"].get(feat) for r in rows if r["features"].get(feat) is not None]
        ca=cusum(ordered)
        c.execute("INSERT INTO research_drift_metrics VALUES(?,?,?,?,?,?,?,?)",
                  (source,VERSION,feat,pv,status,ca,len(a),len(b)))
    c.commit()

def precision_recall(c,source,rows):
    c.execute("DELETE FROM research_precision_recall WHERE source=? AND version=?",(source,VERSION))
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        sr=[r for r in rows if split_of(r["time"])==sp]
        scores=[r["score"] for r in sr if r["score"] is not None]
        if scores:
            thresholds=sorted(set([min(scores),quantile(scores,.25),quantile(scores,.5),quantile(scores,.75),max(scores)]))
            for th in thresholds:
                pred=[r for r in sr if r["score"] is not None and r["score"]>=th]
                tp=sum(r["hit"] for r in pred); fp=len(pred)-tp
                positives=sum(r["hit"] for r in sr); fn=max(0,positives-tp)
                pr=tp/(tp+fp) if tp+fp else None; rc=tp/(tp+fn) if tp+fn else None
                f1=2*pr*rc/(pr+rc) if pr is not None and rc is not None and pr+rc else None
                c.execute("INSERT INTO research_precision_recall VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (source,VERSION,sp,th,tp,fp,fn,pr,rc,f1))
        else:
            # If Gate score is absent, at least report current frozen candidate classifier.
            pred=[r for r in sr if is_candidate(source,r["group"])]
            tp=sum(r["hit"] for r in pred); fp=len(pred)-tp
            positives=sum(r["hit"] for r in sr); fn=max(0,positives-tp)
            pr=tp/(tp+fp) if tp+fp else None; rc=tp/(tp+fn) if tp+fn else None
            f1=2*pr*rc/(pr+rc) if pr is not None and rc is not None and pr+rc else None
            c.execute("INSERT INTO research_precision_recall VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (source,VERSION,sp,-1,tp,fp,fn,pr,rc,f1))
    c.commit()
def mover_recall(c,source,rows):
    """Recall of actual archived mover opportunities when the source supports it."""
    c.execute("DELETE FROM research_mover_recall WHERE source=? AND version=?",(source,VERSION))
    if source=="BINANCE" and table(c,"daily_movers"):
        movers=c.execute("""SELECT trade_date,symbol,change_24h FROM daily_movers
          WHERE change_24h>=40 ORDER BY trade_date,symbol""").fetchall()
        bysplit=defaultdict(list)
        candidates=[r for r in rows if is_candidate(source,r["group"])]
        for m in movers:
            end=dt(str(m["trade_date"])+"T23:59:59+00:00")
            if not end:continue
            sp=split_of(end.isoformat())
            if sp not in ("DISCOVERY","CALIBRATION","FINAL_TEST"):continue
            hits=[]
            for r in candidates:
                t=dt(r["time"])
                if r["asset"]==m["symbol"] and t and end-timedelta(hours=72)<=t<=end:
                    hits.append((end-t).total_seconds()/60.0)
            bysplit[sp].append(min(hits) if hits else None)
        for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
            vals=bysplit.get(sp,[])
            caught=[x for x in vals if x is not None]
            n=len(vals)
            c.execute("INSERT INTO research_mover_recall VALUES(?,?,?,?,?,?,?,?,?)",
                      (source,VERSION,sp,"DAILY_24H_CHANGE_GTE_40",n,len(caught),
                       len(caught)/n if n else None,1-len(caught)/n if n else None,
                       median(caught)))
    else:
        # Gate currently has a broader observed universe with explicit missed-mover flags.
        # This is not identical to Binance daily-mover recall, so it is labelled separately.
        if table(c,"gate_opportunity_observations") and table(c,"gate_spot_history"):
            rows2=c.execute("""SELECT o.missed_mover,h.change_24h
              FROM gate_opportunity_observations o
              JOIN gate_spot_history h ON h.batch_id=o.batch_id AND h.pair=o.pair
              WHERE h.change_24h>=15""").fetchall()
            n=len(rows2); missed=sum(int(r[0] or 0) for r in rows2); caught=max(0,n-missed)
            c.execute("INSERT INTO research_mover_recall VALUES(?,?,?,?,?,?,?,?,?)",
                      (source,VERSION,"ALL_OBSERVED","GATE_CHANGE_24H_GTE_15",n,caught,
                       caught/n if n else None,missed/n if n else None,None))
    c.commit()


def kelly(c,source,rows):
    c.execute("DELETE FROM research_kelly WHERE source=? AND version=?",(source,VERSION))
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        vals=[r["net"] for r in rows if split_of(r["time"])==sp and is_candidate(source,r["group"]) and r["net"] is not None]
        wins=[x for x in vals if x>0]; losses=[x for x in vals if x<0]
        wr=len(wins)/len(vals) if vals else None; aw=mean(wins); al=-mean(losses) if losses else None
        raw=None
        if wr is not None and aw and al and al>0:
            b=aw/al; raw=wr-(1-wr)/b
        half=raw/2 if raw is not None else None; quarter=raw/4 if raw is not None else None
        capped=max(0.0,min(KELLY_CAP,quarter)) if quarter is not None else None
        c.execute("INSERT INTO research_kelly VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (source,VERSION,sp,len(vals),wr,aw,al,raw,half,quarter,capped))
    c.commit()

def latency(c,source):
    c.execute("DELETE FROM research_latency_summary WHERE source=? AND version=?",(source,VERSION))
    if not table(c,"research_source_latency"):c.commit(); return
    rows=c.execute("""SELECT provider,latency_ms,age_at_receive_ms,status FROM research_source_latency
      WHERE source_engine=? ORDER BY received_utc""",(source,)).fetchall()
    by=defaultdict(list)
    for r in rows:by[r["provider"]].append(dict(r))
    for provider,g in by.items():
        lat=[f(r["latency_ms"]) for r in g if f(r["latency_ms"]) is not None]
        ages=[f(r["age_at_receive_ms"]) for r in g if f(r["age_at_receive_ms"]) is not None]
        c.execute("INSERT INTO research_latency_summary VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (source,VERSION,provider,len(g),median(lat),quantile(lat,.95),max(lat) if lat else None,
                   sum(r["status"]!="OK" for r in g)/len(g),median(ages),quantile(ages,.95)))
    c.commit()

def register_existing_experiments(c,source):
    """Central trial ledger: count every predeclared test family, not only winners."""
    if source=="BINANCE" and table(c,"binance_activation_math_results"):
        for r in c.execute("SELECT split,combo,target_pct FROM binance_activation_math_results"):
            c.execute("""INSERT OR REPLACE INTO research_experiment_registry VALUES(?,?,?,?,?,?,?,?)""",
                      (source,VERSION,"ACTIVATION_COMBO",f"{r['combo']}|+{r['target_pct']}",
                       r["split"],now(),1 if r["split"]=="VALIDATION" else 0,
                       "Imported from frozen Binance activation family."))
    if source=="GATE" and table(c,"gate_activation_math_results"):
        for r in c.execute("SELECT split,combo,target_pct FROM gate_activation_math_results"):
            c.execute("""INSERT OR REPLACE INTO research_experiment_registry VALUES(?,?,?,?,?,?,?,?)""",
                      (source,VERSION,"ACTIVATION_COMBO",f"{r['combo']}|+{r['target_pct']}",
                       r["split"],now(),1 if r["split"]=="VALIDATION" else 0,
                       "Imported from frozen Gate activation family."))
    c.commit()

def report(c,source,rows,effective_families):
    def pctv(x): return "-" if x is None else f"%{100*x:.1f}"
    lines=[f"# {source} AVCI — Research Validation Layer", "",
           f"- Version: {VERSION}",
           f"- Discovery bitiş: {DISCOVERY_END_UTC}",
           f"- Calibration bitiş: {CALIBRATION_END_UTC}",
           f"- Purge/embargo: {PURGE_HOURS} saat outcome purge + {EMBARGO_HOURS} saat embargo",
           "- FINAL_TEST: kilitli; sonuçları kural/eşik seçmek için kullanılamaz.",
           f"- Toplam kapanmış event: {len(rows)}",
           f"- Feature bağımsız bilgi ailesi (yaklaşık): {effective_families}",
           f"- Kayıtlı hipotez/test sayısı: {c.execute('SELECT COUNT(*) FROM research_experiment_registry WHERE source=? AND version=?',(source,VERSION)).fetchone()[0]}",
           f"- Purged fold satırı: {c.execute('SELECT COUNT(*) FROM research_purged_folds WHERE source=? AND version=?',(source,VERSION)).fetchone()[0]}", ""]
    lines += ["## Candidate vs control (+10)", ""]
    for r in c.execute("""SELECT * FROM research_significance WHERE source=? AND version=?
      ORDER BY CASE split WHEN 'DISCOVERY' THEN 1 WHEN 'CALIBRATION' THEN 2 ELSE 3 END""",(source,VERSION)):
        lines.append(f"- {r['split']}: aday {r['candidate_hits']}/{r['candidate_n']} vs kontrol "
                     f"{r['control_hits']}/{r['control_n']} | fark {pctv(r['diff'])} | "
                     f"bootstrap CI [{pctv(r['ci_low'])}, {pctv(r['ci_high'])}] | "
                     f"cluster CI [{pctv(r['cluster_ci_low'])}, {pctv(r['cluster_ci_high'])}]")
    lines += ["", "## Bağımlılık / effective n", ""]
    for r in c.execute("""SELECT * FROM research_dependence_stats WHERE source=? AND version=?
      ORDER BY split,group_type""",(source,VERSION)):
        lines.append(f"- {r['split']} / {r['group_type']}: raw n={r['raw_n']} | "
                     f"cluster={r['cluster_n']} | effective n={r['effective_n']:.2f} | "
                     f"max cluster={r['max_cluster_size']}")
    lines += ["", "## Rejim örneklemi", ""]
    for r in c.execute("""SELECT * FROM research_regime_stats WHERE source=? AND version=? AND split='FINAL_TEST'
      ORDER BY regime,group_type""",(source,VERSION)):
        lines.append(f"- {r['regime']} / {r['group_type']}: {r['hits']}/{r['n']} = {pctv(r['rate'])} "
                     f"(Wilson %95 {pctv(r['ci_low'])}–{pctv(r['ci_high'])})")
    lines += ["", "## Drift", ""]
    for r in c.execute("""SELECT * FROM research_drift_metrics WHERE source=? AND version=?
      ORDER BY CASE psi_status WHEN 'HIGH' THEN 0 WHEN 'WARN' THEN 1 ELSE 2 END, psi DESC LIMIT 10""",(source,VERSION)):
        lines.append(f"- {r['feature']}: PSI={r['psi'] if r['psi'] is not None else '-'} "
                     f"[{r['psi_status']}] | CUSUM alarm={r['cusum_alarms']}")
    lines += ["", "## Feature katkısı (CALIBRATION)", ""]
    for r in c.execute("""SELECT * FROM research_feature_attribution WHERE source=? AND version=? AND split='CALIBRATION'
      ORDER BY CASE WHEN q_value IS NULL THEN 1 ELSE 0 END,q_value ASC,ABS(diff) DESC LIMIT 12""",(source,VERSION)):
        lines.append(f"- {r['feature']}: high {r['high_hits']}/{r['high_n']} vs low {r['low_hits']}/{r['low_n']} "
                     f"| lift={r['lift']} | diff={pctv(r['diff'])} | q={r['q_value']}")
    lines += ["", "## Baselines (FINAL_TEST)", ""]
    for r in c.execute("""SELECT * FROM research_baselines WHERE source=? AND version=? AND split='FINAL_TEST'
      ORDER BY baseline""",(source,VERSION)):
        lines.append(f"- {r['baseline']}: {r['hits']}/{r['n']} = {pctv(r['rate'])} | expectancy={r['expectancy']}")
    lines += ["", "## Precision / Recall (FINAL_TEST)", ""]
    for r in c.execute("""SELECT * FROM research_precision_recall WHERE source=? AND version=? AND split='FINAL_TEST'
      ORDER BY score_threshold""",(source,VERSION)):
        lines.append(f"- threshold={r['score_threshold']}: precision={pctv(r['precision'])} | "
                     f"recall={pctv(r['recall'])} | F1={r['f1']} | TP/FP/FN={r['tp']}/{r['fp']}/{r['fn']}")
    lines += ["", "## Gerçek mover recall", ""]
    for r in c.execute("""SELECT * FROM research_mover_recall WHERE source=? AND version=? ORDER BY split""",(source,VERSION)):
        lines.append(f"- {r['split']} / {r['definition']}: {r['caught_n']}/{r['opportunity_n']} "
                     f"| recall={pctv(r['recall'])} | miss={pctv(r['miss_rate'])} | lead_min={r['median_lead_minutes']}")
    lines += ["", "## Kaynak latency / data age", ""]
    for r in c.execute("""SELECT * FROM research_latency_summary WHERE source=? AND version=? ORDER BY provider""",(source,VERSION)):
        lines.append(f"- {r['provider']}: n={r['n']} | latency p50/p95={r['p50_ms']}/{r['p95_ms']} ms "
                     f"| data-age p50/p95={r['age_p50_ms']}/{r['age_p95_ms']} ms | error={pctv(r['error_rate'])}")
    lines += ["", "## Kelly (yalnız araştırma)", ""]
    for r in c.execute("""SELECT * FROM research_kelly WHERE source=? AND version=? ORDER BY split""",(source,VERSION)):
        lines.append(f"- {r['split']}: n={r['n']} | raw={r['raw_kelly']} | half={r['half_kelly']} | "
                     f"quarter={r['quarter_kelly']} | capped-quarter={r['capped_quarter_kelly']}")
    lines += ["", "## Portföy", ""]
    for r in c.execute("""SELECT * FROM research_portfolio_metrics WHERE source=? AND version=? AND split='FINAL_TEST'
      ORDER BY strategy""",(source,VERSION)):
        lines.append(f"- {r['strategy']}: trade={r['trades']} | getiri={pctv(r['total_return'])} | "
                     f"maxDD={pctv(r['max_drawdown'])} | Sharpe={r['sharpe']} | Sortino={r['sortino']}")
    lines += ["", "## Not", "",
              "Bu katman araştırma ölçümüdür. Frozen Avcı kurallarını değiştirmez; "
              "FINAL_TEST sonuçlarından yeni eşik seçilmez. Yeni fikirler yeni sürümde yeniden discovery ile başlar."]
    return "\n".join(lines)+"\n"

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").upper()
    if mode not in ("BINANCE","GATE"):raise SystemExit("usage: research_validation_layer.py binance|gate")
    if mode=="BINANCE":
        db=os.getenv("BINANCE_DB","binance_avci2.db")
        if not os.path.exists(db):print("Binance DB yok");return
        with sqlite3.connect(db,timeout=60) as c:
            c.row_factory=sqlite3.Row; init(c); rows=load_binance(c)
            store_splits(c,mode,rows); build_purged_folds(c,mode,rows); th=attribution(c,mode,rows); ef=correlations(c,mode,rows,th)
            significance(c,mode,rows); dependence(c,mode,rows); baselines(c,mode,rows); portfolio(c,mode,rows)
            regimes(c,mode,rows); drift(c,mode,rows,th); precision_recall(c,mode,rows)
            mover_recall(c,mode,rows); kelly(c,mode,rows); latency(c,mode); register_existing_experiments(c,mode)
            c.execute("INSERT OR REPLACE INTO research_validation_runs VALUES(?,?,?,?,?,?,?)",
                      (mode,VERSION,now(),DISCOVERY_END_UTC,CALIBRATION_END_UTC,1,
                       json.dumps({"rules_mutated":False,"final_test_selection_eligible":False,
                                   "portfolio_is_paper":True},sort_keys=True)))
            c.commit(); out=report(c,mode,rows,ef)
        open("binance_research_validation.md","w",encoding="utf-8").write(out); print(out)
    else:
        odb=os.getenv("AVCI_DB","avci2.db"); vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
        if not os.path.exists(odb) or not os.path.exists(vdb):print("Gate DB eksik");return
        with sqlite3.connect(odb,timeout=60) as c, sqlite3.connect(vdb,timeout=60) as v:
            c.row_factory=v.row_factory=sqlite3.Row; init(c); rows=load_gate(c,v)
            store_splits(c,mode,rows); build_purged_folds(c,mode,rows); th=attribution(c,mode,rows); ef=correlations(c,mode,rows,th)
            significance(c,mode,rows); dependence(c,mode,rows); baselines(c,mode,rows); portfolio(c,mode,rows)
            regimes(c,mode,rows); drift(c,mode,rows,th); precision_recall(c,mode,rows)
            mover_recall(c,mode,rows); kelly(c,mode,rows); latency(c,mode); register_existing_experiments(c,mode)
            c.execute("INSERT OR REPLACE INTO research_validation_runs VALUES(?,?,?,?,?,?,?)",
                      (mode,VERSION,now(),DISCOVERY_END_UTC,CALIBRATION_END_UTC,1,
                       json.dumps({"rules_mutated":False,"final_test_selection_eligible":False,
                                   "portfolio_is_paper":True},sort_keys=True)))
            c.commit(); out=report(c,mode,rows,ef)
        open("gate_research_validation.md","w",encoding="utf-8").write(out); print(out)

if __name__=="__main__":main()
