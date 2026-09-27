#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit Binance Spot top movers against Avci's own prior scans.

Observation-only. Does not change frozen signal rules or capital gates.
Purpose: answer, for coins already moving strongly, whether Avci saw them early,
saw them late, or did not scan/signal them at all, and why.
"""
import os, sqlite3
from datetime import datetime, timezone, timedelta
import requests

DB=os.getenv("BINANCE_DB","binance_avci2.db")
SPOT_BASES=("https://data-api.binance.vision","https://api.binance.com")
TIMEOUT=20
MIN_CHANGE=10.0
MIN_QUOTE_VOLUME=3_000_000.0
MAX_ROWS=10

def api_24h():
    last=None
    for base in SPOT_BASES:
        try:
            r=requests.get(base+"/api/v3/ticker/24hr",timeout=TIMEOUT,
                           headers={"User-Agent":"binance-avci-top-mover-audit"})
            if r.status_code in (403,418,429,451):
                last=RuntimeError(f"{r.status_code} {base}")
                continue
            r.raise_for_status()
            data=r.json()
            if isinstance(data,list): return data
        except Exception as e:
            last=e
    raise last or RuntimeError("Binance spot ticker unavailable")

def ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS top_mover_audit(
      scan_time_utc TEXT NOT NULL,
      symbol TEXT NOT NULL,
      current_change_24h REAL NOT NULL,
      quote_volume_24h REAL,
      universe_status TEXT,
      exclusion_reason TEXT,
      first_feature_time_utc TEXT,
      first_feature_change_24h REAL,
      first_signal_time_utc TEXT,
      first_signal_change_24h REAL,
      first_signal_stage TEXT,
      first_signal_class TEXT,
      audit_status TEXT NOT NULL,
      audit_reason TEXT,
      created_at_utc TEXT NOT NULL,
      PRIMARY KEY(scan_time_utc,symbol)
    )""")

def parse_dt(s):
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00"))
    except Exception: return datetime.now(timezone.utc)

def latest_universe(c,ts,symbol):
    try:
        return c.execute("""SELECT status,exclusion_reason FROM universe_history
          WHERE scan_time_utc=? AND symbol=? LIMIT 1""",(ts,symbol)).fetchone()
    except sqlite3.OperationalError:
        return None

def first_feature(c,symbol,since,ts):
    try:
        return c.execute("""SELECT scan_time_utc,change_24h,stage,selection_class,is_signal
          FROM features WHERE symbol=? AND scan_time_utc>=? AND scan_time_utc<=?
          ORDER BY scan_time_utc ASC LIMIT 1""",(symbol,since,ts)).fetchone()
    except sqlite3.OperationalError:
        return None

def first_signal(c,symbol,since,ts):
    try:
        return c.execute("""SELECT scan_time_utc,change_24h,stage,selection_class
          FROM features WHERE symbol=? AND scan_time_utc>=? AND scan_time_utc<=?
            AND (is_signal=1 OR stage!='OBSERVE')
          ORDER BY scan_time_utc ASC LIMIT 1""",(symbol,since,ts)).fetchone()
    except sqlite3.OperationalError:
        return None

def classify(change,uni,ff,fs):
    if fs:
        at=float(fs["change_24h"] or 0)
        if at < 10:
            return "EARLY_CAUGHT",f"Avcı ilk sinyali 24s hareket henüz %{at:.1f} iken gördü"
        if at < 15:
            return "CAUGHT",f"Avcı ilk sinyali %{at:.1f} iken gördü"
        return "LATE_CAUGHT",f"İlk sinyal ancak hareket %{at:.1f} olduktan sonra geldi"
    if not uni:
        return "NOT_IN_SNAPSHOT","Son taramanın evren kaydı bulunamadı"
    if str(uni["status"] or "")=="EXCLUDED":
        reason=str(uni["exclusion_reason"] or "UNKNOWN")
        return "OUTSIDE_CORE_UNIVERSE",f"Çekirdek tarama evreninden elendi: {reason}"
    if ff:
        return "MISSED",f"Tarandı ama +%{change:.1f} harekete rağmen sinyal üretmedi"
    return "MISSED","Evrene dahil görünmesine rağmen feature/sinyal izi bulunamadı"

def main():
    if not os.path.exists(DB):
        print("Top mover audit: DB yok"); return
    tickers=api_24h()
    movers=[]
    for x in tickers:
        try:
            sym=str(x.get("symbol") or "")
            if not sym.endswith("USDT"): continue
            ch=float(x.get("priceChangePercent") or 0)
            vol=float(x.get("quoteVolume") or 0)
            if ch>=MIN_CHANGE and vol>=MIN_QUOTE_VOLUME:
                movers.append((ch,vol,sym))
        except Exception:
            pass
    movers.sort(reverse=True)
    with sqlite3.connect(DB,timeout=60) as c:
        c.row_factory=sqlite3.Row
        ensure(c)
        scan=c.execute("""SELECT scan_time_utc FROM scans
          WHERE health_status!='INVALID' ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:
            print("Top mover audit: geçerli scan yok"); return
        ts=scan["scan_time_utc"]
        since=(parse_dt(ts)-timedelta(hours=24)).isoformat()
        c.execute("DELETE FROM top_mover_audit WHERE scan_time_utc=?",(ts,))
        counts={}
        for ch,vol,sym in movers[:MAX_ROWS]:
            uni=latest_universe(c,ts,sym)
            ff=first_feature(c,sym,since,ts)
            fs=first_signal(c,sym,since,ts)
            status,reason=classify(ch,uni,ff,fs)
            counts[status]=counts.get(status,0)+1
            c.execute("""INSERT OR REPLACE INTO top_mover_audit(
              scan_time_utc,symbol,current_change_24h,quote_volume_24h,
              universe_status,exclusion_reason,first_feature_time_utc,
              first_feature_change_24h,first_signal_time_utc,
              first_signal_change_24h,first_signal_stage,first_signal_class,
              audit_status,audit_reason,created_at_utc)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                ts,sym,ch,vol,
                uni["status"] if uni else None,
                uni["exclusion_reason"] if uni else None,
                ff["scan_time_utc"] if ff else None,
                ff["change_24h"] if ff else None,
                fs["scan_time_utc"] if fs else None,
                fs["change_24h"] if fs else None,
                fs["stage"] if fs else None,
                fs["selection_class"] if fs else None,
                status,reason,datetime.now(timezone.utc).isoformat()
            ))
        c.commit()
    print("Binance top mover audit:",len(movers[:MAX_ROWS]),counts)

if __name__=="__main__":
    main()
