#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-engine portfolio and benchmark governance for Avci.

Loads both engine DBs, measures overlap/correlation, simulates one global
72-hour-capital-lock portfolio, compares against BTC/SOL buy-and-hold, and
measures benchmark beta plus engine/asset concentration.
Research-only; never places orders.
"""
import json, math, os, sqlite3, statistics
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
import requests

VERSION="global-governance-v2-20260926"
HOLDOUT_START="2026-10-07T00:00:00+00:00"
MAX_GLOBAL_POSITIONS=5
MAX_PER_ENGINE=3
CAPITAL_PER_TRADE=0.10
LOCK_HOURS=72
START_EQUITY=100.0

def dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None
def avg(xs):return sum(xs)/len(xs) if xs else None
def sd(xs):return statistics.pstdev(xs) if len(xs)>=2 else None
def corr(a,b):
    if len(a)<3:return None
    ma=avg(a);mb=avg(b);va=sum((x-ma)**2 for x in a);vb=sum((y-mb)**2 for y in b)
    if va<=0 or vb<=0:return None
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb)
def beta(a,b):
    if len(a)<5 or len(a)!=len(b):return None
    ma=avg(a);mb=avg(b)
    vb=sum((y-mb)**2 for y in b)
    if vb<=0:return None
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/vb
def hhi(counts):
    total=sum(counts.values())
    if total<=0:return None
    return sum((v/total)**2 for v in counts.values())
def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def load_binance(path):
    if not os.path.exists(path):return []
    with sqlite3.connect(path) as c:
        c.row_factory=sqlite3.Row
        if not (table(c,"signal_events") and table(c,"outcome_labels")):return []
        return [dict(r) for r in c.execute("""SELECT 'BINANCE' engine,s.event_id event_key,
          s.signal_time_utc event_time,s.symbol asset,s.btc_regime regime,
          o.net_return_pct net_return
          FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
          WHERE s.event_class='CANDIDATE' AND o.label_status='CLOSED'
            AND o.net_return_pct IS NOT NULL ORDER BY s.signal_time_utc""")]

def load_gate(obs_path,val_path):
    if not (os.path.exists(obs_path) and os.path.exists(val_path)):return []
    with sqlite3.connect(val_path) as v:
        v.row_factory=sqlite3.Row
        if not table(v,"validation_events"):return []
        return [dict(r) for r in v.execute("""SELECT 'GATE' engine,CAST(id AS TEXT) event_key,
          signal_iso event_time,network_id||':'||token_contract asset,
          btc_regime regime,net_final_pct net_return
          FROM validation_events
          WHERE group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')
            AND status='CLOSED_72H' AND net_final_pct IS NOT NULL
          ORDER BY signal_ts""")]

def benchmark(symbol):
    start=int(dt(HOLDOUT_START).timestamp()*1000)
    try:
        r=requests.get("https://api.binance.com/api/v3/klines",
          params={"symbol":symbol,"interval":"1d","startTime":start,"limit":1000},timeout=20)
        r.raise_for_status(); rows=r.json()
        if len(rows)<2:return {"status":"INSUFFICIENT","daily_returns":{}}
        first=float(rows[0][1]); last=float(rows[-1][4])
        daily={}
        prev=None
        for row in rows:
            close=float(row[4])
            day=datetime.fromtimestamp(int(row[0])/1000,timezone.utc).date().isoformat()
            if prev is not None and prev>0:
                daily[day]=close/prev-1.0
            prev=close
        return {"status":"OK","start_price":first,"end_price":last,
                "return_pct":100*(last/first-1),"bars":len(rows),"daily_returns":daily}
    except Exception as e:
        return {"status":"UNAVAILABLE","error":str(e),"daily_returns":{}}

def aligned_beta(port_daily,bench_daily):
    days=sorted(set(port_daily)&set(bench_daily))
    if len(days)<5:return None
    return beta([port_daily[d] for d in days],[bench_daily[d] for d in days])

def simulate(rows):
    rows=sorted([r for r in rows if dt(r["event_time"])],key=lambda r:dt(r["event_time"]))
    eq=START_EQUITY; peak=eq; mdd=0.0; active=[]; accepted=[]; daily_pnl=defaultdict(float)
    def close_due(t):
        nonlocal eq,peak,mdd
        keep=[]
        for p in active:
            if p["close"]<=t:
                pnl=p["allocation"]*(float(p["net_return"])/100.0)
                eq+=pnl
                daily_pnl[p["close"].date().isoformat()]+=pnl/START_EQUITY
                peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak else 0)
            else:keep.append(p)
        active[:]=keep
    for r in rows:
        t=dt(r["event_time"]);close_due(t)
        per_engine=sum(p["engine"]==r["engine"] for p in active)
        if len(active)>=MAX_GLOBAL_POSITIONS or per_engine>=MAX_PER_ENGINE:continue
        allocation=eq*CAPITAL_PER_TRADE
        active.append({"close":t+timedelta(hours=LOCK_HOURS),"allocation":allocation,
                       "net_return":r["net_return"],"engine":r["engine"],"asset":r["asset"]})
        accepted.append(r)
    close_due(datetime.max.replace(tzinfo=timezone.utc))
    d=[daily_pnl[k] for k in sorted(daily_pnl)]
    s=sd(d);sh=avg(d)/s*math.sqrt(365) if d and s not in (None,0) else None
    down=math.sqrt(avg([min(0,x)**2 for x in d])) if d else None
    so=avg(d)/down*math.sqrt(365) if d and down else None
    engine_counts=Counter(r["engine"] for r in accepted)
    asset_counts=Counter(r["asset"] for r in accepted)
    return {
      "trades":len(accepted),"final_equity":eq,
      "total_return_pct":100*(eq/START_EQUITY-1),
      "max_drawdown_pct":100*mdd,"sharpe":sh,"sortino":so,
      "daily_returns":dict(daily_pnl),
      "engine_counts":dict(engine_counts),"asset_counts":dict(asset_counts),
      "engine_concentration_hhi":hhi(engine_counts),
      "asset_concentration_hhi":hhi(asset_counts),
      "max_single_asset_share":(max(asset_counts.values())/len(accepted)) if accepted else None
    }

def main():
    bpath=os.getenv("BINANCE_DB","binance_avci2.db")
    opath=os.getenv("AVCI_DB","avci2.db")
    vpath=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
    rows=load_binance(bpath)+load_gate(opath,vpath)
    hold=[r for r in rows if dt(r["event_time"]) and dt(r["event_time"])>=dt(HOLDOUT_START)]
    days=sorted({dt(r["event_time"]).date().isoformat() for r in hold})
    bc=[];gc=[]; overlap_days=0
    for day in days:
        b=sum(r["engine"]=="BINANCE" and dt(r["event_time"]).date().isoformat()==day for r in hold)
        g=sum(r["engine"]=="GATE" and dt(r["event_time"]).date().isoformat()==day for r in hold)
        bc.append(b);gc.append(g)
        if b and g:overlap_days+=1
    weeks=defaultdict(lambda:[0,0])
    for r in hold:
        d=dt(r["event_time"]); y,w,_=d.isocalendar()
        weeks[(y,w)][0 if r["engine"]=="BINANCE" else 1]+=1
    overlap_weeks=sum(a>0 and b>0 for a,b in weeks.values())
    regimes=defaultdict(lambda:[0,0])
    for r in hold:
        regimes[r.get("regime") or "UNKNOWN"][0 if r["engine"]=="BINANCE" else 1]+=1

    btc=benchmark("BTCUSDT");sol=benchmark("SOLUSDT");port=simulate(hold)
    btc_beta=aligned_beta(port["daily_returns"],btc.get("daily_returns") or {})
    sol_beta=aligned_beta(port["daily_returns"],sol.get("daily_returns") or {})
    dc=corr(bc,gc)

    global_kill_reasons=[]
    if port["max_drawdown_pct"]>20.0:
        global_kill_reasons.append("GLOBAL_MAX_DRAWDOWN")
    if dc is not None and dc>=0.75 and overlap_days>=5:
        global_kill_reasons.append("CROSS_ENGINE_SIGNAL_CROWDING")
    if btc.get("status")=="OK" and port["total_return_pct"]<=btc["return_pct"]:
        global_kill_reasons.append("NO_EXCESS_VS_BTC")
    if port["max_single_asset_share"] is not None and port["max_single_asset_share"]>0.25 and port["trades"]>=20:
        global_kill_reasons.append("SINGLE_ASSET_CONCENTRATION")

    report={
      "version":VERSION,"generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "holdout_start":HOLDOUT_START,"events":len(hold),
      "binance_events":sum(r["engine"]=="BINANCE" for r in hold),
      "gate_events":sum(r["engine"]=="GATE" for r in hold),
      "same_day_overlap_days":overlap_days,"same_week_overlap_weeks":overlap_weeks,
      "daily_signal_count_correlation":dc,
      "regime_exposure":dict(regimes),
      "global_portfolio":port,
      "btc_buy_hold":{k:v for k,v in btc.items() if k!="daily_returns"},
      "sol_buy_hold":{k:v for k,v in sol.items() if k!="daily_returns"},
      "btc_beta":btc_beta,"sol_beta":sol_beta,
      "excess_vs_btc_pct":port["total_return_pct"]-btc["return_pct"] if btc.get("status")=="OK" else None,
      "excess_vs_sol_pct":port["total_return_pct"]-sol["return_pct"] if sol.get("status")=="OK" else None,
      "sector_concentration_status":"BLOCKED_UNTIL_FROZEN_SECTOR_TAXONOMY_IS_RECORDED",
      "global_kill_switch":"RESEARCH_ONLY" if global_kill_reasons else "ARMED_PAPER_ONLY",
      "global_kill_reasons":global_kill_reasons,
      "risk_note":"Global cap=5, per-engine cap=3, 72h capital lock. Beta uses matched daily realized portfolio PnL and benchmark close-to-close returns."
    }
    with sqlite3.connect("avci_global_governance.db") as c:
        c.execute("""CREATE TABLE IF NOT EXISTS global_governance(
          version TEXT PRIMARY KEY,generated_at_utc TEXT,report_json TEXT)""")
        c.execute("INSERT OR REPLACE INTO global_governance VALUES(?,?,?)",
                  (VERSION,report["generated_at_utc"],json.dumps(report,sort_keys=True)))
        c.commit()
    open("avci_global_governance.json","w",encoding="utf-8").write(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
