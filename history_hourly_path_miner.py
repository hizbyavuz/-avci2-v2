#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hourly pre-rise path miner for History Miner.

For each archived RISE event, derive the first daily window where price reaches
+10% versus the event anchor close, then fetch hourly candles around that window
and align t0 to the first hourly +10% crossing. For CONTROL anchors, t0 is the
end of the control day. Save snapshots at -72/-48/-24/-12/-6/-3/-1 hours.

Research-only. It does not alter live Avci thresholds or send orders.
"""
from __future__ import annotations
import json, math, os, sqlite3, statistics, time
from urllib import parse, request, error

DB=os.getenv("HISTORY_DB","history_miner.db")
BASE="https://api.gateio.ws/api/v4/spot/candlesticks"
BUDGET=int(os.getenv("HISTORY_PATH_BUDGET","60"))
SLEEP=float(os.getenv("HISTORY_PATH_SLEEP","0.20"))
VERSION="hourly-path-v0.1-20260924"
OFFSETS=(-72,-48,-24,-12,-6,-3,-1)

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory=sqlite3.Row
    return c

def f(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except (TypeError,ValueError):
        return None

def pct(a,b):
    return 100.0*(b/a-1.0) if a and b and a>0 else None

def avg(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return sum(xs)/len(xs) if xs else None

def med(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(xs) if xs else None

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS path_cases(
      pair TEXT NOT NULL, anchor_ts INTEGER NOT NULL, label TEXT NOT NULL,
      base_price REAL NOT NULL, onset_day_ts INTEGER, t0_ts INTEGER,
      status TEXT NOT NULL, last_error TEXT, version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,label,version)
    );
    CREATE TABLE IF NOT EXISTS hourly_path_snapshots(
      pair TEXT NOT NULL, anchor_ts INTEGER NOT NULL, label TEXT NOT NULL,
      t0_ts INTEGER NOT NULL, offset_h INTEGER NOT NULL, snap_ts INTEGER NOT NULL,
      price REAL, ret_1h REAL, ret_3h REAL, ret_6h REAL, ret_12h REAL, ret_24h REAL,
      vol_ratio_1_24 REAL, vol_ratio_3_24 REAL,
      range_ratio_1_24 REAL, close_location REAL,
      dist_low_24h REAL, dist_high_24h REAL,
      version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,label,offset_h,version)
    );
    """)

def fetch_hourly(pair,start_ts,end_ts):
    q=parse.urlencode({"currency_pair":pair,"interval":"1h","from":int(start_ts),"to":int(end_ts)})
    url=BASE+"?"+q
    with request.urlopen(url,timeout=25) as r:
        data=json.load(r)
    if not isinstance(data,list):
        raise ValueError("invalid_hourly_payload")
    out=[]
    for row in data:
        if not isinstance(row,list) or len(row)<7: continue
        ts=int(float(row[0])); qv=f(row[1]); close=f(row[2]); high=f(row[3]); low=f(row[4]); op=f(row[5]); bv=f(row[6])
        if None in (close,high,low,op) or min(close,high,low,op)<=0: continue
        out.append({"ts":ts,"open":op,"high":high,"low":low,"close":close,"qv":qv,"bv":bv})
    out.sort(key=lambda x:x["ts"])
    return out

def daily_rows(c,pair):
    return c.execute("""SELECT ts,open,high,low,close,volume_quote FROM daily_bars
        WHERE pair=? ORDER BY ts""",(pair,)).fetchall()

def derive_onset_day(bars,anchor_ts,base):
    pos={int(r["ts"]):i for i,r in enumerate(bars)}
    i=pos.get(int(anchor_ts))
    if i is None: return None
    for r in bars[i+1:min(len(bars),i+62)]:
        if r["high"] is not None and float(r["high"]) >= base*1.10:
            return int(r["ts"])
    return None

def snapshot(hourly,idx):
    if idx<24: return None
    cur=hourly[idx]
    closes=[x["close"] for x in hourly]
    highs=[x["high"] for x in hourly]
    lows=[x["low"] for x in hourly]
    qv=[x["qv"] for x in hourly]
    def ret(h):
        return pct(closes[idx-h],closes[idx]) if idx>=h else None
    prev_q=[x for x in qv[idx-24:idx] if x is not None]
    base_v=med(prev_q)
    v1=(qv[idx]/base_v) if base_v and qv[idx] is not None else None
    v3avg=avg(qv[max(0,idx-2):idx+1])
    v3=(v3avg/base_v) if base_v and v3avg is not None else None
    ranges=[100*(highs[j]/lows[j]-1) for j in range(idx-24,idx) if lows[j] and lows[j]>0]
    cur_range=100*(cur["high"]/cur["low"]-1) if cur["low"]>0 else None
    rr=(cur_range/med(ranges)) if cur_range is not None and med(ranges) else None
    cl=((cur["close"]-cur["low"])/(cur["high"]-cur["low"])) if cur["high"]>cur["low"] else 0.5
    lo=min(lows[idx-23:idx+1]); hi=max(highs[idx-23:idx+1])
    return (
        cur["close"],ret(1),ret(3),ret(6),ret(12),ret(24),
        v1,v3,rr,cl,pct(lo,cur["close"]),pct(hi,cur["close"])
    )

