"""Observational Binance structure engines.

This module never changes frozen candidate scoring or thresholds. It records
pre-signal market-structure evidence for later validation:
- sector-relative strength (curated sector groups only)
- spot/futures lead-lag
- OI anomaly versus the symbol's own recent scans
- funding acceleration
- order-book pressure
"""

import json
import math
import sqlite3
import statistics
from datetime import datetime

from binance_scanner import spot_api_get, futures_api_get

DB = "binance_avci2.db"
VERSION = "binance-structure-observer-v0.1-20260923"

SECTORS = {
    "AI": {"TAO","FET","RENDER","VIRTUAL","ARKM","WLD","NEAR","ICP","GRT"},
    "L1": {"ETH","SOL","BNB","ADA","AVAX","SUI","APT","TON","SEI","INJ","ATOM","DOT","HBAR"},
    "L2": {"ARB","OP","STRK","ZK","MANTA","METIS"},
    "DEFI": {"UNI","AAVE","CRV","MKR","LDO","PENDLE","ENA","COMP","SNX","JUP","RAY"},
    "MEME": {"DOGE","SHIB","PEPE","FLOKI","BONK","WIF","PENGU","BRETT"},
    "RWA": {"ONDO","OM","QNT","POLYX","CFG"},
    "GAMING": {"IMX","GALA","SAND","MANA","AXS","RON","BEAM"},
    "INFRA": {"LINK","FIL","AR","TIA","PYTH","JTO","STG","W","WORMHOLE"},
}


def base_asset(symbol):
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def sector_for(symbol):
    base = base_asset(symbol)
    for name, members in SECTORS.items():
        if base in members:
            return name
    return None


def robust_z(value, history):
    vals = [float(x) for x in history if x is not None and math.isfinite(float(x))]
    if value is None or len(vals) < 6:
        return None
    med = statistics.median(vals)
    deviations = [abs(x - med) for x in vals]
    mad = statistics.median(deviations)
    if mad <= 1e-12:
        return None
    return 0.67448975 * (float(value) - med) / mad


def depth_pressure(depth, levels=20):
    try:
        bids = sum(float(p)*float(q) for p,q in (depth.get("bids") or [])[:levels])
        asks = sum(float(p)*float(q) for p,q in (depth.get("asks") or [])[:levels])
        if bids <= 0 or asks <= 0:
            return None
        return bids / asks
    except (TypeError, ValueError, OverflowError):
        return None


def closed_return(rows, bars=3):
    if not isinstance(rows, list) or len(rows) < bars + 1:
        return None
    try:
        closed = rows[:-1] if int(rows[-1][6]) > int(datetime.now().timestamp()*1000) else rows
        if len(closed) < bars + 1:
            return None
        start = float(closed[-bars-1][4])
        end = float(closed[-1][4])
        return 100 * (end/start - 1) if start > 0 else None
    except (TypeError, ValueError, IndexError):
        return None


def lead_lag(symbol):
    spot = spot_api_get("/api/v3/klines", {"symbol":symbol,"interval":"5m","limit":5})
    fut = futures_api_get("/fapi/v1/klines", {"symbol":symbol,"interval":"5m","limit":5})
    sret = closed_return(spot, 3)
    fret = closed_return(fut, 3) if fut else None
    if sret is None or fret is None:
        return None, sret, fret
    gap = sret - fret
    if gap >= 0.45:
        return "SPOT_LEAD", sret, fret
    if gap <= -0.45:
        return "FUTURES_LEAD", sret, fret
    return "BALANCED", sret, fret


