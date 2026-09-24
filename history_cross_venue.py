#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-venue History Miner validation: Gate vs Binance Spot.

Purpose:
- keep frozen V0/V2/V3 untouched
- split archived Gate path cases into Binance-symbol-match vs Gate-only/no-history
- fetch Binance Spot 1h OHLCV where the symbol exists and historical candles
  are available at the Gate event time
- compute the same pre-event path features at T-72/-48/-36/-24/-12/-6/-3/-1
  relative to the Gate t0 clock
- for RISE cases, estimate whether Binance's own +10% crossing occurred before
  or after Gate's +10% t0
- compare earliest descriptive separation on Binance versus Gate subgroups

IMPORTANT: ticker equality is NOT proof of identical token identity. Results are
tagged SYMBOL_MATCH_UNVERIFIED until contract/asset identity is independently
verified. This layer is research-only and never trades.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import time
from urllib import parse, request, error

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="cross-venue-v0.1-20260924"
PATH_VERSION="hourly-path-v0.1-20260924"
BUDGET=int(os.getenv("CROSS_VENUE_BUDGET","60"))
SLEEP=float(os.getenv("CROSS_VENUE_SLEEP","0.12"))
OFFSETS=(-72,-48,-36,-24,-12,-6,-3,-1)
BINANCE_BASES=(
    "https://data-api.binance.vision",
    "https://api.binance.com",
)

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory=sqlite3.Row
    return c

def f(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):
        return None

def pct(a,b):
    return 100.0*(b/a-1.0) if a and b and a>0 else None

def med(xs):
    vals=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(vals) if vals else None

