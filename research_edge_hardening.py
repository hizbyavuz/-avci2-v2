#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write-only edge hardening diagnostics for frozen Avci systems.

This module NEVER changes candidate membership, scanner thresholds, frozen
rulesets, execution rules, Telegram labels or holdout boundaries.

It adds five audit layers:
1) 72h overlapping-event episodes + conservative effective sample size
2) 2x transaction-cost stress
3) same-scan matched-control audit (parallel only)
4) Binance pre-signal conditional-BTC-beta residual fixed-exit return
5) execution-realism flags (gap/impact/cost-vs-stop)

All outputs are research-only and versioned.
"""
from __future__ import annotations

import json, math, os, random, sqlite3, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from research_validation_layer import split_of

VERSION="edge-hardening-v1-20260930"
EPISODE_HOURS=72
MATCHES_PER_CANDIDATE=5
BOOTSTRAPS=2000
RNG_SEED=20260930
MIN_BETA_PAIRS=60


def dt(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:
        return None

def f(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None

def mean(xs):
    xs=[x for x in xs if x is not None]
    return sum(xs)/len(xs) if xs else None

def stdev(xs):
    xs=[x for x in xs if x is not None]
    return statistics.pstdev(xs) if len(xs)>=2 else None

def cols(c,t):
    return {r[1] for r in c.execute(f"PRAGMA table_info({t})")}

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS research_episode_events(
      source TEXT,event_key TEXT,event_time TEXT,asset TEXT,group_type TEXT,split TEXT,
      regime TEXT,episode_id TEXT,episode_size INTEGER,version TEXT,
      PRIMARY KEY(source,event_key,version));
    CREATE TABLE IF NOT EXISTS research_episode_summary(
      source TEXT,split TEXT,group_type TEXT,raw_n INTEGER,episode_n INTEGER,
      unique_asset_n INTEGER,effective_n REAL,max_episode_size INTEGER,version TEXT,
      PRIMARY KEY(source,split,group_type,version));
    CREATE TABLE IF NOT EXISTS research_cost_stress(
      source TEXT,event_key TEXT,split TEXT,group_type TEXT,net_return_pct REAL,
      baseline_cost_pct REAL,stressed_2x_net_pct REAL,cost_known INTEGER,version TEXT,
      PRIMARY KEY(source,event_key,version));
    CREATE TABLE IF NOT EXISTS research_execution_realism(
      source TEXT,event_key TEXT,split TEXT,metric TEXT,value REAL,status TEXT,
      details TEXT,version TEXT,PRIMARY KEY(source,event_key,metric,version));
    CREATE TABLE IF NOT EXISTS research_matched_control_pairs(
      source TEXT,split TEXT,candidate_key TEXT,control_key TEXT,scan_key TEXT,
      episode_id TEXT,distance REAL,candidate_net REAL,control_net REAL,diff_net REAL,
      version TEXT,PRIMARY KEY(source,candidate_key,control_key,version));
    CREATE TABLE IF NOT EXISTS research_matched_control_summary(
      source TEXT,split TEXT,pair_n INTEGER,candidate_n INTEGER,episode_n INTEGER,
      mean_diff REAL,cluster_ci_low REAL,cluster_ci_high REAL,version TEXT,
      PRIMARY KEY(source,split,version));
    CREATE TABLE IF NOT EXISTS research_residual_returns(
      source TEXT,event_key TEXT,split TEXT,regime TEXT,beta REAL,alpha REAL,
      beta_pair_n INTEGER,market_return_pct REAL,fixed_exit_net_pct REAL,
      residual_return_pct REAL,version TEXT,PRIMARY KEY(source,event_key,version));
    CREATE TABLE IF NOT EXISTS research_peer_asof_audit(
      source TEXT,event_key TEXT,event_time TEXT,eligible_peer_n INTEGER,
      future_peer_n INTEGER,status TEXT,version TEXT,
      PRIMARY KEY(source,event_key,version));
    """)
    c.commit()

def parse_raw_json(x):
    try:
        v=json.loads(x or "{}")
        return v if isinstance(v,dict) else {}
    except Exception:
        return {}

