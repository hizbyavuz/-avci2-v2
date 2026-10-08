#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V3.2 dual-hypothesis shadow observer.

Research-only. This module never changes V3.1 decisions, Telegram messages,
watch-state, confirmation rules, or execution logic. It reads the latest V3.1
analyst snapshot, measures LONG and SHORT evidence independently, and stores
forward outcomes in a separate SQLite database.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone

import requests

import long_short_crowding_shadow as crowding

ANALYST_DB = os.getenv("LS_DB", "long_short_v31_analyst.db")
SHADOW_DB = os.getenv("LS_V32_SHADOW_DB", "long_short_v32_shadow.db")
VERSION = "LS_V3_2_DUAL_HYPOTHESIS_SHADOW_2026-10-07"
TIMEOUT = float(os.getenv("LS_V32_HTTP_TIMEOUT", "8"))
MAX_SYMBOLS = int(os.getenv("LS_V32_MAX_SYMBOLS", "30"))
USER_AGENT = "long-short-v32-shadow/1.0"

BINANCE_FUTURES = (
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
)
BINANCE_SPOT = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def iso_to_epoch(ts):
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()

def _get_json(url, params=None):
    r = requests.get(url, params=params or {}, timeout=TIMEOUT,
                     headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.json()

def fetch_klines(symbol, interval, limit=90):
    errors = []
    for base in BINANCE_FUTURES:
        try:
            rows = _get_json(base + "/fapi/v1/klines",
                             {"symbol": symbol, "interval": interval, "limit": limit})
            if rows:
                return rows, "BINANCE_FUTURES", True
        except Exception as exc:
            errors.append("bf:" + type(exc).__name__)
    for base in BINANCE_SPOT:
        try:
            rows = _get_json(base + "/api/v3/klines",
                             {"symbol": symbol, "interval": interval, "limit": limit})
            if rows:
                return rows, "BINANCE_SPOT", True
        except Exception as exc:
            errors.append("bs:" + type(exc).__name__)
    try:
        from long_short_data_router import multi_venue_perp_klines
        out = multi_venue_perp_klines(symbol, interval, limit)
        rows = list(out.get("rows") or [])
        if rows:
            return rows, str(out.get("provider") or "MULTI_VENUE"), False
    except Exception as exc:
        errors.append("mv:" + type(exc).__name__)
    raise RuntimeError(symbol + " kline unavailable " + ",".join(errors[-6:]))

def closed_rows(rows):
    now_ms = int(time.time() * 1000)
    return [r for r in rows if len(r) >= 7 and int(float(r[6])) <= now_ms - 250]

def ema(values, period):
    values = [float(x) for x in values]
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    x = values[0]
    for v in values[1:]:
        x = alpha * v + (1.0 - alpha) * x
    return x

def swing_levels_from_rows(rows, lookback=48):
    rows = list(rows)[-lookback:]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]
    if len(rows) < 8:
        p = closes[-1]
        return {"support": p, "resistance": p}
    piv_hi, piv_lo = [], []
    for i in range(2, len(rows) - 2):
        if highs[i] >= highs[i-1] and highs[i] >= highs[i-2] and highs[i] >= highs[i+1] and highs[i] >= highs[i+2]:
            piv_hi.append(highs[i])
        if lows[i] <= lows[i-1] and lows[i] <= lows[i-2] and lows[i] <= lows[i+1] and lows[i] <= lows[i+2]:
            piv_lo.append(lows[i])
    price = closes[-1]
    below = sorted((x for x in piv_lo if x < price), reverse=True)
    above = sorted(x for x in piv_hi if x > price)
    support = below[0] if below else min(lows[-12:])
    resistance = above[0] if above else max(highs[-12:])
    return {"support": support, "resistance": resistance}

def structure_sign(rows):
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    if len(rows) < 8:
        return 0
    h1, h2 = max(highs[-8:-4]), max(highs[-4:])
    l1, l2 = min(lows[-8:-4]), min(lows[-4:])
    if h2 > h1 and l2 > l1:
        return 1
    if h2 < h1 and l2 < l1:
        return -1
    return 0

