#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recover honest historical timing proxies from archived Avci snapshots.

This does NOT reconstruct network/request latency that was never recorded.
It extracts only timing facts that were actually archived:
- Binance source-age proxy from feature.raw_json.data_staleness_minutes
- signal-vs-last-closed-bar age
- Gate signal-vs-scan-batch alignment when available

Research-only; never changes signal rules.
"""
from __future__ import annotations
import json, os, sqlite3, statistics, sys
from datetime import datetime, timezone

BDB=os.getenv("BINANCE_DB","binance_avci2.db")
GDB=os.getenv("AVCI_DB","avci2.db")
VDB=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
VERSION="historical-timing-proxy-v1-20260926"

def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None
def q(xs,p):
    xs=sorted(float(x) for x in xs if x is not None)
    if not xs:return None
    return xs[min(len(xs)-1,int((len(xs)-1)*p))]
def summary(xs):
    xs=[float(x) for x in xs if x is not None]
    return {"n":len(xs),"p50":statistics.median(xs) if xs else None,
            "p95":q(xs,.95),"p99":q(xs,.99),"max":max(xs) if xs else None}

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS historical_timing_proxy(
      source TEXT,version TEXT,metric TEXT,n INTEGER,p50_ms REAL,p95_ms REAL,
      p99_ms REAL,max_ms REAL,truth_class TEXT,notes TEXT,
      PRIMARY KEY(source,version,metric))""")
    c.commit()

def write(c,source,metric,s,truth,notes):
    c.execute("INSERT OR REPLACE INTO historical_timing_proxy VALUES(?,?,?,?,?,?,?,?,?,?)",
      (source,VERSION,metric,s["n"],s["p50"],s["p95"],s["p99"],s["max"],truth,notes))

def binance():
    if not os.path.exists(BDB):return
    with sqlite3.connect(BDB,timeout=60) as c:
        c.row_factory=sqlite3.Row;init(c)
        stale=[]; bar_age=[]
        if table(c,"features"):
            for r in c.execute("SELECT scan_time_utc,signal_bar_close_ms,raw_json FROM features"):
                try:
                    d=json.loads(r["raw_json"] or "{}")
                    v=d.get("data_staleness_minutes")
                    if v is not None:stale.append(float(v)*60000.0)
                except Exception:pass
                t=dt(r["scan_time_utc"])
                if t and r["signal_bar_close_ms"] is not None:
                    age=t.timestamp()*1000.0-float(r["signal_bar_close_ms"])
                    if age>=0:bar_age.append(age)
        write(c,"BINANCE","ARCHIVED_DATA_STALENESS",summary(stale),"ARCHIVED_SOURCE_AGE_PROXY",
              "Stored at scan time in feature.raw_json; not network latency.")
        write(c,"BINANCE","SIGNAL_TO_LAST_CLOSED_BAR",summary(bar_age),"ARCHIVED_PIPELINE_AGE_PROXY",
              "Scan timestamp minus last closed signal-bar timestamp; not request latency.")
        c.commit()
        report={"source":"BINANCE","version":VERSION,
                "archived_data_staleness_ms":summary(stale),
                "signal_to_last_closed_bar_ms":summary(bar_age),
                "historical_network_latency":"NOT_RECONSTRUCTABLE"}
    open("historical_timing_proxy_binance.json","w",encoding="utf-8").write(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))

def gate():
    if not (os.path.exists(GDB) and os.path.exists(VDB)):return
    with sqlite3.connect(GDB,timeout=60) as o,sqlite3.connect(VDB,timeout=60) as v:
        o.row_factory=v.row_factory=sqlite3.Row;init(o)
        health={}
        if table(o,"gate_scan_health"):
            health={str(r["batch_id"]):int(r["scan_ts"]) for r in o.execute("SELECT batch_id,scan_ts FROM gate_scan_health")}
        diffs=[]
        if table(v,"validation_events"):
            for r in v.execute("SELECT batch_id,signal_ts FROM validation_events"):
                h=health.get(str(r["batch_id"]))
                if h is not None and r["signal_ts"] is not None:
                    diffs.append(abs(float(r["signal_ts"])-float(h))*1000.0)
        write(o,"GATE","SIGNAL_TO_SCAN_BATCH_ALIGNMENT",summary(diffs),"ARCHIVED_PIPELINE_ALIGNMENT_PROXY",
              "Absolute signal timestamp vs archived scan-batch timestamp; not provider/network latency.")
        o.commit()
        report={"source":"GATE","version":VERSION,
                "signal_to_scan_batch_alignment_ms":summary(diffs),
                "historical_network_latency":"NOT_RECONSTRUCTABLE"}
    open("historical_timing_proxy_gate.json","w",encoding="utf-8").write(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))

def main():
    m=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if m=="binance":binance()
    elif m=="gate":gate()
    else:raise SystemExit("usage: historical_timing_proxy.py binance|gate")

if __name__=="__main__":main()