def load_binance(c):
    if not table(c,"signal_events") or not table(c,"outcome_labels"):
        return []
    rows=c.execute("""SELECT s.event_id,s.symbol,s.signal_time_utc,s.event_class,
      s.validation_tier,s.btc_regime,s.fee_bps_per_side,s.slippage_bps_per_side,
      s.spread_bps,s.buy_impact_1k_bps,s.entry_open_time_ms,
      o.net_return_pct,o.btc_return_pct,o.mae_pct,o.mfe_pct,o.primary_exit_reason,
      o.horizon_metrics_json,
      f.volume_z_15m,f.return_z_15m,f.change_24h,f.btc_relative_24h,f.raw_json
      FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
      LEFT JOIN features f ON f.scan_time_utc=s.signal_time_utc AND f.symbol=s.symbol
      WHERE o.label_status='CLOSED'
        AND s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
      ORDER BY s.signal_time_utc""").fetchall()
    out=[]
    for r in rows:
        raw=parse_raw_json(r["raw_json"])
        fixed=None
        try:
            fixed=f(json.loads(r["horizon_metrics_json"] or "{}").get("72",{}).get("close_return_pct"))
        except Exception:
            pass
        qv=f(raw.get("quote_volume_24h"))
        out.append({
          "key":r["event_id"],"asset":r["symbol"],"time":r["signal_time_utc"],
          "group":r["event_class"],"regime":r["btc_regime"] or "UNKNOWN",
          "scan":r["signal_time_utc"],"net":f(r["net_return_pct"]),
          "fixed_net":fixed,"btc_return":f(r["btc_return_pct"]),
          "mae":f(r["mae_pct"]),"mfe":f(r["mfe_pct"]),
          "exit_reason":r["primary_exit_reason"],"fee":f(r["fee_bps_per_side"]),
          "slip":f(r["slippage_bps_per_side"]),"spread":f(r["spread_bps"]),
          "impact":f(r["buy_impact_1k_bps"]),"entry_ms":r["entry_open_time_ms"],
          "features":{
            "volume_z":f(r["volume_z_15m"]),"return_z":f(r["return_z_15m"]),
            "change24":f(r["change_24h"]),"btc_rel":f(r["btc_relative_24h"]),
            "log_qv":math.log(max(qv,1.0)) if qv is not None else None,
          },
        })
    return out

def load_gate(obs,val):
    if not table(val,"validation_events"):
        return []
    rows=val.execute("""SELECT id,batch_id,group_type,network_id,token_contract,
      signal_ts,signal_iso,btc_regime,net_final_pct,gross_final_pct,
      estimated_total_cost_pct,stop_pct,mae_pct,mfe_pct,result_10
      FROM validation_events WHERE status='CLOSED_72H'
        AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
      ORDER BY signal_ts,id""").fetchall()
    out=[]
    for r in rows:
        o=None
        if table(obs,"gate_early_observations"):
            o=obs.execute("""SELECT own_volume_ratio,change_24h,liquidity,buys_5m,sells_5m
              FROM gate_early_observations WHERE batch_id=? AND network_id=?
              AND token_contract=? LIMIT 1""",
              (r["batch_id"],r["network_id"],r["token_contract"])).fetchone()
        bs=None
        if o:
            buys=f(o["buys_5m"]); sells=f(o["sells_5m"])
            bs=(buys/max(1.0,sells)) if buys is not None else None
        out.append({
          "key":str(r["id"]),"asset":f"{r['network_id']}:{r['token_contract']}",
          "time":r["signal_iso"],"group":r["group_type"],
          "regime":r["btc_regime"] or "UNKNOWN","scan":str(r["batch_id"]),
          "net":f(r["net_final_pct"]),"fixed_net":f(r["net_final_pct"]),
          "gross":f(r["gross_final_pct"]),"cost":f(r["estimated_total_cost_pct"]),
          "stop":f(r["stop_pct"]),"mae":f(r["mae_pct"]),"mfe":f(r["mfe_pct"]),
          "features":{
            "own_vol":f(o["own_volume_ratio"]) if o else None,
            "change24":f(o["change_24h"]) if o else None,
            "log_liq":math.log(max(f(o["liquidity"]) or 1.0,1.0)) if o else None,
            "buy_sell":bs,
          },
        })
    return out