def micro_features(rows5, taker_available=True):
    rows5 = closed_rows(rows5)
    if len(rows5) < 30:
        raise RuntimeError("insufficient closed 5m candles")

    ref = rows5[:-3]
    levels = swing_levels_from_rows(ref, 48)
    recent = rows5[-3:]
    prev = rows5[-6:-3]
    closes = [float(r[4]) for r in rows5]
    vols = [float(r[5]) for r in rows5]

    px = closes[-1]
    change15 = 100.0 * (px / closes[-4] - 1.0) if closes[-4] > 0 else None
    support = float(levels["support"])
    resistance = float(levels["resistance"])
    tol = 0.0005

    support_sweep_reclaim = any(
        float(r[3]) < support * (1.0 - tol) and float(r[4]) > support for r in recent
    )
    resistance_sweep_reject = any(
        float(r[2]) > resistance * (1.0 + tol) and float(r[4]) < resistance for r in recent
    )
    higher_low = min(float(r[3]) for r in recent) > min(float(r[3]) for r in prev)
    lower_high = max(float(r[2]) for r in recent) < max(float(r[2]) for r in prev)

    e7_now = ema(closes[-14:], 7)
    e7_prev = ema(closes[-15:-1], 7)
    ema7_slope_pct = ((e7_now / e7_prev) - 1.0) * 100.0 if e7_prev else 0.0

    vol_base = sum(vols[-23:-3]) / max(1, len(vols[-23:-3]))
    recent_vol = sum(vols[-3:]) / 3.0
    volume_mult = recent_vol / vol_base if vol_base > 0 else 1.0

    taker_share = None
    taker_delta = None
    if taker_available:
        def share(block):
            q = sum(float(r[7]) for r in block)
            tbq = sum(float(r[10]) for r in block)
            return (tbq / q) if q > 0 else None
        cur = share(recent)
        old = share(rows5[-9:-3])
        if cur is not None:
            taker_share = cur
        if cur is not None and old is not None:
            taker_delta = cur - old

    return {
        "price": px,
        "price_change_15m_pct": change15,
        "support": support,
        "resistance": resistance,
        "dist_support_pct": ((px / support) - 1.0) * 100.0 if support else None,
        "dist_resistance_pct": ((resistance / px) - 1.0) * 100.0 if px else None,
        "support_sweep_reclaim": bool(support_sweep_reclaim),
        "resistance_sweep_reject": bool(resistance_sweep_reject),
        "higher_low": bool(higher_low),
        "lower_high": bool(lower_high),
        "structure_5m": int(structure_sign(rows5)),
        "ema7_slope_pct": float(ema7_slope_pct),
        "volume_mult": float(volume_mult),
        "taker_buy_share": taker_share,
        "taker_delta": taker_delta,
    }

def fnum(x, default=0.0):
    try:
        x = float(x)
        return x if math.isfinite(x) else default
    except Exception:
        return default

def bool_reason(flag, text, reasons):
    if flag:
        reasons.append(text)
    return 1 if flag else 0

