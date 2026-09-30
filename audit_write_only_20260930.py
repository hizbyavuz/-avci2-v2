#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write-only audit layer from the 2026-09-30 independent review.

Never changes frozen candidate rules, thresholds, scoring, Telegram labels,
entries/exits, holdout boundaries, or historical labels. It only adds
research tables/JSON diagnostics to copied/restored SQLite state.
"""
from __future__ import annotations
import json, math, os, sqlite3, sys
from datetime import datetime, timezone, timedelta

VERSION = "audit-writeonly-v1-20260930"
BLOCK_HOURS = 96
RUNNER_LEVELS = (20, 30, 50, 100, 200)

def dt(x):
    try: return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except Exception: return None

def f(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except Exception: return None

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def cols(c,t):
    return {r[1] for r in c.execute(f"PRAGMA table_info({t})")}

def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS audit_96h_blocks(
      source TEXT,event_key TEXT,event_time TEXT,asset TEXT,group_type TEXT,
      regime TEXT,block_id TEXT,block_size INTEGER,version TEXT,
      PRIMARY KEY(source,event_key,version));
    CREATE TABLE IF NOT EXISTS audit_outcome_semantics(
      source TEXT,event_key TEXT,mfe_before_stop REAL,mfe_full REAL,mae_before_stop REAL,
      terminal_state TEXT,cost_stress_2x REAL,notes TEXT,version TEXT,
      PRIMARY KEY(source,event_key,version));
    CREATE TABLE IF NOT EXISTS audit_runner_distribution(
      source TEXT,event_key TEXT,mfe_full REAL,hit_20 INTEGER,hit_30 INTEGER,hit_50 INTEGER,
      hit_100 INTEGER,hit_200 INTEGER,version TEXT,
      PRIMARY KEY(source,event_key,version));
    CREATE TABLE IF NOT EXISTS audit_hypothesis_registry(
      source TEXT,version TEXT,registered_at_utc TEXT,primary_metric TEXT,
      clustering_rule TEXT,control_rule TEXT,exclusions TEXT,decision_rule TEXT,
      code_sha TEXT,notes TEXT,PRIMARY KEY(source,version));
    """)
    c.commit()

def assign_blocks(c,source,rows):
    c.execute("DELETE FROM audit_96h_blocks WHERE source=? AND version=?",(source,VERSION))
    ordered=sorted([r for r in rows if dt(r["time"])], key=lambda r: dt(r["time"]))
    blocks=[]; cur=[]; end=None
    for r in ordered:
        t=dt(r["time"])
        if end is None or t <= end:
            cur.append(r); end=max(end or t, t+timedelta(hours=BLOCK_HOURS))
        else:
            blocks.append(cur); cur=[r]; end=t+timedelta(hours=BLOCK_HOURS)
    if cur: blocks.append(cur)
    for i,b in enumerate(blocks,1):
        bid=f"{source}-96H-{i:05d}"
        for r in b:
            c.execute("INSERT OR REPLACE INTO audit_96h_blocks VALUES(?,?,?,?,?,?,?,?,?)",
                      (source,r["key"],r["time"],r["asset"],r["group"],r.get("regime"),bid,len(b),VERSION))
    c.commit()
    return len(blocks)

def binance_rows(c):
    if not (table(c,"signal_events") and table(c,"outcome_labels")): return []
    sc=cols(c,"signal_events"); oc=cols(c,"outcome_labels")
    wanted=["event_id","symbol","signal_time_utc","event_class","btc_regime","fee_bps_per_side","slippage_bps_per_side"]
    sel=[x for x in wanted if x in sc]
    ow=["event_id","net_return_pct","mfe_pct","mae_pct","primary_exit_reason","label_status"]
    osel=[x for x in ow if x in oc]
    q=f"SELECT {','.join('s.'+x for x in sel)},{','.join('o.'+x for x in osel)} FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id"
    rows=[]
    for r in c.execute(q):
        d=dict(zip(sel+osel,r))
        if d.get("label_status") not in (None,"CLOSED"): continue
        rows.append({"key":str(d.get("event_id")),"asset":d.get("symbol"),"time":d.get("signal_time_utc"),
                     "group":d.get("event_class"),"regime":d.get("btc_regime"),"net":f(d.get("net_return_pct")),
                     "mfe":f(d.get("mfe_pct")),"mae":f(d.get("mae_pct")),"exit":d.get("primary_exit_reason"),
                     "fee":f(d.get("fee_bps_per_side")),"slip":f(d.get("slippage_bps_per_side"))})
    return rows

