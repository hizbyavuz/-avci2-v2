#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shadow paper-trade cohort for readiness-qualified Avci signals.

No orders. Tracks only signals that pass PAPER_ELIGIBLE so the new stack can be
validated separately from the original scanner candidates.
"""
import os, sqlite3, sys
from datetime import datetime, timezone, timedelta

VERSION="shadow-paper-v1-20260926"
HOURS=72

def now(): return datetime.now(timezone.utc)
def parse_dt(x):
    try: return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception: return None
def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS readiness_shadow_trades(
      source TEXT NOT NULL,
      batch_key TEXT NOT NULL,
      asset_key TEXT NOT NULL,
      display_name TEXT NOT NULL,
      signal_time_utc TEXT NOT NULL,
      entry_price REAL NOT NULL,
      due_at_utc TEXT NOT NULL,
      latest_price REAL,
      peak_price REAL,
      trough_price REAL,
      mfe_pct REAL,
      mae_pct REAL,
      final_return_pct REAL,
      status TEXT NOT NULL DEFAULT 'OPEN',
      closed_at_utc TEXT,
      version TEXT NOT NULL,
      PRIMARY KEY(source,batch_key,asset_key,version)
    )""")
def pct(a,b): return 100*(float(b)/float(a)-1) if a and b else None

def register_binance(c):
    if not table(c,"trade_readiness"): return 0
    rows=c.execute("""SELECT tr.*,f.price FROM trade_readiness tr
      JOIN features f ON f.scan_time_utc=tr.batch_key AND f.symbol=tr.asset_key
      WHERE tr.source='BINANCE' AND tr.readiness='PAPER_ELIGIBLE'
      ORDER BY tr.created_at_utc DESC""").fetchall()
    n=0
    for r in rows:
        if not r["price"]: continue
        t=parse_dt(r["batch_key"])
        if not t: continue
        c.execute("""INSERT OR IGNORE INTO readiness_shadow_trades
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("BINANCE",r["batch_key"],r["asset_key"],r["display_name"],t.isoformat(),
           float(r["price"]),(t+timedelta(hours=HOURS)).isoformat(),float(r["price"]),
           float(r["price"]),float(r["price"]),0.0,0.0,None,"OPEN",None,VERSION))
        n+=c.execute("SELECT changes()").fetchone()[0]
    return n

def register_gate(c):
    if not table(c,"trade_readiness"): return 0
    rows=c.execute("""SELECT * FROM trade_readiness WHERE source='GATE'
      AND readiness='PAPER_ELIGIBLE' ORDER BY created_at_utc DESC""").fetchall()
    n=0
    for r in rows:
        try: net,contract=r["asset_key"].split(":",1)
        except ValueError:
            contract=r["asset_key"]; net=None
        q="""SELECT price,scan_ts FROM gate_early_observations
             WHERE token_contract=? AND price IS NOT NULL"""
        args=[contract]
        if net:
            q+=" AND network_id=?"; args.append(net)
        q+=" ORDER BY scan_ts DESC LIMIT 1"
        p=c.execute(q,args).fetchone() if table(c,"gate_early_observations") else None
        if not p or not p["price"]: continue
        t=datetime.fromtimestamp(int(p["scan_ts"]),timezone.utc)
        price=float(p["price"])
        c.execute("""INSERT OR IGNORE INTO readiness_shadow_trades
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("GATE",r["batch_key"],r["asset_key"],r["display_name"],t.isoformat(),
           price,(t+timedelta(hours=HOURS)).isoformat(),price,price,price,0.0,0.0,
           None,"OPEN",None,VERSION))
        n+=c.execute("SELECT changes()").fetchone()[0]
    return n

