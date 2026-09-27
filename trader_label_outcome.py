#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Follow up the exact labels shown in trader Telegram messages.

The message scripts register displayed GÜÇLÜ/ORTA/ZAYIF rows.
This updater measures the subsequent 72h path so the user-facing labels
can be calibrated directly rather than inferred from upstream candidates.
"""
import os, sqlite3, sys
from datetime import datetime, timezone

HOURS=72
def now(): return datetime.now(timezone.utc)
def parse_dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None
def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def pct(a,b):return 100*(float(b)/float(a)-1) if a and b else None

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS trader_label_ledger(
      source TEXT NOT NULL,batch_key TEXT NOT NULL,asset_key TEXT NOT NULL,
      display_name TEXT NOT NULL,label TEXT NOT NULL,signal_time_utc TEXT NOT NULL,
      entry_price REAL,due_at_utc TEXT NOT NULL,latest_price REAL,peak_price REAL,
      trough_price REAL,mfe_pct REAL,mae_pct REAL,final_return_pct REAL,
      status TEXT NOT NULL DEFAULT 'OPEN',closed_at_utc TEXT,
      PRIMARY KEY(source,batch_key,asset_key)
    )""");c.commit()

def update_binance(c,r):
    start=parse_dt(r["signal_time_utc"]); due=parse_dt(r["due_at_utc"]); end=min(now(),due or now())
    if not start or not r["entry_price"]:return
    lo=int(start.timestamp()*1000); hi=int(end.timestamp()*1000)
    mm=c.execute("""SELECT MAX(high_price),MIN(low_price) FROM raw_klines
      WHERE symbol=? AND interval_value='5m' AND open_time_ms>=? AND open_time_ms<=?""",
      (r["asset_key"],lo,hi)).fetchone() if table(c,"raw_klines") else None
    last=c.execute("""SELECT price FROM features WHERE symbol=? AND price IS NOT NULL
      ORDER BY scan_time_utc DESC LIMIT 1""",(r["asset_key"],)).fetchone()
    peak=float(mm[0]) if mm and mm[0] is not None else float(r["peak_price"] or r["entry_price"])
    trough=float(mm[1]) if mm and mm[1] is not None else float(r["trough_price"] or r["entry_price"])
    latest=float(last[0]) if last and last[0] else float(r["latest_price"] or r["entry_price"])
    close=due is not None and now()>=due
    c.execute("""UPDATE trader_label_ledger SET latest_price=?,peak_price=?,trough_price=?,
      mfe_pct=?,mae_pct=?,final_return_pct=?,status=?,closed_at_utc=?
      WHERE source='BINANCE' AND batch_key=? AND asset_key=?""",
      (latest,peak,trough,pct(r["entry_price"],peak),pct(r["entry_price"],trough),
       pct(r["entry_price"],latest) if close else None,"CLOSED" if close else "OPEN",
       now().isoformat() if close else None,r["batch_key"],r["asset_key"]))

def update_gate(c,r):
    try:net,contract=r["asset_key"].split(":",1)
    except ValueError:return
    start=parse_dt(r["signal_time_utc"]); due=parse_dt(r["due_at_utc"]); end=min(now(),due or now())
    if not start or not r["entry_price"]:return
    mm=c.execute("""SELECT MAX(price),MIN(price) FROM gate_early_observations
      WHERE network_id=? AND token_contract=? AND scan_ts>=? AND scan_ts<=?
        AND price IS NOT NULL""",(net,contract,int(start.timestamp()),int(end.timestamp()))).fetchone() if table(c,"gate_early_observations") else None
    last=c.execute("""SELECT price FROM gate_early_observations WHERE network_id=?
      AND token_contract=? AND price IS NOT NULL ORDER BY scan_ts DESC LIMIT 1""",(net,contract)).fetchone() if table(c,"gate_early_observations") else None
    peak=float(mm[0]) if mm and mm[0] is not None else float(r["peak_price"] or r["entry_price"])
    trough=float(mm[1]) if mm and mm[1] is not None else float(r["trough_price"] or r["entry_price"])
    latest=float(last[0]) if last and last[0] else float(r["latest_price"] or r["entry_price"])
    close=due is not None and now()>=due
    c.execute("""UPDATE trader_label_ledger SET latest_price=?,peak_price=?,trough_price=?,
      mfe_pct=?,mae_pct=?,final_return_pct=?,status=?,closed_at_utc=?
      WHERE source='GATE' AND batch_key=? AND asset_key=?""",
      (latest,peak,trough,pct(r["entry_price"],peak),pct(r["entry_price"],trough),
       pct(r["entry_price"],latest) if close else None,"CLOSED" if close else "OPEN",
       now().isoformat() if close else None,r["batch_key"],r["asset_key"]))

def main():
    m=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if m not in ("binance","gate"):raise SystemExit("usage: trader_label_outcome.py binance|gate")
    db=os.getenv("BINANCE_DB","binance_avci2.db") if m=="binance" else os.getenv("AVCI_DB","avci2.db")
    if not os.path.exists(db):return
    with sqlite3.connect(db,timeout=60) as c:
        c.row_factory=sqlite3.Row;init(c)
        rows=c.execute("SELECT * FROM trader_label_ledger WHERE source=? AND status='OPEN'",(m.upper(),)).fetchall()
        for r in rows:
            update_binance(c,r) if m=="binance" else update_gate(c,r)
        c.commit()
    print(f"trader label outcome | {m} | checked={len(rows)}")
if __name__=="__main__":main()
