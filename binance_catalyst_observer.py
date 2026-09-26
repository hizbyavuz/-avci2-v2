#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public-news catalyst observer for Binance Avci.

Uses Google News RSS as an observational source. No LLM scoring, no trading,
and no frozen-rule changes. Headlines are timestamped and stored for later
validation.
"""
import os, sqlite3, requests, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

DB=os.getenv("BINANCE_DB","binance_avci2.db")
VERSION="binance-catalyst-observer-v1-20260926"
MAX_SYMBOLS=10
LOOKBACK_HOURS=24
TIMEOUT=12

POSITIVE=("listing","listed","partnership","partners","integration","upgrade","launch","mainnet","approval","adoption","collaboration")
NEGATIVE=("hack","exploit","breach","delist","delisting","lawsuit","investigation","unlock","outage","attack","drain","rug")
GENERIC_SKIP={"BTC","ETH","BNB","USDT","USDC"}

def now(): return datetime.now(timezone.utc)
def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def base(symbol): return symbol[:-4] if symbol.endswith("USDT") else symbol

def fetch_news(term):
    url=f"https://news.google.com/rss/search?q={quote_plus(term+' crypto when:1d')}&hl=en-US&gl=US&ceid=US:en"
    r=requests.get(url,timeout=TIMEOUT,headers={"User-Agent":"Mozilla/5.0 AvciResearch/1.0"})
    r.raise_for_status()
    root=ET.fromstring(r.content)
    out=[]
    cutoff=now()-timedelta(hours=LOOKBACK_HOURS)
    for item in root.findall(".//item")[:20]:
        title=(item.findtext("title") or "").strip()
        link=(item.findtext("link") or "").strip()
        pub=item.findtext("pubDate") or ""
        try: dt=parsedate_to_datetime(pub).astimezone(timezone.utc)
        except Exception: dt=None
        if dt and dt<cutoff: continue
        out.append((title,link,dt.isoformat() if dt else None))
    return out

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS catalyst_observations(
      scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,source TEXT NOT NULL,
      headline_count INTEGER NOT NULL,positive_count INTEGER NOT NULL,
      negative_count INTEGER NOT NULL,latest_headline TEXT,latest_published_utc TEXT,
      catalyst_state TEXT NOT NULL,error TEXT,version TEXT NOT NULL,
      created_at_utc TEXT NOT NULL,PRIMARY KEY(scan_time_utc,symbol,source,version))""")

def main():
    if not os.path.exists(DB): print("catalyst DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c)
        scan=c.execute("""SELECT scan_time_utc FROM scans WHERE health_status!='INVALID'
          ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan: return
        ts=scan["scan_time_utc"]
        rows=[]
        if table(c,"binance_live_pool"):
            rows=c.execute("""SELECT symbol FROM binance_live_pool WHERE scan_time_utc=?
              AND status IN ('CONFIRMED','BORDERLINE')
              ORDER BY CASE status WHEN 'CONFIRMED' THEN 0 ELSE 1 END,
                       confirmation_score DESC LIMIT ?""",(ts,MAX_SYMBOLS)).fetchall()
        if not rows:
            rows=c.execute("""SELECT symbol FROM features WHERE scan_time_utc=?
              AND selection_class IN ('CANDIDATE','NEAR_MISS')
              ORDER BY score DESC LIMIT ?""",(ts,MAX_SYMBOLS)).fetchall()
        for rr in rows:
            sym=rr["symbol"]; b=base(sym)
            if b in GENERIC_SKIP:
                continue
            err=None; items=[]
            try: items=fetch_news(b)
            except Exception as e: err=f"{type(e).__name__}:{str(e)[:180]}"
            titles=[x[0] for x in items]
            low=[x.lower() for x in titles]
            pos=sum(any(k in t for k in POSITIVE) for t in low)
            neg=sum(any(k in t for k in NEGATIVE) for t in low)
            if neg>0: state="RISK_CATALYST"
            elif pos>0: state="POSITIVE_CATALYST"
            elif items: state="NEWS_PRESENT"
            else: state="UNKNOWN"
            latest=items[0] if items else (None,None,None)
            c.execute("""INSERT OR REPLACE INTO catalyst_observations VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (ts,sym,"GOOGLE_NEWS_RSS",len(items),pos,neg,latest[0],latest[2],
               state,err,VERSION,now().isoformat()))
            print("catalyst",sym,state,"n",len(items),"pos",pos,"neg",neg)
        c.commit()

if __name__=="__main__": main()