def is_candidate(source,g):
    return g=="CANDIDATE" if source=="BINANCE" else g in ("CANDIDATE","EXPANDED_CANDIDATE")

def assign_episodes(c,source,rows):
    c.execute("DELETE FROM research_episode_events WHERE source=? AND version=?",(source,VERSION))
    c.execute("DELETE FROM research_episode_summary WHERE source=? AND version=?",(source,VERSION))
    ordered=sorted([r for r in rows if dt(r["time"])],key=lambda r:dt(r["time"]))
    episodes=[]
    current=[]
    current_end=None
    for r in ordered:
        t=dt(r["time"])
        if current_end is None or t<=current_end:
            current.append(r)
            current_end=max(current_end or t,t+timedelta(hours=EPISODE_HOURS))
        else:
            episodes.append(current); current=[r]; current_end=t+timedelta(hours=EPISODE_HOURS)
    if current: episodes.append(current)
    event_episode={}
    for i,ep in enumerate(episodes,1):
        eid=f"{source}-EP{i:05d}"
        for r in ep:
            event_episode[r["key"]]=eid
            c.execute("""INSERT OR REPLACE INTO research_episode_events VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (source,r["key"],r["time"],r["asset"],r["group"],split_of(r["time"]),
               r["regime"],eid,len(ep),VERSION))
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        for gname,pred in (("CANDIDATE",lambda r:is_candidate(source,r["group"])),
                           ("CONTROL",lambda r:not is_candidate(source,r["group"]))):
            rr=[r for r in rows if split_of(r["time"])==sp and pred(r)]
            counts=defaultdict(int)
            assets=set()
            for r in rr:
                counts[event_episode.get(r["key"],"UNKNOWN")]+=1; assets.add(r["asset"])
            sizes=list(counts.values()); raw=len(rr)
            kish=(raw*raw/sum(x*x for x in sizes)) if sizes else 0.0
            eff=min(kish,float(len(assets))) if raw else 0.0
            c.execute("""INSERT OR REPLACE INTO research_episode_summary VALUES(?,?,?,?,?,?,?,?,?)""",
              (source,sp,gname,raw,len(counts),len(assets),eff,max(sizes) if sizes else 0,VERSION))
    c.commit()
    return event_episode

def cost_stress_and_execution(c,source,rows):
    c.execute("DELETE FROM research_cost_stress WHERE source=? AND version=?",(source,VERSION))
    c.execute("DELETE FROM research_execution_realism WHERE source=? AND version=?",(source,VERSION))
    for r in rows:
        sp=split_of(r["time"])
        if source=="BINANCE":
            fee=r.get("fee"); slip=r.get("slip")
            base=(2.0*((fee or 0)+(slip or 0))/100.0) if fee is not None and slip is not None else None
            stressed=(r["net"]-base) if r["net"] is not None and base is not None else None
            known=int(base is not None)
            if r.get("exit_reason")=="STOP" and r.get("mae") is not None:
                excess=max(0.0,abs(r["mae"])-7.0)
                status="GAP_RISK" if excess>0.5 else "OK"
                c.execute("""INSERT OR REPLACE INTO research_execution_realism VALUES(?,?,?,?,?,?,?,?)""",
                  (source,r["key"],sp,"STOP_MAE_BEYOND_BARRIER",excess,status,
                   "Diagnostic only: MAE below stop suggests possible gap/slippage; frozen fill is unchanged.",VERSION))
            observed=max([x for x in (r.get("spread"),r.get("impact")) if x is not None],default=None)
            if observed is not None and slip is not None:
                ratio=observed/max(slip,1e-9)
                status="ASSUMPTION_TOO_LOW" if ratio>1.5 else "OK"
                c.execute("""INSERT OR REPLACE INTO research_execution_realism VALUES(?,?,?,?,?,?,?,?)""",
                  (source,r["key"],sp,"OBSERVED_FRICTION_VS_ASSUMED_SLIPPAGE",ratio,status,
                   f"max(signal spread, $1k impact)={observed:.1f}bps vs assumed slippage={slip:.1f}bps",VERSION))
        else:
            base=r.get("cost")
            stressed=(r["net"]-base) if r["net"] is not None and base is not None else None
            known=int(base is not None)
            stop=abs(r.get("stop") or 0)
            if base is not None and stop>0:
                ratio=base/stop
                status="SEVERE" if ratio>=1.0 else ("HIGH" if ratio>=0.5 else "OK")
                c.execute("""INSERT OR REPLACE INTO research_execution_realism VALUES(?,?,?,?,?,?,?,?)""",
                  (source,r["key"],sp,"EXIT_COST_TO_STOP_RATIO",ratio,status,
                   "Research-only audit; does not alter frozen Gate tradability filters.",VERSION))
        c.execute("""INSERT OR REPLACE INTO research_cost_stress VALUES(?,?,?,?,?,?,?,?,?)""",
          (source,r["key"],sp,r["group"],r["net"],base,stressed,known,VERSION))
    c.commit()

def _std_distance(a,b,scales,keys):
    s=0.0; n=0
    for k in keys:
        x=a["features"].get(k); y=b["features"].get(k)
        if x is None or y is None: continue
        sc=max(scales.get(k,1.0),1e-9)
        s+=((x-y)/sc)**2; n+=1
    return math.sqrt(s/n) if n>=2 else None

def cluster_bootstrap_diff(pairs,event_episode):
    byep=defaultdict(list)
    for p in pairs:
        ep=event_episode.get(p["candidate_key"],"UNKNOWN")
        byep[ep].append(p["diff"])
    keys=[k for k,v in byep.items() if v]
    if len(keys)<2:return None,None,len(keys)
    rng=random.Random(RNG_SEED); vals=[]
    for _ in range(BOOTSTRAPS):
        chosen=[rng.choice(keys) for _ in keys]
        episode_means=[mean(byep[k]) for k in chosen]
        vals.append(mean(episode_means))
    vals.sort()
    return vals[int(.025*(len(vals)-1))],vals[int(.975*(len(vals)-1))],len(keys)

def matched_controls(c,source,rows,event_episode):
    c.execute("DELETE FROM research_matched_control_pairs WHERE source=? AND version=?",(source,VERSION))
    c.execute("DELETE FROM research_matched_control_summary WHERE source=? AND version=?",(source,VERSION))
    keys=["volume_z","return_z","change24","log_qv"] if source=="BINANCE" else ["own_vol","change24","log_liq","buy_sell"]
    byscan=defaultdict(list)
    for r in rows:byscan[r["scan"]].append(r)
    allpairs=[]
    for scan,g in byscan.items():
        vals={k:[r["features"].get(k) for r in g if r["features"].get(k) is not None] for k in keys}
        scales={k:(stdev(v) or 1.0) for k,v in vals.items()}
        controls=[r for r in g if not is_candidate(source,r["group"]) and r["net"] is not None]
        for cand in [r for r in g if is_candidate(source,r["group"]) and r["net"] is not None]:
            same_reg=[x for x in controls if x["regime"]==cand["regime"]]
            pool=same_reg or controls
            ranked=[]
            for ctrl in pool:
                d=_std_distance(cand,ctrl,scales,keys)
                if d is not None: ranked.append((d,ctrl))
            for d,ctrl in sorted(ranked,key=lambda x:x[0])[:MATCHES_PER_CANDIDATE]:
                rec={"candidate_key":cand["key"],"control_key":ctrl["key"],"diff":cand["net"]-ctrl["net"]}
                allpairs.append(rec)
                c.execute("""INSERT OR REPLACE INTO research_matched_control_pairs VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (source,split_of(cand["time"]),cand["key"],ctrl["key"],scan,
                   event_episode.get(cand["key"]),d,cand["net"],ctrl["net"],rec["diff"],VERSION))
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        pp=[p for p in allpairs if split_of(next(r["time"] for r in rows if r["key"]==p["candidate_key"]))==sp]
        cands={p["candidate_key"] for p in pp}
        lo,hi,epn=cluster_bootstrap_diff(pp,event_episode)
        c.execute("""INSERT OR REPLACE INTO research_matched_control_summary VALUES(?,?,?,?,?,?,?,?,?)""",
          (source,sp,len(pp),len(cands),epn,mean([p["diff"] for p in pp]),lo,hi,VERSION))
    c.commit()

