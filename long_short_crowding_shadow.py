#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Account-crowding vs price shadow observer. Research only: NO orders, NO Telegram.

Majority is measured by number of long/short accounts, NOT capital or
position size. Separate exchange sources are NEVER treated as equivalent.
This observer cannot alter frozen V3.1 signals or the V3.2 evidence model.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timezone

import requests

VERSION = "CROWDING_ACCOUNT_CONTRARIAN_SHADOW_V1"
HORIZONS = (15, 60, 180)
MAX_LABEL_DELAY_SECONDS = 600   # measurement quality, NOT a trade threshold
HTTP_TIMEOUT_SECONDS = 8


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def valid_positive(v):
    try:
        val = float(v)
        return val if math.isfinite(val) and val > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def valid_number(v):
    try:
        val = float(v)
        return val if math.isfinite(val) else None
    except (ValueError, TypeError, OverflowError):
        return None


def crowding_features(payload, micro, chart_venue):
    """Return a descriptive observation, never a trading decision.

    No data means unknown, NEVER a fabricated 50/50 split. One source is
    required for account-ratio, OI and related data. Price and account-ratio
    sources may differ in diagnostic logs but must match for research outcomes.
    """
    ready = payload.get("derivatives_ready") is True
    mv = payload.get("multi_venue_derivatives") or {}
    mode = str(payload.get("data_mode") or "")
    provider = ("BINANCE_FUTURES" if mode == "BINANCE_FUTURES"
                else str(payload.get("derivatives_selected_provider") or ""))
    ratio = (valid_positive(payload.get("long_short_ratio")) if ready and mode == "BINANCE_FUTURES"
             else valid_positive(mv.get("long_short_ratio")) if ready and mv.get("source_consistent") is True
             else None)
    if not provider or ratio is None:
        provider = provider or None
    if ready and mode != "BINANCE_FUTURES":
        field_provider = str((mv.get("field_source") or {}).get("long_short_ratio") or "")
        if field_provider != provider:
            ratio = None

    share = (ratio / (1.0 + ratio)) if ratio is not None else None
    change = valid_number(micro.get("price_change_15m_pct"))
    oi = valid_number((payload.get("oi") or {}).get("oi_change_1h")) if ready else None
    taker = valid_positive(payload.get("taker_ratio")) if ready else None
    funding = valid_number(payload.get("funding_pct")) if ready else None
    majority = "LONG" if share is not None and share > .5 else (
        "SHORT" if share is not None and share < .5 else "BALANCED" if share is not None else "UNKNOWN"
    )
    classification = "UNKNOWN"
    opposite_direction = None
    if majority in ("LONG", "SHORT") and change is not None:
        if majority == "LONG" and change < 0:
            classification, opposite_direction = "AGAINST_LONGS", "SHORT"
        elif majority == "SHORT" and change > 0:
            classification, opposite_direction = "AGAINST_SHORTS", "LONG"
        elif change == 0:
            classification = "FLAT"
        else:
            classification = "WITH_MAJORITY"
    elif majority == "BALANCED":
        classification = "BALANCED"

    chart_venue = str(chart_venue or "")
    if ratio is None:
        quality = "MISSING_RATIO_OR_UNVERIFIED"
    elif change is None:
        quality = "MISSING_PRICE_DIRECTION"
    elif provider != chart_venue:
        quality = "CROSS_VENUE_DIAGNOSTIC"
    else:
        quality = "SAME_VENUE"
    return {
        "ratio": ratio, "long_account_share": share, "crowd_majority": majority,
        "price_change_15m_pct": change, "classification": classification,
        "opposite_direction": opposite_direction, "ratio_venue": provider,
        "chart_venue": chart_venue, "oi_change_1h_pct": oi,
        "taker_buy_sell_ratio": taker, "funding_pct": funding,
        "data_quality": quality,
    }


