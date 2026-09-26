#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Institutional signal overlay for Avci.

Research/reporting only. Does not change frozen scanner membership, thresholds,
scores, or place orders.

Adds:
- empirical Beta-Binomial success probability + Wilson 95% CI + N
- regime-conditioned probability
- cost-aware candidate/control expectancy and clustered-by-week bootstrap CI
- explicit system mode / genesis-contamination disclosure
- volatility/liquidity regime labels
- catalyst/confounder and attention-crowding observations
"""
from __future__ import annotations
import json, math, os, random, sqlite3, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone

VERSION="institutional-overlay-v1-20260926"
PRIMARY_TARGET=10
MIN_N=8
HOLDOUT_START="2026-10-07T00:00:00+00:00"

def now(): return datetime.now(timezone.utc).isoformat()
def table(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def cols(c,t): return {r[1] for r in c.execute(f"PRAGMA table_info({t})")} if table(c,t) else set()
def obj(x):
    try:return json.loads(x or "{}")
    except Exception:return {}
def mean(x): return sum(x)/len(x) if x else None
def dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None

def wilson(k,n,z=1.96):
    if n<=0:return (None,None)
    p=k/n; den=1+z*z/n
    mid=(p+z*z/(2*n))/den
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return max(0,mid-half),min(1,mid+half)

def calibrated(k,n):
    # weak Beta(1,1) prior prevents 0%/100% overconfidence in small samples
    return (k+1)/(n+2) if n>=0 else None

def week_key(t):
    x=dt(t)
    if not x:return "UNKNOWN"
    iso=x.isocalendar(); return f"{iso.year}-W{iso.week:02d}"

def cluster_boot_diff(cand,ctrl,reps=800):
    cand=[r for r in cand if r["net"] is not None]; ctrl=[r for r in ctrl if r["net"] is not None]
    if len(cand)<MIN_N or len(ctrl)<MIN_N:return (None,None,None)
    obs=mean([r["net"] for r in cand])-mean([r["net"] for r in ctrl])
    groups=defaultdict(lambda:{"c":[],"k":[]})
    for r in cand:groups[week_key(r["t"])]["c"].append(r["net"])
    for r in ctrl:groups[week_key(r["t"])]["k"].append(r["net"])
    keys=list(groups)
    if len(keys)<2:return (obs,None,None)
    rng=random.Random(20260926); vals=[]
    for _ in range(reps):
        cc=[];kk=[]
        for key in [rng.choice(keys) for __ in keys]:
            cc.extend(groups[key]["c"]);kk.extend(groups[key]["k"])
        if cc and kk:vals.append(mean(cc)-mean(kk))
    if not vals:return (obs,None,None)
    vals.sort()
    return obs,vals[int(.025*(len(vals)-1))],vals[int(.975*(len(vals)-1))]

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS institutional_signal_overlay(
      source TEXT NOT NULL,batch_key TEXT NOT NULL,asset_key TEXT NOT NULL,
      system_mode TEXT NOT NULL,probability_method TEXT NOT NULL,
      success_probability REAL,success_n INTEGER,success_ci_low REAL,success_ci_high REAL,
      regime_probability REAL,regime_n INTEGER,regime_ci_low REAL,regime_ci_high REAL,
      candidate_net_expectancy_pct REAL,control_net_expectancy_pct REAL,
      expectancy_diff_pct REAL,expectancy_ci_low REAL,expectancy_ci_high REAL,
      volatility_regime TEXT,liquidity_regime TEXT,regime_key TEXT,
      confounders_json TEXT NOT NULL,crowding_status TEXT,crowding_value REAL,
      external_validation_status TEXT,genesis_contamination_note TEXT NOT NULL,
      version_isolation_status TEXT NOT NULL,version TEXT NOT NULL,created_at_utc TEXT NOT NULL,
      PRIMARY KEY(source,batch_key,asset_key,version))""")
    c.commit()

def system_mode(c,source):
    if table(c,"decision_discipline_state"):
        r=c.execute("""SELECT go_live_status,kill_switch_status,time_budget_status
          FROM decision_discipline_state WHERE source=? ORDER BY run_utc DESC LIMIT 1""",(source,)).fetchone()
        if r:
            if r["kill_switch_status"]=="RESEARCH_ONLY":return "ARAŞTIRMA"
            if r["go_live_status"]=="PASS_FOR_TINY_LIVE_REVIEW":return "TINY-LIVE REVIEW"
            return "PAPER"
    return "ARAŞTIRMA"

def b_success(reach,net):
    d=obj(reach)
    for k,v in d.items():
        if str(k).replace("%","").replace("+","")=="10":
            if isinstance(v,bool):return v
            if isinstance(v,(int,float)):return bool(v)
            if isinstance(v,str):return v.upper() in ("TRUE","YES","HIT","TARGET_FIRST","1")
    return (float(net)>0) if net is not None else False