def independent_hypotheses(m, payload):
    """Measure both directions; counts are evidence, never trading scores."""
    spot = payload.get("spot_flow") or {}
    v3 = payload.get("v3") or {}
    residual = (v3.get("residual") or payload.get("residual") or {})
    oi = payload.get("oi") or {}

    spot_delta = fnum(spot.get("delta_share"), 0.0)
    residual3 = fnum(residual.get("residual_3h_pct"), 0.0)
    oi1h = fnum(oi.get("oi_change_1h"), 0.0)
    funding = fnum(payload.get("funding_pct"), 0.0)
    ls_ratio = fnum(payload.get("long_short_ratio"), 1.0)

    tb = m.get("taker_buy_share")
    td = m.get("taker_delta")
    near_support = m["dist_support_pct"] is not None and 0.0 <= m["dist_support_pct"] <= 0.70
    near_res = m["dist_resistance_pct"] is not None and 0.0 <= m["dist_resistance_pct"] <= 0.70

    lc, sc, lr, sr = [], [], [], []

    bool_reason(m["structure_5m"] >= 0, "5m yapı aşağı değil", lc)
    bool_reason(near_res, "dirence yakın", lc)
    bool_reason(m["ema7_slope_pct"] > 0, "EMA7 eğimi yukarı", lc)
    bool_reason(tb is not None and tb > 0.52, "taker alım payı güçlü", lc)
    bool_reason(spot_delta > 0, "spot delta pozitif", lc)
    bool_reason(residual3 > 0, "BTC residual pozitif", lc)

    bool_reason(m["structure_5m"] <= 0, "5m yapı yukarı değil", sc)
    bool_reason(near_support, "desteğe yakın", sc)
    bool_reason(m["ema7_slope_pct"] < 0, "EMA7 eğimi aşağı", sc)
    bool_reason(tb is not None and tb < 0.48, "taker satış payı güçlü", sc)
    bool_reason(spot_delta < 0, "spot delta negatif", sc)
    bool_reason(residual3 < 0, "BTC residual negatif", sc)

    bool_reason(m["support_sweep_reclaim"], "destek süpürülüp geri alındı", lr)
    bool_reason(m["higher_low"], "5m higher-low oluşuyor", lr)
    bool_reason(m["ema7_slope_pct"] > 0, "EMA7 yukarı dönüyor", lr)
    bool_reason(td is not None and td > 0, "taker akışı alıma doğru iyileşiyor", lr)
    bool_reason(spot_delta > 0, "spot alıcıları pozitif", lr)
    bool_reason(residual3 > 0, "BTC'ye göre göreli güç pozitif", lr)
    bool_reason(oi1h < 0, "OI düşüyor; satış baskısı tasfiye/çözülme olabilir", lr)

    bool_reason(m["resistance_sweep_reject"], "direnç süpürülüp geri reddedildi", sr)
    bool_reason(m["lower_high"], "5m lower-high oluşuyor", sr)
    bool_reason(m["ema7_slope_pct"] < 0, "EMA7 aşağı dönüyor", sr)
    bool_reason(td is not None and td < 0, "taker akışı satışa doğru kötüleşiyor", sr)
    bool_reason(spot_delta < 0, "spot satıcıları baskın", sr)
    bool_reason(residual3 < 0, "BTC'ye göre göreli güç negatif", sr)
    bool_reason(oi1h < 0, "OI düşüyor; alış baskısı short-cover olabilir", sr)

    return {
        "spot_delta_share": spot_delta,
        "oi_change_1h": oi1h,
        "funding_pct": funding,
        "long_short_ratio": ls_ratio,
        "btc_residual_3h_pct": residual3,
        "long_cont_evidence": len(lc),
        "short_cont_evidence": len(sc),
        "long_rev_evidence": len(lr),
        "short_rev_evidence": len(sr),
        "long_cont_reasons": lc,
        "short_cont_reasons": sc,
        "long_rev_reasons": lr,
        "short_rev_reasons": sr,
    }

