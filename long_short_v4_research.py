#!/usr/bin/env python3
"""V4 research features and quality gates; every field is source/timestamp aware.
Strictly observational. No autonomous trade scoring and no Telegram.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
import time
from datetime import datetime,timezone

from long_short_v4_native_pipeline import SOURCE, initialize
from long_short_v4_paper_math import path_result

VERSION="LS_V4_FEATURES_V1_2026_10_08"
CONFIG={"min_history_5m":21,"max_bar_age_ms":450000,
        "max_mark_age_ms":180000,"max_external_oi_age_ms":1200000,
        "expected_1m_ms":60000,"expected_5m_ms":300000,
        "min_episodes_to_calibrate":150,"fee_bps_per_side":5,
        "slippage_bps_per_side":10}
CONFIG_HASH=hashlib.sha256(json.dumps(CONFIG,sort_keys=True).encode()).hexdigest()[:20]

def setup(c):
    initialize(c)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS external_derivatives(
       id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
       observed_ms INTEGER NOT NULL, provider TEXT NOT NULL,
       oi_change_1h REAL, oi_now REAL, funding_pct REAL,
       taker_ratio REAL, long_short_ratio REAL,
       source_consistent INTEGER NOT NULL, source_age_seconds REAL,
       source_payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ext_oi_by_symbol ON external_derivatives(symbol,observed_ms);
    CREATE TABLE IF NOT EXISTS feature_snapshots(
       symbol TEXT NOT NULL, close_ms INTEGER NOT NULL, version TEXT NOT NULL,
       config_hash TEXT NOT NULL, price_source TEXT NOT NULL,
       price REAL, volume_ratio REAL, atr_pct REAL, taker_buy_share REAL,
       vwap REAL, highest_20 REAL, lowest_20 REAL, sweep_low INTEGER,
       sweep_high INTEGER, funding_rate REAL, mark_price REAL,
       liquidation_15m_usd REAL, oi_change_1h_external REAL,
       external_oi_provider TEXT, btc_regime TEXT, fully_native INTEGER NOT NULL,
       health TEXT NOT NULL, errors_json TEXT NOT NULL, features_json TEXT NOT NULL,
       PRIMARY KEY(symbol,close_ms,version)
    );
    CREATE TABLE IF NOT EXISTS paper_regrades(
       cohort TEXT NOT NULL, event_key TEXT NOT NULL, horizon_min INTEGER NOT NULL,
       label_version TEXT NOT NULL, direction TEXT NOT NULL,
       price_source TEXT, source_venue_matched INTEGER NOT NULL,
       status TEXT NOT NULL, result_json TEXT NOT NULL,
       PRIMARY KEY(cohort,event_key,horizon_min,label_version)
    );
    """)
    c.commit()

def contiguous(rows,step):
    if not rows:return False
    ts=[int(x["open_ms"]) for x in rows]
    return all(b-a==step for a,b in zip(ts,ts[1:]))

def _read_klines(c,symbol,interval,close_ms,n):
    c.row_factory=sqlite3.Row
    return c.execute("""SELECT * FROM closed_klines
     WHERE symbol=? AND interval=? AND close_ms<=? AND source=?
     ORDER BY open_ms DESC LIMIT ?""",
     (symbol,interval,close_ms,SOURCE,n)).fetchall()[::-1]

def _atr(rows):
    if len(rows)<15:return None
    vals=[]
    for p,r in zip(rows[-15:-1],rows[-14:]):
        hi,lo,prev=float(r["high"]),float(r["low"]),float(p["close"])
        vals.append(max(hi-lo,abs(hi-prev),abs(lo-prev)))
    last=float(rows[-1]["close"])
    return 100*statistics.mean(vals)/last if last>0 else None

def _vwap(rows):
    values=[(float(r["close"]),float(r["quote_volume"] or 0),
             float(r["volume"] or 0)) for r in rows]
    base=sum(x[2] for x in values)
    return sum(x[1] for x in values)/base if base>0 else None

def _closest_mark(c,symbol,close_ms):
    r=c.execute("""SELECT mark,funding_rate,event_ms FROM marks
      WHERE symbol=? AND event_ms<=? ORDER BY event_ms DESC LIMIT 1""",
      (symbol,close_ms)).fetchone()
    return r if r and close_ms-int(r[2])<=CONFIG["max_mark_age_ms"] else None

