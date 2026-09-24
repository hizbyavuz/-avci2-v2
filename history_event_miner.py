#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Broad historical event miner for Avci research.

Purpose:
- pull long, cheap daily histories for real Gate USDT markets
- mark large forward moves as events (+20/+50/+100)
- mark matched non-events as controls
- describe only information available BEFORE each event
- keep the frozen live Gate Avci logic untouched

This is research-only and never trades.
"""

from __future__ import annotations
import json, math, os, sqlite3, statistics, time
from datetime import datetime, timezone
from urllib import parse, request, error

DB=os.getenv("HISTORY_DB","history_miner.db")
SOURCE_DB=os.getenv("AVCI_DB","avci2.db")
BASE="https://api.gateio.ws/api/v4/spot/candlesticks"
LOOKBACK=int(os.getenv("HISTORY_LOOKBACK_DAYS","730"))
PAIR_BUDGET=int(os.getenv("HISTORY_EVENT_PAIR_BUDGET","80"))
SLEEP=float(os.getenv("HISTORY_EVENT_SLEEP","0.25"))
VERSION="broad-event-miner-v0.1-20260924"

STABLES={"USDT","USDC","DAI","FDUSD","TUSD","USDE","USDS","PYUSD","USDD","FRAX","GUSD","LUSD","USDP","USD0","USD1"}

def utcnow(): return datetime.now(timezone.utc).isoformat()

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory=sqlite3.Row
    return c

def init_db():
    with con() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS cex_pairs(
          pair TEXT PRIMARY KEY,symbol TEXT NOT NULL,source TEXT NOT NULL,
          priority INTEGER NOT NULL DEFAULT 100,status TEXT NOT NULL DEFAULT 'QUEUED',
          attempts INTEGER NOT NULL DEFAULT 0,last_run_utc TEXT,last_error TEXT);
        CREATE TABLE IF NOT EXISTS daily_bars(
          pair TEXT NOT NULL,ts INTEGER NOT NULL,open REAL NOT NULL,high REAL NOT NULL,
          low REAL NOT NULL,close REAL NOT NULL,volume_base REAL,volume_quote REAL,
          source TEXT NOT NULL,PRIMARY KEY(pair,ts));
        CREATE TABLE IF NOT EXISTS rise_events(
          pair TEXT NOT NULL,event_ts INTEGER NOT NULL,horizon_days INTEGER NOT NULL,
          max_return_pct REAL NOT NULL,hit20 INTEGER NOT NULL,hit50 INTEGER NOT NULL,
          hit100 INTEGER NOT NULL,peak_ts INTEGER,event_version TEXT NOT NULL,
          PRIMARY KEY(pair,event_ts,event_version));
        CREATE TABLE IF NOT EXISTS event_features(
          pair TEXT NOT NULL,event_ts INTEGER NOT NULL,label TEXT NOT NULL,
          ret_7d REAL,ret_30d REAL,ret_90d REAL,drawdown_30d REAL,
          vol_ratio_7_30 REAL,vol_ratio_30_90 REAL,realized_vol_30d REAL,
          range_compression_7_30 REAL,green_ratio_14d REAL,dist_high_90d REAL,
          pre_price REAL,pre_volume30 REAL,feature_version TEXT NOT NULL,
          PRIMARY KEY(pair,event_ts,label,feature_version));
        CREATE TABLE IF NOT EXISTS miner_stats(
          run_id TEXT PRIMARY KEY,started_utc TEXT NOT NULL,finished_utc TEXT,
          pairs_attempted INTEGER NOT NULL DEFAULT 0,pairs_ok INTEGER NOT NULL DEFAULT 0,
          bars INTEGER NOT NULL DEFAULT 0,events INTEGER NOT NULL DEFAULT 0,
          controls INTEGER NOT NULL DEFAULT 0,version TEXT NOT NULL);
        """)

