#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recent-performance brake for Avci.

Research-only. It never places orders and never changes frozen scanner rules.
If the most recent readiness-qualified paper cohort deteriorates materially,
the capital layer is forced closed until evidence recovers.
"""
import json, os, sqlite3, sys
from datetime import datetime, timezone

VERSION="performance-brake-v1-20260927"
LOOKBACK=20
MIN_N=10
MIN_EXPECTANCY=-0.10
MIN_PROFIT_FACTOR=0.80
MAX_CONSECUTIVE_LOSSES=5

def table(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def now(): return datetime.now(timezone.utc).isoformat()
def pf(xs):
    pos=sum(x for x in xs if x>0); neg=-sum(x for x in xs if x<0)
    if neg<=0: return 999.0 if pos>0 else None
    return pos/neg
def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS performance_brake(
      source TEXT PRIMARY KEY,status TEXT NOT NULL,n INTEGER NOT NULL,
      expectancy_pct REAL,profit_factor REAL,consecutive_losses INTEGER NOT NULL,
      reasons_json TEXT NOT NULL,version TEXT NOT NULL,created_at_utc TEXT NOT NULL
    )""");c.commit()
def run(c,source):
    if not table(c,"readiness_shadow_trades"):
        vals=[]
    else:
        rows=c.execute("""SELECT final_return_pct FROM readiness_shadow_trades
          WHERE source=? AND status='CLOSED' AND final_return_pct IS NOT NULL
          ORDER BY closed_at_utc DESC LIMIT ?""",(source,LOOKBACK)).fetchall()
        vals=[float(r[0]) for r in rows]
    exp=sum(vals)/len(vals) if vals else None
    p=pf(vals)
    losses=0
    for x in vals:
        if x<0: losses+=1
        else: break
    reasons=[]
    strong=[]
    if table(c,"trader_label_ledger"):
        sr=c.execute("""SELECT final_return_pct FROM trader_label_ledger
          WHERE source=? AND label='GÜÇLÜ' AND status='CLOSED'
            AND final_return_pct IS NOT NULL
          ORDER BY closed_at_utc DESC LIMIT ?""",(source,LOOKBACK)).fetchall()
        strong=[float(r[0]) for r in sr]
    if len(vals)>=MIN_N:
        if exp is not None and exp<MIN_EXPECTANCY: reasons.append(f"son {len(vals)} paper işlem beklentisi negatif (%{exp:.2f})")
        if p is not None and p<MIN_PROFIT_FACTOR: reasons.append(f"son dönem profit factor düşük ({p:.2f})")
        if losses>=MAX_CONSECUTIVE_LOSSES: reasons.append(f"arka arkaya {losses} paper kaybı")
    if len(strong)>=MIN_N:
        se=sum(strong)/len(strong); sp=pf(strong)
        sl=0
        for x in strong:
            if x<0: sl+=1
            else: break
        if se<MIN_EXPECTANCY: reasons.append(f"son GÜÇLÜ sinyallerin beklentisi negatif (%{se:.2f})")
        if sp is not None and sp<MIN_PROFIT_FACTOR: reasons.append(f"GÜÇLÜ sinyallerin profit factorı düşük ({sp:.2f})")
        if sl>=MAX_CONSECUTIVE_LOSSES: reasons.append(f"arka arkaya {sl} GÜÇLÜ sinyal kaybı")
    enough=max(len(vals),len(strong))
    status="ENGAGED" if reasons else ("MONITORING" if enough<MIN_N else "CLEAR")
    c.execute("""INSERT OR REPLACE INTO performance_brake VALUES(?,?,?,?,?,?,?,?,?)""",
      (source,status,len(vals),exp,p,losses,json.dumps(reasons,ensure_ascii=False),VERSION,now()))
    c.commit()
    print(f"{source} performance brake={status} n={len(vals)}")
def main():
    m=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if m not in ("binance","gate"): raise SystemExit("usage: performance_brake.py binance|gate")
    db=os.getenv("BINANCE_DB","binance_avci2.db") if m=="binance" else os.getenv("AVCI_DB","avci2.db")
    if not os.path.exists(db): return
    with sqlite3.connect(db,timeout=60) as c: run(c,m.upper())
if __name__=="__main__":main()
