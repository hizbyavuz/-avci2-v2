#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Correlation-adjusted position research overlay.

Uses recent realized price paths among simultaneously active candidates.
Produces a suggested size multiplier for research/paper display only.
Never places orders or changes frozen candidate rules.
"""
from __future__ import annotations
import json,math,os,sqlite3,statistics,sys
from datetime import datetime,timezone,timedelta

VERSION="correlation-position-v1-20260926"
def now():return datetime.now(timezone.utc).isoformat()
def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def corr(a,b):
    n=min(len(a),len(b))
    if n<12:return None
    a=a[-n:];b=b[-n:];ma=sum(a)/n;mb=sum(b)/n
    va=sum((x-ma)**2 for x in a);vb=sum((y-mb)**2 for y in b)
    if va<=0 or vb<=0:return None
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb)
def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS correlation_position_overlay(
      source TEXT,batch_key TEXT,asset_key TEXT,peer_count INTEGER,max_peer_corr REAL,
      avg_peer_corr REAL,size_multiplier REAL,risk_label TEXT,details_json TEXT,
      version TEXT,created_at_utc TEXT,
      PRIMARY KEY(source,batch_key,asset_key,version))""");c.commit()
def mult(maxc,peers):
    if maxc is None:return 1.0,"UNKNOWN"
    if maxc>=0.90:return 0.33,"VERY_HIGH_CORRELATION"
    if maxc>=0.75:return 0.50,"HIGH_CORRELATION"
    if maxc>=0.50:return 0.75,"MEDIUM_CORRELATION"
    return 1.0,"LOW_CORRELATION"
def binance():
    db=os.getenv("BINANCE_DB","binance_avci2.db")
    if not os.path.exists(db):return
    with sqlite3.connect(db,timeout=30) as c:
        c.row_factory=sqlite3.Row;init(c)
        s=c.execute("""SELECT scan_time_utc FROM scans WHERE health_status!='INVALID' ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not s:return
        ts=s[0];syms=[r[0] for r in c.execute("""SELECT symbol FROM features WHERE scan_time_utc=? AND selection_class='CANDIDATE'""",(ts,))]
        paths={}
        for sym in syms:
            rows=c.execute("""SELECT close_price FROM raw_klines WHERE symbol=? AND interval_value='5m'
              ORDER BY open_time_ms DESC LIMIT 145""",(sym,)).fetchall()
            px=[float(r[0]) for r in reversed(rows)]
            paths[sym]=[px[i]/px[i-1]-1 for i in range(1,len(px)) if px[i-1]>0]
        for sym in syms:
            vals=[]
            for other in syms:
                if other==sym:continue
                x=corr(paths.get(sym,[]),paths.get(other,[]))
                if x is not None:vals.append((other,x))
            mx=max((x for _,x in vals),default=None);av=sum(x for _,x in vals)/len(vals) if vals else None
            m,label=mult(mx,len(vals))
            c.execute("INSERT OR REPLACE INTO correlation_position_overlay VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              ("BINANCE",ts,sym,len(vals),mx,av,m,label,json.dumps(vals),VERSION,now()))
        c.commit();print("correlation sizing BINANCE",len(syms))
def gate():
    db=os.getenv("AVCI_DB","avci2.db");vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
    if not (os.path.exists(db) and os.path.exists(vdb)):return
    with sqlite3.connect(db,timeout=30) as c,sqlite3.connect(vdb,timeout=30) as v:
        c.row_factory=v.row_factory=sqlite3.Row;init(c)
        h=c.execute("""SELECT batch_id FROM gate_scan_health WHERE status='VALID' ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not h:return
        batch=str(h[0]);ev=v.execute("""SELECT network_id,token_contract FROM validation_events
          WHERE batch_id=? AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')""",(batch,)).fetchall()
        keys=[f"{r['network_id']}:{r['token_contract']}" for r in ev]
        paths={}
        for r in ev:
            rows=c.execute("""SELECT raw_json FROM snapshots WHERE network_id=? AND token_contract=?
              ORDER BY id DESC LIMIT 72""",(r["network_id"],r["token_contract"])).fetchall()
            px=[]
            for row in reversed(rows):
                try:p=float(json.loads(row[0]).get("price_usd") or 0)
                except Exception:p=0
                if p>0:px.append(p)
            key=f"{r['network_id']}:{r['token_contract']}"
            paths[key]=[px[i]/px[i-1]-1 for i in range(1,len(px)) if px[i-1]>0]
        for key in keys:
            vals=[]
            for other in keys:
                if other==key:continue
                x=corr(paths.get(key,[]),paths.get(other,[]))
                if x is not None:vals.append((other,x))
            mx=max((x for _,x in vals),default=None);av=sum(x for _,x in vals)/len(vals) if vals else None
            m,label=mult(mx,len(vals))
            c.execute("INSERT OR REPLACE INTO correlation_position_overlay VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              ("GATE",batch,key,len(vals),mx,av,m,label,json.dumps(vals),VERSION,now()))
        c.commit();print("correlation sizing GATE",len(keys))
def main():
    m=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if m=="binance":binance()
    elif m=="gate":gate()
    else:raise SystemExit("usage: correlation_position_overlay.py binance|gate")
if __name__=="__main__":main()
