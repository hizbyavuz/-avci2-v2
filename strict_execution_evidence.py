#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write-only strict execution evidence ledger.

Never changes candidate membership, scanner thresholds, frozen labels, targets,
stops or existing returns. It only records how strong the execution evidence is
for each already-existing event and computes a stricter return only when the
underlying execution evidence supports it.
"""
from __future__ import annotations
import os, sqlite3, sys
from datetime import datetime, timezone

VERSION="strict-execution-evidence-v1-20260930"

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def cols(c,t):
    return {r[1] for r in c.execute(f"PRAGMA table_info({t})")} if table(c,t) else set()

def f(v):
    try: return float(v)
    except (TypeError,ValueError): return None

def now():
    return datetime.now(timezone.utc).isoformat()

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS strict_execution_evidence(
      source TEXT NOT NULL,event_key TEXT NOT NULL,event_time TEXT,
      group_type TEXT,model_net_pct REAL,
      assumed_cost_pct REAL,signal_observed_cost_pct REAL,
      barrier_time_quote_cost_pct REAL,realized_cost_pct REAL,
      strict_net_pct REAL,evidence_grade TEXT NOT NULL,
      barrier_time_verified INTEGER NOT NULL DEFAULT 0,
      notes TEXT NOT NULL,version TEXT NOT NULL,created_at_utc TEXT NOT NULL,
      PRIMARY KEY(source,event_key,version)
    )""")
    c.commit()

def realized(c,source,key):
    if not table(c,"execution_fill_observations"): return None
    cc=cols(c,"execution_fill_observations")
    if not {"source","event_key","total_realized_cost_pct"}.issubset(cc): return None
    r=c.execute("""SELECT SUM(total_realized_cost_pct) FROM execution_fill_observations
      WHERE source=? AND event_key=?""",(source,str(key))).fetchone()
    return f(r[0]) if r and r[0] is not None else None

def run_binance(path):
    with sqlite3.connect(path,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c)
        if not table(c,"signal_events") or not table(c,"outcome_labels"): return 0
        rows=c.execute("""SELECT s.event_id,s.signal_time_utc,s.event_class,
          s.fee_bps_per_side,s.slippage_bps_per_side,s.spread_bps,
          s.buy_impact_1k_bps,s.sell_impact_1k_bps,
          o.net_return_pct,o.primary_exit_time_utc
          FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
          WHERE o.label_status='CLOSED'
            AND s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')""").fetchall()
        n=0
        for r in rows:
            fee=f(r["fee_bps_per_side"]); slip=f(r["slippage_bps_per_side"])
            assumed=(2*((fee or 0)+(slip or 0))/100.0) if fee is not None and slip is not None else None
            observed_parts=[f(r["spread_bps"]),f(r["buy_impact_1k_bps"]),f(r["sell_impact_1k_bps"])]
            signal_obs=sum(x for x in observed_parts if x is not None)/100.0 if any(x is not None for x in observed_parts) else None
            real=realized(c,"BINANCE",r["event_id"])
            model=f(r["net_return_pct"])
            # Only realized fill evidence is strong enough here to replace the frozen
            # modeled round-trip cost. Signal-time book data is recorded, not promoted
            # to barrier-time evidence.
            strict=None; grade="MODEL_ONLY"; verified=0
            notes="No barrier-time executable exit evidence; frozen modeled net retained separately."
            if real is not None and model is not None and assumed is not None:
                strict=model + assumed - real
                grade="REALIZED_FILL"; verified=1
                notes="Strict net replaces frozen assumed round-trip cost with independently imported realized cost."
            elif signal_obs is not None:
                grade="SIGNAL_TIME_QUOTE"
                notes="Signal-time spread/book impact observed; not equivalent to barrier-time exit."
            c.execute("""INSERT OR REPLACE INTO strict_execution_evidence VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("BINANCE",r["event_id"],r["signal_time_utc"],r["event_class"],model,
               assumed,signal_obs,None,real,strict,grade,verified,notes,VERSION,now()))
            n+=1
        c.commit(); return n

def run_gate(obs_path,val_path):
    with sqlite3.connect(obs_path,timeout=30) as obs, sqlite3.connect(val_path,timeout=30) as val:
        obs.row_factory=val.row_factory=sqlite3.Row; init(obs)
        if not table(val,"validation_events"): return 0
        vc=cols(val,"validation_events")
        needed={"id","signal_iso","group_type","net_final_pct","estimated_total_cost_pct"}
        if not needed.issubset(vc): return 0
        extra=[]
        for x in ("exit_loss_pct","cost_status"):
            extra.append(x if x in vc else f"NULL AS {x}")
        rows=val.execute(f"""SELECT id,signal_iso,group_type,net_final_pct,
          estimated_total_cost_pct,{','.join(extra)}
          FROM validation_events
          WHERE status='CLOSED_72H'
            AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')""").fetchall()
        n=0
        for r in rows:
            model=f(r["net_final_pct"]); assumed=f(r["estimated_total_cost_pct"])
            quote=f(r["exit_loss_pct"]) if "exit_loss_pct" in r.keys() else None
            real=realized(obs,"GATE",r["id"])
            strict=None; grade="MODEL_ONLY"; verified=0
            notes="Recorded Gate cost is not assumed to be a barrier-time realized fill."
            if real is not None and model is not None and assumed is not None:
                strict=model + assumed - real
                grade="REALIZED_FILL"; verified=1
                notes="Strict net replaces modeled total cost with independently imported realized cost."
            elif quote is not None:
                grade="SIGNAL_TIME_QUOTE"
                notes="DEX quote evidence exists, but barrier-time timestamp equivalence is not proven by this table."
            c.execute("""INSERT OR REPLACE INTO strict_execution_evidence VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("GATE",str(r["id"]),r["signal_iso"],r["group_type"],model,assumed,
               quote,None,real,strict,grade,verified,notes,VERSION,now()))
            n+=1
        obs.commit(); return n

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "all").lower()
    total=0
    if mode in ("all","binance"):
        b=os.getenv("BINANCE_DB","binance_avci2.db")
        if os.path.exists(b): total+=run_binance(b)
    if mode in ("all","gate"):
        g=os.getenv("AVCI_DB","avci2.db"); v=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
        if os.path.exists(g) and os.path.exists(v): total+=run_gate(g,v)
    print(f"strict execution evidence | rows={total} | version={VERSION}")

if __name__=="__main__":
    main()
