#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-venue spot confirmation for Binance Avci live-pool survivors.

Uses public OKX and Gate spot data. Observational only; never changes frozen
scanner rules.
"""
import os, sqlite3, requests
from datetime import datetime, timezone

DB=os.getenv("BINANCE_DB","binance_avci2.db")
VERSION="binance-crossvenue-spot-v1-20260926"
TIMEOUT=12
MAX_SYMBOLS=10

def now(): return datetime.now(timezone.utc).isoformat()
def f(x):
    try: return float(x)
    except Exception: return None
def pct(a,b):
    return 100*(b/a-1) if a not in (None,0) and b is not None else None
def get_json(url,params=None):
    r=requests.get(url,params=params,timeout=TIMEOUT,headers={"User-Agent":"avci-crossvenue/1.0"})
    r.raise_for_status(); return r.json()

def okx_15m(base):
    inst=f"{base}-USDT"
    body=get_json("https://www.okx.com/api/v5/market/candles",
                  {"instId":inst,"bar":"5m","limit":"5"})
    if str(body.get("code","0"))!="0": return None
    rows=body.get("data") or []
    if len(rows)<4: return None
    rows=sorted(rows,key=lambda x:int(x[0]))
    closed=rows[:-1] if rows and rows[-1][8]=="0" else rows
    if len(closed)<4: return None
    start=f(closed[-4][4]); end=f(closed[-1][4])
    vol=sum(f(x[7]) or 0 for x in closed[-3:])
    return {"return_15m":pct(start,end),"quote_volume_15m":vol,"price":end}

def gate_15m(base):
    pair=f"{base}_USDT"
    rows=get_json("https://api.gateio.ws/api/v4/spot/candlesticks",
                  {"currency_pair":pair,"interval":"5m","limit":5})
    if not isinstance(rows,list) or len(rows)<4: return None
    parsed=sorted(rows,key=lambda x:int(float(x[0])))
    closed=parsed[-4:]
    start=f(closed[0][2]); end=f(closed[-1][2])
    # Gate field 1 is quote volume in current API output.
    vol=sum(f(x[1]) or 0 for x in closed[-3:])
    return {"return_15m":pct(start,end),"quote_volume_15m":vol,"price":end}

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS crossvenue_spot_observations(
      scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,source TEXT NOT NULL,
      return_15m REAL,quote_volume_15m REAL,price REAL,status TEXT NOT NULL,
      error TEXT,version TEXT NOT NULL,created_at_utc TEXT NOT NULL,
      PRIMARY KEY(scan_time_utc,symbol,source,version))""")
    c.execute("""CREATE TABLE IF NOT EXISTS crossvenue_spot_summary(
      scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,
      binance_return_15m REAL,confirmed_sources INTEGER,positive_sources INTEGER,
      direction_agreement REAL,summary TEXT NOT NULL,version TEXT NOT NULL,
      created_at_utc TEXT NOT NULL,PRIMARY KEY(scan_time_utc,symbol,version))""")

def main():
    if not os.path.exists(DB): print("crossvenue DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c)
        scan=c.execute("""SELECT scan_time_utc FROM scans WHERE health_status!='INVALID'
          ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan: return
        ts=scan["scan_time_utc"]
        rows=c.execute("""SELECT p.symbol,f.change_15m,p.status,p.confirmation_score
          FROM binance_live_pool p JOIN features f
          ON f.scan_time_utc=p.scan_time_utc AND f.symbol=p.symbol
          WHERE p.scan_time_utc=? AND p.status IN ('CONFIRMED','BORDERLINE')
          ORDER BY CASE p.status WHEN 'CONFIRMED' THEN 0 ELSE 1 END,
                   p.confirmation_score DESC LIMIT ?""",(ts,MAX_SYMBOLS)).fetchall()
        for r in rows:
            base=r["symbol"][:-4] if r["symbol"].endswith("USDT") else r["symbol"]
            vals=[]
            for source,fn in (("OKX_SPOT",okx_15m),("GATE_SPOT",gate_15m)):
                status="OK"; err=None; d=None
                try: d=fn(base)
                except Exception as e: status="ERROR"; err=f"{type(e).__name__}:{str(e)[:160]}"
                if d is None and status=="OK": status="NO_PAIR_OR_DATA"
                c.execute("""INSERT OR REPLACE INTO crossvenue_spot_observations
                  VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (ts,r["symbol"],source,d.get("return_15m") if d else None,
                   d.get("quote_volume_15m") if d else None,d.get("price") if d else None,
                   status,err,VERSION,now()))
                if d and d.get("return_15m") is not None: vals.append(float(d["return_15m"]))
            b=float(r["change_15m"] or 0)
            positive=sum(v>0 for v in vals)
            same=sum((v>=0)==(b>=0) for v in vals)
            agreement=(same/len(vals)) if vals else None
            if len(vals)>=2 and agreement==1.0:
                summary="CONFIRMED"
            elif vals and agreement>=0.5:
                summary="PARTIAL"
            elif vals:
                summary="DIVERGENT"
            else:
                summary="UNKNOWN"
            c.execute("""INSERT OR REPLACE INTO crossvenue_spot_summary
              VALUES(?,?,?,?,?,?,?,?,?)""",
              (ts,r["symbol"],b,len(vals),positive,agreement,summary,VERSION,now()))
            print("crossvenue",r["symbol"],summary,vals)
        c.commit()

if __name__=="__main__": main()