def init_db():
    with sqlite3.connect(SHADOW_DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS shadow_snapshots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL,
            analyst_scan_time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            v31_status TEXT,
            v31_direction TEXT,
            v31_eligible INTEGER,
            v31_setup_type TEXT,
            v31_veto_json TEXT,
            market_data_source TEXT,
            market_price REAL,
            support REAL,
            resistance REAL,
            dist_support_pct REAL,
            dist_resistance_pct REAL,
            support_sweep_reclaim INTEGER,
            resistance_sweep_reject INTEGER,
            higher_low INTEGER,
            lower_high INTEGER,
            structure_5m INTEGER,
            ema7_slope_pct REAL,
            volume_mult REAL,
            taker_buy_share REAL,
            taker_delta REAL,
            spot_delta_share REAL,
            oi_change_1h REAL,
            funding_pct REAL,
            long_short_ratio REAL,
            btc_residual_3h_pct REAL,
            long_cont_evidence INTEGER,
            short_cont_evidence INTEGER,
            long_rev_evidence INTEGER,
            short_rev_evidence INTEGER,
            long_cont_reasons_json TEXT,
            short_cont_reasons_json TEXT,
            long_rev_reasons_json TEXT,
            short_rev_reasons_json TEXT,
            price_15m REAL, return_15m_pct REAL, label_15m_at_utc TEXT, label_15m_delay_sec REAL,
            price_60m REAL, return_60m_pct REAL, label_60m_at_utc TEXT, label_60m_delay_sec REAL,
            price_180m REAL, return_180m_pct REAL, label_180m_at_utc TEXT, label_180m_delay_sec REAL,
            raw_payload_json TEXT,
            UNIQUE(analyst_scan_time, symbol)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_v32_shadow_time ON shadow_snapshots(observed_at_utc)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_v32_shadow_symbol ON shadow_snapshots(symbol,observed_at_utc)")
    crowding.init_db(SHADOW_DB)

def load_latest_analyses():
    if not os.path.exists(ANALYST_DB):
        raise RuntimeError("analyst DB missing: " + ANALYST_DB)
    with sqlite3.connect(ANALYST_DB) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT MAX(scan_time_utc) ts FROM analyses").fetchone()
        if not row or not row["ts"]:
            return []
        rows = con.execute("""SELECT scan_time_utc,symbol,status,price,payload_json
                              FROM analyses WHERE scan_time_utc=?
                              ORDER BY symbol LIMIT ?""",
                           (row["ts"], MAX_SYMBOLS)).fetchall()
        return [dict(x) for x in rows]

def insert_snapshot(a):
    payload = json.loads(a.get("payload_json") or "{}")
    v3 = payload.get("v3") or {}
    direction = str(v3.get("direction") or "NONE")
    if direction not in ("LONG", "SHORT"):
        direction = "NONE"
    rows5, source, taker_available = fetch_klines(a["symbol"], "5m", 90)
    m = micro_features(rows5, taker_available=taker_available)
    h = independent_hypotheses(m, payload)
    values = {
        **m, **h,
        "version": VERSION,
        "observed_at_utc": now_iso(),
        "analyst_scan_time": a["scan_time_utc"],
        "symbol": a["symbol"],
        "v31_status": a.get("status"),
        "v31_direction": direction,
        "v31_eligible": 1 if v3.get("eligible") else 0,
        "v31_setup_type": str(v3.get("setup_type") or "NONE"),
        "v31_veto_json": json.dumps(v3.get("veto_reasons") or [], ensure_ascii=False),
        "market_data_source": source,
        "raw_payload_json": json.dumps({
            "v3": v3,
            "spot_flow": payload.get("spot_flow"),
            "oi": payload.get("oi"),
            "funding_pct": payload.get("funding_pct"),
            "long_short_ratio": payload.get("long_short_ratio"),
        }, ensure_ascii=False, separators=(",", ":")),
    }
    cols = [
        "version","observed_at_utc","analyst_scan_time","symbol","v31_status","v31_direction",
        "v31_eligible","v31_setup_type","v31_veto_json","market_data_source","price","support",
        "resistance","dist_support_pct","dist_resistance_pct","support_sweep_reclaim",
        "resistance_sweep_reject","higher_low","lower_high","structure_5m","ema7_slope_pct",
        "volume_mult","taker_buy_share","taker_delta","spot_delta_share","oi_change_1h",
        "funding_pct","long_short_ratio","btc_residual_3h_pct","long_cont_evidence",
        "short_cont_evidence","long_rev_evidence","short_rev_evidence","long_cont_reasons",
        "short_cont_reasons","long_rev_reasons","short_rev_reasons","raw_payload_json",
    ]
    db_cols = [
        "version","observed_at_utc","analyst_scan_time","symbol","v31_status","v31_direction",
        "v31_eligible","v31_setup_type","v31_veto_json","market_data_source","market_price","support",
        "resistance","dist_support_pct","dist_resistance_pct","support_sweep_reclaim",
        "resistance_sweep_reject","higher_low","lower_high","structure_5m","ema7_slope_pct",
        "volume_mult","taker_buy_share","taker_delta","spot_delta_share","oi_change_1h",
        "funding_pct","long_short_ratio","btc_residual_3h_pct","long_cont_evidence",
        "short_cont_evidence","long_rev_evidence","short_rev_evidence","long_cont_reasons_json",
        "short_cont_reasons_json","long_rev_reasons_json","short_rev_reasons_json","raw_payload_json",
    ]
    data = []
    for c in cols:
        v = values.get(c)
        if c.endswith("_reasons"):
            v = json.dumps(v or [], ensure_ascii=False)
        if c in ("support_sweep_reclaim","resistance_sweep_reject","higher_low","lower_high"):
            v = 1 if v else 0
        data.append(v)
    with sqlite3.connect(SHADOW_DB) as con:
        con.execute(
            f"""INSERT OR IGNORE INTO shadow_snapshots({','.join(db_cols)})
                VALUES({','.join('?' for _ in db_cols)})""",
            data,
        )
    crowding.observe(SHADOW_DB, a, payload, m, source)

def fetch_current_price(symbol):
    for base in BINANCE_FUTURES:
        try:
            x = _get_json(base + "/fapi/v1/ticker/price", {"symbol": symbol})
            return float(x["price"]), "BINANCE_FUTURES"
        except Exception:
            pass
    for base in BINANCE_SPOT:
        try:
            x = _get_json(base + "/api/v3/ticker/price", {"symbol": symbol})
            return float(x["price"]), "BINANCE_SPOT"
        except Exception:
            pass
    try:
        x = _get_json("https://api.bybit.com/v5/market/tickers",
                      {"category":"linear","symbol":symbol})
        rows = ((x or {}).get("result") or {}).get("list") or []
        if rows:
            return float(rows[0]["lastPrice"]), "BYBIT_LINEAR"
    except Exception:
        pass
    try:
        base = symbol[:-4] if symbol.endswith("USDT") else symbol
        x = _get_json("https://api.gateio.ws/api/v4/futures/usdt/tickers",
                      {"contract": base + "_USDT"})
        rows = x if isinstance(x, list) else []
        if rows:
            return float(rows[0]["last"]), "GATE_FUTURES"
    except Exception:
        pass
    raise RuntimeError(symbol + " ticker unavailable")

def label_due():
    now = time.time()
    horizons = [
        (15, "price_15m", "return_15m_pct", "label_15m_at_utc", "label_15m_delay_sec"),
        (60, "price_60m", "return_60m_pct", "label_60m_at_utc", "label_60m_delay_sec"),
        (180, "price_180m", "return_180m_pct", "label_180m_at_utc", "label_180m_delay_sec"),
    ]
    with sqlite3.connect(SHADOW_DB) as con:
        con.row_factory = sqlite3.Row
        due = con.execute("""SELECT id,symbol,observed_at_utc,market_price,
                            price_15m,price_60m,price_180m
                            FROM shadow_snapshots
                            WHERE price_180m IS NULL
                            ORDER BY observed_at_utc LIMIT 800""").fetchall()
        by_symbol = {}
        for r in due:
            age = now - iso_to_epoch(r["observed_at_utc"])
            needed = any(age >= mins*60 and r[pcol] is None for mins,pcol,_,_,_ in horizons)
            if needed:
                by_symbol.setdefault(r["symbol"], []).append(r)

        for symbol, rows in by_symbol.items():
            try:
                px, _ = fetch_current_price(symbol)
            except Exception as exc:
                print("LABEL_PRICE_ERROR", symbol, type(exc).__name__, str(exc)[:120])
                continue
            label_time = now_iso()
            for r in rows:
                age = now - iso_to_epoch(r["observed_at_utc"])
                updates, args = [], []
                entry = float(r["market_price"] or 0.0)
                if entry <= 0:
                    continue
                for mins,pcol,rcol,tcol,dcol in horizons:
                    if age >= mins*60 and r[pcol] is None:
                        updates += [f"{pcol}=?", f"{rcol}=?", f"{tcol}=?", f"{dcol}=?"]
                        args += [px, (px/entry - 1.0)*100.0, label_time, age - mins*60]
                if updates:
                    args.append(r["id"])
                    con.execute(f"UPDATE shadow_snapshots SET {','.join(updates)} WHERE id=?", args)

def summary():
    with sqlite3.connect(SHADOW_DB) as con:
        con.row_factory = sqlite3.Row
        n = con.execute("SELECT COUNT(*) n FROM shadow_snapshots").fetchone()["n"]
        latest = con.execute("""SELECT analyst_scan_time,
            SUM(v31_direction='LONG') long_n,
            SUM(v31_direction='SHORT') short_n,
            SUM(v31_direction='NONE') none_n,
            AVG(long_rev_evidence) avg_lr,
            AVG(short_rev_evidence) avg_sr
            FROM shadow_snapshots
            WHERE analyst_scan_time=(SELECT MAX(analyst_scan_time) FROM shadow_snapshots)
        """).fetchone()
        print("V32_SHADOW_OK", "rows=", n,
              "scan=", latest["analyst_scan_time"] if latest else None,
              "v31_long=", latest["long_n"] if latest else 0,
              "v31_short=", latest["short_n"] if latest else 0,
              "v31_none=", latest["none_n"] if latest else 0,
              "avg_long_rev=", round(float(latest["avg_lr"] or 0.0),2) if latest else 0,
              "avg_short_rev=", round(float(latest["avg_sr"] or 0.0),2) if latest else 0)

def main():
    init_db()
    label_due()
    crowding.label_due(SHADOW_DB)
    rows = load_latest_analyses()
    errors = 0
    for a in rows:
        try:
            insert_snapshot(a)
        except Exception as exc:
            errors += 1
            print("SHADOW_SYMBOL_ERROR", a.get("symbol"), type(exc).__name__, str(exc)[:160])
    label_due()
    crowding.label_due(SHADOW_DB)
    summary()
    crowding.summary(SHADOW_DB)
    if rows and errors == len(rows):
        raise SystemExit("all V3.2 shadow observations failed")

if __name__ == "__main__":
    main()
