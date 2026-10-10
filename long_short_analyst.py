#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Futures Long/Short Analyst v1
- Analysis only. Never places orders.
- Deterministic scoring; Telegram is presentation only.
- Separate state/database from frozen Avci engines.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import sqlite3
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from binance_notify import resolve_chat_id
from long_short_data_router import multi_venue_derivatives, multi_venue_perp_universe, multi_venue_perp_klines
from long_short_v3_core import V3_VERSION, discovery_rank as v3_discovery_rank, beta_residual_3h, decide_setup as v3_decide_setup
from long_short_direction_engine import DIRECTION_ENGINE_VERSION, decide_direction as production_decide_direction
from long_short_room_v32_shadow import from_analyst_snapshots as v32_room_shadow
from long_short_dual_v32 import build_both_sides as build_v32_dual_scenarios

FUTURES_BASES = (
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi4.binance.com",
)
SPOT_BASES = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
)
DATA_MODE = "BINANCE_FUTURES"
DB = os.getenv("LS_DB", "long_short_analyst.db")
MIN_24H_QUOTE_VOL = float(os.getenv("LS_MIN_24H_QUOTE_VOL", "25000000"))
DISCOVERY_MIN_24H_QUOTE_VOL = float(os.getenv("LS_DISCOVERY_MIN_24H_QUOTE_VOL", "8000000"))
DISCOVERY_META = {}
MULTI_DERIV_CACHE = {}
MAX_SYMBOLS = int(os.getenv("LS_MAX_SYMBOLS", "80"))
REQUEST_TIMEOUT = 12
TELEGRAM_LIMIT = 4096
VERSION = DIRECTION_ENGINE_VERSION
PRESELECT_MAX = max(12, min(24, int(os.getenv("LS_PRESELECT_MAX", "24"))))
UNIVERSE_MOVER_SHARE = float(os.getenv("LS_UNIVERSE_MOVER_SHARE", "0.75"))
PREFILTER_WORKERS = int(os.getenv("LS_PREFILTER_WORKERS", "6"))
DEEP_WORKERS = int(os.getenv("LS_DEEP_WORKERS", "6"))
PAPER_NOTIONAL_USDT = float(os.getenv("LS_PAPER_NOTIONAL_USDT", "250"))
MAX_CROSS_VENUE_BASIS_PCT = float(os.getenv("LS_MAX_CROSS_VENUE_BASIS_PCT", "0.35"))
HTF_CACHE_TTL_1H = int(os.getenv("LS_HTF_CACHE_TTL_1H", "900"))
HTF_CACHE_TTL_4H = int(os.getenv("LS_HTF_CACHE_TTL_4H", "3600"))
HTF_CACHE_TTL_1D = int(os.getenv("LS_HTF_CACHE_TTL_1D", "7200"))
HTF_NEAR_LEVEL_PCT = float(os.getenv("LS_HTF_NEAR_LEVEL_PCT", "0.80"))
HTF_GATE_MIN_SCORE = int(os.getenv("LS_HTF_GATE_MIN_SCORE", "5"))
OKX_BASE = "https://www.okx.com"
FEE_BPS_PER_SIDE = float(os.getenv("LS_FEE_BPS_PER_SIDE", "5"))
SLIPPAGE_BPS_PER_SIDE = float(os.getenv("LS_SLIPPAGE_BPS_PER_SIDE", "5"))
SIGNAL_EXPIRY_MIN = int(os.getenv("LS_SIGNAL_EXPIRY_MIN", "180"))
SIGNAL_COOLDOWN_MIN = int(os.getenv("LS_SIGNAL_COOLDOWN_MIN", "120"))
TELEGRAM_SUMMARY = os.getenv("LS_TELEGRAM_SUMMARY", "0").strip().lower() in ("1","true","yes","on")
STRUCTURE_GATE_VERSION = "LS_STRUCTURE_GATE_V3_1_2026-10-07"
STRUCTURE_MIN_ROOM_FLOOR_PCT = float(os.getenv("LS_STRUCTURE_MIN_ROOM_FLOOR_PCT", "0.90"))
STRUCTURE_MIN_ROOM_COST_MULT = float(os.getenv("LS_STRUCTURE_MIN_ROOM_COST_MULT", "3.0"))
VALIDATION_MIN_SLIPPAGE_BPS_PER_SIDE = float(os.getenv("LS_VALIDATION_MIN_SLIPPAGE_BPS_PER_SIDE", "10"))
STRUCTURE_MIN_NET_T1_R = float(os.getenv("LS_STRUCTURE_MIN_NET_T1_R", "1.0"))
STRUCTURE_MIN_VOLUME_MULT = float(os.getenv("LS_STRUCTURE_MIN_VOLUME_MULT", "1.10"))
STRUCTURE_MIN_BODY_RATIO = float(os.getenv("LS_STRUCTURE_MIN_BODY_RATIO", "0.45"))
STRUCTURE_MAX_REJECTION_WICK = float(os.getenv("LS_STRUCTURE_MAX_REJECTION_WICK", "0.35"))
STRUCTURE_LEVEL_NEAR_PCT = float(os.getenv("LS_STRUCTURE_LEVEL_NEAR_PCT", "0.80"))

EXCLUDED_BASES = {
    "USDC","FDUSD","TUSD","USDP","DAI","BUSD","USDE","USDS","PYUSD",
    "RLUSD","USD1","USDD","GUSD","FRAX","LUSD","USD0","EURC",
    "EUR","TRY","BTCST",
}
EXCLUDED_MARKERS = ("UP","DOWN","BULL","BEAR")
FROZEN_CONFIG_PATH = os.getenv("LS_FROZEN_CONFIG_PATH", "LONG_SHORT_V3_FROZEN_CONFIG.json")


def frozen_config_hash():
    with open(FROZEN_CONFIG_PATH, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


FROZEN_CONFIG_HASH = frozen_config_hash()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def market_episode_id(ts: str, direction: str, regime: str) -> str:
    """Group correlated signals from the same market wave.

    This is evaluation metadata only: multiple altcoin LONGs in the same
    half-hour/BTC regime are not counted as independent evidence.
    """
    dt=datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=timezone.utc)
    bucket=int(dt.timestamp()//1800)
    return f"{bucket}|{regime}|{direction}"


def fget(path: str, params: dict | None = None):
    global DATA_MODE
    last = None
    spot_map = {
        "/fapi/v1/klines": "/api/v3/klines",
        "/fapi/v1/exchangeInfo": "/api/v3/exchangeInfo",
        "/fapi/v1/ticker/24hr": "/api/v3/ticker/24hr",
        "/fapi/v1/depth": "/api/v3/depth",
    }

    # Once HTTP 451 confirms a location restriction, never retry a native
    # Futures-only endpoint on another Binance hostname in this process.
    # Fail closed: spot candles must never masquerade as Futures derivatives.
    if DATA_MODE=="BINANCE_SPOT_GRAPH_ONLY" and path not in spot_map:
        raise requests.HTTPError("Binance Futures HTTP 451: native endpoint unavailable; no spot equivalent")
    # Supported spot endpoints remain explicitly research-only.
    if DATA_MODE=="BINANCE_SPOT_GRAPH_ONLY" and path in spot_map:
        for base in SPOT_BASES:
            try:
                r=requests.get(
                    base + spot_map[path], params=params or {}, timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": "long-short-analyst/1.1"},
                )
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as exc:
                last=exc
        raise last or RuntimeError("Binance Spot market data unavailable")

    for base in FUTURES_BASES:
        try:
            r = requests.get(
                base + path, params=params or {}, timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "long-short-analyst/1.1"},
            )
            if r.status_code == 451:
                # Geo/legal restriction: other Binance Futures hostnames do not
                # grant permission. Do not keep retrying them for each request.
                last = requests.HTTPError("Binance Futures HTTP 451: location restricted")
                DATA_MODE = "BINANCE_SPOT_GRAPH_ONLY"
                print("BINANCE_FUTURES_451_RESTRICTED: native futures disabled; fallback is research-only", flush=True)
                break
            r.raise_for_status()
            body = r.json()
            DATA_MODE = "BINANCE_FUTURES"
            return body
        except (requests.RequestException, ValueError) as exc:
            last = exc

    spot_path = spot_map.get(path)
    if spot_path:
        for base in SPOT_BASES:
            try:
                r = requests.get(
                    base + spot_path, params=params or {}, timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": "long-short-analyst/1.1"},
                )
                r.raise_for_status()
                DATA_MODE = "BINANCE_SPOT_GRAPH_ONLY"
                return r.json()
            except (requests.RequestException, ValueError) as exc:
                last = exc
    raise last or RuntimeError("Binance market data unavailable")


def spot_delta_proxy(symbol: str) -> dict[str, Any]:
    """Real Binance Spot aggressive-flow proxy from CLOSED 1m klines.

    delta_share = sum(2*taker_buy_quote - quote_volume) / sum(quote_volume).
    Never uses normalized external candles, so missing Spot data stays missing.
    """
    last=None
    for base in SPOT_BASES:
        try:
            r=requests.get(
                base + "/api/v3/klines",
                params={"symbol":symbol,"interval":"1m","limit":14},
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent":"long-short-v3-spot-flow/1.0"},
            )
            r.raise_for_status()
            rows=r.json()
            now_ms=int(time.time()*1000)
            rows=[x for x in rows if int(x[6]) <= now_ms-250]
            if len(rows)<8:
                continue
            recent=rows[-8:]
            quote=sum(float(x[7] or 0.0) for x in recent)
            delta=sum(2.0*float(x[10] or 0.0)-float(x[7] or 0.0) for x in recent)
            first=recent[:4]; second=recent[4:]
            def dshare(xs):
                q=sum(float(x[7] or 0.0) for x in xs)
                d=sum(2.0*float(x[10] or 0.0)-float(x[7] or 0.0) for x in xs)
                return d/q if q>0 else 0.0
            return {
                "available":True,
                "provider":"BINANCE_SPOT",
                "delta_share":delta/quote if quote>0 else 0.0,
                "delta_share_prev4":dshare(first),
                "delta_share_last4":dshare(second),
            }
        except Exception as exc:
            last=exc
    return {
        "available":False,
        "provider":"UNAVAILABLE",
        "delta_share":None,
        "error":None if last is None else f"{type(last).__name__}:{str(last)[:120]}",
    }


def okx_get(path: str, params: dict | None = None):
    r=requests.get(
        OKX_BASE + path, params=params or {}, timeout=REQUEST_TIMEOUT,
        headers={"User-Agent":"long-short-analyst/1.2"},
    )
    r.raise_for_status()
    body=r.json()
    if str(body.get("code","0"))!="0":
        raise RuntimeError(body.get("msg") or "OKX error")
    return body.get("data") or []


def okx_derivatives(symbol: str):
    """Cross-venue derivatives context. Never mislabeled as Binance-native."""
    base=symbol[:-4] if symbol.endswith("USDT") else symbol
    inst=f"{base}-USDT-SWAP"
    out={
        "source":"CROSS_VENUE_OKX","inst_id":inst,"status":"UNAVAILABLE",
        "oi_usd":None,"funding_pct":None,"mark_price":None,
        "index_price":None,"basis_pct":None,
    }
    try:
        oi=(okx_get("/api/v5/public/open-interest",{"instType":"SWAP","instId":inst}) or [{}])[0]
        fr=(okx_get("/api/v5/public/funding-rate",{"instId":inst}) or [{}])[0]
        mp=(okx_get("/api/v5/public/mark-price",{"instType":"SWAP","instId":inst}) or [{}])[0]
        ix=(okx_get("/api/v5/market/index-tickers",{"instId":f"{base}-USDT"}) or [{}])[0]
        def fv(x):
            try: return float(x)
            except (TypeError,ValueError): return None
        out["oi_usd"]=fv(oi.get("oiUsd"))
        fund=fv(fr.get("fundingRate"))
        out["funding_pct"]=fund*100.0 if fund is not None else None
        out["mark_price"]=fv(mp.get("markPx"))
        out["index_price"]=fv(ix.get("idxPx"))
        if out["mark_price"] is not None and out["index_price"] not in (None,0):
            out["basis_pct"]=100.0*(out["mark_price"]/out["index_price"]-1.0)
        out["status"]="OK"
    except Exception as exc:
        out["error"]=f"{type(exc).__name__}:{str(exc)[:140]}"
    return out


def pct(a: float, b: float) -> float:
    return 0.0 if not a else (b / a - 1.0) * 100.0


def mean(xs):
    return statistics.fmean(xs) if xs else 0.0


