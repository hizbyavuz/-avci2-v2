#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-sectional + trajectory context for Binance Avci.

Research-only. Adds market breadth and multi-scan persistence/acceleration
without changing frozen candidate thresholds.
"""
import os, sqlite3, statistics
from datetime import datetime, timezone

DB=os.getenv("BINANCE_DB","binance_avci2.db")
VERSION="binance-context-v1-20260926"

def now(): return datetime.now(timezone.utc).isoformat()
def pct_rank(values,x):
    xs=[float(v) for v in values if v is not None]
    if not xs or x is None: return None
    return 100.0*sum(v<=float(x) for v in xs)/len(xs)
def med(xs):
    xs=[float(x) for x in xs if x is not None]
    return statistics.median(xs) if xs else None

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS binance_context_observations(
      scan_time_utc TEXT NOT NULL,
      symbol TEXT NOT NULL,
      breadth_15m_positive_pct REAL,
      breadth_1h_positive_pct REAL,
      breadth_24h_positive_pct REAL,
      return_15m_percentile REAL,
      return_1h_percentile REAL,
      btc_relative_percentile REAL,
      volume_z_percentile REAL,
      last6_seen INTEGER,
      last6_positive_1h INTEGER,
      last6_high_rarity INTEGER,
      volume_z_slope REAL,
      btc_relative_slope REAL,
      taker_ratio_median REAL,
      timeframe_alignment TEXT,
      trajectory TEXT,
      context_score INTEGER,
      version TEXT NOT NULL,
      created_at_utc TEXT NOT NULL,
      PRIMARY KEY(scan_time_utc,symbol,version)
    )""")

def slope(vals):
    vals=[float(v) for v in vals if v is not None]
    if len(vals)<2: return None
    return vals[-1]-vals[0]

def main():
    if not os.path.exists(DB): print("context DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c)
        scan=c.execute("""SELECT scan_time_utc FROM scans WHERE health_status!='INVALID'
          ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan: print("context scan yok"); return
        ts=scan["scan_time_utc"]
        universe=c.execute("""SELECT * FROM features WHERE scan_time_utc=?""",(ts,)).fetchall()
        if not universe: return
        b15=100*sum(float(r["change_15m"] or 0)>0 for r in universe)/len(universe)
        b1=100*sum(float(r["change_1h"] or 0)>0 for r in universe)/len(universe)
        b24=100*sum(float(r["change_24h"] or 0)>0 for r in universe)/len(universe)
        vals15=[r["change_15m"] for r in universe]
        vals1=[r["change_1h"] for r in universe]
        valsrel=[r["btc_relative_24h"] for r in universe]
        valsvz=[r["volume_z_15m"] for r in universe]

        # Context for broad pool first; otherwise selected/near-miss candidates.
        pool_syms=[]
        try:
            pool_syms=[r[0] for r in c.execute("""SELECT symbol FROM binance_live_pool
              WHERE scan_time_utc=? ORDER BY rank_value LIMIT 25""",(ts,)).fetchall()]
        except sqlite3.OperationalError:
            pass
        if not pool_syms:
            pool_syms=[r["symbol"] for r in universe
                       if r["selection_class"] in ("CANDIDATE","NEAR_MISS")][:25]

        for sym in pool_syms:
            r=next((x for x in universe if x["symbol"]==sym),None)
            if not r: continue
            hist=c.execute("""SELECT scan_time_utc,change_1h,cross_sectional_rarity_pct,
                 volume_z_15m,btc_relative_24h,taker_buy_ratio_15m
              FROM features WHERE symbol=? AND scan_time_utc<=?
              ORDER BY scan_time_utc DESC LIMIT 6""",(sym,ts)).fetchall()
            chronological=list(reversed(hist))
            seen=len(hist)
            pos=sum(float(x["change_1h"] or 0)>0 for x in hist)
            high=sum(float(x["cross_sectional_rarity_pct"] or 0)>=75 for x in hist)
            vzs=[x["volume_z_15m"] for x in chronological]
            rels=[x["btc_relative_24h"] for x in chronological]
            takers=[x["taker_buy_ratio_15m"] for x in hist]
            v_slope=slope(vzs); rel_slope=slope(rels)
            tm=sum(float(r[k] or 0)>0 for k in ("change_15m","change_1h","change_3h"))
            alignment="ALIGNED_UP" if tm==3 else ("MIXED" if tm in (1,2) else "DOWN")
            score=0
            if pct_rank(vals15,r["change_15m"]) is not None and pct_rank(vals15,r["change_15m"])>=75: score+=1
            if pct_rank(vals1,r["change_1h"]) is not None and pct_rank(vals1,r["change_1h"])>=75: score+=1
            if pct_rank(valsrel,r["btc_relative_24h"]) is not None and pct_rank(valsrel,r["btc_relative_24h"])>=70: score+=1
            if pct_rank(valsvz,r["volume_z_15m"]) is not None and pct_rank(valsvz,r["volume_z_15m"])>=75: score+=1
            if seen>=3 and pos>=max(2,seen//2): score+=1
            if seen>=3 and high>=2: score+=1
            if v_slope is not None and v_slope>0: score+=1
            if rel_slope is not None and rel_slope>0: score+=1
            if med(takers) is not None and med(takers)>=0.52: score+=1
            if alignment=="ALIGNED_UP": score+=1
            trajectory="STRENGTHENING" if score>=7 else ("STABLE" if score>=4 else "WEAKENING")
            c.execute("""INSERT OR REPLACE INTO binance_context_observations VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (ts,sym,b15,b1,b24,pct_rank(vals15,r["change_15m"]),
               pct_rank(vals1,r["change_1h"]),pct_rank(valsrel,r["btc_relative_24h"]),
               pct_rank(valsvz,r["volume_z_15m"]),seen,pos,high,v_slope,rel_slope,
               med(takers),alignment,trajectory,score,VERSION,now()))
        c.commit()
        print(f"context | breadth 15m={b15:.0f}% 1h={b1:.0f}% 24h={b24:.0f}% | {len(pool_syms)} coin")

if __name__=="__main__": main()
