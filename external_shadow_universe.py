#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent external-universe shadow validation.

Scans liquid OKX and Gate spot universes independently using only the frozen
Binance Wake-up anomaly core (return-z + volume-z). This is deliberately a
shadow validation subset, not a replacement signal engine. It never feeds live
candidate selection.

Tracks 24h outcome for shadow signals and contemporaneous random controls.
"""
from __future__ import annotations
import json,math,os,random,sqlite3,statistics,time
from datetime import datetime,timezone,timedelta
import requests

DB=os.getenv("EXTERNAL_SHADOW_DB","external_shadow.db")
VERSION="external-shadow-wakeup-v1-20260926"
WAKE_VOLUME_Z=2.5
WAKE_RETURN_Z=2.0
MAX_PER_VENUE=25
UA={"User-Agent":"avci-external-shadow/1.0"}

def now():return datetime.now(timezone.utc)
def z(v,xs):
    if len(xs)<10:return None
    m=statistics.fmean(xs);s=statistics.pstdev(xs)
    return (v-m)/s if s>1e-12 else 0.0
def ret(a,b):return 100*(b/a-1) if a else None
def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS external_shadow_signals(
      venue TEXT,symbol TEXT,signal_time_utc TEXT,class TEXT,price REAL,
      volume_z REAL,return_z REAL,return_15m_pct REAL,quote_volume_24h REAL,
      outcome_due_utc TEXT,outcome_24h_pct REAL,outcome_status TEXT,
      version TEXT,created_at_utc TEXT,
      PRIMARY KEY(venue,symbol,signal_time_utc,class,version))""")
    c.execute("""CREATE TABLE IF NOT EXISTS external_shadow_summary(
      run_date TEXT,venue TEXT,candidate_n INTEGER,control_n INTEGER,
      candidate_expectancy_24h REAL,control_expectancy_24h REAL,diff REAL,
      status TEXT,version TEXT,created_at_utc TEXT,
      PRIMARY KEY(run_date,venue,version))""")
    c.commit()
def get(url,params=None):
    r=requests.get(url,params=params,headers=UA,timeout=15);r.raise_for_status();return r.json()

def gate_universe():
    ticks=get("https://api.gateio.ws/api/v4/spot/tickers")
    out=[]
    for r in ticks:
        p=r.get("currency_pair","")
        if not p.endswith("_USDT"):continue
        try:qv=float(r.get("quote_volume") or 0)
        except:qv=0
        if qv>=3_000_000:out.append((p,qv))
    return sorted(out,key=lambda x:x[1],reverse=True)[:MAX_PER_VENUE]
def gate_bars(sym):
    rows=get("https://api.gateio.ws/api/v4/spot/candlesticks",
             {"currency_pair":sym,"interval":"5m","limit":80})
    out=[]
    for r in sorted(rows,key=lambda x:int(float(x[0]))):
        try:
            close=float(r[2]);qv=float(r[1])
            out.append((int(float(r[0])),close,qv))
        except:pass
    return out
def okx_universe():
    body=get("https://www.okx.com/api/v5/market/tickers",{"instType":"SPOT"})
    out=[]
    for r in body.get("data") or []:
        s=r.get("instId","")
        if not s.endswith("-USDT"):continue
        try:qv=float(r.get("volCcy24h") or 0)
        except:qv=0
        if qv>=3_000_000:out.append((s,qv))
    return sorted(out,key=lambda x:x[1],reverse=True)[:MAX_PER_VENUE]
def okx_bars(sym):
    body=get("https://www.okx.com/api/v5/market/candles",{"instId":sym,"bar":"5m","limit":"80"})
    out=[]
    for r in sorted(body.get("data") or [],key=lambda x:int(x[0])):
        try:
            if len(r)>8 and str(r[8])=="0":continue
            out.append((int(r[0])//1000,float(r[4]),float(r[7])))
        except:pass
    return out

def classify(bars):
    if len(bars)<40:return None
    closes=[x[1] for x in bars];vols=[x[2] for x in bars]
    r15=ret(closes[-4],closes[-1])
    hist_ret=[ret(closes[i-3],closes[i]) for i in range(3,len(closes)-3)]
    hist_vol=[sum(vols[i-2:i+1]) for i in range(2,len(vols)-3)]
    vz=z(sum(vols[-3:]),hist_vol);rz=z(r15,hist_ret)
    if vz is None or rz is None:return None
    return {"price":closes[-1],"r15":r15,"vz":vz,"rz":rz,
            "candidate":vz>=WAKE_VOLUME_Z and rz>=WAKE_RETURN_Z}

def update_due(c,venue,fetcher):
    rows=c.execute("""SELECT rowid,* FROM external_shadow_signals
      WHERE venue=? AND outcome_status='OPEN' AND outcome_due_utc<=?""",
      (venue,now().isoformat())).fetchall()
    for r in rows:
        try:
            bars=fetcher(r["symbol"])
            if not bars:continue
            p=bars[-1][1];out=ret(float(r["price"]),p)
            c.execute("UPDATE external_shadow_signals SET outcome_24h_pct=?,outcome_status='CLOSED' WHERE rowid=?",(out,r["rowid"]))
        except Exception:continue

def scan_venue(c,venue,universe,fetcher):
    random.seed(int(now().strftime("%Y%m%d")))
    observed=[]
    for sym,qv in universe():
        try:
            d=classify(fetcher(sym))
            if d:observed.append((sym,qv,d))
        except Exception:pass
        time.sleep(.08)
    cand=[x for x in observed if x[2]["candidate"]]
    non=[x for x in observed if not x[2]["candidate"]]
    controls=random.sample(non,min(len(non),max(1,len(cand)))) if non else []
    stamp=now();due=(stamp+timedelta(hours=24)).isoformat()
    for cls,rows in (("CANDIDATE",cand),("RANDOM_CONTROL",controls)):
        for sym,qv,d in rows:
            c.execute("""INSERT OR IGNORE INTO external_shadow_signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (venue,sym,stamp.isoformat(timespec="minutes"),cls,d["price"],d["vz"],d["rz"],d["r15"],qv,
               due,None,"OPEN",VERSION,stamp.isoformat()))
    update_due(c,venue,fetcher)
    closed=c.execute("""SELECT class,outcome_24h_pct FROM external_shadow_signals
      WHERE venue=? AND version=? AND outcome_status='CLOSED'""",(venue,VERSION)).fetchall()
    ca=[float(r[1]) for r in closed if r[0]=="CANDIDATE" and r[1] is not None]
    co=[float(r[1]) for r in closed if r[0]=="RANDOM_CONTROL" and r[1] is not None]
    diff=(statistics.fmean(ca)-statistics.fmean(co)) if ca and co else None
    c.execute("INSERT OR REPLACE INTO external_shadow_summary VALUES(?,?,?,?,?,?,?,?,?,?)",
      (stamp.date().isoformat(),venue,len(ca),len(co),statistics.fmean(ca) if ca else None,
       statistics.fmean(co) if co else None,diff,"OK" if len(ca)>=20 and len(co)>=20 else "COLLECTING",
       VERSION,stamp.isoformat()))
    print(venue,"shadow new",len(cand),"closed",len(ca),len(co),"diff",diff)

def main():
    with sqlite3.connect(DB,timeout=60) as c:
        c.row_factory=sqlite3.Row;init(c)
        scan_venue(c,"GATE_SPOT",gate_universe,gate_bars)
        scan_venue(c,"OKX_SPOT",okx_universe,okx_bars)
        c.commit()
if __name__=="__main__":main()
