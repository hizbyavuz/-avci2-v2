#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""15-minute live confirmation pool for Binance Avci.

Runs after the broad scanner. Selects 15-25 unusual coins, samples only those
coins at 0/3/6/9/12/15 minutes, then classifies persistence without changing frozen
scanner rules. Research-only.
"""
import json, os, sqlite3, statistics, time
from datetime import datetime, timezone

from binance_scanner import spot_api_get

DB=os.getenv("BINANCE_DB","binance_avci2.db")
POOL_MIN=15
POOL_MAX=25
SAMPLE_EVERY_SECONDS=int(os.getenv("POOL_SAMPLE_SECONDS","180"))
SAMPLES=6
VERSION="binance-live-pool-v1-20260926"

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS binance_live_pool(
      scan_time_utc TEXT NOT NULL,
      symbol TEXT NOT NULL,
      rank_value INTEGER NOT NULL,
      pool_score REAL NOT NULL,
      first_price REAL,
      status TEXT NOT NULL DEFAULT 'WATCHING',
      sample_count INTEGER NOT NULL DEFAULT 0,
      confirmed_at_utc TEXT,
      confirmation_score INTEGER,
      reason_json TEXT,
      version TEXT NOT NULL,
      created_at_utc TEXT NOT NULL,
      PRIMARY KEY(scan_time_utc,symbol,version)
    );
    CREATE TABLE IF NOT EXISTS binance_live_pool_samples(
      scan_time_utc TEXT NOT NULL,
      symbol TEXT NOT NULL,
      sample_no INTEGER NOT NULL,
      sampled_at_utc TEXT NOT NULL,
      price REAL,
      change_from_start_pct REAL,
      quote_volume_5m REAL,
      trade_count_5m INTEGER,
      taker_buy_ratio_5m REAL,
      volume_ratio_vs_first REAL,
      book_imbalance REAL,
      spread_bps REAL,
      bid_capacity_usd REAL,
      ask_capacity_usd REAL,
      version TEXT NOT NULL,
      PRIMARY KEY(scan_time_utc,symbol,sample_no,version)
    );
    """)
    sample_cols={r[1] for r in c.execute("PRAGMA table_info(binance_live_pool_samples)")}
    for definition in ("spread_bps REAL","bid_capacity_usd REAL","ask_capacity_usd REAL"):
        name=definition.split()[0]
        if name not in sample_cols:
            c.execute(f"ALTER TABLE binance_live_pool_samples ADD COLUMN {definition}")

def score_row(r):
    # Broad research-pool ranking only; does not alter frozen candidate score.
    vals=[
      min(3.0,max(0.0,float(r["volume_z_15m"] or 0))/2.5),
      min(3.0,max(0.0,float(r["trade_z_15m"] or 0))/2.0),
      min(3.0,max(0.0,float(r["return_z_15m"] or 0))/2.0),
      min(2.0,max(0.0,float(r["cross_sectional_rarity_pct"] or 0))/50.0),
      1.0 if int(r["wakeup"] or 0) else 0.0,
      1.0 if int(r["reignition"] or 0) else 0.0,
      1.0 if int(r["trigger"] or 0) else 0.0,
      1.0 if int(r["retention"] or 0) else 0.0,
    ]
    penalty=2.0 if int(r["climax_risk"] or 0) else 0.0
    return sum(vals)-penalty

def build_pool(c,ts):
    rows=c.execute("""SELECT * FROM features WHERE scan_time_utc=?
      AND selection_class!='RANDOM_CONTROL' AND price IS NOT NULL
      ORDER BY score DESC,cross_sectional_rarity_pct DESC""",(ts,)).fetchall()
    ranked=sorted(((score_row(r),r) for r in rows),key=lambda x:x[0],reverse=True)
    # Keep breadth: at least 15 when available, at most 25.
    chosen=ranked[:min(POOL_MAX,max(POOL_MIN,min(len(ranked),POOL_MAX)))]
    for i,(s,r) in enumerate(chosen,1):
        c.execute("""INSERT OR IGNORE INTO binance_live_pool
          (scan_time_utc,symbol,rank_value,pool_score,first_price,status,sample_count,
           version,created_at_utc)
          VALUES(?,?,?,?,?,'WATCHING',0,?,?)""",
          (ts,r["symbol"],i,s,r["price"],VERSION,utc_now()))
    c.commit()
    return [r["symbol"] for _,r in chosen]

def snapshot(symbol):
    k=spot_api_get("/api/v3/klines",{"symbol":symbol,"interval":"5m","limit":2})
    if not k: return None
    row=k[-1]
    close=float(row[4]); qv=float(row[7]); trades=int(row[8]); taker=float(row[10])
    taker_ratio=(taker/qv) if qv>0 else None
    book=spot_api_get("/api/v3/depth",{"symbol":symbol,"limit":20})
    bid_levels=(book.get("bids") or [])
    ask_levels=(book.get("asks") or [])
    bids=sum(float(p)*float(q) for p,q in bid_levels)
    asks=sum(float(p)*float(q) for p,q in ask_levels)
    imb=(bids-asks)/(bids+asks) if bids+asks>0 else None
    best_bid=float(bid_levels[0][0]) if bid_levels else None
    best_ask=float(ask_levels[0][0]) if ask_levels else None
    mid=(best_bid+best_ask)/2 if best_bid and best_ask else None
    spread=((best_ask-best_bid)/mid*10000) if mid else None
    return close,qv,trades,taker_ratio,imb,spread,bids,asks