def latest_candidates(con):
    scan = con.execute("""SELECT scan_time_utc,data_mode,health_status
        FROM scans ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
    if not scan or scan[2] == "INVALID":
        return None, []
    rows = con.execute("""SELECT symbol,stage,score,change_15m,change_24h,
            volume_rarity_pct,cross_sectional_rarity_pct,oi_change_1h_pct,
            funding_rate,price
        FROM features WHERE scan_time_utc=?
          AND change_24h < 25
        ORDER BY CASE WHEN stage='OBSERVE' THEN 1 ELSE 0 END,
                 cross_sectional_rarity_pct DESC, volume_rarity_pct DESC
        LIMIT 30""", (scan[0],)).fetchall()
    return scan, rows


def main(path=DB):
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS structure_observations (
            scan_time_utc TEXT NOT NULL, symbol TEXT NOT NULL,
            version TEXT NOT NULL, sector TEXT, sector_excess_15m REAL,
            oi_anomaly_z REAL, funding_acceleration REAL,
            book_bid_ask_ratio REAL, lead_lag TEXT,
            spot_return_15m REAL, futures_return_15m REAL,
            structure_flags_json TEXT NOT NULL,
            PRIMARY KEY(scan_time_utc,symbol,version))""")
        scan, rows = latest_candidates(con)
        if not scan:
            print("Binance yapı gözlemi: geçerli tarama yok")
            return 0

        sector_returns = {}
        all_latest = con.execute("""SELECT symbol,change_15m FROM features
            WHERE scan_time_utc=?""", (scan[0],)).fetchall()
        for r in all_latest:
            sec = sector_for(r["symbol"])
            if sec and r["change_15m"] is not None:
                sector_returns.setdefault(sec, []).append(float(r["change_15m"]))

        written = 0
        for idx, row in enumerate(rows):
            symbol = row["symbol"]
            sec = sector_for(symbol)
            sector_excess = None
            if sec and len(sector_returns.get(sec, [])) >= 3:
                sector_excess = float(row["change_15m"] or 0) - statistics.median(sector_returns[sec])

            hist = con.execute("""SELECT oi_change_1h_pct,funding_rate
                FROM raw_derivs WHERE symbol=? AND scan_time_utc<?
                ORDER BY scan_time_utc DESC LIMIT 24""",
                (symbol, scan[0])).fetchall()
            oi_z = robust_z(row["oi_change_1h_pct"], [x[0] for x in hist])
            funding_accel = None
            fvals = [float(x[1]) for x in hist[:8] if x[1] is not None]
            if row["funding_rate"] is not None and len(fvals) >= 3:
                funding_accel = float(row["funding_rate"]) - statistics.median(fvals)

            pressure = None
            lead = None
            sret = fret = None
            # Limit live API work to the strongest 12 observational rows.
            if idx < 12:
                try:
                    pressure = depth_pressure(spot_api_get(
                        "/api/v3/depth", {"symbol":symbol,"limit":100}))
                except Exception:
                    pressure = None
                if scan[1] != "SPOT_ONLY":
                    try:
                        lead, sret, fret = lead_lag(symbol)
                    except Exception:
                        lead = None

            flags = []
            if sector_excess is not None and sector_excess >= 1.0:
                flags.append("SECTOR_OUTPERFORMANCE")
            if oi_z is not None and oi_z >= 2.5:
                flags.append("OI_ANOMALY")
            if funding_accel is not None and abs(funding_accel) >= 0.00015:
                flags.append("FUNDING_ACCELERATION")
            if pressure is not None and pressure >= 1.8:
                flags.append("ORDERBOOK_PRESSURE")
            if lead in ("SPOT_LEAD","FUTURES_LEAD"):
                flags.append(lead)

            con.execute("""INSERT OR REPLACE INTO structure_observations
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (scan[0],symbol,VERSION,sec,sector_excess,oi_z,funding_accel,
                 pressure,lead,sret,fret,json.dumps(flags)))
            written += 1
            if flags:
                print(f"Yapı gözlemi {symbol}: {', '.join(flags)}")
        con.commit()
        print(f"Binance yapı gözlemi: {written} coin kaydedildi; frozen skor değişmedi.")
        return written
    finally:
        con.close()


if __name__ == "__main__":
    main()
