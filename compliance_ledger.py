#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jurisdiction-neutral compliance/accounting ledger.

This module does not determine tax liability and does not give legal or tax
advice. It preserves transaction facts needed for later jurisdiction-specific
accounting: fills, fees, funding/network costs, transfers, realized P&L links,
order/tx identifiers, and source evidence.

No order placement. No tax classification is inferred automatically.
"""
from __future__ import annotations
import argparse, json, os, sqlite3
from datetime import datetime, timezone

BDB=os.getenv("BINANCE_DB","binance_avci2.db")
GDB=os.getenv("AVCI_DB","avci2.db")

def now(): return datetime.now(timezone.utc).isoformat()
def table(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS compliance_transactions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT NOT NULL,
      event_key TEXT NOT NULL DEFAULT '',
      asset TEXT,
      event_time_utc TEXT NOT NULL,
      record_type TEXT NOT NULL,
      side TEXT,
      quantity REAL,
      unit_price REAL,
      gross_value REAL,
      fee_value REAL,
      fee_pct REAL,
      funding_value REAL,
      network_fee_value REAL,
      realized_pnl_value REAL,
      currency TEXT,
      external_order_id TEXT NOT NULL DEFAULT '',
      txid TEXT NOT NULL DEFAULT '',
      jurisdiction TEXT,
      tax_status TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
      evidence_type TEXT NOT NULL,
      notes TEXT,
      imported_at_utc TEXT NOT NULL,
      UNIQUE(source,record_type,event_time_utc,external_order_id,txid,event_key)
    );
    CREATE TABLE IF NOT EXISTS compliance_transfers(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT NOT NULL,
      event_time_utc TEXT NOT NULL,
      asset TEXT NOT NULL,
      quantity REAL NOT NULL,
      direction TEXT NOT NULL,
      from_account TEXT,
      to_account TEXT,
      network TEXT,
      fee_value REAL,
      txid TEXT NOT NULL DEFAULT '',
      jurisdiction TEXT,
      tax_status TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
      notes TEXT,
      imported_at_utc TEXT NOT NULL,
      UNIQUE(source,event_time_utc,asset,direction,txid)
    );
    """)
    c.commit()

def sync_fill_table(c,source):
    if not table(c,"execution_fill_observations"):
        return 0
    rows=c.execute("""SELECT source,event_key,asset,event_time_utc,side,quantity,
      filled_price,fee_pct,total_realized_cost_pct,external_order_id,notes
      FROM execution_fill_observations WHERE source=?""",(source,)).fetchall()
    n=0
    for r in rows:
        qty=float(r[5]) if r[5] is not None else None
        px=float(r[6]) if r[6] is not None else None
        gross=(qty*px) if qty is not None and px is not None else None
        fee_pct=float(r[7]) if r[7] is not None else None
        fee_value=(gross*fee_pct/100.0) if gross is not None and fee_pct is not None else None
        notes=(r[10] or "")
        if r[8] is not None:
            notes=(notes+" | total_realized_cost_pct="+str(r[8])).strip(" |")
        c.execute("""INSERT OR IGNORE INTO compliance_transactions
          (source,event_key,asset,event_time_utc,record_type,side,quantity,
           unit_price,gross_value,fee_value,fee_pct,funding_value,network_fee_value,
           realized_pnl_value,currency,external_order_id,txid,jurisdiction,
           tax_status,evidence_type,notes,imported_at_utc)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (source,r[1] or "",r[2],r[3],"FILL",r[4],qty,px,gross,fee_value,fee_pct,
           None,None,None,"USDT" if source=="BINANCE" else None,r[9] or "","",None,
           "UNCLASSIFIED","READ_ONLY_FILL_IMPORT",notes,now()))
        n+=c.execute("SELECT changes()").fetchone()[0]
    c.commit()
    return n

def add_transfer(c,args):
    c.execute("""INSERT OR IGNORE INTO compliance_transfers
      (source,event_time_utc,asset,quantity,direction,from_account,to_account,
       network,fee_value,txid,jurisdiction,tax_status,notes,imported_at_utc)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (args.source,args.time,args.asset,args.quantity,args.direction,
       args.from_account,args.to_account,args.network,args.fee,args.txid or "",
       args.jurisdiction,"UNCLASSIFIED",args.notes,now()))
    c.commit()
    return c.execute("SELECT changes()").fetchone()[0]

def summary(c):
    tx=c.execute("""SELECT source,record_type,COUNT(*) n,
      COALESCE(SUM(gross_value),0) gross,COALESCE(SUM(fee_value),0) fees
      FROM compliance_transactions GROUP BY source,record_type
      ORDER BY source,record_type""").fetchall()
    tr=c.execute("""SELECT source,direction,COUNT(*) n,
      COALESCE(SUM(fee_value),0) fees
      FROM compliance_transfers GROUP BY source,direction
      ORDER BY source,direction""").fetchall()
    return {
      "transactions":[{"source":r[0],"record_type":r[1],"n":r[2],"gross":r[3],"fees":r[4]} for r in tx],
      "transfers":[{"source":r[0],"direction":r[1],"n":r[2],"fees":r[3]} for r in tr],
      "tax_classification":"UNCLASSIFIED_JURISDICTION_SPECIFIC",
      "warning":"Tax/regulatory treatment must be determined separately for the user's actual jurisdiction and facts."
    }

def db_for(source):
    return BDB if source=="BINANCE" else GDB

def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest="cmd",required=True)

    s=sub.add_parser("sync-fills")
    s.add_argument("--source",choices=["BINANCE","GATE","ALL"],default="ALL")

    t=sub.add_parser("add-transfer")
    t.add_argument("--source",choices=["BINANCE","GATE"],required=True)
    t.add_argument("--time",required=True)
    t.add_argument("--asset",required=True)
    t.add_argument("--quantity",type=float,required=True)
    t.add_argument("--direction",choices=["IN","OUT","INTERNAL"],required=True)
    t.add_argument("--from-account",default="")
    t.add_argument("--to-account",default="")
    t.add_argument("--network",default="")
    t.add_argument("--fee",type=float)
    t.add_argument("--txid",default="")
    t.add_argument("--jurisdiction",default="")
    t.add_argument("--notes",default="")

    q=sub.add_parser("summary")
    q.add_argument("--source",choices=["BINANCE","GATE"],required=True)

    args=ap.parse_args()
    if args.cmd=="sync-fills":
        sources=["BINANCE","GATE"] if args.source=="ALL" else [args.source]
        out={}
        for src in sources:
            db=db_for(src)
            if not os.path.exists(db):
                out[src]="DB_MISSING";continue
            with sqlite3.connect(db,timeout=30) as c:
                init(c);out[src]=sync_fill_table(c,src)
        print(json.dumps(out,ensure_ascii=False))
    elif args.cmd=="add-transfer":
        db=db_for(args.source)
        if not os.path.exists(db):raise SystemExit("DB missing")
        with sqlite3.connect(db,timeout=30) as c:
            init(c);print({"inserted":add_transfer(c,args)})
    else:
        db=db_for(args.source)
        if not os.path.exists(db):raise SystemExit("DB missing")
        with sqlite3.connect(db,timeout=30) as c:
            init(c);print(json.dumps(summary(c),ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