def ema(xs, period):
    if not xs:
        return 0.0
    a = 2.0 / (period + 1.0)
    value = xs[0]
    for x in xs[1:]:
        value = a * x + (1 - a) * value
    return value


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for a, b in zip(closes[-period-1:-1], closes[-period:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = mean(gains), mean(losses)
    if al <= 1e-12:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def atr(rows, period=14):
    if len(rows) < period + 1:
        return 0.0
    tr = []
    for prev, cur in zip(rows[-period-1:-1], rows[-period:]):
        ph = float(cur[2]); pl = float(cur[3]); pc = float(prev[4])
        tr.append(max(ph-pl, abs(ph-pc), abs(pl-pc)))
    return mean(tr)


def parse_klines(rows):
    return {
        "open": [float(r[1]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": [float(r[4]) for r in rows],
        "volume": [float(r[7]) for r in rows],
        "trades": [float(r[8]) for r in rows],
        "taker_buy_quote": [float(r[10]) for r in rows],
        "raw": rows,
    }


def fetch_klines(symbol, interval, limit=220):
    """Return CLOSED candles only, with a public perpetual fallback.

    Binance Futures REST can return HTTP 451 on GitHub-hosted runners. In that
    case fget() falls back to Binance Spot. If the symbol is futures-only (or its
    Spot history is too short), use Bybit/Gate USDT-perpetual candles for chart
    discovery instead of silently dropping the coin from the scanner.
    """
    rows=None
    primary_error=None
    try:
        rows=fget("/fapi/v1/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
    except Exception as exc:
        primary_error=exc

    now_ms=int(time.time()*1000)
    closed=[r for r in (rows or []) if int(r[6]) <= now_ms-250]

    if len(closed)<20 and DATA_MODE!="BINANCE_FUTURES":
        try:
            ext=multi_venue_perp_klines(symbol,interval,limit)
            ext_rows=ext.get("rows") or []
            ext_closed=[r for r in ext_rows if int(r[6]) <= now_ms-250]
            if len(ext_closed)>len(closed):
                closed=ext_closed
        except Exception as exc:
            if primary_error is None:
                primary_error=exc

    if len(closed)<20:
        suffix=f"; fallback={type(primary_error).__name__}:{str(primary_error)[:120]}" if primary_error else ""
        raise RuntimeError(f"{symbol} {interval}: not enough closed candles ({len(closed)}){suffix}")
    return parse_klines(closed)


def timeframe_features(k):
    c, h, l, v = k["close"], k["high"], k["low"], k["volume"]
    e20, e50, e200 = ema(c[-80:],20), ema(c[-140:],50), ema(c,200)
    price = c[-1]
    prev20h = max(h[-21:-1]) if len(h) >= 21 else max(h[:-1])
    prev20l = min(l[-21:-1]) if len(l) >= 21 else min(l[:-1])
    a = atr(k["raw"],14)
    vol_base = mean(v[-31:-1]) or 1.0
    vol_mult = v[-1] / vol_base
    structure = 0
    if len(h) >= 8:
        recent_highs = [max(h[-8:-4]), max(h[-4:])]
        recent_lows = [min(l[-8:-4]), min(l[-4:])]
        structure = (1 if recent_highs[1] > recent_highs[0] and recent_lows[1] > recent_lows[0]
                     else -1 if recent_highs[1] < recent_highs[0] and recent_lows[1] < recent_lows[0]
                     else 0)
    return {
        "price":price, "ema20":e20, "ema50":e50, "ema200":e200,
        "rsi":rsi(c), "atr":a, "atr_pct":(a/price*100 if price else 0),
        "vol_mult":vol_mult,
        "change_1":pct(c[-2], c[-1]) if len(c)>1 else 0,
        "change_4":pct(c[-5], c[-1]) if len(c)>5 else 0,
        "breakout20": price > prev20h,
        "breakdown20": price < prev20l,
        "dist_high20_pct": pct(price, prev20h) if price else 0,
        "dist_low20_pct": pct(prev20l, price) if prev20l else 0,
        "structure": structure,
        "above20": price > e20, "above50": price > e50, "above200": price > e200,
    }


def swing_levels(k, lookback=48):
    """Recent structural support/resistance from completed candles."""
    h=k["high"][-lookback:]
    l=k["low"][-lookback:]
    c=k["close"][-lookback:]
    if len(h)<8:
        p=c[-1]
        return {"support":p,"resistance":p,"support2":p,"resistance2":p}
    # Ignore current candle; use local pivots from completed bars.
    piv_hi=[]; piv_lo=[]
    for i in range(2,len(h)-2):
        if h[i]>=h[i-1] and h[i]>=h[i-2] and h[i]>=h[i+1] and h[i]>=h[i+2]:
            piv_hi.append(h[i])
        if l[i]<=l[i-1] and l[i]<=l[i-2] and l[i]<=l[i+1] and l[i]<=l[i+2]:
            piv_lo.append(l[i])
    price=c[-1]
    below=sorted([x for x in piv_lo if x<price], reverse=True)
    above=sorted([x for x in piv_hi if x>price])
    support=below[0] if below else min(l[-12:])
    support2=below[1] if len(below)>1 else min(l[-24:])
    resistance=above[0] if above else max(h[-12:])
    resistance2=above[1] if len(above)>1 else max(h[-24:])
    return {
        "support":support,"support2":support2,
        "resistance":resistance,"resistance2":resistance2,
    }


def _pivot_levels(k, lookback=120):
    """Closed-candle local pivot highs/lows used to build support/resistance zones."""
    highs=k["high"][-lookback:]
    lows=k["low"][-lookback:]
    out_hi=[]; out_lo=[]
    for i in range(2,len(highs)-2):
        if highs[i]>=highs[i-1] and highs[i]>=highs[i-2] and highs[i]>=highs[i+1] and highs[i]>=highs[i+2]:
            out_hi.append(float(highs[i]))
        if lows[i]<=lows[i-1] and lows[i]<=lows[i-2] and lows[i]<=lows[i+1] and lows[i]<=lows[i+2]:
            out_lo.append(float(lows[i]))
    return out_hi,out_lo


def _cluster_levels(levels, tolerance):
    if not levels:
        return []
    clusters=[]
    for level in sorted(float(x) for x in levels):
        placed=False
        for cluster in clusters:
            center=sum(cluster)/len(cluster)
            if abs(level-center)<=tolerance:
                cluster.append(level); placed=True; break
        if not placed:
            clusters.append([level])
    return [
        {"center":sum(xs)/len(xs),"low":min(xs)-tolerance*0.5,
         "high":max(xs)+tolerance*0.5,"touches":len(xs)}
        for xs in clusters
    ]


def _timeframe_zones(k, label, weight, price):
    a=max(atr(k["raw"],14),price*0.001)
    tolerance=max(price*0.0012,a*0.30)
    piv_hi,piv_lo=_pivot_levels(k)
    zones=[]
    for side,levels in (("RESISTANCE",piv_hi),("SUPPORT",piv_lo)):
        for z in _cluster_levels(levels,tolerance):
            z.update({
                "side":side,"timeframe":label,"weight":float(weight),
                "strength":float(z["touches"])*float(weight),
                "tolerance":tolerance,
            })
            zones.append(z)
    return zones


def _closed_candle_alignment(k, direction, trigger=None):
    o=float(k["open"][-1]); h=float(k["high"][-1]); l=float(k["low"][-1]); c=float(k["close"][-1])
    rng=max(h-l,1e-12)
    body=abs(c-o)
    body_ratio=body/rng
    close_location=(c-l)/rng
    volumes=[float(x) for x in k["volume"]]
    base=mean(volumes[-21:-1]) if len(volumes)>=21 else mean(volumes[:-1])
    vol_mult=(volumes[-1]/base) if base>0 else 1.0
    if direction=="LONG":
        direction_ok=c>o
        close_location_ok=close_location>=0.65
        rejection_wick=(h-max(o,c))/rng
        fake_breakout=bool(trigger is not None and h>float(trigger) and c<=float(trigger))
    else:
        direction_ok=c<o
        close_location_ok=close_location<=0.35
        rejection_wick=(min(o,c)-l)/rng
        fake_breakout=bool(trigger is not None and l<float(trigger) and c>=float(trigger))
    return {
        "open":o,"high":h,"low":l,"close":c,
        "volume_mult":vol_mult,"body_ratio":body_ratio,
        "close_location":close_location,"rejection_wick_ratio":rejection_wick,
        "direction_ok":bool(direction_ok),
        "volume_ok":bool(vol_mult>=STRUCTURE_MIN_VOLUME_MULT),
        "body_ok":bool(body_ratio>=STRUCTURE_MIN_BODY_RATIO),
        "close_location_ok":bool(close_location_ok),
        "rejection_wick_ok":bool(rejection_wick<=STRUCTURE_MAX_REJECTION_WICK),
        "fake_breakout":fake_breakout,
    }


def structure_min_round_trip_cost_pct():
    """Minimum two-sided cost used by live V2.1 gating, expressed in percent."""
    return 2.0 * (FEE_BPS_PER_SIDE + VALIDATION_MIN_SLIPPAGE_BPS_PER_SIDE) / 100.0


def structure_required_room_pct():
    """Room must be large enough that costs are not the whole trade."""
    return max(STRUCTURE_MIN_ROOM_FLOOR_PCT,
               structure_min_round_trip_cost_pct() * STRUCTURE_MIN_ROOM_COST_MULT)


def structure_net_t1_r(direction, trigger, invalidation, target1):
    """Unified net R, including round-trip costs in reward AND risk."""
    from long_short_r_math import trade_net_r
    outcome = trade_net_r(direction, trigger, invalidation, target1,
                          structure_min_round_trip_cost_pct())
    return outcome["net_r"] if outcome["ok"] else None


def build_structure_gate(direction, price, setup_plan, k5, k15, k30, k1h, k4h=None):
    """V2 live-alert structure layer. Does not alter frozen V1.9 scores or paper labels."""
    trigger=float(setup_plan["trigger_level"])
    zones=[]
    for k,label,weight in ((k5,"5m",0.75),(k15,"15m",1.0),(k30,"30m",1.35),(k1h,"1h",1.75)):
        zones.extend(_timeframe_zones(k,label,weight,price))
    # V3: 4h is obstacle/context only. It contributes zones for room checks,
    # never a directional score.
    if k4h is not None:
        zones.extend(_timeframe_zones(k4h,"4h",2.25,price))

    desired="RESISTANCE" if direction=="LONG" else "SUPPORT"
    near_limit=max(trigger*STRUCTURE_LEVEL_NEAR_PCT/100.0,price*0.0015)
    near=[z for z in zones if z["side"]==desired and abs(float(z["center"])-trigger)<=near_limit]
    trigger_zone=max(near,key=lambda z:(z["strength"],-abs(z["center"]-trigger))) if near else None

    if trigger_zone:
        confluence_tfs={
            z["timeframe"] for z in zones
            if z["side"]==desired and abs(float(z["center"])-float(trigger_zone["center"]))<=max(float(z["tolerance"]),float(trigger_zone["tolerance"]))
        }
        confluence=len(confluence_tfs)
        level_strength=float(trigger_zone["strength"])
        touches=int(trigger_zone["touches"])
        level_ok=bool(touches>=2 or confluence>=2 or level_strength>=2.0)
    else:
        confluence=0; level_strength=0.0; touches=0; level_ok=False

    if direction=="LONG":
        opposing=[z for z in zones if z["side"]=="RESISTANCE" and float(z["low"])>max(price,trigger)*(1.0003)]
        opposing.sort(key=lambda z:float(z["low"]))
        next_zone=opposing[0] if opposing else None
        zone_room=((float(next_zone["low"])/price-1.0)*100.0) if next_zone else None
        target=float(setup_plan.get("target1") or 0.0)
        target_room=((target/price-1.0)*100.0) if target>price else 0.0
    else:
        opposing=[z for z in zones if z["side"]=="SUPPORT" and float(z["high"])<min(price,trigger)*(0.9997)]
        opposing.sort(key=lambda z:float(z["high"]),reverse=True)
        next_zone=opposing[0] if opposing else None
        zone_room=((price/float(next_zone["high"])-1.0)*100.0) if next_zone else None
        target=float(setup_plan.get("target1") or 0.0)
        target_room=((price/target-1.0)*100.0) if 0<target<price else 0.0

    room_candidates=[x for x in (zone_room,target_room) if x is not None and x>=0]
    room_pct=min(room_candidates) if room_candidates else 0.0
    required_room_pct=structure_required_room_pct()
    room_ok=bool(room_pct>=required_room_pct)
    net_t1_r=structure_net_t1_r(direction,trigger,setup_plan.get("invalidation"),setup_plan.get("target1"))
    rr_ok=bool(net_t1_r is not None and net_t1_r>=STRUCTURE_MIN_NET_T1_R)
    candle=_closed_candle_alignment(k5,direction,trigger)

    reasons=[]
    if level_ok:
        reasons.append(f"{desired.lower()} bölgesi tekrar/confluence ile doğrulandı")
    else:
        reasons.append("tetik seviyesi yeterli tekrar/confluence göstermiyor")
    if room_ok:
        reasons.append(f"sonraki engele alan var (%{room_pct:.2f}; min %{required_room_pct:.2f})")
    else:
        reasons.append(f"sonraki destek/direnç çok yakın (%{room_pct:.2f}; min %{required_room_pct:.2f})")
    if rr_ok:
        reasons.append(f"T1 maliyet sonrası R yeterli ({net_t1_r:.2f}R)")
    else:
        reasons.append("T1 maliyet sonrası R yetersiz")

    return {
        "version":STRUCTURE_GATE_VERSION,
        "qualified_precheck":bool((level_ok or setup_plan.get("setup_type")=="PULLBACK") and room_ok and rr_ok),
        "direction":direction,
        "trigger_level":trigger,
        "level_ok":level_ok,
        "level_touches":touches,
        "level_strength":level_strength,
        "timeframe_confluence":confluence,
        "room_ok":room_ok,
        "room_pct":room_pct,
        "required_room_pct":required_room_pct,
        "minimum_round_trip_cost_pct":structure_min_round_trip_cost_pct(),
        "net_t1_r":net_t1_r,
        "rr_ok":rr_ok,
        "trigger_zone":trigger_zone,
        "next_opposing_zone":next_zone,
        "last_closed_5m_alignment":candle,
        "thresholds":{
            "level_near_pct":STRUCTURE_LEVEL_NEAR_PCT,
            "min_room_floor_pct":STRUCTURE_MIN_ROOM_FLOOR_PCT,
            "min_room_cost_multiple":STRUCTURE_MIN_ROOM_COST_MULT,
            "validation_min_slippage_bps_per_side":VALIDATION_MIN_SLIPPAGE_BPS_PER_SIDE,
            "min_net_t1_r":STRUCTURE_MIN_NET_T1_R,
            "min_volume_mult":STRUCTURE_MIN_VOLUME_MULT,
            "min_body_ratio":STRUCTURE_MIN_BODY_RATIO,
            "max_rejection_wick_ratio":STRUCTURE_MAX_REJECTION_WICK,
        },
        "reasons":reasons,
    }


def build_setup_plan(direction, price, t5k, t15, chart):
    levels=swing_levels(t5k,48)
    a=max(t15["atr"],price*0.002)
    pad=max(0.12*a,price*0.001)
    if direction=="SHORT":
        trigger=levels["support"]
        retest_low=trigger
        retest_high=trigger+pad
        invalid=max(levels["resistance"], trigger+0.9*a)
        # Build targets from the actual stop risk, not a fixed ATR shortcut.
        # This prevents WATCH setups that can never pass the later net-R gate.
        risk_gap=max(invalid-trigger,1e-12)
        cost_gap=trigger*(structure_min_round_trip_cost_pct()/100.0)
        min_t1_gap=max(1.0*a,trigger*0.004,1.15*risk_gap+cost_gap)
        min_t2_gap=max(0.8*a,trigger*0.003,0.75*risk_gap)
        structural_t1=levels["support2"]
        target1=structural_t1 if structural_t1 <= trigger-min_t1_gap else trigger-min_t1_gap
        target2=min(target1-min_t2_gap, trigger-(min_t1_gap+min_t2_gap))
        candle="5dk"
        text=(
            f"{candle} mum {trigger:.10g} altında kapanırsa ve "
            f"{trigger:.10g}-{retest_high:.10g} retestinde tekrar reddedilirse SHORT değerlendirilebilir."
        )
        return {
            "direction":"SHORT","trigger_level":trigger,"close_tf":candle,
            "retest_low":retest_low,"retest_high":retest_high,
            "invalidation":invalid,"target1":target1,"target2":target2,
            "triggered":bool(chart["short_trigger"] and price<trigger),
            "instruction":text,
        }
    trigger=levels["resistance"]
    retest_low=trigger-pad
    retest_high=trigger
    invalid=min(levels["support"], trigger-0.9*a)
    # Build targets from the actual stop risk, not a fixed ATR shortcut.
    risk_gap=max(trigger-invalid,1e-12)
    cost_gap=trigger*(structure_min_round_trip_cost_pct()/100.0)
    min_t1_gap=max(1.0*a,trigger*0.004,1.15*risk_gap+cost_gap)
    min_t2_gap=max(0.8*a,trigger*0.003,0.75*risk_gap)
    structural_t1=levels["resistance2"]
    target1=structural_t1 if structural_t1 >= trigger+min_t1_gap else trigger+min_t1_gap
    target2=max(target1+min_t2_gap, trigger+(min_t1_gap+min_t2_gap))
    candle="5dk"
    text=(
        f"{candle} mum {trigger:.10g} üstünde kapanırsa ve "
        f"{retest_low:.10g}-{trigger:.10g} retestinde seviye korunursa LONG değerlendirilebilir."
    )
    return {
        "direction":"LONG","trigger_level":trigger,"close_tf":candle,
        "retest_low":retest_low,"retest_high":retest_high,
        "invalidation":invalid,"target1":target1,"target2":target2,
        "triggered":bool(chart["long_trigger"] and price>trigger),
        "instruction":text,
    }


def build_pullback_plan(direction, price, t5k, t15):
    """Micro-structure restart plan after a controlled pullback."""
    a=max(float(t15["atr"]),price*0.002)
    highs=[float(x) for x in t5k["high"]]
    lows=[float(x) for x in t5k["low"]]
    if direction=="LONG":
        trigger=max(highs[-4:-1]) if len(highs)>=4 else highs[-1]
        invalid=min(lows[-8:]) if lows else price-1.0*a
        if invalid>=trigger:
            invalid=trigger-1.0*a
        risk_gap=max(trigger-invalid,1e-12)
        cost_gap=trigger*(structure_min_round_trip_cost_pct()/100.0)
        min_t1_gap=max(1.4*a,1.15*risk_gap+cost_gap)
        target1=max(max(highs[-24:]),trigger+min_t1_gap)
        target2=max(target1+max(0.8*a,0.75*risk_gap),trigger+min_t1_gap+0.8*a)
        return {
            "setup_type":"PULLBACK","direction":"LONG","trigger_level":trigger,"close_tf":"5dk",
            "retest_low":trigger-0.30*a,"retest_high":trigger,
            "invalidation":invalid,"target1":target1,"target2":target2,
            "triggered":False,
            "instruction":"Kontrollü pullback sonrası mikro tepe kırılımı ve kabul beklenir.",
        }
    trigger=min(lows[-4:-1]) if len(lows)>=4 else lows[-1]
    invalid=max(highs[-8:]) if highs else price+1.0*a
    if invalid<=trigger:
        invalid=trigger+1.0*a
    risk_gap=max(invalid-trigger,1e-12)
    cost_gap=trigger*(structure_min_round_trip_cost_pct()/100.0)
    min_t1_gap=max(1.4*a,1.15*risk_gap+cost_gap)
    target1=min(min(lows[-24:]),trigger-min_t1_gap)
    target2=min(target1-max(0.8*a,0.75*risk_gap),trigger-min_t1_gap-0.8*a)
    return {
        "setup_type":"PULLBACK","direction":"SHORT","trigger_level":trigger,"close_tf":"5dk",
        "retest_low":trigger,"retest_high":trigger+0.30*a,
        "invalidation":invalid,"target1":target1,"target2":target2,
        "triggered":False,
        "instruction":"Kontrollü tepki sonrası mikro dip kırılımı ve kabul beklenir.",
    }


def build_reversal_plan(price, t5k, t5, t15, day_change_pct=0.0):
    """
    Separate reversal research engine. It does NOT predict the exact top/bottom.
    It watches for: liquidity sweep -> failed breakout close -> micro structure break
    -> retest -> 1m rejection. The live pool performs the sequential confirmation.
    """
    levels=swing_levels(t5k,48)
    a=max(t15["atr"],price*0.002)
    pad=max(0.18*a,price*0.0012)

    # Extension is only a discovery filter, never an entry trigger.
    short_ext=max(float(day_change_pct),0.0)*0.7 + max(t15["rsi"]-60.0,0.0)*0.45 + max(t15["change_4"],0.0)*1.2
    long_ext=max(-float(day_change_pct),0.0)*0.7 + max(40.0-t15["rsi"],0.0)*0.45 + max(-t15["change_4"],0.0)*1.2

    direction=None
    if short_ext>=6.0 and short_ext>=long_ext:
        direction="SHORT"
    elif long_ext>=6.0 and long_ext>short_ext:
        direction="LONG"
    else:
        return None

    if direction=="SHORT":
        sweep=float(levels["resistance"])
        micro=float(levels["support"])
        # A reversal short needs room between the swept high and the micro support.
        if sweep<=price*0.998 or micro>=sweep:
            return None
        retest_low=micro
        retest_high=micro+pad
        invalid=sweep+max(0.35*a,sweep*0.0015)
        t1=min(float(levels["support2"]), micro-max(0.8*a,micro*0.003))
        t2=min(t1-max(0.7*a,micro*0.0025), micro-2.0*a)
        score=min(99,int(round(short_ext*5.0)))
        return {
            "engine":"REVERSAL","direction":"SHORT","score":score,
            "sweep_level":sweep,"failed_close_level":sweep,
            "micro_break_level":micro,
            "retest_low":retest_low,"retest_high":retest_high,
            "invalidation":invalid,"target1":t1,"target2":t2,
            "why":"Yükseliş uzamış; eski tepe süpürülüp geri alınamazsa dönüş aranacak.",
            "entry_rule":"Tepe süpürmesi + 5dk tepe altı kapanış + mikro dip kırılımı + retest reddi + 1dk aşağı kapanış.",
        }

    sweep=float(levels["support"])
    micro=float(levels["resistance"])
    if sweep>=price*1.002 or micro<=sweep:
        return None
    retest_low=micro-pad
    retest_high=micro
    invalid=sweep-max(0.35*a,sweep*0.0015)
    t1=max(float(levels["resistance2"]), micro+max(0.8*a,micro*0.003))
    t2=max(t1+max(0.7*a,micro*0.0025), micro+2.0*a)
    score=min(99,int(round(long_ext*5.0)))
    return {
        "engine":"REVERSAL","direction":"LONG","score":score,
        "sweep_level":sweep,"failed_close_level":sweep,
        "micro_break_level":micro,
        "retest_low":retest_low,"retest_high":retest_high,
        "invalidation":invalid,"target1":t1,"target2":t2,
        "why":"Düşüş uzamış; eski dip süpürülüp geri alınırsa dönüş aranacak.",
        "entry_rule":"Dip süpürmesi + 5dk dip üstü kapanış + mikro tepe kırılımı + retest tutuşu + 1dk yukarı kapanış.",
    }


def chart_state(t1, t5, t15, t1h):
    """Deterministic price-action state. No LLM/AI scoring."""
    bullish_htf = t1h["price"] > t1h["ema20"] > t1h["ema50"]
    bearish_htf = t1h["price"] < t1h["ema20"] < t1h["ema50"]
    bullish_ltf = t5["structure"] > 0 and t5["above20"]
    bearish_ltf = t5["structure"] < 0 and not t5["above20"]

    if bullish_htf and bullish_ltf and t15["structure"] >= 0:
        state = "TRENDING_UP"
    elif bearish_htf and bearish_ltf and t15["structure"] <= 0:
        state = "TRENDING_DOWN"
    elif bullish_htf and t5["structure"] < 0:
        state = "UPTREND_PULLBACK"
    elif bearish_htf and t5["structure"] > 0:
        state = "DOWNTREND_BOUNCE"
    elif t15["breakout20"]:
        state = "BREAKOUT"
    elif t15["breakdown20"]:
        state = "BREAKDOWN"
    else:
        state = "RANGE_TRANSITION"

    short_trigger = (
        t5["structure"] < 0
        and t1["structure"] <= 0
        and not t5["above20"]
        and (t15["structure"] < 0 or t15["change_1"] < 0)
    )
    long_trigger = (
        t5["structure"] > 0
        and t1["structure"] >= 0
        and t5["above20"]
        and (t15["structure"] > 0 or t15["change_1"] > 0)
    )
    return {
        "state": state,
        "long_trigger": bool(long_trigger),
        "short_trigger": bool(short_trigger),
        "overextended_up": bool(t15["rsi"] >= 74 or t15["change_4"] >= 6.0),
        "overextended_down": bool(t15["rsi"] <= 26 or t15["change_4"] <= -6.0),
    }


def fetch_oi(symbol):
    if DATA_MODE!="BINANCE_FUTURES":
        return {"oi_change_1h":0.0,"oi_now":0.0}
    try:
        rows = fget("/futures/data/openInterestHist", {
            "symbol":symbol,"period":"5m","limit":13
        })
        if not rows:
            return {"oi_change_1h":None,"oi_now":None}
        # V3: use contract/base-unit OI, not quote-value OI. Quote-value OI
        # mechanically changes with price and can fake "new positioning".
        vals=[float(x.get("sumOpenInterest") or 0) for x in rows]
        vals=[x for x in vals if x>0]
        if len(vals)<2:
            return {"oi_change_1h":None,"oi_now":vals[-1] if vals else None}
        return {"oi_change_1h":pct(vals[0],vals[-1]),"oi_now":vals[-1]}
    except Exception:
        return {"oi_change_1h":None,"oi_now":None}


def fetch_funding(symbol):
    if DATA_MODE!="BINANCE_FUTURES":
        return 0.0
    try:
        x=fget("/fapi/v1/premiumIndex",{"symbol":symbol})
        return float(x.get("lastFundingRate") or 0.0) * 100.0
    except Exception:
        return None


def fetch_taker(symbol):
    if DATA_MODE!="BINANCE_FUTURES":
        return 1.0
    try:
        rows=fget("/futures/data/takerlongshortRatio",{
            "symbol":symbol,"period":"5m","limit":12
        })
        ratios=[float(x.get("buySellRatio") or 1.0) for x in rows]
        return mean(ratios[-6:]) if ratios else 1.0
    except Exception:
        return None


def fetch_long_short(symbol):
    if DATA_MODE!="BINANCE_FUTURES":
        return 1.0
    try:
        rows=fget("/futures/data/globalLongShortAccountRatio",{
            "symbol":symbol,"period":"5m","limit":12
        })
        if not rows: return 1.0
        return float(rows[-1].get("longShortRatio") or 1.0)
    except Exception:
        return None


def _book_vwap(rows, quote_notional, side):
    remain=float(quote_notional)
    base_qty=0.0
    spent=0.0
    for p,q in rows:
        px=float(p); qty=float(q)
        level_quote=px*qty
        take_quote=min(remain,level_quote)
        if take_quote<=0:
            continue
        base_qty += take_quote/px
        spent += take_quote
        remain -= take_quote
        if remain<=1e-9:
            break
    if remain>max(0.01,quote_notional*0.001) or base_qty<=0:
        return None
    return spent/base_qty


def _cached_multi_venue_derivatives(symbol):
    cached=MULTI_DERIV_CACHE.get(symbol)
    if cached is None:
        cached=multi_venue_derivatives(symbol)
        MULTI_DERIV_CACHE[symbol]=cached
    return cached


def fetch_depth_metrics(symbol):
    """Order-book imbalance plus executable-cost proxy.

    Native/Spot Binance depth is preferred. Futures-only symbols can be absent
    from Spot, so a failed Spot depth lookup falls back to the already fail-closed
    multi-venue derivatives bundle for imbalance only. We never invent spread or
    slippage costs from contract-size books whose units are not normalized.
    """
    try:
        d=fget("/fapi/v1/depth",{"symbol":symbol,"limit":100})
    except Exception as primary_exc:
        mv=_cached_multi_venue_derivatives(symbol)
        imbalance=mv.get("depth_imbalance")
        if imbalance is None:
            return {
                "source":"ORDERBOOK_UNAVAILABLE_V3_DIAGNOSTIC_ONLY",
                "imbalance":0.0,
                "spread_bps":None,
                "costs":{
                    str(int(n)):{"buy_bps":None,"sell_bps":None}
                    for n in (100.0,PAPER_NOTIONAL_USDT,500.0)
                },
                "binance_depth_error":type(primary_exc).__name__+":"+str(primary_exc)[:120],
            }
        return {
            "source":"EXTERNAL_PERP_DEPTH_"+str(mv.get("selected_provider") or "PARTIAL"),
            "imbalance":float(imbalance),
            "spread_bps":None,
            "costs":{
                str(int(n)):{"buy_bps":None,"sell_bps":None}
                for n in (100.0,PAPER_NOTIONAL_USDT,500.0)
            },
            "binance_depth_error":type(primary_exc).__name__+":"+str(primary_exc)[:120],
        }

    bids=d.get("bids",[]) or []
    asks=d.get("asks",[]) or []
    bid_notional=sum(float(p)*float(q) for p,q in bids[:20])
    ask_notional=sum(float(p)*float(q) for p,q in asks[:20])
    total=bid_notional+ask_notional
    imbalance=0.0 if total<=0 else (bid_notional-ask_notional)/total
    best_bid=float(bids[0][0]) if bids else 0.0
    best_ask=float(asks[0][0]) if asks else 0.0
    mid=(best_bid+best_ask)/2.0 if best_bid and best_ask else 0.0
    spread_bps=((best_ask-best_bid)/mid*10000.0) if mid else None
    sizes=(100.0,PAPER_NOTIONAL_USDT,500.0)
    costs={}
    for n in sizes:
        buy=_book_vwap(asks,n,"BUY")
        sell=_book_vwap(bids,n,"SELL")
        costs[str(int(n))]={
            "buy_bps":((buy/mid-1.0)*10000.0) if buy is not None and mid else None,
            "sell_bps":((1.0-sell/mid)*10000.0) if sell is not None and mid else None,
        }
    return {
        "source":"BINANCE_FUTURES_BOOK" if DATA_MODE=="BINANCE_FUTURES" else "BINANCE_SPOT_BOOK_PROXY",
        "imbalance":imbalance,
        "spread_bps":spread_bps,
        "costs":costs,
    }


def fetch_depth_imbalance(symbol):
    return float(fetch_depth_metrics(symbol)["imbalance"])


def universe():
    """Build a broad USDT-perpetual discovery universe.

    Discovery may go below the actionable liquidity floor so fast movers are not
    invisible. The hard MIN_24H_QUOTE_VOL gate is enforced later before a symbol
    can become WAIT/LONG/SHORT; lower-liquidity or unverified names are radar-only.
    """
    global DISCOVERY_META
    DISCOVERY_META={}

    info=fget("/fapi/v1/exchangeInfo")
    tick=fget("/fapi/v1/ticker/24hr")

    tradable={}
    for x in info.get("symbols",[]):
        if x.get("quoteAsset")!="USDT" or x.get("status")!="TRADING":
            continue
        if DATA_MODE=="BINANCE_FUTURES" and x.get("contractType")!="PERPETUAL":
            continue
        base=x.get("baseAsset","")
        if base in EXCLUDED_BASES or any(base.endswith(m) for m in EXCLUDED_MARKERS):
            continue
        tradable[x["symbol"]]=base

    if DATA_MODE=="BINANCE_FUTURES":
        eligible=[]
        for x in tick:
            sym=x.get("symbol")
            if sym not in tradable:
                continue
            qv=float(x.get("quoteVolume") or 0)
            if qv < DISCOVERY_MIN_24H_QUOTE_VOL:
                continue
            DISCOVERY_META[sym]={
                "source":"BINANCE_FUTURES_NATIVE",
                "binance_native_verified":True,
                "binance_spot_member":True,
                "external_only_unverified":False,
                "provider_count":1,
            }
            eligible.append((
                sym, qv,
                float(x.get("lastPrice") or 0),
                float(x.get("priceChangePercent") or 0),
            ))
    else:
        spot_stats={}
        for x in tick:
            sym=x.get("symbol")
            if sym not in tradable:
                continue
            try:
                spot_stats[sym]=(
                    float(x.get("quoteVolume") or 0),
                    float(x.get("lastPrice") or 0),
                    float(x.get("priceChangePercent") or 0),
                )
            except (TypeError,ValueError):
                continue

        merged={}
        try:
            perp_rows=multi_venue_perp_universe()
        except Exception as exc:
            print("PERP_DISCOVERY_FALLBACK_ERROR",type(exc).__name__,str(exc)[:160])
            perp_rows=[]

        for x in perp_rows:
            sym=str(x.get("symbol") or "")
            base=sym[:-4] if sym.endswith("USDT") else sym
            if not sym.endswith("USDT"):
                continue
            if base in EXCLUDED_BASES or any(base.endswith(m) for m in EXCLUDED_MARKERS):
                continue
            qv=float(x.get("quote_volume") or 0.0)
            if qv < DISCOVERY_MIN_24H_QUOTE_VOL:
                continue
            providers=list(x.get("providers") or [])
            spot_member=sym in tradable
            external_only_unverified=bool(not spot_member and len(providers)<2)
            merged[sym]=(
                qv,
                float(x.get("last_price") or 0.0),
                float(x.get("day_change_pct") or 0.0),
            )
            DISCOVERY_META[sym]={
                "source":"MULTI_VENUE_PERP",
                "binance_native_verified":False,
                "binance_spot_member":bool(spot_member),
                "external_only_unverified":external_only_unverified,
                "provider_count":len(providers),
                "providers":providers,
            }

        for sym,(qv,px,ch) in spot_stats.items():
            if sym in merged or qv < DISCOVERY_MIN_24H_QUOTE_VOL:
                continue
            merged[sym]=(qv,px,ch)
            DISCOVERY_META[sym]={
                "source":"BINANCE_SPOT_BACKUP",
                "binance_native_verified":False,
                "binance_spot_member":True,
                "external_only_unverified":False,
                "provider_count":0,
                "providers":[],
            }

        eligible=[(sym,qv,px,ch) for sym,(qv,px,ch) in merged.items()]
        liquid=sum(1 for _,qv,_,_ in eligible if qv>=MIN_24H_QUOTE_VOL)
        unverified=sum(
            1 for sym,_,_,_ in eligible
            if bool((DISCOVERY_META.get(sym) or {}).get("external_only_unverified"))
        )
        print(
            "DISCOVERY_MODE MULTI_VENUE_PERP_PLUS_BINANCE_SPOT",
            "spot=",len(spot_stats),"perp=",len(perp_rows),
            "eligible=",len(eligible),"actionable_liquid=",liquid,
            "radar_unverified=",unverified,
        )

    # V3: universe admission must NOT reward movement. Absolute 24h movers
    # were the main source of late/chase selection in V1/V2. Liquidity only
    # determines which symbols are cheap/safe enough to inspect; pre-move
    # compression/readiness is ranked later by v3_discovery_rank().
    # Research-only full eligible universe snapshot; no trade/Telegram effect.
    # Rotate the limited candle-prefilter budget so the same 30 liquid symbols
    # do not monopolize every scan. Every eligible symbol is included in the
    # lightweight 24h snapshot, even when not selected for candle analysis.
    all_eligible=sorted(eligible,key=lambda z:(-float(z[1]),str(z[0])))
    try:
        audit_30m_coverage(now_iso(),all_eligible)
    except Exception as exc:
        print("OPPORTUNITY_30M_AUDIT_ERROR",type(exc).__name__,str(exc)[:180],flush=True)

    try:
        from pathlib import Path
        state=Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
        state.mkdir(parents=True,exist_ok=True)
        stamp=now_iso()
        with (state/"full_universe_research.jsonl").open("a",encoding="utf-8") as out:
            for sym,qv,px,ch in all_eligible:
                out.write(json.dumps({"timestamp_utc":stamp,"symbol":sym,
                    "quote_volume_24h":qv,"price":px,"change_24h_pct":ch,
                    "research_only":True,"metadata":DISCOVERY_META.get(sym,{})},
                    ensure_ascii=False,default=str)+"\n")
        print("FULL_UNIVERSE_RESEARCH",len(all_eligible),flush=True)
        from long_short_full_universe_forward import track as track_full_universe
        track_full_universe(all_eligible)
        # Resolve previously observed early-watch signals even after a coin
        # disappears from the rotating top-30 shortlist. Price-only rows
        # never create new signals and do not affect trade eligibility.
        from long_short_early_forward import track as track_early_forward
        track_early_forward([
            {"symbol":sym,"price":px,"direction":"NONE",
             "state":"PRICE_ONLY","deep_selected":False}
            for sym,qv,px,ch in all_eligible
        ])
    except Exception as exc:
        print("FULL_UNIVERSE_RESEARCH_ERROR",type(exc).__name__,str(exc)[:160],flush=True)
    cap=max(1,int(MAX_SYMBOLS))
    if len(all_eligible)<=cap:
        return all_eligible
    # Preserve the frozen liquidity/venue gates, but prevent a rotation slot
    # from containing only research-only symbols when actionable ones exist.
    # Both cohorts still rotate; the deep shortlist ranks by readiness later.
    slot=int(time.time()//300)
    actionable=[row for row in all_eligible if
                float(row[1])>=MIN_24H_QUOTE_VOL
                and bool((DISCOVERY_META.get(row[0]) or {}).get("binance_spot_member"))
                and not bool((DISCOVERY_META.get(row[0]) or {}).get("external_only_unverified"))]
    research=[row for row in all_eligible if row not in actionable]
    def rotate(rows,n):
        if not rows or n<=0: return []
        start=(slot*n)%len(rows)
        return (rows+rows)[start:start+min(n,len(rows))]
    actionable_slots=min(cap,len(actionable))
    selected_actionable=rotate(actionable,actionable_slots)
    selected_research=rotate(research,cap-len(selected_actionable))
    rotated=selected_actionable+selected_research
    print("ROTATING_PREFILTER",len(all_eligible),"->",len(rotated),
          "slot",slot,"actionable",len(selected_actionable),
          "research",len(selected_research),flush=True)
    return rotated


def audit_30m_coverage(ts, all_eligible):
    """Research-only 30-minute opportunity coverage audit.

    Compares full-universe ticker snapshots with the scan selection history.
    Does not infer actual executable returns or modify trading decisions.
    """
    now=datetime.fromisoformat(ts)
    previous=(now-timedelta(minutes=30)).isoformat()
    count={"NOT_PREFILTERED":0,"PREFILTERED_NOT_DEEP":0,
           "DEEP_ANALYZED":0,"PREFILTER_DATA_MISSING":0}
    moves=0
    with sqlite3.connect(DB,timeout=15) as con:
        for sym,qv,price,ch in all_eligible:
            if float(price)<=0: continue
            prior=con.execute("""SELECT scan_time_utc,price FROM opportunity_price_snapshots
                WHERE symbol=? AND scan_time_utc<=? ORDER BY scan_time_utc DESC LIMIT 1""",
                (sym,previous)).fetchone()
            if prior and float(prior[1])>0:
                move=(float(price)/float(prior[1])-1)*100
                if abs(move)>=3:
                    moves+=1
                    observed=con.execute("""SELECT shortlisted FROM universe_observations
                        WHERE symbol=? AND scan_time_utc>? AND scan_time_utc<=?
                        ORDER BY shortlisted DESC LIMIT 1""",
                        (sym,prior[0],ts)).fetchone()
                    if observed is None:
                        classification="NOT_PREFILTERED"
                    elif not observed[0]:
                        classification="PREFILTERED_NOT_DEEP"
                    else:
                        analyzed=con.execute("""SELECT 1 FROM analyses
                            WHERE symbol=? AND scan_time_utc>? AND scan_time_utc<=?
                            LIMIT 1""",(sym,prior[0],ts)).fetchone()
                        classification="DEEP_ANALYZED" if analyzed else "PREFILTER_DATA_MISSING"
                    count[classification]+=1
                    con.execute("""INSERT OR IGNORE INTO opportunity_30m_audit
                        (scan_time_utc,symbol,prior_time_utc,prior_price,current_price,
                         move_pct,classification,detail) VALUES(?,?,?,?,?,?,?,?)""",
                        (ts,sym,prior[0],float(prior[1]),float(price),move,
                         classification,"absolute 30m ticker movement >=3%; observational"))
            con.execute("""INSERT OR IGNORE INTO opportunity_price_snapshots
                (scan_time_utc,symbol,price) VALUES(?,?,?)""",(ts,sym,float(price)))
        con.execute("""DELETE FROM opportunity_price_snapshots
            WHERE scan_time_utc<?""",((now-timedelta(days=10)).isoformat(),))
        con.commit()
    print("OPPORTUNITY_30M_AUDIT",json.dumps({"movers_3pct":moves,
          "coverage":count,"universe":len(all_eligible)},ensure_ascii=False),flush=True)


def fetch_htf_cached(symbol, interval, limit=220):
    """Persist 1h/4h candles across runs so slow higher-timeframe data is not refetched every 5m."""
    ttl = HTF_CACHE_TTL_1D if interval=="1d" else (HTF_CACHE_TTL_4H if interval=="4h" else HTF_CACHE_TTL_1H)
    now_ts = time.time()
    try:
        with sqlite3.connect(DB) as con:
            row=con.execute(
                "SELECT fetched_at_epoch,payload_json FROM htf_cache WHERE symbol=? AND interval=?",
                (symbol,interval)
            ).fetchone()
            if row and now_ts-float(row[0]) <= ttl:
                return json.loads(row[1])
    except Exception:
        pass
    data=fetch_klines(symbol,interval,limit)
    try:
        with sqlite3.connect(DB) as con:
            con.execute("""INSERT OR REPLACE INTO htf_cache(symbol,interval,fetched_at_epoch,payload_json)
                           VALUES(?,?,?,?)""",
                        (symbol,interval,now_ts,json.dumps(data,separators=(",",":"))))
    except Exception:
        pass
    return data


def prefilter_symbol(symbol, day_change_pct, quote_volume=0.0):
    """V3 discovery: rank pre-move readiness, never absolute mover magnitude."""
    k5=fetch_klines(symbol,"5m",120)
    k15=fetch_klines(symbol,"15m",120)
    t5=timeframe_features(k5)
    t15=timeframe_features(k15)
    levels=swing_levels(k5,48)
    rank,v3_discovery=v3_discovery_rank(k5,k15,t5,t15,day_change_pct,levels)
    return {
        "symbol":symbol,"day_change":day_change_pct,"quote_volume":float(quote_volume or 0.0),"rank":rank,
        "discovery_meta":dict(DISCOVERY_META.get(symbol) or {}),
        "v3_discovery":v3_discovery,
        "k5":k5,"t5":t5,"k15":k15,"t15":t15,
    }


def select_deep_shortlist(preselected, limit=PRESELECT_MAX):
    """V3 shortlist: spend deep-scan capacity on actually executable candidates first.

    The 8M discovery floor is useful for radar/research, but a low-liquidity or
    no-Binance-Spot symbol cannot pass V3 execution/spot-flow gates. Such names
    must never crowd a >=25M, real-Spot candidate out of the expensive shortlist.
    Within each cohort, V3 pre-move readiness rank decides ordering.
    """
    n=max(0,min(int(limit),len(preselected)))
    def rank_key(x):
        return (-float(x.get("rank") or 0.0),str(x.get("symbol") or ""))
    actionable=[]
    research=[]
    for x in preselected:
        meta=dict(x.get("discovery_meta") or {})
        can_execute=bool(
            float(x.get("quote_volume") or 0.0)>=MIN_24H_QUOTE_VOL
            and meta.get("binance_spot_member")
            and not meta.get("external_only_unverified")
        )
        (actionable if can_execute else research).append(x)
    actionable.sort(key=rank_key)
    research.sort(key=rank_key)
    selected=(actionable+research)[:n]
    # Observability only: no changes to frozen ranking or execution gates.
    try:
        from long_short_shortlist_audit import record_shortlist
        record_shortlist(preselected, selected, limit=n, volume_floor=MIN_24H_QUOTE_VOL)
    except Exception as exc:
        print("SHORTLIST_AUDIT_IMPORT_ERROR",type(exc).__name__,str(exc)[:120],flush=True)
    return selected


def build_htf_gate(symbol, t4h):
    """Observational 1D/4H context gate for the early-entry layer.

    It does not alter the frozen Long/Short score or paper-trade rules. Its job is
    only to decide whether a micro 1m/5m early trigger sits inside a meaningful
    higher-timeframe breakout / near-breakout structure.
    """
    try:
        kd=fetch_htf_cached(symbol,"1d",220)
        td=timeframe_features(kd)
    except Exception as exc:
        # A fresh Futures listing can have enough 1m/5m/4h history to trade but
        # fewer than 20 daily candles. Do not crash the whole deep scan; only the
        # observational early HTF layer is unavailable until more history exists.
        return {
            "qualified":False,
            "direction":"NONE",
            "score":0,
            "long_score":0,
            "short_score":0,
            "extended":False,
            "near_daily_high":False,
            "near_daily_low":False,
            "daily_breakout20":False,
            "daily_breakdown20":False,
            "daily_change_pct":0.0,
            "daily_volume_mult":0.0,
            "reasons":[],
            "daily":None,
            "unavailable":True,
            "error":f"{type(exc).__name__}:{str(exc)[:160]}",
        }

    near_high = 0.0 <= float(td["dist_high20_pct"]) <= HTF_NEAR_LEVEL_PCT
    near_low = 0.0 <= float(td["dist_low20_pct"]) <= HTF_NEAR_LEVEL_PCT
    daily_bull = td["price"] > td["ema20"] > td["ema50"]
    daily_bear = td["price"] < td["ema20"] < td["ema50"]
    h4_bull = t4h["price"] > t4h["ema20"] > t4h["ema50"]
    h4_bear = t4h["price"] < t4h["ema20"] < t4h["ema50"]

    long_score=0
    short_score=0
    reasons_long=[]
    reasons_short=[]

    if td["breakout20"]:
        long_score+=3; reasons_long.append("1D son 20 gün tepesini kırıyor")
    elif near_high:
        long_score+=2; reasons_long.append("1D eski tepeye çok yakın")
    if td["breakdown20"]:
        short_score+=3; reasons_short.append("1D son 20 gün dibini kırıyor")
    elif near_low:
        short_score+=2; reasons_short.append("1D eski dibe çok yakın")

    if daily_bull:
        long_score+=2; reasons_long.append("1D trend yukarı")
    if daily_bear:
        short_score+=2; reasons_short.append("1D trend aşağı")
    if td["structure"]>0:
        long_score+=1
    elif td["structure"]<0:
        short_score+=1

    if t4h["breakout20"]:
        long_score+=2; reasons_long.append("4s yapı kırılımı")
    if t4h["breakdown20"]:
        short_score+=2; reasons_short.append("4s aşağı kırılım")
    if h4_bull:
        long_score+=2; reasons_long.append("4s trend yukarı")
    if h4_bear:
        short_score+=2; reasons_short.append("4s trend aşağı")

    if td["vol_mult"]>=1.15:
        if td["change_1"]>=0:
            long_score+=1; reasons_long.append("1D hacim normalin üstünde")
        else:
            short_score+=1; reasons_short.append("1D hacim normalin üstünde")

    # Do not call a higher-timeframe move "early" after a large daily / 4h extension.
    long_extended = td["change_1"]>=6.0 or t4h["change_4"]>=5.0
    short_extended = td["change_1"]<=-6.0 or t4h["change_4"]<=-5.0

    direction="NONE"
    score=0
    reasons=[]
    extended=False
    if long_score>=HTF_GATE_MIN_SCORE and long_score>=short_score+2:
        direction="LONG"; score=long_score; reasons=reasons_long; extended=long_extended
    elif short_score>=HTF_GATE_MIN_SCORE and short_score>=long_score+2:
        direction="SHORT"; score=short_score; reasons=reasons_short; extended=short_extended

    return {
        "qualified":bool(direction!="NONE" and not extended),
        "direction":direction,
        "score":score,
        "long_score":long_score,
        "short_score":short_score,
        "extended":bool(extended),
        "near_daily_high":bool(near_high),
        "near_daily_low":bool(near_low),
        "daily_breakout20":bool(td["breakout20"]),
        "daily_breakdown20":bool(td["breakdown20"]),
        "daily_change_pct":float(td["change_1"]),
        "daily_volume_mult":float(td["vol_mult"]),
        "reasons":reasons[:5],
        "daily":td,
    }


def btc_regime():
    k=fetch_htf_cached("BTCUSDT","1h",220)
    f=timeframe_features(k)
    if f["price"]>f["ema20"]>f["ema50"] and f["structure"]>=0:
        return "UP"
    if f["price"]<f["ema20"]<f["ema50"] and f["structure"]<=0:
        return "DOWN"
    return "SIDEWAYS"


@dataclass
class Analysis:
    symbol: str
    status: str
    long_score: int
    short_score: int
    confidence: int
    price: float
    entry_low: float | None
    entry_high: float | None
    stop: float | None
    tp1: float | None
    tp2: float | None
    rr1: float | None
    reasons: list[str]
    risks: list[str]
    payload: dict[str,Any]


def score_symbol(symbol, market_regime, day_change_pct=0.0, pre=None):
    t1=timeframe_features(fetch_klines(symbol,"1m",120))
    if pre:
        k5=pre["k5"]; t5=pre["t5"]; k15=pre["k15"]; t15=pre["t15"]
    else:
        k5=fetch_klines(symbol,"5m",220)
        t5=timeframe_features(k5)
        k15=fetch_klines(symbol,"15m",220)
        t15=timeframe_features(k15)
    k30=fetch_klines(symbol,"30m",160)
    k1h=fetch_htf_cached(symbol,"1h",220)
    k4h=fetch_htf_cached(symbol,"4h",220)
    t1h=timeframe_features(k1h)
    t4h=timeframe_features(k4h)
    htf_gate=build_htf_gate(symbol,t4h)
    oi_raw=fetch_oi(symbol)
    funding_raw=fetch_funding(symbol)
    taker_raw=fetch_taker(symbol)
    ls_raw=fetch_long_short(symbol)
    try:
        depth_metrics=fetch_depth_metrics(symbol)
        depth=float(depth_metrics.get("imbalance") or 0.0)
    except Exception as exc:
        depth_metrics={
            "source":"UNAVAILABLE_DIAGNOSTIC_ONLY","imbalance":0.0,"spread_bps":None,
            "costs":{},"error":f"{type(exc).__name__}:{str(exc)[:120]}",
        }
        depth=0.0
    chart=chart_state(t1,t5,t15,t1h)

    # V3 native derivatives fail closed: a failed API field is missing, not a
    # fabricated neutral zero/one. Order-book depth is diagnostic only.
    native_ready=bool(
        DATA_MODE=="BINANCE_FUTURES"
        and oi_raw.get("oi_change_1h") is not None
        and funding_raw is not None
        and taker_raw is not None
        and ls_raw is not None
    )
    oi={
        "oi_change_1h":float(oi_raw.get("oi_change_1h") or 0.0),
        "oi_now":float(oi_raw.get("oi_now") or 0.0),
    }
    funding=float(funding_raw) if funding_raw is not None else 0.0
    taker=float(taker_raw) if taker_raw is not None else 1.0
    ls=float(ls_raw) if ls_raw is not None else 1.0

    # Data-integrity rule: never let missing Futures fields silently turn into
    # actionable neutral values. Fallback must be a coherent single-venue bundle.
    multi_deriv=None
    cross=None
    deriv_ready=native_ready
    if DATA_MODE=="BINANCE_FUTURES" and not native_ready:
        risks_native=[
            k for k,v in {
                "oi_change_1h":oi_raw.get("oi_change_1h"),
                "funding_pct":funding_raw,
                "taker_ratio":taker_raw,
                "long_short_ratio":ls_raw,
            }.items() if v is None
        ]
    else:
        risks_native=[]

    if DATA_MODE!="BINANCE_FUTURES":
        multi_deriv=_cached_multi_venue_derivatives(symbol)
        cross=(multi_deriv.get("sources") or {}).get("okx")
        if bool(multi_deriv.get("v3_core_ready")) and bool(multi_deriv.get("source_consistent")):
            oi={
                "oi_change_1h":float(multi_deriv["oi_change_1h"]),
                "oi_now":float(multi_deriv.get("oi_now") or 0.0),
            }
            funding=float(multi_deriv["funding_pct"])
            taker=float(multi_deriv["taker_ratio"])
            ls=float(multi_deriv["long_short_ratio"])
            depth=float(multi_deriv.get("depth_imbalance") or 0.0)
            deriv_ready=True
            mark=multi_deriv.get("mark_price")
            spot_px=float(t5["price"] or 0.0)
            if mark is not None and spot_px>0:
                spot_mark_basis=100.0*(float(mark)/spot_px-1.0)
                multi_deriv["spot_vs_selected_mark_basis_pct"]=spot_mark_basis
                if abs(spot_mark_basis)>MAX_CROSS_VENUE_BASIS_PCT:
                    deriv_ready=False
                    multi_deriv["quality"]="BASIS_MISMATCH"

    long=short=0
    reasons=[]; risks=[]
    if risks_native:
        risks.append("Native türev veri eksik; V3 giriş kilitli: "+",".join(risks_native))
    components={}

    # Higher timeframe trend: max 28 points each side.
    _l0,_s0=long,short
    for tf,name,w in ((t4h,"4s",12),(t1h,"1s",10),(t15,"15dk",6)):
        if tf["price"]>tf["ema20"]>tf["ema50"]:
            long += w; reasons.append(f"{name} trend yukarı")
        elif tf["price"]<tf["ema20"]<tf["ema50"]:
            short += w; reasons.append(f"{name} trend aşağı")
    components["trend"]={"long":long-_l0,"short":short-_s0}

    # Structure / breakout + micro chart state.
    _l0,_s0=long,short
    if t15["structure"]>0: long+=7
    if t15["structure"]<0: short+=7
    if t1h["structure"]>0: long+=6
    if t1h["structure"]<0: short+=6
    if t15["breakout20"] and t15["vol_mult"]>=1.25:
        long+=5; reasons.append("15dk hacimli kırılım")
    if t15["breakdown20"] and t15["vol_mult"]>=1.25:
        short+=5; reasons.append("15dk hacimli aşağı kırılım")

    if chart["long_trigger"]:
        long+=10; reasons.append("Grafik tetikleyicisi: 1dk+5dk yapı LONG lehine")
    if chart["short_trigger"]:
        short+=10; reasons.append("Grafik tetikleyicisi: 1dk+5dk yapı SHORT lehine")
    if chart["overextended_up"]:
        long-=5; risks.append("Grafik yukarı aşırı uzamış; long kovalamak yerine pullback/retest bekle")
    if chart["overextended_down"]:
        short-=5; risks.append("Grafik aşağı aşırı uzamış; short kovalamak yerine tepki/retest bekle")
    components["structure_chart"]={"long":long-_l0,"short":short-_s0}

    # Momentum / RSI: avoid chasing extremes.
    _l0,_s0=long,short
    if 52 <= t15["rsi"] <= 68 and t5["rsi"]>=50: long+=8
    if 32 <= t15["rsi"] <= 48 and t5["rsi"]<=50: short+=8
    if t15["rsi"]>=76:
        long-=6; risks.append("15dk RSI aşırı yüksek; long kovalamak riskli")
    if t15["rsi"]<=24:
        short-=6; risks.append("15dk RSI aşırı düşük; short kovalamak riskli")
    components["momentum"]={"long":long-_l0,"short":short-_s0}

    # Derivatives. Native Binance Futures is preferred. If it is blocked,
    # a complete Bybit/OKX public bundle is allowed, with provenance preserved.
    price1h=t1h["change_1"]
    deriv_source="BINANCE_FUTURES" if DATA_MODE=="BINANCE_FUTURES" else (
        "MULTI_VENUE_PUBLIC" if deriv_ready else "DERIVATIVES_INCOMPLETE"
    )
    deriv_funding=funding if deriv_ready else None
    deriv_basis=(multi_deriv or {}).get("basis_pct") if DATA_MODE!="BINANCE_FUTURES" else None

    if DATA_MODE!="BINANCE_FUTURES":
        coverage=float((multi_deriv or {}).get("coverage") or 0.0)
        quality=str((multi_deriv or {}).get("quality") or "UNAVAILABLE")
        if deriv_ready:
            provider=(multi_deriv or {}).get("selected_provider") or "MULTI_VENUE"
            age=(multi_deriv or {}).get("source_age_seconds")
            age_txt=f", yaş {float(age):.0f}s" if age is not None else ""
            reasons.append(f"Türev veri tam: {provider} %{coverage*100:.0f}{age_txt}")
        else:
            missing=[
                k for k in ((multi_deriv or {}).get("critical_fields") or [])
                if (multi_deriv or {}).get(k) is None
            ]
            risks.append(
                "Türev veri eksik; sinyal kilitli"
                + (": " + ",".join(missing) if missing else "")
            )

    _l0,_s0=long,short
    if deriv_ready:
        oic=oi["oi_change_1h"]
        if price1h>0 and oic>1.5:
            long+=8; reasons.append(f"Fiyat↑ + OI↑ ({oic:+.1f}%): yeni kaldıraçlı talep")
        elif price1h<0 and oic>1.5:
            short+=8; reasons.append(f"Fiyat↓ + OI↑ ({oic:+.1f}%): yeni short baskısı")
        elif price1h>0 and oic<-1.5:
            long+=3; risks.append("Fiyat↑ + OI↓: short squeeze olabilir; devam teyidi zayıf")
        elif price1h<0 and oic<-1.5:
            short+=3; risks.append("Fiyat↓ + OI↓: long tasfiyesi olabilir; devam teyidi zayıf")

        if taker>=1.08:
            long+=7; reasons.append(f"Taker alım üstün ({taker:.2f}x)")
        elif taker<=0.92:
            short+=7; reasons.append(f"Taker satış üstün ({taker:.2f}x)")

        if depth>=0.08:
            long+=5; reasons.append(f"Perp order-book bid üstün ({depth:+.2f})")
        elif depth<=-0.08:
            short+=5; reasons.append(f"Perp order-book ask üstün ({depth:+.2f})")

        # Crowding/funding is a modifier, never a standalone direction signal.
        if funding>=0.05:
            long-=4; short+=2; risks.append(f"Funding yüksek pozitif %{funding:.3f}; long kalabalık olabilir")
        elif funding<=-0.05:
            short-=4; long+=2; risks.append(f"Funding yüksek negatif %{funding:.3f}; short kalabalık olabilir")
        if ls>=2.0:
            long-=3; risks.append(f"Long/short oranı {ls:.2f}; long tarafı kalabalık")
        elif ls<=0.5:
            short-=3; risks.append(f"Long/short oranı {ls:.2f}; short tarafı kalabalık")

        if DATA_MODE!="BINANCE_FUTURES" and deriv_basis is not None and abs(deriv_basis)>=0.15:
            risks.append(f"Çapraz-venue basis %{deriv_basis:+.2f}; spot-perp ayrışması yüksek")
    components["derivatives"]={"long":long-_l0,"short":short-_s0}

    _l0,_s0=long,short
    if market_regime=="UP": long+=4; short-=2
    elif market_regime=="DOWN": short+=4; long-=2
    components["btc_regime"]={"long":long-_l0,"short":short-_s0}

    # Multi-timeframe veto: do not fight both 4h and 1h trend.
    _l0,_s0=long,short
    htf_bull = t4h["price"]>t4h["ema20"]>t4h["ema50"] and t1h["price"]>t1h["ema20"]>t1h["ema50"]
    htf_bear = t4h["price"]<t4h["ema20"]<t4h["ema50"] and t1h["price"]<t1h["ema20"]<t1h["ema50"]
    if htf_bull:
        short=max(0,short-12); risks.append("4s+1s ana trend yukarı; karşı-trend SHORT ağır cezalı")
    elif htf_bear:
        long=max(0,long-12); risks.append("4s+1s ana trend aşağı; karşı-trend LONG ağır cezalı")
    components["htf_veto"]={"long":long-_l0,"short":short-_s0}

    raw_long=float(long); raw_short=float(short)
    long=max(0,min(100,int(round(long))))
    short=max(0,min(100,int(round(short))))
    edge=abs(long-short)
    best=max(long,short)

    if best>=55 and edge>=12:
        direction="LONG" if long>short else "SHORT"
        trigger_ok = chart["long_trigger"] if direction=="LONG" else chart["short_trigger"]
        status=direction if trigger_ok else "WAIT"
        if not trigger_ok:
            risks.append(direction + " yönü güçlü ama grafik giriş tetikleyicisi henüz oluşmadı")
    elif best>=42:
        status="WAIT"
    else:
        status="NO_TRADE"

    # Research-only counterfactuals. These do NOT change the live decision.
    # They allow forward tests to answer whether each layer adds edge or only
    # delays/filters good moves.
    def _decision_from_scores(ll,ss):
        ll=max(0,min(100,int(round(ll))))
        ss=max(0,min(100,int(round(ss))))
        ee=abs(ll-ss); bb=max(ll,ss)
        if bb<55 or ee<12:
            return "WAIT"
        dd="LONG" if ll>ss else "SHORT"
        trig=chart["long_trigger"] if dd=="LONG" else chart["short_trigger"]
        return dd if trig else "WAIT"

    ablations={}
    for cname,delta in components.items():
        ll=raw_long-float(delta.get("long",0.0))
        ss=raw_short-float(delta.get("short",0.0))
        ablations[cname]={
            "long_score":max(0,min(100,int(round(ll)))),
            "short_score":max(0,min(100,int(round(ss)))),
            "decision":_decision_from_scores(ll,ss),
        }

    # Discovery is intentionally broader than the actionable universe. Preserve
    # the original 25M USDT liquidity safety floor for real WAIT/LONG/SHORT setups,
    # while keeping thinner or single-venue external movers as radar-only data.
    pre_qv=float((pre or {}).get("quote_volume") or 0.0)
    discovery_meta=dict((pre or {}).get("discovery_meta") or {})
    actionable_liquidity_ok=bool(pre is None or pre_qv>=MIN_24H_QUOTE_VOL)
    external_only_unverified=bool(discovery_meta.get("external_only_unverified"))
    if not actionable_liquidity_ok:
        risks.append(
            f"24s {discovery_meta.get('source') or 'UNKNOWN'} kaynak hacmi {pre_qv/1_000_000:.1f}M USDT; "
            f"işlem için minimum {MIN_24H_QUOTE_VOL/1_000_000:.0f}M, sadece radar"
        )
        status="NO_TRADE"
    if external_only_unverified:
        risks.append("Binance üyeliği doğrulanmadı; tek dış perp venue, sadece radar")
        status="NO_TRADE"

    price=t5["price"]
    a=max(t15["atr"], price*0.002)
    if status=="LONG":
        entry_low=price-0.25*a; entry_high=price+0.10*a
        stop=price-1.35*a; tp1=price+2.0*a; tp2=price+3.2*a
    elif status=="SHORT":
        entry_low=price-0.10*a; entry_high=price+0.25*a
        stop=price+1.35*a; tp1=price-2.0*a; tp2=price-3.2*a
    else:
        entry_low=entry_high=stop=tp1=tp2=None
    rr1=None
    if stop is not None and tp1 is not None:
        risk=abs(price-stop)
        rr1=abs(tp1-price)/risk if risk else None

    # Data-health safety gate: chart-only or partial derivatives data can watch,
    # but only native Binance Futures or a complete multi-venue bundle can issue
    # an entry-qualified paper signal.
    if not deriv_ready and status in ("LONG","SHORT"):
        risks.append("Türev veri paketi tam değil; giriş sinyali kilitli")
        status="WAIT"

    # Volatility risk gate. High-ATR names stay visible through the radar layer
    # but cannot silently become actionable merely because the live watcher sees
    # a later candle close.
    if t15["atr_pct"]>=4.0:
        risks.append(f"15dk ATR %{t15['atr_pct']:.1f}; aşırı oynaklık, sadece radar")
        if status in ("WAIT","LONG","SHORT"):
            status="NO_TRADE"

    # ---- Production direction + separate entry layer ----------------------
    # V3.1 hard-gate logic is preserved as SHADOW diagnostics. Production
    # direction now comes from combined evidence: trend/structure/momentum/
    # derivatives/BTC context plus soft Spot-flow and beta-residual evidence.
    # A single disagreeing soft feature must not erase the full thesis.
    btc1h=fetch_htf_cached("BTCUSDT","1h",220)
    residual=beta_residual_3h(k1h,btc1h)
    btc_tf=timeframe_features(btc1h)
    btc_atr_pct=max(float(btc_tf.get("atr_pct") or 0.0),1e-9)
    residual["btc_1h_change_pct"]=float(btc_tf.get("change_1") or 0.0)
    residual["btc_1h_atr_pct"]=btc_atr_pct
    residual["btc_shock_atr"]=abs(residual["btc_1h_change_pct"])/btc_atr_pct
    spot_flow=spot_delta_proxy(symbol)

    v3_hard_gate_shadow=v3_decide_setup(
        symbol=symbol,k5=k5,k15=k15,k1h=k1h,t5=t5,t15=t15,t1h=t1h,
        levels=swing_levels(k5,48),day_change_pct=day_change_pct,
        deriv_ready=deriv_ready,oi_change_1h=float(oi.get("oi_change_1h") or 0.0),
        funding_pct=float(funding or 0.0),taker_ratio=float(taker or 1.0),
        long_short_ratio=float(ls or 1.0),spot_flow=spot_flow,residual=residual,
    )

    # The legacy V3 shadow gate is NOT the production veto. Keep its audit
    # distinct to avoid diagnosing spot-flow shadow disagreements as live blocks.
    try:
        from long_short_gate_audit import record_setup_gates
        record_setup_gates(symbol, v3_hard_gate_shadow)
    except Exception as exc:
        print('SIGNAL_GATE_AUDIT_IMPORT_ERROR', type(exc).__name__, str(exc)[:120], flush=True)

    v3=production_decide_direction(
        long_score=long,
        short_score=short,
        deriv_ready=deriv_ready,
        actionable_liquidity_ok=actionable_liquidity_ok,
        external_only_unverified=external_only_unverified,
        spot_flow=spot_flow,
        residual=residual,
        phase=str(v3_hard_gate_shadow.get("phase") or "NONE"),
        atr_pct=t15.get("atr_pct"),
    )

    print("PRODUCTION_DIRECTION_AUDIT",json.dumps({
        "symbol":symbol,"direction":v3.get("direction"),
        "eligible":v3.get("eligible"),"hard_blockers":v3.get("hard_blockers"),
        "decision_reasons":v3.get("decision_reasons"),
        "best_score":v3.get("best_score"),"edge":v3.get("edge"),
        "shadow_veto":v3_hard_gate_shadow.get("veto"),
        "data_mode":DATA_MODE,
        "venue_verified":bool(discovery_meta.get("binance_native_verified")),
        "source":discovery_meta.get("source"),
        "quote_volume_24h_usdt":round(pre_qv,2),
        "minimum_quote_volume_24h_usdt":MIN_24H_QUOTE_VOL,
        "quote_volume_provenance":discovery_meta.get("source"),
        "binance_spot_member":bool(discovery_meta.get("binance_spot_member")),
        "derivatives_ready":bool(deriv_ready),
        "provider_count":discovery_meta.get("provider_count"),
    },ensure_ascii=False,default=str),flush=True)

    preferred_direction=v3.get("direction") if v3.get("direction") in ("LONG","SHORT") else ("LONG" if long>short else "SHORT")

    # Reuse the old V3 setup-type diagnosis when it agrees with the production
    # direction; otherwise choose the entry style from the current phase.
    shadow_direction=str(v3_hard_gate_shadow.get("direction") or "NONE")
    shadow_setup=str(v3_hard_gate_shadow.get("setup_type") or "NONE")
    if shadow_direction==preferred_direction and shadow_setup in ("BREAKOUT","PULLBACK"):
        setup_type=shadow_setup
    else:
        setup_type=str(v3.get("setup_type") or "BREAKOUT")
    v3["setup_type"]=setup_type

    if setup_type=="PULLBACK" and preferred_direction in ("LONG","SHORT"):
        setup_plan=build_pullback_plan(preferred_direction,price,k5,t15)
    else:
        setup_plan=build_setup_plan(preferred_direction,price,k5,t15,chart)
        setup_plan["setup_type"]="BREAKOUT"

    structure_gate=build_structure_gate(preferred_direction,price,setup_plan,k5,k15,k30,k1h,k4h)
    # Research-only: candidate's own trigger zone must not be counted as the
    # next distinct obstacle. Entry must clear the outer edge of that zone.
    # Frozen V3.1 'structure_gate' remains EXACTLY as it was.
    try:
        v32_room_diagnostic=v32_room_shadow(
            preferred_direction,
            setup_plan["trigger_level"],setup_plan["invalidation"],setup_plan["target1"],
            structure_gate.get("trigger_zone"),
            k5,k15,k30,k1h,k4h,price,_timeframe_zones,
            structure_required_room_pct(),structure_min_round_trip_cost_pct(),
            STRUCTURE_MIN_NET_T1_R,
        )
    except Exception as exc:
        v32_room_diagnostic={
            "valid":False,"research_only":True,
            "reason":"SHADOW_CALCULATION_ERROR:"+type(exc).__name__,
        }


    # V3.2: every scanned coin receives TWO independently generated locked
    # hypotheses (LONG + SHORT), with separate trigger/stop/targets/geometry.
    # Only V3.1's presently authorized side can pass the V3.2 trade-quality
    # permission gate. The alternative is retained as counterfactual evidence.
    # This does not mutate frozen V3.1 model choice or its telegram signals.
    v32_dual_sides=build_v32_dual_scenarios(
        symbol=symbol,price=price,k5=k5,t15=t15,chart=chart,
        build_breakout=build_setup_plan,build_pullback=build_pullback_plan,
        build_structure=build_structure_gate,k15=k15,k30=k30,k1h=k1h,k4h=k4h,
        long_score=v3.get("long_score",long),short_score=v3.get("short_score",short),
        preferred_direction=v3.get("direction"),preferred_setup_type=setup_type,
        analyst_eligible=bool(v3.get("eligible")),
        derivatives_ready=deriv_ready,liquidity_ok=actionable_liquidity_ok,
        external_only=external_only_unverified,atr_pct=t15["atr_pct"],
        source=deriv_source,required_room_pct=structure_required_room_pct(),
        minimum_cost_pct=structure_min_round_trip_cost_pct(),
        min_net_r=STRUCTURE_MIN_NET_T1_R,
    )

    # Direction and entry timing are deliberately separate:
    # - direction engine chooses LONG/SHORT from combined evidence;
    # - live pool still requires a real price trigger + acceptable structure/R.
    if v3.get("eligible"):
        status="WAIT"
    else:
        status="NO_TRADE"

    entry_low=entry_high=stop=tp1=tp2=None
    rr1=None
    confidence=int(round(float(v3.get("best_score") or 0.0)))
    reversal_plan=build_reversal_plan(price,k5,t5,t15,day_change_pct)
    payload={
        "version":VERSION,"frozen_config_hash":FROZEN_CONFIG_HASH,"data_mode":DATA_MODE,"market_regime":market_regime,
        "day_change_pct":day_change_pct,"quote_volume_24h":pre_qv,"actionable_liquidity_ok":actionable_liquidity_ok,
        "discovery_meta":discovery_meta,
        "v3_discovery":dict((pre or {}).get("v3_discovery") or {}),
        "discovery_rank":float((pre or {}).get("rank") or 0.0),
        "chart":chart,"setup_plan":setup_plan,"reversal_plan":reversal_plan,
        "htf_gate":htf_gate,"structure_gate":structure_gate,
        "v32_distinct_room_shadow":v32_room_diagnostic,
        "v32_dual_sides":v32_dual_sides,
        "live_alert_version":STRUCTURE_GATE_VERSION,
        "t1":t1,"t5":t5,"t15":t15,"t1h":t1h,"t4h":t4h,
        "oi":oi,"funding_pct":funding,"taker_ratio":taker,
        "long_short_ratio":ls,"depth_imbalance":depth,
        "execution_proxy":depth_metrics,
        "score_components":components,
        "ablations":ablations,
        "raw_scores":{"long":raw_long,"short":raw_short},
        "legacy_score_shadow_only":False,
        "direction_engine":v3,
        "v3":v3,
        "v3_hard_gate_shadow":v3_hard_gate_shadow,
        "spot_flow":spot_flow,
        "beta_residual":residual,
        "derivatives_source":deriv_source,"cross_venue":cross,
        "multi_venue_derivatives":multi_deriv,
        "derivatives_ready":bool(deriv_ready),
        "derivatives_coverage":1.0 if DATA_MODE=="BINANCE_FUTURES" else float((multi_deriv or {}).get("coverage") or 0.0),
        "derivatives_quality":"NATIVE" if DATA_MODE=="BINANCE_FUTURES" else str((multi_deriv or {}).get("quality") or "UNAVAILABLE"),
        "derivatives_v3_core_ready":bool(deriv_ready),
        "derivatives_v3_core_coverage":1.0 if DATA_MODE=="BINANCE_FUTURES" else float((multi_deriv or {}).get("v3_core_coverage") or 0.0),
        "derivatives_selected_provider":None if DATA_MODE=="BINANCE_FUTURES" else (multi_deriv or {}).get("selected_provider"),
        "derivatives_source_age_seconds":None if DATA_MODE=="BINANCE_FUTURES" else (multi_deriv or {}).get("source_age_seconds"),
        "spot_vs_selected_mark_basis_pct":None if DATA_MODE=="BINANCE_FUTURES" else (multi_deriv or {}).get("spot_vs_selected_mark_basis_pct"),
        "derivatives_funding_pct":deriv_funding,
        "next_funding_time_ms":None if DATA_MODE=="BINANCE_FUTURES" else (multi_deriv or {}).get("next_funding_time_ms"),
        "funding_interval_hours":None if DATA_MODE=="BINANCE_FUTURES" else (multi_deriv or {}).get("funding_interval_hours"),
        "derivatives_basis_pct":deriv_basis,
    }
    return Analysis(symbol,status,long,short,confidence,price,entry_low,entry_high,
                    stop,tp1,tp2,rr1,reasons[:8],risks[:6],payload)


def init_db():
    with sqlite3.connect(DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS htf_cache(
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            fetched_at_epoch REAL NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(symbol,interval)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS scans(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time_utc TEXT NOT NULL, version TEXT NOT NULL,
            btc_regime TEXT NOT NULL, universe_size INTEGER NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS reversal_candidates(
            scan_time_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            score INTEGER NOT NULL,
            data_mode TEXT NOT NULL,
            sweep_level REAL NOT NULL,
            micro_break_level REAL NOT NULL,
            retest_low REAL NOT NULL,
            retest_high REAL NOT NULL,
            invalidation REAL NOT NULL,
            target1 REAL,
            target2 REAL,
            payload_json TEXT,
            PRIMARY KEY(scan_time_utc,symbol)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS analyses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time_utc TEXT NOT NULL, symbol TEXT NOT NULL,
            status TEXT NOT NULL, long_score INTEGER, short_score INTEGER,
            confidence INTEGER, price REAL, entry_low REAL, entry_high REAL,
            stop REAL, tp1 REAL, tp2 REAL, rr1 REAL,
            reasons_json TEXT, risks_json TEXT, payload_json TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS paper_setups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            signal_time_utc TEXT NOT NULL, signal_price REAL NOT NULL,
            signal_score INTEGER, stop REAL NOT NULL, tp1 REAL NOT NULL, tp2 REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            expires_at_utc TEXT, closed_time_utc TEXT, outcome TEXT, close_price REAL,
            gross_return_pct REAL, net_return_pct REAL, r_multiple REAL,
            fee_bps_per_side REAL, slippage_bps_per_side REAL,
            funding_pct_at_signal REAL, deriv_source TEXT,
            next_funding_time_ms REAL, funding_interval_hours REAL,
            episode_id TEXT, btc_regime TEXT, paper_notional_usdt REAL
        )""")
        cols={r[1] for r in con.execute("PRAGMA table_info(paper_setups)")}
        additions={
            "signal_score":"INTEGER","expires_at_utc":"TEXT","gross_return_pct":"REAL",
            "net_return_pct":"REAL","r_multiple":"REAL","fee_bps_per_side":"REAL",
            "slippage_bps_per_side":"REAL","funding_pct_at_signal":"REAL","deriv_source":"TEXT",
            "next_funding_time_ms":"REAL","funding_interval_hours":"REAL",
            "episode_id":"TEXT","btc_regime":"TEXT","paper_notional_usdt":"REAL",
        }
        for name,typ in additions.items():
            if name not in cols:
                con.execute(f"ALTER TABLE paper_setups ADD COLUMN {name} {typ}")
        con.execute("CREATE INDEX IF NOT EXISTS ix_analyses_time ON analyses(scan_time_utc)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_paper_open ON paper_setups(status,symbol)")
        con.execute("""CREATE TABLE IF NOT EXISTS performance_reports(
            report_time_utc TEXT PRIMARY KEY, closed_trades INTEGER,
            win_rate REAL, expectancy_r REAL, avg_net_return_pct REAL,
            max_drawdown_pct REAL, buckets_json TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS system_notices(
            notice_key TEXT PRIMARY KEY,
            sent_at_utc TEXT NOT NULL,
            payload_json TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS universe_observations(
            scan_time_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quote_volume REAL,
            day_change_pct REAL,
            prefilter_rank REAL,
            shortlisted INTEGER NOT NULL,
            direction_hint TEXT NOT NULL,
            reference_price REAL NOT NULL,
            risk_pct REAL NOT NULL,
            t5_structure INTEGER,
            t15_structure INTEGER,
            t15_change_4 REAL,
            t15_vol_mult REAL,
            t15_atr_pct REAL,
            breakout20 INTEGER,
            breakdown20 INTEGER,
            payload_json TEXT,
            PRIMARY KEY(scan_time_utc,symbol)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS opportunity_30m_audit(
            scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,
            prior_time_utc TEXT NOT NULL,prior_price REAL NOT NULL,
            current_price REAL NOT NULL,move_pct REAL NOT NULL,
            classification TEXT NOT NULL,detail TEXT NOT NULL,
            PRIMARY KEY(scan_time_utc,symbol)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS opportunity_price_snapshots(
            scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,
            price REAL NOT NULL,PRIMARY KEY(scan_time_utc,symbol)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS data_health(
            scan_time_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            data_mode TEXT,
            derivatives_source TEXT,
            derivatives_quality TEXT,
            derivatives_coverage REAL,
            missing_json TEXT,
            errors_json TEXT,
            PRIMARY KEY(scan_time_utc,symbol)
        )""")


def save_universe_observations(ts, preselected, shortlist):
    """Archive every prefilter observation, including candidates we do not deep-scan.

    This is research-only. It gives the validation layer same-scan controls and
    prevents selection/survivorship bias from disappearing from the database.
    """
    selected={x["symbol"] for x in shortlist}
    with sqlite3.connect(DB) as con:
        for x in preselected:
            try:
                t5=x["t5"]; t15=x["t15"]
                if t15["breakout20"] or t15["structure"]>0:
                    direction="LONG"
                elif t15["breakdown20"] or t15["structure"]<0:
                    direction="SHORT"
                else:
                    direction="LONG" if float(t15["change_4"])>=0 else "SHORT"
                ref=float(t5["price"])
                risk_pct=max(0.20, (1.35*float(t15["atr"])/ref*100.0) if ref else 0.20)
                payload={
                    "research_only":True,
                    "source":"V3_DISCOVERY_SHADOW",
                    "v3_discovery":dict(x.get("v3_discovery") or {}),
                    "discovery_meta":dict(x.get("discovery_meta") or {}),
                    "discovery_rank":float(x.get("rank") or 0.0),
                    "t5":t5,
                    "t15":t15,
                }
                con.execute("""INSERT OR REPLACE INTO universe_observations(
                    scan_time_utc,symbol,quote_volume,day_change_pct,prefilter_rank,
                    shortlisted,direction_hint,reference_price,risk_pct,
                    t5_structure,t15_structure,t15_change_4,t15_vol_mult,t15_atr_pct,
                    breakout20,breakdown20,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ts,x["symbol"],float(x.get("quote_volume") or 0.0),
                 float(x.get("day_change") or 0.0),float(x.get("rank") or 0.0),
                 1 if x["symbol"] in selected else 0,direction,ref,risk_pct,
                 int(t5["structure"]),int(t15["structure"]),float(t15["change_4"]),
                 float(t15["vol_mult"]),float(t15["atr_pct"]),
                 1 if t15["breakout20"] else 0,1 if t15["breakdown20"] else 0,
                 json.dumps(payload,ensure_ascii=False,separators=(",",":"))))
            except Exception as exc:
                print("universe archive error",x.get("symbol"),type(exc).__name__,str(exc)[:120])


def save_reversal_candidates(ts, pres):
    """Archive reversal discovery independently from continuation shortlist."""
    with sqlite3.connect(DB) as con:
        for pre in pres:
            try:
                price=pre["t5"]["price"]
                plan=build_reversal_plan(price,pre["k5"],pre["t5"],pre["t15"],pre["day_change"])
                if not plan:
                    continue
                con.execute("""INSERT OR REPLACE INTO reversal_candidates(
                    scan_time_utc,symbol,direction,score,data_mode,sweep_level,
                    micro_break_level,retest_low,retest_high,invalidation,target1,target2,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ts,pre["symbol"],plan["direction"],int(plan.get("score") or 0),DATA_MODE,
                 float(plan["sweep_level"]),float(plan["micro_break_level"]),
                 float(plan["retest_low"]),float(plan["retest_high"]),float(plan["invalidation"]),
                 float(plan.get("target1") or 0),float(plan.get("target2") or 0),
                 json.dumps(plan,ensure_ascii=False)))
            except Exception as exc:
                print("reversal archive error",pre.get("symbol"),type(exc).__name__,str(exc)[:120])


def save_scan(ts, regime, n, results):
    with sqlite3.connect(DB) as con:
        con.execute("INSERT INTO scans(scan_time_utc,version,btc_regime,universe_size) VALUES(?,?,?,?)",
                    (ts,VERSION,regime,n))
        for a in results:
            con.execute("""INSERT INTO analyses(
                scan_time_utc,symbol,status,long_score,short_score,confidence,price,
                entry_low,entry_high,stop,tp1,tp2,rr1,reasons_json,risks_json,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts,a.symbol,a.status,a.long_score,a.short_score,a.confidence,a.price,
             a.entry_low,a.entry_high,a.stop,a.tp1,a.tp2,a.rr1,
             json.dumps(a.reasons,ensure_ascii=False),
             json.dumps(a.risks,ensure_ascii=False),
             json.dumps(a.payload,ensure_ascii=False)))
            p=a.payload or {}
            mv=p.get("multi_venue_derivatives") or {}
            critical=list(mv.get("critical_fields") or [])
            missing=[k for k in critical if mv.get(k) is None]
            errors=list(mv.get("errors") or [])
            con.execute("""INSERT OR REPLACE INTO data_health(
                scan_time_utc,symbol,data_mode,derivatives_source,
                derivatives_quality,derivatives_coverage,missing_json,errors_json
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (ts,a.symbol,p.get("data_mode"),p.get("derivatives_source"),
             p.get("derivatives_quality"),p.get("derivatives_coverage"),
             json.dumps(missing,ensure_ascii=False),
             json.dumps(errors,ensure_ascii=False)))
            if (not VERSION.startswith("LS_V3_")) and a.status in ("LONG","SHORT") and a.stop and a.tp1 and a.tp2:
                exists=con.execute("""SELECT 1 FROM paper_setups
                    WHERE symbol=? AND direction=? AND (
                      status='OPEN' OR datetime(signal_time_utc) >= datetime(?, ?)
                    ) LIMIT 1""",
                    (a.symbol,a.status,ts,f"-{SIGNAL_COOLDOWN_MIN} minutes")).fetchone()
                if not exists:
                    from datetime import timedelta
                    exp=(datetime.fromisoformat(ts)+timedelta(minutes=SIGNAL_EXPIRY_MIN)).isoformat()
                    score=max(a.long_score,a.short_score)
                    exec_proxy=(a.payload or {}).get("execution_proxy") or {}
                    paper_cost=((exec_proxy.get("costs") or {}).get(str(int(PAPER_NOTIONAL_USDT))) or {})
                    side_cost=paper_cost.get("buy_bps" if a.status=="LONG" else "sell_bps")
                    dynamic_slip=max(SLIPPAGE_BPS_PER_SIDE,float(side_cost or 0.0))
                    con.execute("""INSERT INTO paper_setups(
                        symbol,direction,signal_time_utc,signal_price,signal_score,
                        stop,tp1,tp2,status,expires_at_utc,fee_bps_per_side,
                        slippage_bps_per_side,funding_pct_at_signal,deriv_source,
                        next_funding_time_ms,funding_interval_hours,
                        episode_id,btc_regime,paper_notional_usdt
                    ) VALUES(?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?)""",
                    (a.symbol,a.status,ts,a.price,score,a.stop,a.tp1,a.tp2,exp,
                     FEE_BPS_PER_SIDE,dynamic_slip,
                     a.payload.get("derivatives_funding_pct"),
                     a.payload.get("derivatives_source"),
                     a.payload.get("next_funding_time_ms"),
                     a.payload.get("funding_interval_hours"),
                     market_episode_id(ts,a.status,regime),regime,PAPER_NOTIONAL_USDT))


def update_paper():
    with sqlite3.connect(DB) as con:
        rows=con.execute("""SELECT id,symbol,direction,signal_time_utc,signal_price,
                            signal_score,stop,tp1,tp2,expires_at_utc,
                            fee_bps_per_side,slippage_bps_per_side,
                            funding_pct_at_signal,deriv_source,
                            next_funding_time_ms,funding_interval_hours
                            FROM paper_setups WHERE status='OPEN'""").fetchall()
        now=now_iso()
        for row in rows:
            (rid,symbol,direction,signal_time,entry,score,stop,tp1,tp2,expires,
             fee_bps,slip_bps,funding_at_signal,deriv_source,
             next_funding_ms,funding_interval_hours)=row
            try:
                if expires and now >= expires:
                    last=fetch_klines(symbol,"5m",24)["close"][-1]
                    outcome="TIMEOUT"
                    exit_price=last
                else:
                    k=fetch_klines(symbol,"5m",24)
                    hi=max(k["high"][-2:]); lo=min(k["low"][-2:]); last=k["close"][-1]
                    outcome=None; exit_price=None
                    # Pessimistic same-candle ordering: stop first.
                    if direction=="LONG":
                        if lo<=stop: outcome="STOP"; exit_price=stop
                        elif hi>=tp2: outcome="TP2"; exit_price=tp2
                        elif hi>=tp1: outcome="TP1"; exit_price=tp1
                    else:
                        if hi>=stop: outcome="STOP"; exit_price=stop
                        elif lo<=tp2: outcome="TP2"; exit_price=tp2
                        elif lo<=tp1: outcome="TP1"; exit_price=tp1
                    if not outcome:
                        continue

                gross=(exit_price/entry-1.0)*100.0
                if direction=="SHORT":
                    gross=-gross
                trading_cost_pct=2.0*((fee_bps or FEE_BPS_PER_SIDE)+(slip_bps or SLIPPAGE_BPS_PER_SIDE))/100.0
                # Apply funding only if the position actually crossed the next
                # known settlement timestamp. Positive funding: longs pay,
                # shorts receive. Negative funding is the mirror image.
                funding_cost=0.0
                if funding_at_signal is not None and next_funding_ms:
                    try:
                        sig_ms=datetime.fromisoformat(signal_time).timestamp()*1000.0
                        close_ms=datetime.fromisoformat(now_iso()).timestamp()*1000.0
                        nf=float(next_funding_ms)
                        if sig_ms < nf <= close_ms:
                            rate=float(funding_at_signal)
                            funding_cost=rate if direction=="LONG" else -rate
                    except Exception:
                        funding_cost=0.0
                net=gross-trading_cost_pct-funding_cost
                risk_pct=abs((entry-stop)/entry)*100.0 if entry else 0.0
                rmult=(net/risk_pct) if risk_pct>0 else None
                con.execute("""UPDATE paper_setups SET status='CLOSED',
                    closed_time_utc=?,outcome=?,close_price=?,gross_return_pct=?,
                    net_return_pct=?,r_multiple=? WHERE id=?""",
                    (now_iso(),outcome,exit_price,gross,net,rmult,rid))
            except Exception as exc:
                print("paper update error",symbol,type(exc).__name__,str(exc)[:120])


def performance_summary():
    with sqlite3.connect(DB) as con:
        rows=con.execute("""SELECT signal_score,outcome,net_return_pct,r_multiple,episode_id
                            FROM paper_setups
                            WHERE status='CLOSED' AND net_return_pct IS NOT NULL
                            ORDER BY closed_time_utc,id""").fetchall()
        if not rows:
            return {"closed":0,"text":"Henüz kapanmış paper trade yok."}
        net=[float(r[2]) for r in rows]
        rs=[float(r[3]) for r in rows if r[3] is not None]
        wins=sum(1 for x in net if x>0)
        equity=0.0; peak=0.0; max_dd=0.0
        for x in net:
            equity+=x
            peak=max(peak,equity)
            max_dd=min(max_dd,equity-peak)
        buckets={}
        for lo,hi,label in ((55,64,"55-64"),(65,74,"65-74"),(75,100,"75+")):
            sub=[r for r in rows if r[0] is not None and lo<=int(r[0])<=hi]
            if sub:
                vals=[float(r[2]) for r in sub]
                rvals=[float(r[3]) for r in sub if r[3] is not None]
                buckets[label]={
                    "n":len(sub),
                    "win_rate":100.0*sum(1 for x in vals if x>0)/len(vals),
                    "avg_net_pct":mean(vals),
                    "expectancy_r":mean(rvals) if rvals else None,
                }
        episode_map={}
        for r in rows:
            eid=r[4] or "UNASSIGNED"
            if r[3] is not None:
                episode_map.setdefault(eid,[]).append(float(r[3]))
        episode_means=[mean(v) for v in episode_map.values() if v]
        summary={
            "closed":len(rows),
            "effective_episodes":len(episode_map),
            "win_rate":100.0*wins/len(rows),
            "expectancy_r":mean(rs) if rs else 0.0,
            "episode_expectancy_r":mean(episode_means) if episode_means else 0.0,
            "avg_net_pct":mean(net),
            "max_drawdown_pct":max_dd,
            "buckets":buckets,
        }
        con.execute("""INSERT OR REPLACE INTO performance_reports
            (report_time_utc,closed_trades,win_rate,expectancy_r,
             avg_net_return_pct,max_drawdown_pct,buckets_json)
             VALUES(?,?,?,?,?,?,?)""",
            (now_iso(),summary["closed"],summary["win_rate"],summary["expectancy_r"],
             summary["avg_net_pct"],summary["max_drawdown_pct"],
             json.dumps(buckets,ensure_ascii=False)))
        summary["text"]=(f"Paper: {summary['closed']} kapanış / {summary['effective_episodes']} bağımsız piyasa dalgası | "
                         f"Win %{summary['win_rate']:.1f} | Exp {summary['expectancy_r']:+.2f}R | "
                         f"Dalga-başına Exp {summary['episode_expectancy_r']:+.2f}R | "
                         f"Net ort %{summary['avg_net_pct']:+.2f} | Max DD %{summary['max_drawdown_pct']:.2f}")
        return summary


def fmtp(x):
    if x is None: return "-"
    if abs(x)>=1000: return f"{x:,.2f}"
    if abs(x)>=1: return f"{x:.4f}"
    return f"{x:.8f}".rstrip("0")


def turkish_status(s):
    return {"LONG":"🟢 LONG SETUP","SHORT":"🔴 SHORT SETUP",
            "WAIT":"🟡 ŞİMDİ ALMA — CANLI HAVUZ BEKLİYOR","NO_TRADE":"⚪ İŞLEM YOK"}.get(s,s)


def build_message(ts, regime, results, errors, perf=None):
    actionable=[x for x in results if x.status in ("LONG","SHORT")]
    waits=[x for x in results if x.status=="WAIT"]
    actionable.sort(key=lambda x:(x.confidence,abs(x.payload.get("day_change_pct",0))), reverse=True)
    waits.sort(key=lambda x:(x.confidence,abs(x.payload.get("day_change_pct",0))), reverse=True)
    lines=[
        "📊 LONG / SHORT MOTORU — SADE ANLATIM",
        f"Veri: {DATA_MODE}",
        f"BTC rejimi: {regime} | Taranan: {len(results)} | Hata: {errors}",
        "Otomatik emir YOK — paper-trade / analiz modu.",
        (perf or {}).get("text",""),
        "",
    ]
    shown=(actionable[:5] if actionable else waits[:3])
    if not shown:
        lines.append("⚪ Şu an temiz setup yok. İşlem açmamak da geçerli sonuç.")
    for a in shown:
        lines += [
            f"{turkish_status(a.status)} | {a.symbol} | 24s {a.payload.get('day_change_pct',0):+.1f}%",
            f"Grafik: {a.payload.get('chart',{}).get('state','N/A')}",
            f"Long: {a.long_score}/100 | Short: {a.short_score}/100 | Güven: {a.confidence}/99",
            f"Fiyat: {fmtp(a.price)}",
        ]
        if a.status in ("LONG","SHORT"):
            lines += [
                f"Giriş bölgesi: {fmtp(a.entry_low)} – {fmtp(a.entry_high)}",
                f"Stop/yanlışlanma: {fmtp(a.stop)}",
                f"Hedef 1: {fmtp(a.tp1)} | Hedef 2: {fmtp(a.tp2)} | R/R≈{a.rr1:.2f}",
            ]
        plan=a.payload.get("setup_plan") or {}
        if plan:
            direction=plan.get("direction")
            trig=fmtp(plan.get("trigger_level"))
            rl=fmtp(plan.get("retest_low")); rh=fmtp(plan.get("retest_high"))
            if plan.get("triggered"):
                lines.append(f"✅ {direction} giriş şartı oluşmuş görünüyor.")
            else:
                lines.append("⛔ ŞİMDİ ALMA — canlı havuz giriş şartını bekliyor.")
                if direction=="SHORT":
                    lines.append(
                        f"Beklediğimiz şey: {trig} altında 5 dakikalık mum kapanışı; "
                        f"sonra fiyat {rl}–{rh} bölgesine geri gelip burayı aşamadan yeniden aşağı dönsün."
                    )
                else:
                    lines.append(
                        f"Beklediğimiz şey: {trig} üstünde 5 dakikalık mum kapanışı; "
                        f"sonra fiyat {rl}–{rh} bölgesine geri gelip bu bölgenin üstünde tutunsun."
                    )
                lines.append(
                    f"📩 Bu şartlar oluşursa canlı havuz ayrıca Telegram'dan “{direction} giriş şartları oluştu” mesajı gönderecek."
                )
            lines.append(
                f"❌ Fikir bozulur: {fmtp(plan.get('invalidation'))} seviyesinin karşı tarafında 5 dakikalık mum kapanışı."
            )
            lines.append(
                f"🎯 Olası hedefler: {fmtp(plan.get('target1'))} / {fmtp(plan.get('target2'))}"
            )
        for r in a.reasons[:4]:
            if "Grafik tetikleyicisi" in r:
                continue
            lines.append("• " + r)
        for r in a.risks[:2]: lines.append("⚠️ " + r)
        lines.append("")
    lines += [
        "Terimler: OI = açık vadeli pozisyon miktarı | Funding = long/short taraflarının birbirine ödediği ücret.",
        "Canlı havuz = bu coinleri daha sık izleyen ayrı takip sistemi.",
        "Not: V3 final kararında toplamalı skor kullanılmaz; eski skor yalnız gölge karşılaştırmadır.",
        "Kaldıraç, kötü bir setup'ı iyi yapmaz. Stop ve pozisyon büyüklüğü ayrı karardır."
    ]
    return "\n".join(lines)[:TELEGRAM_LIMIT]


def resolve_telegram_chat(token, configured=""):
    # Reuse the Binance bot's persisted chat-id cache first. This avoids
    # depending on getUpdates being non-empty on every run.
    return resolve_chat_id(
        token,
        configured,
        os.path.join(
            os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".",
            "long_short_telegram_chat_cache.db",
        ),
        "Long/Short Telegram",
    )


def send_health_once(results):
    """Send one deployment/data-health card per version after a real successful scan."""
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or not results:
        return
    notice_key=f"DEPLOY_HEALTH|{VERSION}"
    with sqlite3.connect(DB) as con:
        if con.execute("SELECT 1 FROM system_notices WHERE notice_key=?",(notice_key,)).fetchone():
            return

    ready=sum(1 for a in results if bool((a.payload or {}).get("derivatives_ready")))
    native=sum(1 for a in results if (a.payload or {}).get("derivatives_source")=="BINANCE_FUTURES")
    multi=sum(1 for a in results if (a.payload or {}).get("derivatives_source")=="MULTI_VENUE_PUBLIC")
    if ready<=0:
        return

    msg=(
        f"✅ LONG / SHORT MOTORU AKTİF | V3.1\n"
        f"Veri sağlığı: {ready}/{len(results)} derin tarama coininde V3.1 türev çekirdeği TAM.\n"
        f"Kaynak: Binance Futures {native} | Çoklu-venue {multi}.\n"
        f"OI + Funding + Taker + Long/Short eksikse gerçek sinyal kilitlenir.\n"
        f"Order-book snapshot yalnız tanısal; final yön kararı vermez.\n"
        f"📊 15dk / 1s / 3s sonuç ölçümü açık.\n"
        f"🤖 Otomatik emir KAPALI — Telegram analiz/uyarı modu."
    )
    send_telegram(msg)
    with sqlite3.connect(DB) as con:
        con.execute("INSERT OR REPLACE INTO system_notices(notice_key,sent_at_utc,payload_json) VALUES(?,?,?)",
                    (notice_key,now_iso(),json.dumps({"ready":ready,"total":len(results),"native":native,"multi":multi})))
        con.commit()


def send_telegram(msg):
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print(msg)
        return
    chat=resolve_telegram_chat(token,configured)
    r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id":chat,"text":msg,"disable_web_page_preview":True},
        timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    payload=r.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram API did not confirm delivery")
    print("TELEGRAM_DELIVERY_CONFIRMED", "chat_id_present="+str(bool(chat)), flush=True)


def main():
    started=time.time()
    init_db()
    update_paper()
    ts=now_iso()
    regime=btc_regime()
    uni=universe()

    # Stage 1: cheap broad scan. Keep Binance coverage wide without doing
    # expensive derivatives/HTF calls for every symbol.
    preselected=[]; errors=0
    prefilter_failures={}
    if uni:
        workers=max(1,min(PREFILTER_WORKERS,len(uni)))
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="ls-prefilter") as pool:
            futs={
                pool.submit(prefilter_symbol,symbol,day_change,quote_volume):symbol
                for symbol,quote_volume,_,day_change in uni
            }
            for fut in as_completed(futs):
                symbol=futs[fut]
                try:
                    preselected.append(fut.result())
                except Exception as e:
                    errors+=1
                    prefilter_failures[symbol]={"error_type":type(e).__name__,"error_message":str(e)[:160]}
                    print(f"prefilter {symbol}: {type(e).__name__}: {e}")
    preselected.sort(key=lambda x:x["rank"], reverse=True)
    save_reversal_candidates(ts,preselected)

    # Deep-scan shortlist is activity-ranked only. The previous policy reserved
    # roughly half the slots for raw volume leaders, which systematically pushed
    # BTC/ETH/other mega-liquidity names into the live pool even when faster
    # mid/small-cap perpetuals had stronger current setups.
    shortlist=select_deep_shortlist(preselected,PRESELECT_MAX)
    # Observability: distinguish executable shortlist capacity from research-only
    # backfill. Do not change the frozen signal rules or ranking.
    try:
        actionable_all=0
        actionable_selected=0
        for entry in preselected:
            meta=entry.get("discovery_meta") or {}
            valid=(float(entry.get("quote_volume") or 0)>=MIN_24H_QUOTE_VOL
                   and bool(meta.get("binance_spot_member"))
                   and not bool(meta.get("external_only_unverified")))
            actionable_all+=int(valid)
        for entry in shortlist:
            meta=entry.get("discovery_meta") or {}
            valid=(float(entry.get("quote_volume") or 0)>=MIN_24H_QUOTE_VOL
                   and bool(meta.get("binance_spot_member"))
                   and not bool(meta.get("external_only_unverified")))
            actionable_selected+=int(valid)
        print("SHORTLIST_EXECUTABILITY",json.dumps({
            "window":len(uni),"prefilter_ok":len(preselected),
            "actionable_in_window":actionable_all,
            "actionable_selected":actionable_selected,
            "research_only_selected":len(shortlist)-actionable_selected,
            "selection_limit":PRESELECT_MAX,
        }),flush=True)
    except Exception as exc:
        print("SHORTLIST_EXECUTABILITY_ERROR",type(exc).__name__,str(exc)[:160],flush=True)
    print("FAST_PREFILTER",len(uni),"->",len(shortlist),
          ",".join(x["symbol"] for x in shortlist))
    save_universe_observations(ts,preselected,shortlist)
    # Observation only: one row for every symbol returned by universe(), even if
    # prefilter failed. Separate table; no change to production selection logic.
    try:
        with sqlite3.connect(DB,timeout=15) as audit_con:
            audit_con.execute("""CREATE TABLE IF NOT EXISTS full_universe_stage_audit(
                scan_time_utc TEXT NOT NULL, symbol TEXT NOT NULL,
                stage TEXT NOT NULL, quote_volume REAL, day_change_pct REAL,
                rank REAL, shortlisted INTEGER NOT NULL,
                detail_json TEXT NOT NULL,
                PRIMARY KEY(scan_time_utc,symbol))""")
            audit_pre={x["symbol"]:x for x in preselected}
            audit_selected={x["symbol"] for x in shortlist}
            for audit_symbol,audit_volume,_,audit_change in uni:
                audit_pre_item=audit_pre.get(audit_symbol)
                audit_failure=prefilter_failures.get(audit_symbol)
                audit_stage=("PREFILTER_ERROR" if audit_failure else
                             "UNOBSERVED" if audit_pre_item is None else
                             "DEEP_SELECTED" if audit_symbol in audit_selected else
                             "NOT_SHORTLISTED")
                audit_meta=(audit_pre_item or {}).get("discovery_meta") or {}
                audit_blockers=[]
                if audit_pre_item is not None:
                    if float(audit_volume or 0)<MIN_24H_QUOTE_VOL:
                        audit_blockers.append("BELOW_EXECUTION_VOLUME_FLOOR")
                    if not audit_meta.get("binance_spot_member"):
                        audit_blockers.append("NO_BINANCE_SPOT_MEMBERSHIP")
                    if audit_meta.get("external_only_unverified"):
                        audit_blockers.append("EXTERNAL_ONLY_UNVERIFIED")
                    if audit_stage=="NOT_SHORTLISTED":
                        audit_blockers.append("DEEP_CAPACITY_NOT_SELECTED")
                audit_detail={"prefilter_failure":audit_failure,
                              "discovery_meta":audit_meta,
                              "selection_blockers":audit_blockers,
                              "discovery":(audit_pre_item or {}).get("v3_discovery")}
                audit_con.execute("""INSERT OR REPLACE INTO full_universe_stage_audit
                    (scan_time_utc,symbol,stage,quote_volume,day_change_pct,rank,shortlisted,detail_json)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (ts,audit_symbol,audit_stage,float(audit_volume or 0),
                     float(audit_change or 0),
                     float(audit_pre_item.get("rank") or 0) if audit_pre_item else None,
                     int(audit_symbol in audit_selected),
                     json.dumps(audit_detail,ensure_ascii=False,default=str)))
            audit_con.commit()
        print("ROTATING_WINDOW_STAGE_AUDIT",json.dumps({"window_symbols":len(uni),"prefilter_errors":len(prefilter_failures),"scope":"rotating_window_only","not_full_perpetual_universe":True},ensure_ascii=False),flush=True)
    except Exception as audit_exc:
        print("FULL_UNIVERSE_STAGE_AUDIT_ERROR",type(audit_exc).__name__,str(audit_exc)[:160],flush=True)
    # Read-only early-pattern observations for all prefiltered symbols.
    try:
        from long_short_broad_early import observe as observe_broad_early
        observe_broad_early(preselected, shortlist)
    except Exception as exc:
        print('BROAD_EARLY_OBSERVER_ERROR', type(exc).__name__, str(exc)[:160], flush=True)

    # Stage 2: bounded-concurrency deep scan of every successfully prefiltered symbol.\n    # No arbitrary candidate-count cutoff; data-health and frozen execution gates still apply.
    results=[]
    if shortlist:
        workers=max(1,min(DEEP_WORKERS,len(shortlist)))
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="ls-deep") as pool:
            futs={
                pool.submit(score_symbol,pre["symbol"],regime,pre["day_change"],pre):pre["symbol"]
                for pre in shortlist
            }
            for fut in as_completed(futs):
                symbol=futs[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    errors+=1
                    print(f"deep {symbol}: {type(e).__name__}: {e}")
        order={x["symbol"]:i for i,x in enumerate(shortlist)}
        results.sort(key=lambda x:order.get(x.symbol,9999))

    # Read-only opportunity funnel: record why each scanned coin did not reach a trade plan.
    try:
        selected={x["symbol"] for x in shortlist}
        deep={a.symbol:a for a in results}
        counts={}
        with sqlite3.connect(DB,timeout=15) as con:
            con.execute("CREATE TABLE IF NOT EXISTS opportunity_funnel(scan_time_utc TEXT,symbol TEXT,stage TEXT,rank REAL,price REAL,detail_json TEXT,PRIMARY KEY(scan_time_utc,symbol))")
            for x in preselected:
                sym=x["symbol"]
                a=deep.get(sym)
                stage=("NOT_SELECTED" if sym not in selected else
                       "DEEP_ERROR" if a is None else
                       "MODEL_"+str(a.status))
                counts[stage]=counts.get(stage,0)+1
                payload=(a.payload or {}) if a else {}
                shadow=payload.get("v3_hard_gate_shadow") or {}
                decision=payload.get("direction_engine") or {}
                details={"research_only":True,"discovery":x.get("v3_discovery"),
                         "discovery_meta":x.get("discovery_meta"),
                         "risks":a.risks[:6] if a else [],
                         "long_score":a.long_score if a else None,
                         "short_score":a.short_score if a else None,
                         "shadow_gate":shadow,"production_direction":decision,
                         "derivatives_ready":payload.get("derivatives_ready"),
                         "spot_flow":payload.get("spot_flow"),
                         "t15":x.get("t15"),
                         "t5":x.get("t5")}
                con.execute("INSERT OR REPLACE INTO opportunity_funnel VALUES(?,?,?,?,?,?)",
                            (ts,sym,stage,float(x.get("rank") or 0),
                             float(x["t5"].get("price") or 0),
                             json.dumps(details,ensure_ascii=False,default=str)))
            con.commit()
        print("OPPORTUNITY_FUNNEL",json.dumps(counts,ensure_ascii=False),flush=True)
        # Aggregate diagnostic only. Never changes trade eligibility or exposes tokens.
        from collections import Counter
        blocker_counts=Counter()
        risk_counts=Counter()
        derivatives_counts=Counter()
        for item in preselected:
            sym=item["symbol"]
            if sym not in selected:
                continue
            analysis=deep.get(sym)
            if analysis is None:
                blocker_counts["DEEP_ERROR"]+=1
                continue
            payload=(analysis.payload or {})
            direction=payload.get("direction_engine") or {}
            shadow=payload.get("v3_hard_gate_shadow") or {}
            for value in (direction.get("hard_blockers") or []):
                if isinstance(value,str):
                    blocker_counts[value[:80]]+=1
            for value in (analysis.risks or [])[:6]:
                if isinstance(value,str):
                    risk_counts[value[:80]]+=1
            derivatives_counts[str(payload.get("derivatives_ready"))[:20]]+=1
            if not direction.get("hard_blockers") and analysis.status=="NO_TRADE":
                blocker_counts["NO_EXPLICIT_HARD_BLOCKER"]+=1
        # Explicitly separate eligibility from setup readiness: no hard blocker
        # does NOT imply that a valid LONG/SHORT setup exists.
        decision_counts=Counter()
        decision_reason_counts=Counter()
        unblocked_samples=[]
        for item in preselected:
            sym=item["symbol"]
            if sym not in selected:
                continue
            analysis=deep.get(sym)
            if analysis is None:
                continue
            payload=analysis.payload or {}
            direction=payload.get("direction_engine") or {}
            blockers=direction.get("hard_blockers") or []
            if blockers or analysis.status!="NO_TRADE":
                continue
            decision_counts[str(direction.get("direction") or "NONE")[:24]]+=1
            for reason in (direction.get("decision_reasons") or ["unspecified_model_decision"]):
                decision_reason_counts[str(reason)[:80]]+=1
            if len(unblocked_samples)<8:
                unblocked_samples.append({
                    "symbol":sym,"status":analysis.status,
                    "direction":direction.get("direction"),
                    "eligible":direction.get("eligible"),
                    "best_score":direction.get("best_score"),
                    "edge":direction.get("edge"),
                    "shadow_veto":(payload.get("v3_hard_gate_shadow") or {}).get("veto"),
                    "decision_reasons":direction.get("decision_reasons"),
                    "thresholds":direction.get("thresholds"),
                })
        print("NO_TRADE_WITHOUT_HARD_BLOCKER",json.dumps({
            "count":sum(decision_counts.values()),
            "directions":decision_counts.most_common(),
            "decision_reasons":decision_reason_counts.most_common(),
            "samples":unblocked_samples,
        },ensure_ascii=False,default=str),flush=True)
        print("OPPORTUNITY_BLOCKER_SUMMARY",json.dumps({
            "scope":"deep_selected","selected":len(selected),
            "hard_blockers":blocker_counts.most_common(15),
            "risks":risk_counts.most_common(15),
            "derivatives_ready":derivatives_counts.most_common(8),
        },ensure_ascii=False,default=str),flush=True)
    except Exception as exc:
        print("OPPORTUNITY_FUNNEL_ERROR",type(exc).__name__,str(exc)[:160],flush=True)
    save_scan(ts,regime,len(uni),results)
    send_health_once(results)
    perf=performance_summary()
    msg=build_message(ts,regime,results,errors,perf)
    print(msg)
    # Keep the full analyst summary in logs/DB, but silence Telegram by default.
    # The live pool now owns state-change alerts (early/confirmed/chase/broken).
    if TELEGRAM_SUMMARY:
        send_telegram(msg)
    print(f"SCAN_RUNTIME_SECONDS {time.time()-started:.1f}")


if __name__=="__main__":
    main()