def add_sample(c,ts,symbol,n):
    p=c.execute("""SELECT first_price FROM binance_live_pool
      WHERE scan_time_utc=? AND symbol=? AND version=?""",(ts,symbol,VERSION)).fetchone()
    if not p: return
    try: s=snapshot(symbol)
    except Exception as e:
        print("pool sample error",symbol,str(e)[:120]); return
    if not s: return
    price,qv,trades,taker,imb,spread,bids,asks=s
    first=float(p["first_price"] or price)
    first_q=c.execute("""SELECT quote_volume_5m FROM binance_live_pool_samples
      WHERE scan_time_utc=? AND symbol=? AND sample_no=0 AND version=?""",
      (ts,symbol,VERSION)).fetchone()
    ratio=(qv/float(first_q[0])) if first_q and first_q[0] not in (None,0) else 1.0
    ch=100*(price/first-1) if first>0 else None
    c.execute("""INSERT OR REPLACE INTO binance_live_pool_samples
      (scan_time_utc,symbol,sample_no,sampled_at_utc,price,change_from_start_pct,
       quote_volume_5m,trade_count_5m,taker_buy_ratio_5m,volume_ratio_vs_first,
       book_imbalance,spread_bps,bid_capacity_usd,ask_capacity_usd,version)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (ts,symbol,n,utc_now(),price,ch,qv,trades,taker,ratio,imb,spread,bids,asks,VERSION))
    c.execute("""UPDATE binance_live_pool SET sample_count=(
      SELECT COUNT(*) FROM binance_live_pool_samples
      WHERE scan_time_utc=? AND symbol=? AND version=?)
      WHERE scan_time_utc=? AND symbol=? AND version=?""",
      (ts,symbol,VERSION,ts,symbol,VERSION))

def finalize(c,ts,symbol):
    rows=c.execute("""SELECT * FROM binance_live_pool_samples
      WHERE scan_time_utc=? AND symbol=? AND version=? ORDER BY sample_no""",
      (ts,symbol,VERSION)).fetchall()
    if len(rows)<3:
        status="INSUFFICIENT"; score=0; reasons=["3'ten az canlı örnek"]
    else:
        changes=[float(r["change_from_start_pct"] or 0) for r in rows]
        takers=[float(r["taker_buy_ratio_5m"]) for r in rows if r["taker_buy_ratio_5m"] is not None]
        imbs=[float(r["book_imbalance"]) for r in rows if r["book_imbalance"] is not None]
        vols=[float(r["quote_volume_5m"] or 0) for r in rows]
        spreads=[float(r["spread_bps"]) for r in rows if r["spread_bps"] is not None]
        score=0; reasons=[]
        if changes[-1]>=0: score+=1; reasons.append("15dk sonunda başlangıcın üstünde")
        if max(changes)-min(changes)<=8 and changes[-1]>=-1: score+=1; reasons.append("hareket tamamen geri verilmedi")
        if takers and statistics.median(takers)>=0.52: score+=1; reasons.append("alış akışı çoğu ölçümde üstün")
        if imbs and statistics.median(imbs)>=0: score+=1; reasons.append("order-book ortalaması satışa dönmedi")
        if len(vols)>=3 and statistics.median(vols[1:])>=0.60*max(vols[0],1): score+=1; reasons.append("hacim ilk kıpırdanmadan sonra tamamen sönmedi")
        positive=sum(x>=0 for x in changes[1:])
        if positive>=3: score+=1; reasons.append("birden fazla kontrolde fiyat korunmuş")
        if spreads and statistics.median(spreads)<=20:
            score+=1; reasons.append("15dk boyunca spread makul kaldı")
        if imbs and sum(x>=0 for x in imbs)>=max(3,len(imbs)//2):
            score+=1; reasons.append("order-book baskısı tek ölçüme bağlı değil")
        status="CONFIRMED" if score>=5 else ("BORDERLINE" if score>=4 else "FADED")
    c.execute("""UPDATE binance_live_pool SET status=?,confirmation_score=?,
      confirmed_at_utc=?,reason_json=? WHERE scan_time_utc=? AND symbol=? AND version=?""",
      (status,score,utc_now(),json.dumps(reasons,ensure_ascii=False),ts,symbol,VERSION))

def main():
    if not os.path.exists(DB):
        print("live pool DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c)
        scan=c.execute("""SELECT scan_time_utc FROM scans WHERE health_status!='INVALID'
          ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan: print("live pool scan yok"); return
        ts=scan["scan_time_utc"]
        symbols=build_pool(c,ts)
        print(f"live pool | {len(symbols)} coin | 15dk izleme basladi")
        for n in range(SAMPLES):
            if n>0: time.sleep(SAMPLE_EVERY_SECONDS)
            for sym in symbols: add_sample(c,ts,sym,n)
            c.commit()
            print(f"live pool sample {n+1}/{SAMPLES}")
        for sym in symbols: finalize(c,ts,sym)
        c.commit()
        counts={r["status"]:r["n"] for r in c.execute("""SELECT status,COUNT(*) n
          FROM binance_live_pool WHERE scan_time_utc=? AND version=? GROUP BY status""",(ts,VERSION))}
        print("live pool final",counts)

if __name__=="__main__": main()
