#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-registered decision discipline for Avci research.

Research-only. Never changes frozen signal rules and never places orders.
Adds prospective genesis holdout, sequential decision checkpoints, time budget,
edge-decay, go-live gate, global kill-switch state, benchmark/cost diagnostics.
"""
from __future__ import annotations
import json, math, os, random, sqlite3, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

VERSION="decision-discipline-v1-20260926"
HOLDOUT_START="2026-10-07T00:00:00+00:00"  # calibration end + 72h purge + 24h embargo
MIN_HOLDOUT_DAYS=60
MAX_HOLDOUT_DAYS=120
CHECKPOINT_EFFECTIVE_N=(50,100,200)
EDGE_WINDOWS=(30,60,90)

GO_LIVE={
  "min_calendar_days":60,
  "min_candidate_closed":150,
  "min_effective_n":100.0,
  "min_profit_factor":1.20,
  "min_sharpe":0.80,
  "min_sortino":1.00,
  "max_drawdown":0.20,
  "min_expectancy_pct":0.0,
  "candidate_control_ci_low_gt":0.0,
  "max_data_failure_rate":0.05,
  "min_execution_coverage":0.90,
  "min_excess_vs_benchmark_pct":0.0
}
KILL={
  "rolling_window_trades":30,
  "expectancy_below_pct":0.0,
  "sharpe_below":0.0,
  "max_drawdown":0.20,
  "max_data_failure_rate":0.10,
  "high_psi_feature_count":3,
  "source_error_rate":0.20,
  "observed_vs_assumed_cost_ratio":2.0
}

def dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None
def now(): return datetime.now(timezone.utc)
def table(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def cols(c,t): return {r[1] for r in c.execute(f"PRAGMA table_info({t})")} if table(c,t) else set()
def avg(xs): return sum(xs)/len(xs) if xs else None
def med(xs): return statistics.median(xs) if xs else None
def sd(xs): return statistics.pstdev(xs) if len(xs)>=2 else None
def pf(vals):
    pos=sum(x for x in vals if x>0); neg=-sum(x for x in vals if x<0)
    return pos/neg if neg>0 else (999.0 if pos>0 else None)
def sharpe(vals):
    s=sd(vals); return avg(vals)/s*math.sqrt(365) if vals and s not in (None,0) else None
def sortino(vals):
    if not vals:return None
    d=math.sqrt(avg([min(0,x)**2 for x in vals]))
    return avg(vals)/d*math.sqrt(365) if d else None
def maxdd(vals):
    eq=1.0; peak=1.0; out=0.0
    for x in vals:
        eq*=1+x/100.0; peak=max(peak,eq)
        out=max(out,(peak-eq)/peak)
    return out

def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS decision_discipline_state(
      source TEXT,version TEXT,run_utc TEXT,genesis_holdout_start TEXT,
      holdout_age_days REAL,time_budget_status TEXT,sequential_status TEXT,
      next_checkpoint_effective_n REAL,go_live_status TEXT,kill_switch_status TEXT,
      reasons_json TEXT,PRIMARY KEY(source,version));
    CREATE TABLE IF NOT EXISTS sequential_checkpoint_ledger(
      source TEXT,version TEXT,checkpoint_effective_n REAL,first_crossed_utc TEXT,
      status TEXT,decision_allowed INTEGER,metrics_json TEXT,
      PRIMARY KEY(source,version,checkpoint_effective_n));
    CREATE TABLE IF NOT EXISTS edge_decay_metrics(
      source TEXT,version TEXT,window_days INTEGER,period TEXT,n INTEGER,
      precision REAL,expectancy_pct REAL,profit_factor REAL,sharpe REAL,
      PRIMARY KEY(source,version,window_days,period));
    CREATE TABLE IF NOT EXISTS cost_model_calibration(
      source TEXT,version TEXT,event_key TEXT,event_time TEXT,
      assumed_cost_pct REAL,observed_quote_cost_pct REAL,realized_cost_pct REAL,
      observed_minus_assumed_pct REAL,realized_minus_assumed_pct REAL,
      evidence_type TEXT,notes TEXT,PRIMARY KEY(source,version,event_key));
    """)
    c.commit()