def _closest_external(c,symbol,close_ms):
    r=c.execute("""SELECT provider,oi_change_1h,observed_ms,source_consistent
      FROM external_derivatives WHERE symbol=? AND observed_ms<=?
      ORDER BY observed_ms DESC LIMIT 1""",(symbol,close_ms)).fetchone()
    if not r or not r[3] or close_ms-int(r[2])>CONFIG["max_external_oi_age_ms"]:
        return None
    return r

def _btc_regime(c,close_ms):
    rows=_read_klines(c,"BTCUSDT","5m",close_ms,13)
    if len(rows)<13 or not contiguous(rows,300000):return None
    first,last=float(rows[0]["close"]),float(rows[-1]["close"])
    if first<=0:return None
    change=(last/first-1)*100
    return "UP" if change>0.35 else "DOWN" if change<-.35 else "SIDEWAYS"

def extract_external_derivatives(c,analyst_db):
    """Read complete OUTSIDE-venue OI unchanged from V3, never claim Binance OI."""
    if not analyst_db or not os.path.isfile(analyst_db):return 0
    n=0
    try:
        with sqlite3.connect("file:"+os.path.abspath(analyst_db)+"?mode=ro",uri=True) as a:
            a.row_factory=sqlite3.Row
            last=a.execute("SELECT MAX(scan_time_utc) FROM analyses").fetchone()[0]
            if not last:return 0
            ts=int(datetime.fromisoformat(last.replace("Z","+00:00")).timestamp()*1000)
            for x in a.execute("SELECT symbol,payload_json FROM analyses WHERE scan_time_utc=?",(last,)):
                p=json.loads(x["payload_json"] or "{}")
                provider=p.get("derivatives_selected_provider")
                bundle=p.get("multi_venue_derivatives") or {}
                if not provider or not bool(p.get("derivatives_ready")):continue
                if not bool(bundle.get("source_consistent")):continue
                oi=bundle.get("oi_change_1h")
                if oi is None:continue
                c.execute("""INSERT INTO external_derivatives
                (symbol,observed_ms,provider,oi_change_1h,oi_now,funding_pct,
                 taker_ratio,long_short_ratio,source_consistent,source_age_seconds,source_payload_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (x["symbol"],ts,provider,float(oi),bundle.get("oi_now"),
                 bundle.get("funding_pct"),bundle.get("taker_ratio"),
                 bundle.get("long_short_ratio"),1,bundle.get("source_age_seconds"),
                 json.dumps({"analyst_time":last,"provider":provider,
                             "field_source":bundle.get("field_source"),"raw":bundle},
                            separators=(",",":"))))
                n+=1
    except (sqlite3.Error,ValueError,TypeError) as exc:
        return 0
    c.commit()
    return n

def feature_at(c,symbol,close_ms):
    c.row_factory=sqlite3.Row
    rows=_read_klines(c,symbol,"5m",close_ms,CONFIG["min_history_5m"])
    reasons=[]
    if len(rows)<CONFIG["min_history_5m"]:reasons.append("INSUFFICIENT_5M_HISTORY")
    if rows and not contiguous(rows,300000):reasons.append("MISSING_5M_CANDLE")
    if not rows or int(rows[-1]["close_ms"])!=close_ms:reasons.append("MISSING_LATEST_CANDLE")
    # Deliberately do NOT fill missing 1m with spot or other venues.
    one=_read_klines(c,symbol,"1m",close_ms,5)
    if len(one)!=5 or not contiguous(one,60000) or int(one[-1]["close_ms"])!=close_ms:
        reasons.append("MISSING_1M_PATH")
    mark=_closest_mark(c,symbol,close_ms)
    if mark is None:reasons.append("NATIVE_MARK_FUNDING_MISSING")
    ext=_closest_external(c,symbol,close_ms)
    if ext is None:reasons.append("EXTERNAL_OI_UNAVAILABLE")
    if rows and now_ms()-close_ms>24*3600000:reasons.append("HISTORICAL_SNAPSHOT")
    price=float(rows[-1]["close"]) if rows else None
    hist=rows[:-1] if len(rows)>=2 else []
    vm=(float(rows[-1]["volume"])/statistics.mean(float(r["volume"]) for r in hist)
        if hist and statistics.mean(float(r["volume"]) for r in hist)>0 else None)
    vol=float(rows[-1]["volume"]) if rows else 0
    buy=float(rows[-1]["taker_buy_base"]) if rows and rows[-1]["taker_buy_base"] is not None else None
    share=buy/vol if buy is not None and vol>0 else None
    high=max((float(r["high"]) for r in hist),default=None)
    low=min((float(r["low"]) for r in hist),default=None)
    low_sweep=(int(float(rows[-1]["low"])<low and price>low)
               if low is not None and price is not None else None)
    high_sweep=(int(float(rows[-1]["high"])>high and price<high)
                if high is not None and price is not None else None)
    liq=c.execute("""SELECT SUM(notional),COUNT(*) FROM liquidations
      WHERE symbol=? AND event_ms>? AND event_ms<=?""",
      (symbol,close_ms-900000,close_ms)).fetchone()
    # Snapshot liquidations may undercount during collector gaps.
    available_1m="MISSING_1M_PATH" not in reasons
    usable=bool(available_1m and len(rows)==21 and contiguous(rows,300000)
                and mark is not None)
    native_full=bool(usable and ext is None) # "fully_native" means no outside-OI; not signal ready
    feat={
        "price":price,"volume_ratio":vm,"atr_pct":_atr(rows),"taker_buy_share":share,
        "vwap_5m20":_vwap(rows[-20:]),"highest_20":high,"lowest_20":low,
        "sweep_low_reclaimed":low_sweep,"sweep_high_rejected":high_sweep,
        "mark_price":float(mark[0]) if mark else None,
        "funding_rate":float(mark[1]) if mark and mark[1] is not None else None,
        "liquidation_15m_observed_usd":float(liq[0] or 0) if liq else None,
        "liquidation_covers_full_window":False,
        "external_oi_change_1h":float(ext[1]) if ext else None,
        "external_oi_provider":str(ext[0]) if ext else None,
        "btc_regime":_btc_regime(c,close_ms),
        "binance_native_oi_available":False,
        "signal_authorized":False,
        "quality_reasons":reasons,
        "source":SOURCE,"closed_candle_only":True,
    }
    c.execute("""INSERT OR IGNORE INTO feature_snapshots
      (symbol,close_ms,version,config_hash,price_source,price,volume_ratio,
       atr_pct,taker_buy_share,vwap,highest_20,lowest_20,sweep_low,sweep_high,
       funding_rate,mark_price,liquidation_15m_usd,oi_change_1h_external,
       external_oi_provider,btc_regime,fully_native,health,errors_json,features_json)
       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (symbol,close_ms,VERSION,CONFIG_HASH,SOURCE,price,vm,feat["atr_pct"],share,
       feat["vwap_5m20"],high,low,low_sweep,high_sweep,feat["funding_rate"],
       feat["mark_price"],feat["liquidation_15m_observed_usd"],
       feat["external_oi_change_1h"],feat["external_oi_provider"],
       feat["btc_regime"],int(native_full),
       "OBSERVATION_ONLY" if usable else "DATA_FAILURE",
       json.dumps(reasons),json.dumps(feat,separators=(",",":"))))
    return feat

