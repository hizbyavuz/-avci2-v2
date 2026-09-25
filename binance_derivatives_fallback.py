#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-venue derivatives fallback for Binance Avci research.

When Binance USD-M derivatives are unavailable, selected Binance symbols can be
observed on OKX perpetual swaps. These rows are tagged CROSS_VENUE_OKX and never
pretend to be Binance-native data or alter frozen Binance candidate rules.
"""

import os
import sqlite3
from datetime import datetime, timezone

import requests

DB = os.getenv("BINANCE_DB", "binance_avci2.db")
BASE = "https://www.okx.com"
TIMEOUT = 15
MAX_SYMBOLS = int(os.getenv("BINANCE_FALLBACK_MAX_SYMBOLS", "12"))


def get(path, params=None):
    r = requests.get(BASE + path, params=params, timeout=TIMEOUT,
                     headers={"User-Agent": "avci-binance-crossvenue/1.0"})
    r.raise_for_status()
    body = r.json()
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body.get("msg") or "OKX error")
    return body.get("data") or []


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS deriv_fallback_observations (
        scan_time_utc TEXT NOT NULL,
        symbol TEXT NOT NULL,
        source TEXT NOT NULL,
        inst_id TEXT,
        oi_contracts REAL,
        oi_ccy REAL,
        oi_usd REAL,
        oi_change_since_prev_pct REAL,
        funding_rate REAL,
        mark_price REAL,
        index_price REAL,
        basis_pct REAL,
        status TEXT NOT NULL,
        error TEXT,
        created_at_utc TEXT NOT NULL,
        PRIMARY KEY(scan_time_utc,symbol,source)
    )""")


def main():
    if not os.path.exists(DB):
        print("Binance derivatives fallback: DB yok")
        return
    with sqlite3.connect(DB, timeout=30) as con:
        con.row_factory = sqlite3.Row
        ensure(con)
        scan = con.execute(
            "SELECT scan_time_utc,data_mode FROM scans WHERE health_status!='INVALID' ORDER BY scan_time_utc DESC LIMIT 1"
        ).fetchone()
        if not scan:
            print("Binance derivatives fallback: valid scan yok")
            return
        symbols = [r[0] for r in con.execute("""SELECT symbol FROM features
            WHERE scan_time_utc=? AND selection_class IN ('CANDIDATE','NEAR_MISS')
            ORDER BY CASE selection_class WHEN 'CANDIDATE' THEN 0 ELSE 1 END, score DESC LIMIT ?""",
            (scan["scan_time_utc"], MAX_SYMBOLS)).fetchall()]
        if not symbols:
            print("Binance derivatives fallback: aday/near-miss yok")
            return
        try:
            instruments = get("/api/v5/public/instruments", {"instType": "SWAP"})
            available = {str(x.get("instId")) for x in instruments if x.get("state") == "live"}
        except Exception as exc:
            print("Binance derivatives fallback instrument error", type(exc).__name__, exc)
            return
        for symbol in symbols:
            base = symbol[:-4] if symbol.endswith("USDT") else symbol
            inst = f"{base}-USDT-SWAP"
            status, err = "OK", None
            oi_contracts = oi_ccy = oi_usd = funding = mark = index = basis = None
            if inst not in available:
                status, err = "NO_MATCHING_OKX_SWAP", "instrument missing"
            else:
                try:
                    oi = (get("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}) or [{}])[0]
                    fr = (get("/api/v5/public/funding-rate", {"instId": inst}) or [{}])[0]
                    mp = (get("/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst}) or [{}])[0]
                    ix = (get("/api/v5/market/index-tickers", {"instId": f"{base}-USDT"}) or [{}])[0]
                    oi_contracts, oi_ccy, oi_usd = f(oi.get("oi")), f(oi.get("oiCcy")), f(oi.get("oiUsd"))
                    funding, mark, index = f(fr.get("fundingRate")), f(mp.get("markPx")), f(ix.get("idxPx"))
                    if mark is not None and index not in (None, 0):
                        basis = 100 * (mark / index - 1)
                except Exception as exc:
                    status, err = "OKX_ERROR", f"{type(exc).__name__}:{str(exc)[:180]}"
            prev = con.execute("""SELECT oi_usd FROM deriv_fallback_observations
                WHERE symbol=? AND source='CROSS_VENUE_OKX' AND oi_usd IS NOT NULL
                  AND scan_time_utc<? ORDER BY scan_time_utc DESC LIMIT 1""",
                (symbol, scan["scan_time_utc"])).fetchone()
            oi_ch = None
            if prev and prev[0] not in (None, 0) and oi_usd is not None:
                oi_ch = 100 * (oi_usd / prev[0] - 1)
            con.execute("""INSERT OR REPLACE INTO deriv_fallback_observations
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                scan["scan_time_utc"], symbol, "CROSS_VENUE_OKX", inst,
                oi_contracts, oi_ccy, oi_usd, oi_ch, funding, mark, index, basis,
                status, err, datetime.now(timezone.utc).isoformat()))
            print("Deriv fallback", symbol, status, "OIΔ", oi_ch, "fund", funding, "basis", basis)
        con.commit()


if __name__ == "__main__":
    main()