def returns_from_closes(rows):
    out={}
    prev=None
    for t,close in rows:
        close=f(close)
        if close is None or close<=0: continue
        if prev is not None:
            out[t]=(close/prev-1.0)
        prev=close
    return out

def conditional_beta(c,symbol,entry_ms,regime):
    if not entry_ms or not table(c,"raw_klines"): return None,None,None
    lo=int(entry_ms)-7*24*3600*1000
    def q(sym):
        return c.execute("""SELECT open_time_ms,close_price FROM raw_klines
          WHERE symbol=? AND interval_value='5m' AND open_time_ms>=? AND open_time_ms<?
          ORDER BY open_time_ms""",(sym,lo,int(entry_ms))).fetchall()
    ar=returns_from_closes([(int(r[0]),r[1]) for r in q(symbol)])
    br=returns_from_closes([(int(r[0]),r[1]) for r in q("BTCUSDT")])
    pairs=[]
    for t in sorted(set(ar)&set(br)):
        b=br[t]
        if regime=="DOWN" and b>=0: continue
        if regime=="UP" and b<=0: continue
        pairs.append((br[t],ar[t]))
    if len(pairs)<MIN_BETA_PAIRS:return None,None,len(pairs)
    xb=[x for x,y in pairs]; ya=[y for x,y in pairs]
    mx=mean(xb); my=mean(ya)
    var=sum((x-mx)**2 for x in xb)
    if var<=1e-18:return None,None,len(pairs)
    beta=sum((x-mx)*(y-my) for x,y in pairs)/var
    alpha=my-beta*mx
    return beta,alpha,len(pairs)