def refresh(c,analyst_db=None):
    setup(c)
    copied=extract_external_derivatives(c,analyst_db)
    q=c.execute("SELECT symbol,MAX(close_ms) FROM closed_klines WHERE interval='5m' GROUP BY symbol").fetchall()
    n=0
    health={}
    for symbol,close_ms in q:
        if not close_ms:continue
        f=feature_at(c,symbol,int(close_ms))
        n+=1
        health[symbol]=f["quality_reasons"]
    c.commit()
    return {"version":VERSION,"config_hash":CONFIG_HASH,"features":n,
            "external_oi_references":copied,"issues_by_symbol":health,
            "live_trading_enabled":False,"telegram_enabled":False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="long_short_v4_native.db")
    p.add_argument("--analyst-db",default="")
    p.add_argument("--out",default="long_short_v4_features_report.json")
    args=p.parse_args()
    with sqlite3.connect(args.db) as c:out=refresh(c,args.analyst_db)
    with open(args.out,"w",encoding="utf-8") as f:json.dump(out,f,indent=2,ensure_ascii=False)
    print(json.dumps({"version":out["version"],"features":out["features"],
                      "external_oi_references":out["external_oi_references"],
                      "symbols_with_data_problems":sum(bool(x) for x in out["issues_by_symbol"].values())},
                     ensure_ascii=False))

if __name__=="__main__": main()