def init_db(db):
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS crowding_observations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL, observed_at_utc TEXT NOT NULL,
            analyst_scan_time TEXT NOT NULL, symbol TEXT NOT NULL,
            chart_venue TEXT, ratio_venue TEXT, data_quality TEXT NOT NULL,
            account_long_short_ratio REAL, long_account_share REAL,
            crowd_majority TEXT, price_change_15m_pct REAL,
            classification TEXT NOT NULL, opposite_direction TEXT,
            oi_change_1h_pct REAL, taker_buy_sell_ratio REAL, funding_pct REAL,
            entry_price REAL, v31_status TEXT, v31_direction TEXT,
            UNIQUE(analyst_scan_time,symbol)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS crowding_outcomes(
            observation_id INTEGER NOT NULL,
            horizon_min INTEGER NOT NULL,
            status TEXT NOT NULL,
            label_at_utc TEXT NOT NULL,
            label_delay_seconds REAL NOT NULL,
            observed_price REAL, raw_return_pct REAL, opposite_correct INTEGER,
            PRIMARY KEY(observation_id,horizon_min),
            FOREIGN KEY(observation_id) REFERENCES crowding_observations(id)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_crowding_time ON crowding_observations(observed_at_utc)")


def current_price_at_venue(symbol, venue):
    """Never silently substitute another exchange for the observation exchange."""
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    if venue == "GATE_FUTURES":
        url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
        params = {"contract": base + "_USDT"}
    elif venue == "BYBIT_LINEAR":
        url = "https://api.bybit.com/v5/market/tickers"
        params = {"category": "linear", "symbol": symbol}
    elif venue == "BINANCE_FUTURES":
        url = "https://fapi.binance.com/fapi/v1/ticker/price"
        params = {"symbol": symbol}
    elif venue == "BINANCE_SPOT":
        url = "https://data-api.binance.vision/api/v3/ticker/price"
        params = {"symbol": symbol}
    else:
        raise ValueError("unsupported venue " + str(venue))
    r = requests.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
    r.raise_for_status()
    data = r.json()
    if venue == "GATE_FUTURES":
        rows = data if isinstance(data, list) else []
        px = rows[0].get("last") if rows else None
    elif venue == "BYBIT_LINEAR":
        rows = (data.get("result") or {}).get("list") or []
        px = rows[0].get("lastPrice") if rows else None
    else:
        px = data.get("price")
    price = valid_positive(px)
    if price is None:
        raise ValueError("invalid ticker price")
    return price


def observe(db, analyst, payload, micro, market_venue, price_fn=current_price_at_venue):
    """Insert exactly one append-only sample for each V3.1 scan + coin."""
    init_db(db)
    scan = analyst["scan_time_utc"]
    symbol = analyst["symbol"]
    with sqlite3.connect(db) as con:
        if con.execute("SELECT 1 FROM crowding_observations WHERE analyst_scan_time=? AND symbol=?",
                       (scan, symbol)).fetchone():
            return False
    f = crowding_features(payload, micro, market_venue)
    px = None
    if f["data_quality"] == "SAME_VENUE":
        try:
            px = valid_positive(price_fn(symbol, f["chart_venue"]))
        except Exception as exc:
            print("CROWDING_ENTRY_PRICE_UNAVAILABLE", symbol, type(exc).__name__, flush=True)
            f["data_quality"] = "NO_ENTRY_PRICE"
    v3 = payload.get("v3") or {}
    with sqlite3.connect(db) as con:
        con.execute("""INSERT OR IGNORE INTO crowding_observations(
            version,observed_at_utc,analyst_scan_time,symbol,chart_venue,ratio_venue,
            data_quality,account_long_short_ratio,long_account_share,crowd_majority,
            price_change_15m_pct,classification,opposite_direction,oi_change_1h_pct,
            taker_buy_sell_ratio,funding_pct,entry_price,v31_status,v31_direction
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (VERSION,utc_now(),scan,symbol,f["chart_venue"],f["ratio_venue"],
         f["data_quality"],f["ratio"],f["long_account_share"],f["crowd_majority"],
         f["price_change_15m_pct"],f["classification"],f["opposite_direction"],
         f["oi_change_1h_pct"],f["taker_buy_sell_ratio"],f["funding_pct"],px,
         analyst.get("status"),v3.get("direction")))
    return True


def label_due(db, price_fn=current_price_at_venue, now_epoch=None):
    """Late, absent or cross-venue prices never become positive outcomes."""
    init_db(db)
    now = time.time() if now_epoch is None else float(now_epoch)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""SELECT * FROM crowding_observations
                              WHERE data_quality='SAME_VENUE' AND entry_price>0
                              ORDER BY observed_at_utc DESC LIMIT 1500""").fetchall()
        pending = []
        for o in rows:
            when = datetime.fromisoformat(o["observed_at_utc"]).timestamp()
            for horizon in HORIZONS:
                if now < when + horizon * 60:
                    continue
                exists = con.execute("""SELECT 1 FROM crowding_outcomes
                    WHERE observation_id=? AND horizon_min=?""", (o["id"], horizon)).fetchone()
                if not exists:
                    pending.append((o, horizon, now - when - horizon * 60))
        prices = {}
        for o, horizon, delay in pending:
            key = (o["symbol"], o["chart_venue"])
            status = "VALID"
            price = None
            if delay > MAX_LABEL_DELAY_SECONDS:
                status = "DATA_GAP_LATE"
            else:
                if key not in prices:
                    try:
                        prices[key] = valid_positive(price_fn(*key))
                    except Exception as exc:
                        prices[key] = None
                        print("CROWDING_OUTCOME_PRICE_UNAVAILABLE", key[0],
                              type(exc).__name__, flush=True)
                price = prices[key]
                if price is None:
                    status = "DATA_GAP_PRICE"
            entry = float(o["entry_price"])
            ret = ((price / entry - 1.0) * 100.0) if price is not None and status == "VALID" else None
            opposite = o["opposite_direction"]
            correct = (int(ret < 0) if opposite == "SHORT" else int(ret > 0) if opposite == "LONG"
                       else None) if ret is not None else None
            con.execute("""INSERT OR IGNORE INTO crowding_outcomes(
                 observation_id,horizon_min,status,label_at_utc,label_delay_seconds,
                 observed_price,raw_return_pct,opposite_correct)
                 VALUES(?,?,?,?,?,?,?,?)""",
                 (o["id"],horizon,status,datetime.fromtimestamp(now,timezone.utc).isoformat(),
                  delay,price if status == "VALID" else None,ret,correct))
        return len(pending)


def summary(db):
    init_db(db)
    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM crowding_observations").fetchone()[0]
        latest = con.execute("""SELECT data_quality,classification,COUNT(*) FROM crowding_observations
            WHERE analyst_scan_time=(SELECT MAX(analyst_scan_time) FROM crowding_observations)
            GROUP BY data_quality,classification ORDER BY data_quality,classification""").fetchall()
        outcome_rows = con.execute("""SELECT o.id,o.symbol,o.observed_at_utc,o.classification,
            t.horizon_min,t.opposite_correct FROM crowding_observations o
            JOIN crowding_outcomes t ON t.observation_id=o.id
            WHERE o.data_quality='SAME_VENUE' AND t.status='VALID'
              AND o.opposite_direction IS NOT NULL
            ORDER BY o.observed_at_utc,o.id""").fetchall()
    # Non-overlapping, earliest sample per 2-hour symbol bucket; no repeated
    # near-identical 10-minute samples inflating the apparent evidence.
    seen = set()
    by_horizon = {}
    for oid, sym, stamp, cls, horizon, correct in outcome_rows:
        bucket = int(datetime.fromisoformat(stamp).timestamp() // 7200)
        key = (sym, bucket, cls, horizon)
        if key in seen:
            continue
        seen.add(key)
        acc = by_horizon.setdefault(horizon, [0, 0])
        acc[0] += 1
        acc[1] += int(correct)
    print("CROWDING_SHADOW_OK", "samples=", n, "latest=", latest,
          "independent_2h_outcomes=", by_horizon, flush=True)
    return {"samples": n, "latest": latest, "independent_2h_outcomes": by_horizon}