def holdout_effective_n(rows):
    clusters=defaultdict(int)
    for r in rows:
        t=dt(r.get("t"))
        if not t:continue
        key=f"{t.date().isoformat()}|{r.get('regime') or 'UNKNOWN'}"
        clusters[key]+=1
    sizes=list(clusters.values()); n=sum(sizes)
    return (n*n/sum(x*x for x in sizes)) if sizes else 0.0

def portfolio_metrics(c,source):
    if not table(c,"research_portfolio_metrics"):return {}
    r=c.execute("""SELECT * FROM research_portfolio_metrics
      WHERE source=? AND split='FINAL_TEST' AND strategy='AVCI'
      ORDER BY version DESC LIMIT 1""",(source,)).fetchone()
    return dict(r) if r else {}

def significance(c,source):
    if not table(c,"research_significance"):return {}
    r=c.execute("""SELECT * FROM research_significance
      WHERE source=? AND split='FINAL_TEST' AND test_name='CANDIDATE_VS_CONTROL_10'
      ORDER BY version DESC LIMIT 1""",(source,)).fetchone()
    return dict(r) if r else {}

def closed_rows(c,source):
    if source=="BINANCE" and table(c,"signal_events") and table(c,"outcome_labels"):
        return [dict(r) for r in c.execute("""SELECT s.event_id key,s.signal_time_utc t,
          s.event_class grp,s.btc_regime regime,o.net_return_pct net,o.excess_vs_btc_pct excess
          FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
          WHERE o.label_status='CLOSED'
            AND s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
          ORDER BY s.signal_time_utc""")]
    if source=="GATE" and table(c,"decision_gate_events"):
        return []
    # Gate closed rows live in validation DB, handled separately by caller.
    return []

def gate_closed(v):
    if not table(v,"validation_events"):return []
    return [dict(r) for r in v.execute("""SELECT id key,signal_iso t,group_type grp,
      btc_regime regime,net_final_pct net FROM validation_events
      WHERE status='CLOSED_72H'
        AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
      ORDER BY signal_ts""")]

def is_candidate(source,r):
    g=str(r.get("grp") or "")
    return g=="CANDIDATE" if source=="BINANCE" else g in ("CANDIDATE","EXPANDED_CANDIDATE")

def holdout_diff_ci(source,rows,nboot=1000):
    ca=[r for r in rows if is_candidate(source,r) and r.get("net") is not None]
    co=[r for r in rows if not is_candidate(source,r) and r.get("net") is not None]
    if len(ca)<8 or len(co)<8:return None
    clusters=defaultdict(list)
    for r in rows:
        if r.get("net") is None:continue
        t=dt(r.get("t"))
        if not t:continue
        k=f"{t.date().isoformat()}|{r.get('regime') or 'UNKNOWN'}"
        clusters[k].append(r)
    keys=list(clusters)
    if len(keys)<3:return None
    rng=random.Random(26092026); diffs=[]
    for _ in range(nboot):
        sample=[]
        for _k in keys:sample.extend(clusters[rng.choice(keys)])
        a=[float(r["net"]) for r in sample if is_candidate(source,r)]
        b=[float(r["net"]) for r in sample if not is_candidate(source,r)]
        if a and b:diffs.append(avg(a)-avg(b))
    if len(diffs)<100:return None
    diffs.sort()
    return diffs[int(.025*(len(diffs)-1))]

def edge_decay(c,source,rows):
    c.execute("DELETE FROM edge_decay_metrics WHERE source=? AND version=?",(source,VERSION))
    end=now()
    for days in EDGE_WINDOWS:
        start=end-timedelta(days=days)
        recent=[r for r in rows if is_candidate(source,r) and dt(r.get("t")) and dt(r["t"])>=start and r.get("net") is not None]
        first=[r for r in rows if is_candidate(source,r) and r.get("net") is not None][:len(recent)] if recent else []
        for label,g in (("RECENT",recent),("EARLY_MATCHED_N",first)):
            vals=[float(r["net"]) for r in g]
            precision=sum(x>0 for x in vals)/len(vals) if vals else None
            c.execute("INSERT INTO edge_decay_metrics VALUES(?,?,?,?,?,?,?,?,?)",
              (source,VERSION,days,label,len(vals),precision,avg(vals),pf(vals),sharpe(vals)))
    c.commit()

