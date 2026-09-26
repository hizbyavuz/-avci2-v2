#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import independently observed real fills into Avci cost calibration.

No trading. This only ingests exported/read-only execution records.
"""
import csv,os,sqlite3,sys
from datetime import datetime,timezone

FILE=os.getenv("EXECUTION_FILL_FILE","execution_fill_observations.csv")
BDB=os.getenv("BINANCE_DB","binance_avci2.db")
GDB=os.getenv("AVCI_DB","avci2.db")

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS execution_fill_observations(
      source TEXT,event_key TEXT,asset TEXT,event_time_utc TEXT,side TEXT,
      quantity REAL,expected_price REAL,filled_price REAL,fee_pct REAL,
      slippage_pct REAL,total_realized_cost_pct REAL,external_order_id TEXT,
      notes TEXT,imported_at_utc TEXT,
      PRIMARY KEY(source,event_key,external_order_id))""")
    c.commit()

def main():
    if not os.path.exists(FILE):
        print("fill import: file missing");return
    with open(FILE,newline="",encoding="utf-8") as f:
        rows=list(csv.DictReader(f))
    for source,db in (("BINANCE",BDB),("GATE",GDB)):
        if not os.path.exists(db):continue
        with sqlite3.connect(db) as c:
            init(c)
            n=0
            for r in rows:
                if (r.get("source") or "").upper()!=source:continue
                try:
                    vals=(source,r.get("event_key"),r.get("asset"),r.get("event_time_utc"),r.get("side"),
                          float(r["quantity"]) if r.get("quantity") else None,
                          float(r["expected_price"]) if r.get("expected_price") else None,
                          float(r["filled_price"]) if r.get("filled_price") else None,
                          float(r["fee_pct"]) if r.get("fee_pct") else None,
                          float(r["slippage_pct"]) if r.get("slippage_pct") else None,
                          float(r["total_realized_cost_pct"]) if r.get("total_realized_cost_pct") else None,
                          r.get("external_order_id") or "",r.get("notes"),
                          datetime.now(timezone.utc).isoformat())
                    c.execute("INSERT OR REPLACE INTO execution_fill_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",vals);n+=1
                except Exception:
                    continue
            c.commit()
            print(f"fill import {source}: {n}")

if __name__=="__main__":main()