def avg(xs):
    vals=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return sum(vals)/len(vals) if vals else None

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS cross_venue_cases(
      pair TEXT NOT NULL,
      anchor_ts INTEGER NOT NULL,
      label TEXT NOT NULL,
      gate_t0_ts INTEGER NOT NULL,
      binance_symbol TEXT,
      venue_group TEXT NOT NULL,
      identity_status TEXT NOT NULL,
      history_status TEXT NOT NULL,
      binance_anchor_price REAL,
      binance_t0_ts INTEGER,
      lead_hours REAL,
      last_error TEXT,
      version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,label,version)
    );
    CREATE TABLE IF NOT EXISTS cross_venue_snapshots(
      pair TEXT NOT NULL,
      anchor_ts INTEGER NOT NULL,
      label TEXT NOT NULL,
      venue TEXT NOT NULL,
      offset_h INTEGER NOT NULL,
      snap_ts INTEGER NOT NULL,
      price REAL,
      ret_1h REAL,
      ret_3h REAL,
      ret_6h REAL,
      ret_12h REAL,
      ret_24h REAL,
      vol_ratio_1_24 REAL,
      vol_ratio_3_24 REAL,
      range_ratio_1_24 REAL,
      close_location REAL,
      dist_low_24h REAL,
      dist_high_24h REAL,
      version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,label,venue,offset_h,version)
    );
    CREATE TABLE IF NOT EXISTS cross_venue_summary(
      venue_group TEXT NOT NULL,
      venue TEXT NOT NULL,
      feature TEXT NOT NULL,
      offset_h INTEGER NOT NULL,
      rise_n INTEGER NOT NULL,
      control_n INTEGER NOT NULL,
      rise_median REAL,
      control_median REAL,
      effect REAL,
      version TEXT NOT NULL,
      PRIMARY KEY(venue_group,venue,feature,offset_h,version)
    );
    CREATE TABLE IF NOT EXISTS cross_venue_runs(
      run_id TEXT PRIMARY KEY,
      started_utc TEXT NOT NULL,
      finished_utc TEXT,
      attempted INTEGER NOT NULL DEFAULT 0,
      ok INTEGER NOT NULL DEFAULT 0,
      symbol_match INTEGER NOT NULL DEFAULT 0,
      gate_only INTEGER NOT NULL DEFAULT 0,
      notes TEXT,
      version TEXT NOT NULL
    );
    """)

def http_json(url):
    last=None
    for _ in range(2):
        try:
            req=request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with request.urlopen(req,timeout=25) as r:
                return json.load(r)
        except (error.URLError,TimeoutError,OSError,ValueError) as e:
            last=e
    raise last or RuntimeError("http_json_failed")

def exchange_symbols():
    last=None
    for base in BINANCE_BASES:
        try:
            data=http_json(base+"/api/v3/exchangeInfo")
            out=set()
            for s in data.get("symbols",[]):
                if s.get("quoteAsset")=="USDT" and s.get("status")=="TRADING":
                    out.add(str(s.get("symbol") or "").upper())
            if out:
                return out
        except Exception as e:
            last=e
    raise RuntimeError("binance_exchange_info:"+str(last)[:120])

def fetch_binance_hourly(symbol,start_ts,end_ts):
    params=parse.urlencode({
        "symbol":symbol,
        "interval":"1h",
        "startTime":int(start_ts)*1000,
        "endTime":int(end_ts)*1000,
        "limit":1000,
    })
    last=None
    for base in BINANCE_BASES:
        try:
            data=http_json(base+"/api/v3/klines?"+params)
            if not isinstance(data,list):
                raise ValueError("invalid_klines_payload")
            out=[]
            for row in data:
                if not isinstance(row,list) or len(row)<8:
                    continue
                ts=int(int(row[0])/1000)
                op=f(row[1]); high=f(row[2]); low=f(row[3]); close=f(row[4])
                qv=f(row[7])
                if None in (op,high,low,close) or min(op,high,low,close)<=0:
                    continue
                out.append({"ts":ts,"open":op,"high":high,"low":low,"close":close,"qv":qv})
            out.sort(key=lambda x:x["ts"])
            return out
        except Exception as e:
            last=e
    raise RuntimeError("binance_klines:"+str(last)[:120])

def snapshot(hourly,idx):
    if idx<24:return None
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

def nearest_index(hourly,target,max_gap=3600):
    eligible=[(i,x["ts"]) for i,x in enumerate(hourly)
              if x["ts"]<=target and target-x["ts"]<=max_gap]
    return max(eligible,key=lambda z:z[1])[0] if eligible else None

def gate_symbol(pair):
    # Gate pair form is BASE_USDT; Binance form is BASEUSDT.
    if not pair.endswith("_USDT"):
        return None
    return pair.replace("_","")

def due_cases(c,limit):
    return c.execute("""SELECT p.pair,p.anchor_ts,p.label,p.t0_ts
      FROM path_cases p
      LEFT JOIN cross_venue_cases x
        ON x.pair=p.pair AND x.anchor_ts=p.anchor_ts
       AND x.label=p.label AND x.version=?
      WHERE p.version=? AND p.status IN ('DONE','PARTIAL')
        AND p.t0_ts IS NOT NULL AND x.pair IS NULL
      ORDER BY p.anchor_ts DESC
      LIMIT ?""",(VERSION,PATH_VERSION,limit)).fetchall()

def process_case(c,row,symbols):
    pair=row["pair"]; anchor=int(row["anchor_ts"]); label=row["label"]
    gate_t0=int(row["t0_ts"])
    symbol=gate_symbol(pair)
    if not symbol or symbol not in symbols:
        c.execute("""INSERT OR REPLACE INTO cross_venue_cases
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (pair,anchor,label,gate_t0,symbol,"GATE_ONLY",
           "NO_BINANCE_SYMBOL_MATCH","NOT_AVAILABLE",None,None,None,None,VERSION))
        return "GATE_ONLY",0

    # Same ticker is only a candidate identity match, never proof of same contract.
    identity="SYMBOL_MATCH_UNVERIFIED"
    start=min(anchor-6*3600,gate_t0-100*3600)
    end=max(gate_t0+30*3600,anchor+30*3600)
    hourly=fetch_binance_hourly(symbol,start,end)
    if len(hourly)<72:
        c.execute("""INSERT OR REPLACE INTO cross_venue_cases
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (pair,anchor,label,gate_t0,symbol,"BINANCE_SYMBOL_MATCH",
           identity,"NO_EVENT_TIME_HISTORY",None,None,None,
           f"short_hourly:{len(hourly)}",VERSION))
        return "MATCH_NO_HISTORY",0

    # Require real Binance history around the Gate anchor/event timestamp.
    aidx=nearest_index(hourly,anchor+3600,7200)
    if aidx is None:
        c.execute("""INSERT OR REPLACE INTO cross_venue_cases
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (pair,anchor,label,gate_t0,symbol,"BINANCE_SYMBOL_MATCH",
           identity,"NO_EVENT_TIME_HISTORY",None,None,None,
           "anchor_not_covered",VERSION))
        return "MATCH_NO_HISTORY",0

    anchor_price=hourly[aidx]["close"]
    bin_t0=None
    lead=None
    if label=="RISE":
        crossings=[x for x in hourly
                   if x["ts"]>=anchor and x["ts"]<=gate_t0+24*3600
                   and x["high"]>=anchor_price*1.10]
        if crossings:
            bin_t0=int(crossings[0]["ts"])
            lead=(bin_t0-gate_t0)/3600.0

    saved=0
    for off in OFFSETS:
        target=gate_t0+off*3600
        idx=nearest_index(hourly,target,3600)
        if idx is None:continue
        feat=snapshot(hourly,idx)
        if not feat:continue
        c.execute("""INSERT OR REPLACE INTO cross_venue_snapshots
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (pair,anchor,label,"BINANCE",off,int(hourly[idx]["ts"]),*feat,VERSION))
        saved+=1

    status="OK" if saved>=6 else "PARTIAL"
    c.execute("""INSERT OR REPLACE INTO cross_venue_cases
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (pair,anchor,label,gate_t0,symbol,"BINANCE_SYMBOL_MATCH",
       identity,status,anchor_price,bin_t0,lead,None,VERSION))
    return "BINANCE_SYMBOL_MATCH",saved

