#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Human decision ledger separated from system research outcomes.

Records manual ENTRY/EXIT/SKIP/OVERRIDE decisions with the system state that
was visible at decision time. Never modifies signal outcomes or scanner scores.
"""
import argparse,json,os,sqlite3
from datetime import datetime,timezone

BDB=os.getenv("BINANCE_DB","binance_avci2.db")
GDB=os.getenv("AVCI_DB","avci2.db")

def now():return datetime.now(timezone.utc).isoformat()
def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS human_decision_ledger(
      id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,event_key TEXT NOT NULL,
      asset_key TEXT,action TEXT NOT NULL,price REAL,quantity REAL,notes TEXT,
      system_mode TEXT,trade_readiness TEXT,system_snapshot_json TEXT NOT NULL,
      decided_at_utc TEXT NOT NULL,actor TEXT NOT NULL DEFAULT 'USER',
      UNIQUE(source,event_key,action,decided_at_utc))""")
    c.commit()

def snapshot(c,source,event):
    out={}
    if source=="BINANCE" and table(c,"signal_events"):
        r=c.execute("SELECT * FROM signal_events WHERE event_id=?",(event,)).fetchone()
        if r:out["signal_event"]=dict(r);asset=r["symbol"]
        else:asset=None
    elif source=="GATE":
        asset=None
        if table(c,"trade_readiness"):
            r=c.execute("""SELECT * FROM trade_readiness WHERE source='GATE'
              AND asset_key=? ORDER BY created_at_utc DESC LIMIT 1""",(event,)).fetchone()
            if r:out["trade_readiness"]=dict(r);asset=r["asset_key"]
    else:asset=None
    if table(c,"decision_discipline_state"):
        r=c.execute("""SELECT * FROM decision_discipline_state WHERE source=?
          ORDER BY run_utc DESC LIMIT 1""",(source,)).fetchone()
        if r:out["decision_discipline"]=dict(r)
    if table(c,"institutional_signal_overlay"):
        if source=="BINANCE":
            r=c.execute("""SELECT * FROM institutional_signal_overlay WHERE source='BINANCE'
              AND asset_key=(SELECT symbol FROM signal_events WHERE event_id=?)
              ORDER BY created_at_utc DESC LIMIT 1""",(event,)).fetchone()
        else:
            r=c.execute("""SELECT * FROM institutional_signal_overlay WHERE source='GATE'
              AND asset_key=? ORDER BY created_at_utc DESC LIMIT 1""",(event,)).fetchone()
        if r:out["institutional_overlay"]=dict(r)
    return asset,out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",choices=["BINANCE","GATE"],required=True)
    ap.add_argument("--event-key",required=True)
    ap.add_argument("--action",choices=["ENTRY","EXIT","SKIP","OVERRIDE"],required=True)
    ap.add_argument("--price",type=float)
    ap.add_argument("--quantity",type=float)
    ap.add_argument("--notes",default="")
    args=ap.parse_args()
    db=BDB if args.source=="BINANCE" else GDB
    if not os.path.exists(db):raise SystemExit("DB missing")
    with sqlite3.connect(db,timeout=30) as c:
        c.row_factory=sqlite3.Row;init(c)
        asset,snap=snapshot(c,args.source,args.event_key)
        mode=None;ready=None
        dd=snap.get("decision_discipline") or {}
        if dd:mode="RESEARCH_ONLY" if dd.get("kill_switch_status")=="RESEARCH_ONLY" else dd.get("go_live_status")
        ov=snap.get("institutional_overlay") or {}
        if ov:mode=ov.get("system_mode") or mode
        tr=snap.get("trade_readiness") or {}
        if tr:ready=tr.get("readiness")
        c.execute("""INSERT INTO human_decision_ledger
          (source,event_key,asset_key,action,price,quantity,notes,system_mode,trade_readiness,
           system_snapshot_json,decided_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (args.source,args.event_key,asset,args.action,args.price,args.quantity,args.notes,
           mode,ready,json.dumps(snap,default=str,ensure_ascii=False),now()))
        c.commit()
        print({"source":args.source,"event":args.event_key,"action":args.action,
               "system_mode":mode,"asset":asset})
if __name__=="__main__":main()