def cost_calibration_binance(c):
    if not table(c,"signal_events"):return
    c.execute("DELETE FROM cost_model_calibration WHERE source='BINANCE' AND version=?",(VERSION,))
    for r in c.execute("""SELECT event_id,signal_time_utc,fee_bps_per_side,slippage_bps_per_side,
      spread_bps,buy_impact_1k_bps,sell_impact_1k_bps FROM signal_events
      WHERE event_class='CANDIDATE'"""):
        assumed=(2*(float(r["fee_bps_per_side"] or 0)+float(r["slippage_bps_per_side"] or 0)))/100.0
        obs=(float(r["spread_bps"] or 0)+float(r["buy_impact_1k_bps"] or 0)+float(r["sell_impact_1k_bps"] or 0))/100.0
        realized=None
        if table(c,"execution_fill_observations"):
            q=c.execute("""SELECT SUM(total_realized_cost_pct) FROM execution_fill_observations
              WHERE source='BINANCE' AND event_key=?""",(r["event_id"],)).fetchone()
            if q and q[0] is not None:realized=float(q[0])
        c.execute("INSERT OR REPLACE INTO cost_model_calibration VALUES(?,?,?,?,?,?,?,?,?,?,?)",
          ("BINANCE",VERSION,r["event_id"],r["signal_time_utc"],assumed,obs,realized,
           obs-assumed,(realized-assumed) if realized is not None else None,
           "REALIZED_FILL" if realized is not None else "QUOTE_OBSERVED",
           "Realized only when imported read-only fill data exists; otherwise signal-time spread+book impact."))
    c.commit()

def cost_calibration_gate(obs,val):
    if not table(val,"validation_events"):return
    obs.execute("DELETE FROM cost_model_calibration WHERE source='GATE' AND version=?",(VERSION,))
    for r in val.execute("""SELECT id,signal_iso,estimated_total_cost_pct,exit_loss_pct,cost_status
      FROM validation_events WHERE group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')"""):
        assumed=float(r["estimated_total_cost_pct"]) if r["estimated_total_cost_pct"] is not None else None
        observed=float(r["exit_loss_pct"]) if r["exit_loss_pct"] is not None else None
        diff=(observed-assumed) if assumed is not None and observed is not None else None
        realized=None
        if table(obs,"execution_fill_observations"):
            q=obs.execute("""SELECT SUM(total_realized_cost_pct) FROM execution_fill_observations
              WHERE source='GATE' AND event_key=?""",(str(r["id"]),)).fetchone()
            if q and q[0] is not None:realized=float(q[0])
        obs.execute("INSERT OR REPLACE INTO cost_model_calibration VALUES(?,?,?,?,?,?,?,?,?,?,?)",
          ("GATE",VERSION,str(r["id"]),r["signal_iso"],assumed,observed,realized,diff,
           (realized-assumed) if realized is not None and assumed is not None else None,
           "REALIZED_FILL" if realized is not None else "QUOTE_OBSERVED",
           f"Gate {r['cost_status']}; realized only when imported read-only fill data exists."))
    obs.commit()

def failure_rate(c):
    if not table(c,"data_issues"):return 0.0
    n=c.execute("SELECT COUNT(*) FROM data_issues").fetchone()[0]
    scans=c.execute("SELECT COUNT(*) FROM scans").fetchone()[0] if table(c,"scans") else 0
    return n/max(1,n+scans)

def execution_coverage(c,source):
    if not table(c,"trade_readiness"):return 0.0
    n=c.execute("SELECT COUNT(*) FROM trade_readiness WHERE source=?",(source,)).fetchone()[0]
    if not n:return 0.0
    good=c.execute("""SELECT COUNT(*) FROM trade_readiness
      WHERE source=? AND execution_quality IS NOT NULL
      AND execution_quality NOT IN ('UNKNOWN','DATA_MISSING')""",(source,)).fetchone()[0]
    return good/n

def benchmark_excess(rows):
    xs=[float(r["excess"]) for r in rows if r.get("excess") is not None]
    return avg(xs) if xs else None