def percentile_regime(value,vals,high_good=False):
    xs=sorted(float(x) for x in vals if x is not None)
    if value is None or len(xs)<5:return "UNKNOWN"
    lo=xs[len(xs)//3]; hi=xs[(2*len(xs))//3]; v=float(value)
    if v<=lo:return "LOW"
    if v>=hi:return "HIGH"
    return "MID"

def binance():
    db=os.getenv("BINANCE_DB","binance_avci2.db")
    if not os.path.exists(db):return
    with sqlite3.connect(db,timeout=60) as c:
        c.row_factory=sqlite3.Row;init(c)
        scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID' ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:return
        ts=scan["scan_time_utc"]; mode=system_mode(c,"BINANCE")
        cur=c.execute("SELECT * FROM features WHERE scan_time_utc=?",(ts,)).fetchall()
        vols=[]; liquid=[]
        for r in cur:
            raw=obj(r["raw_json"]); rv=raw.get("realized_volatility_24h")
            if rv is not None:vols.append(rv)
            if r["spread_bps"] is not None:liquid.append(r["spread_bps"])
        candidates=[r for r in cur if r["selection_class"]=="CANDIDATE"]
        hist=c.execute("""SELECT s.signal_time_utc t,s.event_class,s.stage,s.engine,s.btc_regime,
             s.symbol,o.reach_json,o.net_return_pct
          FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
          WHERE o.label_status='CLOSED' AND s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
          ORDER BY s.signal_time_utc""").fetchall() if table(c,"outcome_labels") else []
        for f in candidates:
            peer=[r for r in hist if r["event_class"]=="CANDIDATE" and r["stage"]==f["stage"] and r["engine"]==f["engine"]]
            if len(peer)<MIN_N:peer=[r for r in hist if r["event_class"]=="CANDIDATE" and r["stage"]==f["stage"]]
            if len(peer)<MIN_N:peer=[r for r in hist if r["event_class"]=="CANDIDATE"]
            k=sum(b_success(r["reach_json"],r["net_return_pct"]) for r in peer); n=len(peer)
            plo,phi=wilson(k,n); prob=calibrated(k,n) if n>=MIN_N else None
            same=[r for r in peer if (r["btc_regime"] or "UNKNOWN")==f["btc_regime"]]
            rk=sum(b_success(r["reach_json"],r["net_return_pct"]) for r in same); rn=len(same)
            rlo,rhi=wilson(rk,rn); rprob=calibrated(rk,rn) if rn>=MIN_N else None
            cand=[{"t":r["t"],"net":float(r["net_return_pct"])} for r in peer if r["net_return_pct"] is not None]
            ctrlrows=[r for r in hist if r["event_class"] in ("NEAR_MISS","RANDOM_CONTROL") and (r["btc_regime"] or "UNKNOWN")==f["btc_regime"]]
            if len(ctrlrows)<MIN_N:ctrlrows=[r for r in hist if r["event_class"] in ("NEAR_MISS","RANDOM_CONTROL")]
            ctrl=[{"t":r["t"],"net":float(r["net_return_pct"])} for r in ctrlrows if r["net_return_pct"] is not None]
            diff,dl,dh=cluster_boot_diff(cand,ctrl)
            raw=obj(f["raw_json"]); rv=raw.get("realized_volatility_24h")
            vr=percentile_regime(rv,vols); lr=percentile_regime(f["spread_bps"],liquid)
            conf=[]
            crowd_status="UNAVAILABLE_TRUE_SOCIAL_FEED"; crowd=None
            if table(c,"catalyst_observations"):
                ca=c.execute("""SELECT * FROM catalyst_observations WHERE scan_time_utc=? AND symbol=?
                  ORDER BY created_at_utc DESC LIMIT 1""",(ts,f["symbol"])).fetchone()
                if ca:
                    if ca["catalyst_state"] not in ("UNKNOWN","NEWS_PRESENT"):conf.append(ca["catalyst_state"])
                    crowd_status="NEWS_ATTENTION_PROXY";crowd=float(ca["headline_count"] or 0)
            ext="UNKNOWN"
            if table(c,"crossvenue_spot_summary"):
                x=c.execute("""SELECT summary FROM crossvenue_spot_summary WHERE scan_time_utc=? AND symbol=?
                  ORDER BY created_at_utc DESC LIMIT 1""",(ts,f["symbol"])).fetchone()
                if x:ext="CROSS_VENUE_"+str(x["summary"])
            vals=(mean([r["net"] for r in cand]),mean([r["net"] for r in ctrl]))
            c.execute("""INSERT OR REPLACE INTO institutional_signal_overlay VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("BINANCE",ts,f["symbol"],mode,"EMPIRICAL_BETA_BINOMIAL_WILSON",
               prob,n,plo,phi,rprob,rn,rlo,rhi,vals[0],vals[1],diff,dl,dh,
               vr,lr,f"{f['btc_regime']}|VOL_{vr}|LIQ_{lr}",json.dumps(conf),
               crowd_status,crowd,ext,
               "Wake-up/retention/trigger architecture predates untouched holdout; definitive validation begins 2026-10-07.",
               "CURRENT_VERSION_ONLY_PROSPECTIVE_HOLDOUT_REQUIRED",VERSION,now()))
        c.commit()
        print("institutional overlay BINANCE",ts,len(candidates))

def gate():
    odb=os.getenv("AVCI_DB","avci2.db");vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
    if not (os.path.exists(odb) and os.path.exists(vdb)):return
    with sqlite3.connect(odb,timeout=60) as c,sqlite3.connect(vdb,timeout=60) as v:
        c.row_factory=v.row_factory=sqlite3.Row;init(c)
        h=c.execute("""SELECT * FROM gate_scan_health WHERE status='VALID' ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not h:return
        batch=str(h["batch_id"]);mode=system_mode(c,"GATE")
        current=v.execute("""SELECT * FROM validation_events WHERE batch_id=? AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')""",(batch,)).fetchall()
        hist=v.execute("""SELECT * FROM validation_events WHERE status='CLOSED_72H'
          AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')""").fetchall()
        for e in current:
            peer=[r for r in hist if r["group_type"] in ("CANDIDATE","EXPANDED_CANDIDATE") and (r["rulesets"] or "")==(e["rulesets"] or "")]
            if len(peer)<MIN_N:peer=[r for r in hist if r["group_type"] in ("CANDIDATE","EXPANDED_CANDIDATE")]
            k=sum(r["result_10"]=="TARGET_FIRST" for r in peer);n=len(peer);plo,phi=wilson(k,n)
            prob=calibrated(k,n) if n>=MIN_N else None
            same=[r for r in peer if (r["btc_regime"] or "UNKNOWN")==e["btc_regime"] and (r["sol_regime"] or "UNKNOWN")==e["sol_regime"]]
            rk=sum(r["result_10"]=="TARGET_FIRST" for r in same);rn=len(same);rlo,rhi=wilson(rk,rn)
            rprob=calibrated(rk,rn) if rn>=MIN_N else None
            cand=[{"t":r["signal_iso"],"net":float(r["net_final_pct"])} for r in peer if r["net_final_pct"] is not None]
            ctrlrows=[r for r in hist if r["group_type"] in ("NEAR_MISS","RANDOM_CONTROL") and (r["btc_regime"] or "UNKNOWN")==e["btc_regime"]]
            if len(ctrlrows)<MIN_N:ctrlrows=[r for r in hist if r["group_type"] in ("NEAR_MISS","RANDOM_CONTROL")]
            ctrl=[{"t":r["signal_iso"],"net":float(r["net_final_pct"])} for r in ctrlrows if r["net_final_pct"] is not None]
            diff,dl,dh=cluster_boot_diff(cand,ctrl)
            snap=c.execute("""SELECT raw_json FROM snapshots WHERE network_id=? AND token_contract=?
              AND zaman_utc>=? ORDER BY id ASC LIMIT 1""",(e["network_id"],e["token_contract"],e["signal_iso"])).fetchone() if table(c,"snapshots") else None
            item=obj(snap[0]) if snap else {}; q=(item.get("exit_1k") if e["network_id"]=="solana" else item.get("evm_exit_1k")) or {}
            loss=q.get("loss_pct"); lr="UNKNOWN" if loss is None else ("LOW" if float(loss)<=1.5 else "MID" if float(loss)<=3 else "HIGH")
            change=abs(float(item.get("price_change_h24") or item.get("price_change_24h") or 0))
            vr="LOW" if change<5 else "MID" if change<15 else "HIGH"
            conf=[]; social=item.get("social_signal") or {}
            cs="UNAVAILABLE";cv=None
            if social.get("status")=="OBSERVED":
                cv=float(social.get("last_15m") or 0); ratio=social.get("ratio")
                cs="X_CROWDED" if cv>=10 and ratio is not None and float(ratio)>=2 else "X_OBSERVED"
            c.execute("""INSERT OR REPLACE INTO institutional_signal_overlay VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("GATE",batch,f"{e['network_id']}:{e['token_contract']}",mode,"EMPIRICAL_BETA_BINOMIAL_WILSON",
               prob,n,plo,phi,rprob,rn,rlo,rhi,mean([r["net"] for r in cand]),mean([r["net"] for r in ctrl]),
               diff,dl,dh,vr,lr,f"{e['btc_regime']}|{e['sol_regime']}|VOL_{vr}|LIQ_{lr}",
               json.dumps(conf),cs,cv,"ONCHAIN_NATIVE",
               "Wake-up/retention/trigger architecture predates untouched holdout; definitive validation begins 2026-10-07.",
               "CURRENT_VERSION_ONLY_PROSPECTIVE_HOLDOUT_REQUIRED",VERSION,now()))
        c.commit();print("institutional overlay GATE",batch,len(current))

def main():
    m=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if m=="binance":binance()
    elif m=="gate":gate()
    else:raise SystemExit("usage: institutional_signal_overlay.py binance|gate")
if __name__=="__main__":main()