def residuals_binance(c,rows):
    c.execute("DELETE FROM research_residual_returns WHERE source='BINANCE' AND version=?",(VERSION,))
    for r in rows:
        beta,alpha,n=conditional_beta(c,r["asset"],r.get("entry_ms"),r["regime"])
        market=r.get("btc_return"); fixed=r.get("fixed_net")
        residual=None
        if None not in (beta,alpha,market,fixed):
            # alpha is a 5m conditional intercept. We intentionally do not
            # compound it for 72h; doing so would create a model-dependent drift.
            # Residual therefore removes beta exposure only and stores alpha for diagnosis.
            residual=fixed-beta*market
        c.execute("""INSERT OR REPLACE INTO research_residual_returns VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          ("BINANCE",r["key"],split_of(r["time"]),r["regime"],beta,alpha,n,market,fixed,residual,VERSION))
    c.commit()

def peer_asof_gate(c,val,rows):
    c.execute("DELETE FROM research_peer_asof_audit WHERE source='GATE' AND version=?",(VERSION,))
    if not table(val,"validation_events"):return
    for r in rows:
        if not is_candidate("GATE",r["group"]):continue
        signal=int(dt(r["time"]).timestamp())
        eligible=val.execute("""SELECT COUNT(*) FROM validation_events
          WHERE status='CLOSED_72H' AND horizon_end_ts<=?""",(signal,)).fetchone()[0]
        future=val.execute("""SELECT COUNT(*) FROM validation_events
          WHERE status='CLOSED_72H' AND signal_ts<? AND horizon_end_ts>?""",(signal,signal)).fetchone()[0]
        status="OK" if future==0 else "WOULD_LEAK_WITHOUT_ASOF_GUARD"
        c.execute("""INSERT OR REPLACE INTO research_peer_asof_audit VALUES(?,?,?,?,?,?,?)""",
          ("GATE",r["key"],r["time"],eligible,future,status,VERSION))
    c.commit()

def write_report(c,source):
    lines=[f"# {source} Edge Hardening",f"Version: {VERSION}",
           "Research-only: frozen signal logic is untouched.",""]
    lines.append("## 72h episode / effective n")
    for r in c.execute("""SELECT * FROM research_episode_summary WHERE source=? AND version=?
      ORDER BY split,group_type""",(source,VERSION)):
        lines.append(f"- {r['split']} {r['group_type']}: raw={r['raw_n']} episode={r['episode_n']} "
                     f"unique_asset={r['unique_asset_n']} effective_n={r['effective_n']:.2f} max_episode={r['max_episode_size']}")
    lines+=["","## Same-scan matched controls"]
    for r in c.execute("""SELECT * FROM research_matched_control_summary WHERE source=? AND version=?
      ORDER BY split""",(source,VERSION)):
        lines.append(f"- {r['split']}: pairs={r['pair_n']} candidates={r['candidate_n']} episodes={r['episode_n']} "
                     f"mean_diff={r['mean_diff']} cluster95=[{r['cluster_ci_low']},{r['cluster_ci_high']}]")
    lines+=["","## 2x cost stress"]
    for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
        rr=c.execute("""SELECT stressed_2x_net_pct FROM research_cost_stress
          WHERE source=? AND version=? AND split=? AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')
          AND stressed_2x_net_pct IS NOT NULL""",(source,VERSION,sp)).fetchall()
        vals=[f(x[0]) for x in rr if f(x[0]) is not None]
        lines.append(f"- {sp}: n={len(vals)} mean_2x_cost_net={mean(vals)}")
    if source=="BINANCE":
        lines+=["","## Conditional BTC-beta residual fixed-exit return"]
        for sp in ("DISCOVERY","CALIBRATION","FINAL_TEST"):
            rr=c.execute("""SELECT residual_return_pct FROM research_residual_returns
              WHERE source='BINANCE' AND version=? AND split=? AND residual_return_pct IS NOT NULL""",(VERSION,sp)).fetchall()
            vals=[f(x[0]) for x in rr if f(x[0]) is not None]
            lines.append(f"- {sp}: n={len(vals)} mean_residual={mean(vals)}")
    lines+=["","## Execution realism flags"]
    for r in c.execute("""SELECT metric,status,COUNT(*) n FROM research_execution_realism
      WHERE source=? AND version=? GROUP BY metric,status ORDER BY metric,status""",(source,VERSION)):
        lines.append(f"- {r['metric']} / {r['status']}: {r['n']}")
    return "\n".join(lines)+"\n"

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").upper()
    if mode not in ("BINANCE","GATE"):
        raise SystemExit("usage: research_edge_hardening.py binance|gate")
    if mode=="BINANCE":
        db=os.getenv("BINANCE_DB","binance_avci2.db")
        if not os.path.exists(db):print("Binance DB yok");return
        with sqlite3.connect(db,timeout=60) as c:
            c.row_factory=sqlite3.Row; init(c); rows=load_binance(c)
            eps=assign_episodes(c,mode,rows); cost_stress_and_execution(c,mode,rows)
            matched_controls(c,mode,rows,eps); residuals_binance(c,rows)
            c.commit(); report=write_report(c,mode)
        open("binance_edge_hardening.md","w",encoding="utf-8").write(report); print(report)
    else:
        odb=os.getenv("AVCI_DB","avci2.db"); vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
        if not os.path.exists(odb) or not os.path.exists(vdb):print("Gate DB eksik");return
        with sqlite3.connect(odb,timeout=60) as c, sqlite3.connect(vdb,timeout=60) as v:
            c.row_factory=v.row_factory=sqlite3.Row; init(c); rows=load_gate(c,v)
            eps=assign_episodes(c,mode,rows); cost_stress_and_execution(c,mode,rows)
            matched_controls(c,mode,rows,eps); peer_asof_gate(c,v,rows)
            c.commit(); report=write_report(c,mode)
        open("gate_edge_hardening.md","w",encoding="utf-8").write(report); print(report)

if __name__=="__main__":
    main()