def gate_rows(v):
    if not table(v,"validation_events"): return []
    vc=cols(v,"validation_events")
    names=["id","token_contract","network_id","signal_iso","group_type","btc_regime","net_final_pct","mfe_pct","mae_pct","status","estimated_total_cost_pct"]
    sel=[x for x in names if x in vc]
    rows=[]
    for r in v.execute("SELECT "+",".join(sel)+" FROM validation_events"):
        d=dict(zip(sel,r))
        if d.get("status") not in (None,"CLOSED_72H"): continue
        rows.append({"key":str(d.get("id")),"asset":f"{d.get('network_id')}:{d.get('token_contract')}",
                     "time":d.get("signal_iso"),"group":d.get("group_type"),"regime":d.get("btc_regime"),
                     "net":f(d.get("net_final_pct")),"mfe":f(d.get("mfe_pct")),"mae":f(d.get("mae_pct")),
                     "cost":f(d.get("estimated_total_cost_pct"))})
    return rows

def write_semantics(c,source,rows):
    c.execute("DELETE FROM audit_outcome_semantics WHERE source=? AND version=?",(source,VERSION))
    c.execute("DELETE FROM audit_runner_distribution WHERE source=? AND version=?",(source,VERSION))
    for r in rows:
        mfe=r.get("mfe")
        terminal="CLOSED" if r.get("net") is not None else "UNRESOLVED"
        stress=None
        if source=="BINANCE" and r.get("net") is not None and r.get("fee") is not None and r.get("slip") is not None:
            baseline=2.0*(r["fee"]+r["slip"])/100.0
            stress=r["net"]-baseline
        elif source=="GATE" and r.get("net") is not None and r.get("cost") is not None:
            stress=r["net"]-r["cost"]
        c.execute("INSERT OR REPLACE INTO audit_outcome_semantics VALUES(?,?,?,?,?,?,?,?,?)",
                  (source,r["key"],None,mfe,None,terminal,stress,
                   "mfe_before_stop unavailable in source schema; full MFE retained only",VERSION))
        hits=[int(mfe is not None and mfe>=x) for x in RUNNER_LEVELS]
        c.execute("INSERT OR REPLACE INTO audit_runner_distribution VALUES(?,?,?,?,?,?,?,?,?)",
                  (source,r["key"],mfe,*hits,VERSION))
    c.commit()

def register(c,source):
    sha=os.getenv("GITHUB_SHA") or "LOCAL_UNKNOWN"
    c.execute("INSERT OR REPLACE INTO audit_hypothesis_registry VALUES(?,?,?,?,?,?,?,?,?,?)",
              (source,VERSION,datetime.now(timezone.utc).isoformat(),
               "cost-adjusted matched-control episode-level return difference",
               "96-hour blocks (72h horizon + 24h embargo); merge if shock spans blocks",
               "parallel matched controls only; frozen original controls remain untouched",
               "unresolved/data-failure excluded from primary and reported separately",
               "no live-money inference from audit tables; FINAL_TEST rules remain frozen",
               sha,"2026-09-30 independent-audit write-only layer; no scanner mutation"))
    c.commit()

def run_binance(path):
    if not os.path.exists(path): return {"source":"BINANCE","status":"MISSING_DB"}
    with sqlite3.connect(path,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c); rows=binance_rows(c)
        blocks=assign_blocks(c,"BINANCE",rows); write_semantics(c,"BINANCE",rows); register(c,"BINANCE")
        return {"source":"BINANCE","events":len(rows),"blocks96h":blocks}

def run_gate(obs_path,val_path):
    if not (os.path.exists(obs_path) and os.path.exists(val_path)): return {"source":"GATE","status":"MISSING_DB"}
    with sqlite3.connect(obs_path,timeout=30) as o, sqlite3.connect(val_path,timeout=30) as v:
        o.row_factory=v.row_factory=sqlite3.Row; init(o); rows=gate_rows(v)
        blocks=assign_blocks(o,"GATE",rows); write_semantics(o,"GATE",rows); register(o,"GATE")
        return {"source":"GATE","events":len(rows),"blocks96h":blocks}

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "all").lower(); out=[]
    if mode in ("all","binance"): out.append(run_binance(os.getenv("BINANCE_DB","binance_avci2.db")))
    if mode in ("all","gate"): out.append(run_gate(os.getenv("AVCI_DB","avci2.db"),os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")))
    print(json.dumps({"version":VERSION,"results":out},ensure_ascii=False))

if __name__=="__main__": main()