def process_case(c,row):
    pair=row["pair"]; anchor=int(row["event_ts"]); label=row["label"]; base=float(row["pre_price"])
    bars=daily_rows(c,pair)
    if not bars: raise ValueError("no_daily_bars")
    if label=="RISE":
        onset=derive_onset_day(bars,anchor,base)
        if onset is None: raise ValueError("no_plus10_onset")
        # Enough history for -72h and enough future to locate the first intraday crossing.
        start=onset-5*86400
        end=onset+2*86400
        hourly=fetch_hourly(pair,start,end)
        if len(hourly)<72: raise ValueError(f"short_hourly:{len(hourly)}")
        crossings=[x for x in hourly if x["ts"]>=onset-86400 and x["high"]>=base*1.10]
        if not crossings: raise ValueError("no_hourly_plus10_cross")
        t0=int(crossings[0]["ts"])
    else:
        onset=None
        # Daily candles are timestamped at their bucket start; compare controls at day end.
        t0=anchor+86400
        start=t0-5*86400
        end=t0+6*3600
        hourly=fetch_hourly(pair,start,end)
        if len(hourly)<72: raise ValueError(f"short_hourly:{len(hourly)}")

    ts_to_i={int(x["ts"]):i for i,x in enumerate(hourly)}
    saved=0
    for off in OFFSETS:
        target=t0+off*3600
        # API timestamps are hourly; exact match should exist. Allow nearest prior hour.
        eligible=[ts for ts in ts_to_i if ts<=target and target-ts<=3600]
        if not eligible: continue
        snap_ts=max(eligible); idx=ts_to_i[snap_ts]
        feat=snapshot(hourly,idx)
        if not feat: continue
        c.execute("""INSERT OR REPLACE INTO hourly_path_snapshots VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (pair,anchor,label,t0,off,snap_ts,*feat,VERSION))
        saved+=1
    status="DONE" if saved>=5 else "PARTIAL"
    c.execute("""INSERT OR REPLACE INTO path_cases
      (pair,anchor_ts,label,base_price,onset_day_ts,t0_ts,status,last_error,version)
      VALUES(?,?,?,?,?,?,?,?,?)""",(pair,anchor,label,base,onset,t0,status,None,VERSION))
    return saved

def due(c,limit):
    return c.execute("""SELECT e.pair,e.event_ts,e.label,e.pre_price
      FROM event_features e
      LEFT JOIN path_cases p
        ON p.pair=e.pair AND p.anchor_ts=e.event_ts AND p.label=e.label AND p.version=?
      WHERE e.label IN ('RISE','CONTROL') AND e.pre_price IS NOT NULL AND p.pair IS NULL
      ORDER BY CASE e.label WHEN 'RISE' THEN 0 ELSE 1 END, e.event_ts DESC
      LIMIT ?""",(VERSION,limit)).fetchall()

def main():
    if not os.path.exists(DB):
        print("Hourly Path Miner: DB yok"); return
    with con() as c:
        init_db(c)
        rows=due(c,BUDGET)
        ok=err=snaps=0
        for row in rows:
            try:
                n=process_case(c,row)
                c.commit(); ok+=1; snaps+=n
                print(f'{row["pair"]} {row["label"]}: snapshots={n}')
            except (error.URLError,TimeoutError,ValueError,OSError) as e:
                c.execute("""INSERT OR REPLACE INTO path_cases
                  (pair,anchor_ts,label,base_price,onset_day_ts,t0_ts,status,last_error,version)
                  VALUES(?,?,?,?,?,?,?,?,?)""",
                  (row["pair"],int(row["event_ts"]),row["label"],float(row["pre_price"]),None,None,
                   "ERROR",type(e).__name__+":"+str(e)[:160],VERSION))
                c.commit(); err+=1
                print(f'{row["pair"]} {row["label"]}: ERROR {type(e).__name__} {str(e)[:100]}')
            time.sleep(SLEEP)
        totals=c.execute("""SELECT label,COUNT(*) FROM path_cases
          WHERE version=? AND status IN ('DONE','PARTIAL') GROUP BY label""",(VERSION,)).fetchall()
    print(f"Hourly Path Miner: attempted={len(rows)} ok={ok} errors={err} snapshots={snaps} totals={[(r[0],r[1]) for r in totals]}")

if __name__=="__main__":
    main()
