#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Activation-pattern research for History Miner.

Uses only archived daily bars. It compares what changes immediately before
historical rise events versus non-event control periods. Research only; it does
not alter live Avci thresholds or send orders.
"""
import os, sqlite3, statistics, math

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="activation-daily-v0.1-20260924"

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.row_factory=sqlite3.Row
    return c

def pct(a,b):
    return 100.0*(b/a-1.0) if a and b and a>0 else None

def avg(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return sum(xs)/len(xs) if xs else None

def med(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(xs) if xs else None

def init_db(c):
    c.execute("""CREATE TABLE IF NOT EXISTS activation_features(
      pair TEXT NOT NULL, anchor_ts INTEGER NOT NULL, label TEXT NOT NULL,
      ret_1d REAL, ret_3d REAL, ret_7d REAL,
      vol_ratio_1_30 REAL, vol_ratio_3_30 REAL,
      range_ratio_1_30 REAL, close_location_1d REAL,
      dist_low_7d REAL, dist_high_30d REAL,
      green_ratio_3d REAL, feature_version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,label,feature_version)
    )""")

def features(bars,i):
    if i < 31: return None
    cur=bars[i]
    closes=[x["close"] for x in bars]
    highs=[x["high"] for x in bars]
    lows=[x["low"] for x in bars]
    vols=[x["volume_quote"] for x in bars]
    r1=pct(closes[i-1],closes[i])
    r3=pct(closes[i-3],closes[i])
    r7=pct(closes[i-7],closes[i])
    base30=med(vols[i-30:i])
    v1=(vols[i]/base30) if base30 and vols[i] is not None else None
    v3avg=avg(vols[i-2:i+1])
    v3=(v3avg/base30) if base30 and v3avg is not None else None
    ranges30=[100*(highs[j]/lows[j]-1) for j in range(i-30,i) if lows[j] and lows[j]>0]
    cur_range=100*(highs[i]/lows[i]-1) if lows[i] and lows[i]>0 else None
    rr=(cur_range/med(ranges30)) if cur_range is not None and med(ranges30) else None
    cl=((closes[i]-lows[i])/(highs[i]-lows[i])) if highs[i]>lows[i] else 0.5
    low7=min(lows[i-6:i+1]); high30=max(highs[i-29:i+1])
    dl=pct(low7,closes[i])
    dh=pct(high30,closes[i])
    green=sum(1 for j in range(i-2,i+1) if closes[j] >= bars[j]["open"])/3.0
    return (r1,r3,r7,v1,v3,rr,cl,dl,dh,green)

def bars_for(c,pair):
    return c.execute("""SELECT ts,open,high,low,close,volume_quote
        FROM daily_bars WHERE pair=? ORDER BY ts""",(pair,)).fetchall()

def main():
    if not os.path.exists(DB):
        print("Activation Miner: DB yok"); return
    with con() as c:
        init_db(c)
        anchors=c.execute("""SELECT pair,event_ts,label
            FROM event_features WHERE label IN ('RISE','CONTROL')
            ORDER BY pair,event_ts""").fetchall()
        by_pair={}
        for a in anchors: by_pair.setdefault(a["pair"],[]).append(a)
        inserted=0
        for pair,rows in by_pair.items():
            bars=bars_for(c,pair)
            if len(bars)<40: continue
            pos={int(b["ts"]):i for i,b in enumerate(bars)}
            for a in rows:
                i=pos.get(int(a["event_ts"]))
                if i is None: continue
                feat=features(bars,i)
                if not feat: continue
                c.execute("""INSERT OR REPLACE INTO activation_features VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pair,int(a["event_ts"]),a["label"],*feat,VERSION))
                inserted+=1
        c.commit()
        nr=c.execute("SELECT COUNT(*) FROM activation_features WHERE label='RISE'").fetchone()[0]
        nc=c.execute("SELECT COUNT(*) FROM activation_features WHERE label='CONTROL'").fetchone()[0]
    print(f"Activation Miner: upserted={inserted} rise={nr} controls={nc} version={VERSION}")

if __name__=="__main__":
    main()