def update_binance(c,r):
    start=parse_dt(r["signal_time_utc"]); end=min(now(),parse_dt(r["due_at_utc"]) or now())
    if not start: return
    lo=int(start.timestamp()*1000); hi=int(end.timestamp()*1000)
    k=c.execute("""SELECT MAX(high_price),MIN(low_price),MAX(close_price)
      FROM raw_klines WHERE symbol=? AND interval_value='5m'
      AND open_time_ms>=? AND open_time_ms<=?""",(r["asset_key"],lo,hi)).fetchone() if table(c,"raw_klines") else None
    latest=c.execute("""SELECT price FROM features WHERE symbol=? AND price IS NOT NULL
      ORDER BY scan_time_utc DESC LIMIT 1""",(r["asset_key"],)).fetchone()
    peak=float(k[0]) if k and k[0] is not None else float(r["peak_price"] or r["entry_price"])
    trough=float(k[1]) if k and k[1] is not None else float(r["trough_price"] or r["entry_price"])
    last=float(latest[0]) if latest and latest[0] else float(r["latest_price"] or r["entry_price"])
    close=end>=parse_dt(r["due_at_utc"])
    c.execute("""UPDATE readiness_shadow_trades SET latest_price=?,peak_price=?,trough_price=?,
      mfe_pct=?,mae_pct=?,final_return_pct=?,status=?,closed_at_utc=? WHERE source=? AND batch_key=?
      AND asset_key=? AND version=?""",
      (last,max(peak,float(r["peak_price"] or peak)),min(trough,float(r["trough_price"] or trough)),
       pct(r["entry_price"],max(peak,float(r["peak_price"] or peak))),
       pct(r["entry_price"],min(trough,float(r["trough_price"] or trough))),
       pct(r["entry_price"],last) if close else None,"CLOSED" if close else "OPEN",
       now().isoformat() if close else None,"BINANCE",r["batch_key"],r["asset_key"],VERSION))

def update_gate(c,r):
    try: net,contract=r["asset_key"].split(":",1)
    except ValueError: net=None; contract=r["asset_key"]
    start=parse_dt(r["signal_time_utc"]); end=min(now(),parse_dt(r["due_at_utc"]) or now())
    if not start: return
    q="""SELECT MAX(price),MIN(price) FROM gate_early_observations
         WHERE token_contract=? AND scan_ts>=? AND scan_ts<=? AND price IS NOT NULL"""
    args=[contract,int(start.timestamp()),int(end.timestamp())]
    if net:
        q=q.replace("WHERE token_contract=?","WHERE token_contract=? AND network_id=?")
        args=[contract,net,int(start.timestamp()),int(end.timestamp())]
    mm=c.execute(q,args).fetchone() if table(c,"gate_early_observations") else None
    q2="""SELECT price FROM gate_early_observations WHERE token_contract=? AND price IS NOT NULL"""
    a2=[contract]
    if net:
        q2+=" AND network_id=?"; a2.append(net)
    q2+=" ORDER BY scan_ts DESC LIMIT 1"
    latest=c.execute(q2,a2).fetchone() if table(c,"gate_early_observations") else None
    peak=float(mm[0]) if mm and mm[0] is not None else float(r["peak_price"] or r["entry_price"])
    trough=float(mm[1]) if mm and mm[1] is not None else float(r["trough_price"] or r["entry_price"])
    last=float(latest[0]) if latest and latest[0] else float(r["latest_price"] or r["entry_price"])
    close=end>=parse_dt(r["due_at_utc"])
    peak=max(peak,float(r["peak_price"] or peak)); trough=min(trough,float(r["trough_price"] or trough))
    c.execute("""UPDATE readiness_shadow_trades SET latest_price=?,peak_price=?,trough_price=?,
      mfe_pct=?,mae_pct=?,final_return_pct=?,status=?,closed_at_utc=? WHERE source=? AND batch_key=?
      AND asset_key=? AND version=?""",
      (last,peak,trough,pct(r["entry_price"],peak),pct(r["entry_price"],trough),
       pct(r["entry_price"],last) if close else None,"CLOSED" if close else "OPEN",
       now().isoformat() if close else None,"GATE",r["batch_key"],r["asset_key"],VERSION))

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if mode not in ("binance","gate"): raise SystemExit("usage: readiness_shadow_paper.py binance|gate")
    db=os.getenv("BINANCE_DB","binance_avci2.db") if mode=="binance" else os.getenv("AVCI_DB","avci2.db")
    if not os.path.exists(db): return
    with sqlite3.connect(db,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c)
        added=register_binance(c) if mode=="binance" else register_gate(c)
        rows=c.execute("""SELECT * FROM readiness_shadow_trades WHERE source=? AND status='OPEN'
          AND version=?""",(mode.upper(),VERSION)).fetchall()
        for r in rows:
            update_binance(c,r) if mode=="binance" else update_gate(c,r)
        c.commit()
    print(f"shadow paper | {mode} | new={added} open_checked={len(rows)}")

if __name__=="__main__": main()
