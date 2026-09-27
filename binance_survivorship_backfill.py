#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Binance survivorship-bias observer/backfill.

Detects previously-seen USDT symbols that disappear from current exchangeInfo
or stop trading, then preserves a small historical daily-candle archive from
Binance Data Vision. Observation-only: it never changes frozen signal rules.
"""
from __future__ import annotations

import io
import os
import sqlite3
import zipfile
from datetime import datetime, timezone
from urllib import error, request

DB=os.getenv("BINANCE_DB","binance_avci2.db")
ARCHIVE_BASE="https://data.binance.vision/data/spot/monthly/klines"
MONTHS_BACK=12
MAX_SYMBOLS_PER_RUN=3
TIMEOUT=20
VERSION="binance-survivorship-v1-20260927"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def month_keys(end_dt, n=MONTHS_BACK):
    y,m=end_dt.year,end_dt.month
    out=[]
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m-=1
        if m==0:
            y-=1;m=12
    return out

def current_exchange_symbols():
    last=None
    for base in ("https://data-api.binance.vision","https://api.binance.com"):
        try:
            with request.urlopen(base+"/api/v3/exchangeInfo",timeout=TIMEOUT) as r:
                import json
                payload=json.load(r)
            rows={}
            for x in payload.get("symbols",[]):
                if x.get("quoteAsset")=="USDT":
                    rows[str(x.get("symbol"))]=str(x.get("status") or "UNKNOWN")
            if rows:return rows
        except Exception as e:
            last=e
    raise last or RuntimeError("exchangeInfo unavailable")

def ensure(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS binance_asset_lifecycle(
      symbol TEXT PRIMARY KEY,
      first_seen_utc TEXT,
      last_seen_utc TEXT,
      latest_exchange_status TEXT,
      lifecycle_status TEXT NOT NULL,
      detected_at_utc TEXT NOT NULL,
      version TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS binance_survivorship_daily(
      symbol TEXT NOT NULL,
      open_time_ms INTEGER NOT NULL,
      close_time_ms INTEGER NOT NULL,
      open REAL,high REAL,low REAL,close REAL,quote_volume REAL,trade_count INTEGER,
      source_month TEXT NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(symbol,open_time_ms)
    );
    CREATE TABLE IF NOT EXISTS binance_survivorship_backfill_status(
      symbol TEXT PRIMARY KEY,
      attempted_at_utc TEXT NOT NULL,
      months_attempted INTEGER NOT NULL,
      months_found INTEGER NOT NULL,
      candles_saved INTEGER NOT NULL,
      status TEXT NOT NULL,
      version TEXT NOT NULL
    );
    """)

def parse_month_zip(blob):
    rows=[]
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if not names:return rows
        text=z.read(names[0]).decode("utf-8").splitlines()
    for line in text:
        p=line.split(",")
        if len(p)<9:continue
        try:
            ot=int(p[0]); ct=int(p[6])
            # Binance public archives may use microsecond timestamps for newer data.
            if ot > 10**14: ot //= 1000
            if ct > 10**14: ct //= 1000
            rows.append((ot,ct,float(p[1]),float(p[2]),float(p[3]),float(p[4]),float(p[7]),int(float(p[8]))))
        except (ValueError,TypeError):
            continue
    return rows

def fetch_month(symbol,ym):
    url=f"{ARCHIVE_BASE}/{symbol}/1d/{symbol}-1d-{ym}.zip"
    try:
        with request.urlopen(url,timeout=TIMEOUT) as r:
            return parse_month_zip(r.read())
    except error.HTTPError as e:
        if e.code==404:return []
        raise

def main():
    if not os.path.exists(DB):
        print("Binance survivorship: DB yok");return
    current=current_exchange_symbols()
    with sqlite3.connect(DB,timeout=60) as con:
        con.row_factory=sqlite3.Row
        ensure(con)
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='asset_metadata'").fetchone():
            print("Binance survivorship: asset_metadata yok");return
        known=con.execute("""SELECT symbol,first_seen_utc,last_seen_utc,last_status,quote_asset
            FROM asset_metadata WHERE quote_asset='USDT'""").fetchall()
        missing=[]
        for r in known:
            sym=r["symbol"]; status=current.get(sym)
            life="ACTIVE" if status=="TRADING" else ("NON_TRADING" if status else "MISSING_FROM_EXCHANGEINFO")
            con.execute("""INSERT OR REPLACE INTO binance_asset_lifecycle
                VALUES(?,?,?,?,?,?,?)""",(sym,r["first_seen_utc"],r["last_seen_utc"],
                status,life,now_iso(),VERSION))
            if life!="ACTIVE":
                done=con.execute("""SELECT attempted_at_utc,status FROM binance_survivorship_backfill_status
                    WHERE symbol=?""",(sym,)).fetchone()
                retry=True
                if done:
                    if done["status"]=="COMPLETE":
                        retry=False
                    else:
                        try:
                            age=(datetime.now(timezone.utc)-datetime.fromisoformat(
                                str(done["attempted_at_utc"]).replace("Z","+00:00"))).total_seconds()
                            retry=age>=24*3600
                        except Exception:
                            retry=True
                if retry:
                    missing.append((sym,r["last_seen_utc"]))
        missing.sort(key=lambda x:x[1] or "")
        selected=missing[:MAX_SYMBOLS_PER_RUN]
        total=0
        for sym,last_seen in selected:
            try:
                end=datetime.fromisoformat(str(last_seen).replace("Z","+00:00")) if last_seen else datetime.now(timezone.utc)
            except Exception:
                end=datetime.now(timezone.utc)
            attempted=found=saved=0
            try:
                for ym in month_keys(end):
                    attempted+=1
                    rows=fetch_month(sym,ym)
                    if not rows:continue
                    found+=1
                    for ot,ct,o,h,l,cl,qv,trades in rows:
                        con.execute("""INSERT OR IGNORE INTO binance_survivorship_daily
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (sym,ot,ct,o,h,l,cl,qv,trades,ym,VERSION))
                        saved+=con.execute("SELECT changes()").fetchone()[0]
                status="COMPLETE" if found else "ARCHIVE_NOT_FOUND"
            except Exception as e:
                status=f"ERROR_{type(e).__name__}"
            con.execute("""INSERT OR REPLACE INTO binance_survivorship_backfill_status
                VALUES(?,?,?,?,?,?,?)""",(sym,now_iso(),attempted,found,saved,status,VERSION))
            total+=saved
        con.commit()
        inactive=sum(1 for r in known if current.get(r["symbol"])!="TRADING")
        print(f"Binance survivorship: known={len(known)} inactive_or_missing={inactive} "
              f"backfilled_symbols={len(selected)} candles_saved={total}")

if __name__=="__main__":
    main()