def sequential(c,source,eff,metrics):
    crossed=[]
    for cp in CHECKPOINT_EFFECTIVE_N:
        existing=c.execute("""SELECT status FROM sequential_checkpoint_ledger
          WHERE source=? AND version=? AND checkpoint_effective_n=?""",(source,VERSION,cp)).fetchone()
        if eff>=cp and not existing:
            c.execute("INSERT INTO sequential_checkpoint_ledger VALUES(?,?,?,?,?,?,?)",
              (source,VERSION,cp,now().isoformat(),"CROSSED",1,json.dumps(metrics,sort_keys=True)))
            crossed.append(cp)
    nextcp=next((x for x in CHECKPOINT_EFFECTIVE_N if eff<x),None)
    c.commit()
    return crossed,nextcp

def evaluate(source,c,rows):
    start=dt(HOLDOUT_START)
    hold_rows=[r for r in rows if dt(r.get("t")) and dt(r["t"])>=start]
    hold_candidates=[r for r in hold_rows if is_candidate(source,r)]
    eff=holdout_effective_n(hold_candidates); pm=portfolio_metrics(c,source)
    vals=[float(r["net"]) for r in hold_candidates if r.get("net") is not None]
    age=(now()-start).total_seconds()/86400 if now()>=start else 0.0
    n=len(vals); exp=avg(vals); pforce=pf(vals); sh=sharpe(vals); so=sortino(vals); dd=maxdd(vals)
    fr=failure_rate(c); cov=execution_coverage(c,source)
    excess=benchmark_excess(hold_candidates) if source=="BINANCE" else None
    ci_low=holdout_diff_ci(source,hold_rows)

    metrics={"candidate_closed":n,"effective_n":eff,"expectancy_pct":exp,"profit_factor":pforce,
             "sharpe":sh,"sortino":so,"max_drawdown":dd,"data_failure_rate":fr,
             "execution_coverage":cov,"candidate_control_ci_low":ci_low,
             "benchmark_excess_pct":excess,"prospective_holdout_rows":len(hold_rows),
             "prospective_holdout_candidates":len(hold_candidates),
             "prospective_holdout_controls":len(hold_rows)-len(hold_candidates)}
    crossed,nextcp=sequential(c,source,eff,metrics)

    reasons=[]
    if age<GO_LIVE["min_calendar_days"]:reasons.append("MIN_CALENDAR_DAYS")
    if n<GO_LIVE["min_candidate_closed"]:reasons.append("MIN_CANDIDATE_CLOSED")
    if eff<GO_LIVE["min_effective_n"]:reasons.append("MIN_EFFECTIVE_N")
    if pforce is None or pforce<GO_LIVE["min_profit_factor"]:reasons.append("PROFIT_FACTOR")
    if sh is None or sh<GO_LIVE["min_sharpe"]:reasons.append("SHARPE")
    if so is None or so<GO_LIVE["min_sortino"]:reasons.append("SORTINO")
    if dd>GO_LIVE["max_drawdown"]:reasons.append("MAX_DRAWDOWN")
    if exp is None or exp<=GO_LIVE["min_expectancy_pct"]:reasons.append("EXPECTANCY")
    if ci_low is None or ci_low<=GO_LIVE["candidate_control_ci_low_gt"]:reasons.append("CANDIDATE_CONTROL_CI")
    if fr>GO_LIVE["max_data_failure_rate"]:reasons.append("DATA_FAILURE_RATE")
    if cov<GO_LIVE["min_execution_coverage"]:reasons.append("EXECUTION_COVERAGE")
    if source=="BINANCE" and (excess is None or excess<=GO_LIVE["min_excess_vs_benchmark_pct"]):
        reasons.append("BTC_BENCHMARK_EXCESS")
    go="PASS_FOR_TINY_LIVE_REVIEW" if not reasons and eff>=max(CHECKPOINT_EFFECTIVE_N) else "PAPER_ONLY"

    kills=[]
    recent=vals[-KILL["rolling_window_trades"]:]
    if len(recent)>=KILL["rolling_window_trades"]:
        if avg(recent)<KILL["expectancy_below_pct"]:kills.append("ROLLING_EXPECTANCY_NEGATIVE")
        rs=sharpe(recent)
        if rs is not None and rs<KILL["sharpe_below"]:kills.append("ROLLING_SHARPE_NEGATIVE")
    if dd>KILL["max_drawdown"]:kills.append("MAX_DRAWDOWN")
    if fr>KILL["max_data_failure_rate"]:kills.append("DATA_FAILURE_RATE")
    if table(c,"research_drift_metrics"):
        high=c.execute("""SELECT COUNT(*) FROM research_drift_metrics
          WHERE source=? AND psi_status='HIGH'""",(source,)).fetchone()[0]
        if high>=KILL["high_psi_feature_count"]:kills.append("MULTI_FEATURE_DRIFT")
    if table(c,"research_latency_summary"):
        er=c.execute("""SELECT MAX(error_rate) FROM research_latency_summary WHERE source=?""",(source,)).fetchone()[0]
        if er is not None and float(er)>KILL["source_error_rate"]:kills.append("SOURCE_ERROR_RATE")
    if table(c,"cost_model_calibration"):
        rr=c.execute("""SELECT assumed_cost_pct,observed_quote_cost_pct
          FROM cost_model_calibration WHERE source=? AND version=?
          AND assumed_cost_pct IS NOT NULL AND observed_quote_cost_pct IS NOT NULL
          AND assumed_cost_pct>0""",(source,VERSION)).fetchall()
        ratios=[float(x[1])/float(x[0]) for x in rr if float(x[0])>0]
        if ratios:
            ratios.sort()
            p95=ratios[min(len(ratios)-1,int(0.95*(len(ratios)-1)))]
            metrics["cost_ratio_p95"]=p95
            if p95>KILL["observed_vs_assumed_cost_ratio"]:
                kills.append("COST_MODEL_UNDERESTIMATES")
    kill="RESEARCH_ONLY" if kills else "ARMED_PAPER_ONLY"

    budget=("NOT_STARTED" if now()<start else
            "IN_WINDOW" if age<=MAX_HOLDOUT_DAYS else
            "INCONCLUSIVE_RESTART_VERSION" if go!="PASS_FOR_TINY_LIVE_REVIEW" else "COMPLETE")
    seq=("CHECKPOINT_CROSSED_"+",".join(map(str,crossed)) if crossed else
         f"WAIT_{nextcp}" if nextcp else "ALL_CHECKPOINTS_CROSSED")
    c.execute("INSERT OR REPLACE INTO decision_discipline_state VALUES(?,?,?,?,?,?,?,?,?,?,?)",
      (source,VERSION,now().isoformat(),HOLDOUT_START,age,budget,seq,nextcp,go,kill,
       json.dumps({"go_live_blockers":reasons,"kill_reasons":kills,"metrics":metrics},sort_keys=True)))
    c.commit()
    return {"source":source,"go_live":go,"kill_switch":kill,"time_budget":budget,
            "sequential":seq,"metrics":metrics,"go_live_blockers":reasons,"kill_reasons":kills}

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").upper()
    if mode=="BINANCE":
        db=os.getenv("BINANCE_DB","binance_avci2.db")
        if not os.path.exists(db):return
        with sqlite3.connect(db,timeout=60) as c:
            c.row_factory=sqlite3.Row; init(c)
            rows=closed_rows(c,"BINANCE"); edge_decay(c,"BINANCE",rows); cost_calibration_binance(c)
            print(json.dumps(evaluate("BINANCE",c,rows),ensure_ascii=False,indent=2))
    elif mode=="GATE":
        odb=os.getenv("AVCI_DB","avci2.db"); vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
        if not (os.path.exists(odb) and os.path.exists(vdb)):return
        with sqlite3.connect(odb,timeout=60) as c, sqlite3.connect(vdb,timeout=60) as v:
            c.row_factory=v.row_factory=sqlite3.Row; init(c)
            rows=gate_closed(v); edge_decay(c,"GATE",rows); cost_calibration_gate(c,v)
            print(json.dumps(evaluate("GATE",c,rows),ensure_ascii=False,indent=2))
    else:
        raise SystemExit("usage: research_decision_discipline.py binance|gate")

if __name__=="__main__":main()