def effect(rise,ctl):
    if len(rise)<8 or len(ctl)<8:return None
    rm=med(rise); cm=med(ctl)
    if rm is None or cm is None:return None
    scale=med([abs(float(x)-cm) for x in ctl if x is not None])
    if not scale or scale<1e-9:scale=max(abs(cm),1.0)
    return (rm-cm)/scale

FEATURES=("dist_low_24h","dist_high_24h","ret_24h","ret_12h","ret_6h","vol_ratio_3_24")

def rebuild_summary(c):
    c.execute("DELETE FROM cross_venue_summary WHERE version=?",(VERSION,))

    # Binance snapshots for symbol-matched, event-time-covered cases.
    for venue_group,venue in (("BINANCE_SYMBOL_MATCH","BINANCE"),):
        for feature in FEATURES:
            for off in OFFSETS:
                rise=[r[0] for r in c.execute(f"""SELECT s.{feature}
                  FROM cross_venue_snapshots s JOIN cross_venue_cases x
                    ON x.pair=s.pair AND x.anchor_ts=s.anchor_ts AND x.label=s.label
                   AND x.version=?
                  WHERE s.version=? AND s.venue=? AND s.offset_h=?
                    AND x.venue_group=? AND x.history_status IN ('OK','PARTIAL')
                    AND s.label='RISE' AND s.{feature} IS NOT NULL""",
                  (VERSION,VERSION,venue,off,venue_group))]
                ctl=[r[0] for r in c.execute(f"""SELECT s.{feature}
                  FROM cross_venue_snapshots s JOIN cross_venue_cases x
                    ON x.pair=s.pair AND x.anchor_ts=s.anchor_ts AND x.label=s.label
                   AND x.version=?
                  WHERE s.version=? AND s.venue=? AND s.offset_h=?
                    AND x.venue_group=? AND x.history_status IN ('OK','PARTIAL')
                    AND s.label='CONTROL' AND s.{feature} IS NOT NULL""",
                  (VERSION,VERSION,venue,off,venue_group))]
                if not rise and not ctl:continue
                rm,cm=med(rise),med(ctl)
                c.execute("""INSERT OR REPLACE INTO cross_venue_summary
                  VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (venue_group,venue,feature,off,len(rise),len(ctl),rm,cm,effect(rise,ctl),VERSION))

    # Gate snapshots split by whether that event has a Binance symbol match/history.
    for venue_group in ("BINANCE_SYMBOL_MATCH","GATE_ONLY"):
        for feature in FEATURES:
            for off in tuple(x for x in OFFSETS if x!=-36):
                rise=[r[0] for r in c.execute(f"""SELECT h.{feature}
                  FROM hourly_path_snapshots h JOIN cross_venue_cases x
                    ON x.pair=h.pair AND x.anchor_ts=h.anchor_ts AND x.label=h.label
                   AND x.version=?
                  WHERE h.version=? AND h.offset_h=? AND x.venue_group=?
                    AND h.label='RISE' AND h.{feature} IS NOT NULL""",
                  (VERSION,PATH_VERSION,off,venue_group))]
                ctl=[r[0] for r in c.execute(f"""SELECT h.{feature}
                  FROM hourly_path_snapshots h JOIN cross_venue_cases x
                    ON x.pair=h.pair AND x.anchor_ts=h.anchor_ts AND x.label=h.label
                   AND x.version=?
                  WHERE h.version=? AND h.offset_h=? AND x.venue_group=?
                    AND h.label='CONTROL' AND h.{feature} IS NOT NULL""",
                  (VERSION,PATH_VERSION,off,venue_group))]
                if not rise and not ctl:continue
                rm,cm=med(rise),med(ctl)
                c.execute("""INSERT OR REPLACE INTO cross_venue_summary
                  VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (venue_group,"GATE",feature,off,len(rise),len(ctl),rm,cm,effect(rise,ctl),VERSION))