def f(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None

def seed_pairs():
    if not os.path.exists(SOURCE_DB): return 0
    try:
        src=sqlite3.connect(f"file:{SOURCE_DB}?mode=ro",uri=True)
        src.row_factory=sqlite3.Row
        tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "gate_spot_market" not in tables: src.close(); return 0
        rows=src.execute("SELECT pair,symbol,volume_24h FROM gate_spot_market WHERE volume_24h>=30000 ORDER BY volume_24h DESC").fetchall()
        added=0
        with con() as c:
            for r in rows:
                sym=str(r["symbol"] or "").upper()
                if sym in STABLES: continue
                before=c.total_changes
                c.execute("INSERT OR IGNORE INTO cex_pairs(pair,symbol,source) VALUES(?,?,?)",(r["pair"],sym,"gate_spot_market"))
                if c.total_changes>before: added+=1
        src.close(); return added
    except sqlite3.Error:
        return 0

def due_pairs(limit):
    with con() as c:
        return c.execute("""SELECT pair,symbol FROM cex_pairs
            ORDER BY CASE status WHEN 'QUEUED' THEN 0 WHEN 'RETRY' THEN 1 ELSE 2 END,
                     attempts ASC,priority ASC,pair LIMIT ?""",(limit,)).fetchall()

def fetch_daily(pair):
    # Gate public endpoint; one call gives up to 1000 daily bars.
    url=BASE+"?"+parse.urlencode({"currency_pair":pair,"interval":"1d","limit":min(1000,LOOKBACK)})
    with request.urlopen(url,timeout=25) as r:
        data=json.load(r)
    if not isinstance(data,list): raise ValueError("invalid candle payload")
    out=[]
    for row in data:
        # Gate v4 candle format: [timestamp, quote_volume, close, high, low, open, base_volume, ...]
        if not isinstance(row,list) or len(row)<7: continue
        ts=int(float(row[0])); qv=f(row[1]); close=f(row[2]); high=f(row[3]); low=f(row[4]); op=f(row[5]); bv=f(row[6])
        if None in (close,high,low,op) or min(close,high,low,op)<=0: continue
        out.append((ts,op,high,low,close,bv,qv))
    out.sort()
    return out

def save_bars(pair,bars):
    with con() as c:
        c.executemany("""INSERT OR REPLACE INTO daily_bars
          (pair,ts,open,high,low,close,volume_base,volume_quote,source)
          VALUES(?,?,?,?,?,?,?,?,?)""",[(pair,*b,"gate") for b in bars])

def pct(a,b):
    return 100*(b/a-1) if a and b and a>0 else None

def stdev_returns(closes):
    rs=[]
    for a,b in zip(closes,closes[1:]):
        if a>0 and b>0: rs.append(math.log(b/a))
    return statistics.pstdev(rs)*100 if len(rs)>=5 else None

def avg(xs):
    xs=[x for x in xs if x is not None]
    return sum(xs)/len(xs) if xs else None

def features_at(bars,i):
    if i<90: return None
    pre=bars[:i+1]; cur=pre[-1]
    closes=[x[4] for x in pre]; vols=[x[6] for x in pre]
    highs=[x[2] for x in pre]; lows=[x[3] for x in pre]
    v7=avg(vols[-7:]); v30=avg(vols[-30:]); v90=avg(vols[-90:])
    r7=pct(closes[-8],closes[-1]) if len(closes)>=8 else None
    r30=pct(closes[-31],closes[-1]); r90=pct(closes[-91],closes[-1])
    hi30=max(highs[-30:]); dd30=pct(hi30,closes[-1])
    ranges7=[100*(h/l-1) for h,l in zip(highs[-7:],lows[-7:]) if l>0]
    ranges30=[100*(h/l-1) for h,l in zip(highs[-30:],lows[-30:]) if l>0]
    comp=(avg(ranges7)/avg(ranges30)) if avg(ranges7) and avg(ranges30) else None
    green=sum(1 for x in pre[-14:] if x[4]>=x[1])/14.0
    hi90=max(highs[-90:]); dh=pct(hi90,closes[-1])
    return (r7,r30,r90,dd30,(v7/v30 if v7 and v30 else None),
            (v30/v90 if v30 and v90 else None),stdev_returns(closes[-31:]),
            comp,green,dh,cur[4],v30)

def build_labels(pair,bars):
    events=[]; controls=[]; last_event=-999
    n=len(bars)
    # Label each day only from its FUTURE; features are strictly pre-event.
    for i in range(90,n-61):
        feat=features_at(bars,i)
        if not feat: continue
        base=bars[i][4]
        future=bars[i+1:min(n,i+61)]
        if not future: continue
        peak=max(future,key=lambda x:x[2])
        mx=100*(peak[2]/base-1)
        if mx>=20 and i-last_event>=20:
            events.append((i,mx,peak[0],feat))
            last_event=i
        elif mx<10 and i%17==0:
            controls.append((i,mx,None,feat))
    return events,controls

def save_labels(pair,bars,events,controls):
    with con() as c:
        for i,mx,peak,feat in events:
            ts=bars[i][0]
            c.execute("""INSERT OR REPLACE INTO rise_events VALUES(?,?,?,?,?,?,?,?,?)""",
                      (pair,ts,60,mx,int(mx>=20),int(mx>=50),int(mx>=100),peak,VERSION))
            c.execute("""INSERT OR REPLACE INTO event_features VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (pair,ts,"RISE",*feat,VERSION))
        for i,mx,_peak,feat in controls:
            ts=bars[i][0]
            c.execute("""INSERT OR REPLACE INTO event_features VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (pair,ts,"CONTROL",*feat,VERSION))

def mark(pair,ok,err=None):
    with con() as c:
        c.execute("""UPDATE cex_pairs SET status=?,attempts=attempts+1,last_run_utc=?,
          last_error=? WHERE pair=?""",("DONE" if ok else "RETRY",utcnow(),err,pair))

def main():
    init_db(); seeded=seed_pairs()
    run=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with con() as c:
        c.execute("INSERT OR REPLACE INTO miner_stats(run_id,started_utc,version) VALUES(?,?,?)",(run,utcnow(),VERSION))
    attempted=okn=barsn=evn=ctln=0
    for row in due_pairs(PAIR_BUDGET):
        pair=row["pair"]; attempted+=1
        try:
            bars=fetch_daily(pair)
            if len(bars)<120: raise ValueError(f"short_history:{len(bars)}")
            save_bars(pair,bars)
            ev,ctl=build_labels(pair,bars)
            save_labels(pair,bars,ev,ctl)
            mark(pair,True)
            okn+=1; barsn+=len(bars); evn+=len(ev); ctln+=len(ctl)
            print(f"{pair}: bars={len(bars)} rise_events={len(ev)} controls={len(ctl)}")
        except (error.URLError,TimeoutError,ValueError,OSError) as e:
            mark(pair,False,type(e).__name__+":"+str(e)[:100])
            print(f"{pair}: ERROR {type(e).__name__} {str(e)[:80]}")
        time.sleep(SLEEP)
    with con() as c:
        c.execute("""UPDATE miner_stats SET finished_utc=?,pairs_attempted=?,pairs_ok=?,
          bars=?,events=?,controls=? WHERE run_id=?""",(utcnow(),attempted,okn,barsn,evn,ctln,run))
        totals=c.execute("""SELECT
          (SELECT COUNT(*) FROM cex_pairs),
          (SELECT COUNT(*) FROM daily_bars),
          (SELECT COUNT(*) FROM rise_events),
          (SELECT COUNT(*) FROM event_features WHERE label='CONTROL')""").fetchone()
    print(f"Broad Miner: seeded={seeded} attempted={attempted} ok={okn} bars={barsn} events={evn} controls={ctln}")
    print(f"Archive totals: pairs={totals[0]} bars={totals[1]} rise_events={totals[2]} controls={totals[3]}")

if __name__=="__main__": main()