def main():
    if not os.path.exists(DB):
        print("Cross Venue: DB yok");return
    run_id=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())
    with con() as c:
        init_db(c)
        c.execute("""INSERT OR REPLACE INTO cross_venue_runs
          (run_id,started_utc,notes,version) VALUES(?,?,?,?)""",
          (run_id,time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
           "Separate diagnostic layer. Ticker equality is unverified identity; event-time Binance history required. Frozen V2/V3 unchanged.",
           VERSION))
        try:
            symbols=exchange_symbols()
        except Exception as e:
            c.execute("""UPDATE cross_venue_runs SET finished_utc=?,notes=? WHERE run_id=?""",
              (time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
               "Binance exchangeInfo unavailable: "+str(e)[:180],run_id))
            c.commit()
            print("Cross Venue: exchangeInfo ERROR",str(e)[:160])
            return

        rows=due_cases(c,BUDGET)
        ok=sym=gate_only=0
        for row in rows:
            try:
                kind,saved=process_case(c,row,symbols)
                if kind=="BINANCE_SYMBOL_MATCH":
                    ok+=1;sym+=1
                elif kind=="GATE_ONLY":
                    gate_only+=1
                c.commit()
                print(f'{row["pair"]} {row["label"]}: {kind} snapshots={saved}')
            except Exception as e:
                pair=row["pair"];anchor=int(row["anchor_ts"]);label=row["label"];gate_t0=int(row["t0_ts"])
                symbol=gate_symbol(pair)
                c.execute("""INSERT OR REPLACE INTO cross_venue_cases
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (pair,anchor,label,gate_t0,symbol,"BINANCE_SYMBOL_MATCH",
                   "SYMBOL_MATCH_UNVERIFIED","ERROR",None,None,None,
                   type(e).__name__+":"+str(e)[:150],VERSION))
                c.commit()
                print(f'{pair} {label}: ERROR {type(e).__name__} {str(e)[:100]}')
            time.sleep(SLEEP)

        rebuild_summary(c)
        c.execute("""UPDATE cross_venue_runs SET finished_utc=?,attempted=?,ok=?,
          symbol_match=?,gate_only=? WHERE run_id=?""",
          (time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
           len(rows),ok,sym,gate_only,run_id))
        total=c.execute("""SELECT venue_group,history_status,COUNT(*)
          FROM cross_venue_cases WHERE version=?
          GROUP BY venue_group,history_status""",(VERSION,)).fetchall()
        leads=[r[0] for r in c.execute("""SELECT lead_hours FROM cross_venue_cases
          WHERE version=? AND label='RISE' AND lead_hours IS NOT NULL""",(VERSION,))]
        c.commit()
    print("Cross Venue totals:",[(r[0],r[1],r[2]) for r in total])
    if leads:
        print(f"Cross Venue lead_hours median={statistics.median(leads):.1f} n={len(leads)} (negative=Binance earlier)")

if __name__=="__main__":
    main()
