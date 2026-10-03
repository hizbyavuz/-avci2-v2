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
import math
import os
import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from binance_notify import resolve_chat_id

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
MAX_SYMBOLS = int(os.getenv("LS_MAX_SYMBOLS", "80"))
REQUEST_TIMEOUT = 12
TELEGRAM_LIMIT = 4096
VERSION = "LSA_V1_4_PLAIN_TR_2026-10-03"
OKX_BASE = "https://www.okx.com"
FEE_BPS_PER_SIDE = float(os.getenv("LS_FEE_BPS_PER_SIDE", "5"))
SLIPPAGE_BPS_PER_SIDE = float(os.getenv("LS_SLIPPAGE_BPS_PER_SIDE", "5"))
SIGNAL_EXPIRY_MIN = int(os.getenv("LS_SIGNAL_EXPIRY_MIN", "180"))
SIGNAL_COOLDOWN_MIN = int(os.getenv("LS_SIGNAL_COOLDOWN_MIN", "120"))

EXCLUDED_BASES = {
    "USDC","FDUSD","TUSD","USDP","DAI","BUSD","EUR","TRY","BTCST",
}
EXCLUDED_MARKERS = ("UP","DOWN","BULL","BEAR")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fget(path: str, params: dict | None = None):
    global DATA_MODE
    last = None
    spot_map = {
        "/fapi/v1/klines": "/api/v3/klines",
        "/fapi/v1/exchangeInfo": "/api/v3/exchangeInfo",
        "/fapi/v1/ticker/24hr": "/api/v3/ticker/24hr",
        "/fapi/v1/depth": "/api/v3/depth",
    }

    # Once the runner is confirmed geo-blocked from Binance Futures, do not
    # waste time retrying five Futures hosts on every symbol.
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
    return parse_klines(fget("/fapi/v1/klines", {
        "symbol": symbol, "interval": interval, "limit": limit
    }))


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


def build_setup_plan(direction, price, t5k, t15, chart):
    levels=swing_levels(t5k,48)
    a=max(t15["atr"],price*0.002)
    pad=max(0.12*a,price*0.001)
    if direction=="SHORT":
        trigger=levels["support"]
        retest_low=trigger
        retest_high=trigger+pad
        invalid=max(levels["resistance"], trigger+0.9*a)
        target1=levels["support2"]
        if target1>=trigger:
            target1=trigger-1.5*a
        target2=min(target1-0.8*a, trigger-2.5*a)
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
    target1=levels["resistance2"]
    if target1<=trigger:
        target1=trigger+1.5*a
    target2=max(target1+0.8*a, trigger+2.5*a)
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
            return {"oi_change_1h":0.0,"oi_now":0.0}
        vals=[float(x.get("sumOpenInterestValue") or 0) for x in rows]
        return {"oi_change_1h":pct(vals[0],vals[-1]),"oi_now":vals[-1]}
    except Exception:
        return {"oi_change_1h":0.0,"oi_now":0.0}


def fetch_funding(symbol):
    if DATA_MODE!="BINANCE_FUTURES":
        return 0.0
    try:
        x=fget("/fapi/v1/premiumIndex",{"symbol":symbol})
        return float(x.get("lastFundingRate") or 0.0) * 100.0
    except Exception:
        return 0.0


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
        return 1.0


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
        return 1.0


def fetch_depth_imbalance(symbol):
    d=fget("/fapi/v1/depth",{"symbol":symbol,"limit":50})
    bids=sum(float(p)*float(q) for p,q in d.get("bids",[])[:20])
    asks=sum(float(p)*float(q) for p,q in d.get("asks",[])[:20])
    total=bids+asks
    return 0.0 if total<=0 else (bids-asks)/total


def universe():
    """
    Futures action universe:
    liquid USDT perpetuals; daily top movers first, then volume leaders.
    """
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

    eligible=[]
    for x in tick:
        sym=x.get("symbol")
        if sym not in tradable:
            continue
        qv=float(x.get("quoteVolume") or 0)
        if qv < MIN_24H_QUOTE_VOL:
            continue
        eligible.append((
            sym, qv,
            float(x.get("lastPrice") or 0),
            float(x.get("priceChangePercent") or 0),
        ))

    by_move=sorted(eligible,key=lambda z:abs(z[3]),reverse=True)
    by_vol=sorted(eligible,key=lambda z:z[1],reverse=True)
    mover_n=max(20, min(50, int(MAX_SYMBOLS*0.60)))
    selected=[]
    seen=set()
    for row in by_move[:mover_n] + by_vol:
        if row[0] in seen:
            continue
        selected.append(row)
        seen.add(row[0])
        if len(selected)>=MAX_SYMBOLS:
            break
    return selected


def btc_regime():
    k=fetch_klines("BTCUSDT","1h",220)
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


def score_symbol(symbol, market_regime, day_change_pct=0.0):
    t1=timeframe_features(fetch_klines(symbol,"1m",220))
    k5=fetch_klines(symbol,"5m",220)
    t5=timeframe_features(k5)
    t15=timeframe_features(fetch_klines(symbol,"15m",220))
    t1h=timeframe_features(fetch_klines(symbol,"1h",220))
    t4h=timeframe_features(fetch_klines(symbol,"4h",220))
    oi=fetch_oi(symbol)
    funding=fetch_funding(symbol)
    taker=fetch_taker(symbol)
    ls=fetch_long_short(symbol)
    depth=fetch_depth_imbalance(symbol)
    chart=chart_state(t1,t5,t15,t1h)
    cross=okx_derivatives(symbol) if DATA_MODE!="BINANCE_FUTURES" else None

    long=short=0
    reasons=[]; risks=[]

    # Higher timeframe trend: max 28 points each side.
    for tf,name,w in ((t4h,"4s",12),(t1h,"1s",10),(t15,"15dk",6)):
        if tf["price"]>tf["ema20"]>tf["ema50"]:
            long += w; reasons.append(f"{name} trend yukarı")
        elif tf["price"]<tf["ema20"]<tf["ema50"]:
            short += w; reasons.append(f"{name} trend aşağı")

    # Structure / breakout: max 18.
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

    # Momentum / RSI: avoid chasing extremes.
    if 52 <= t15["rsi"] <= 68 and t5["rsi"]>=50: long+=8
    if 32 <= t15["rsi"] <= 48 and t5["rsi"]<=50: short+=8
    if t15["rsi"]>=76:
        long-=6; risks.append("15dk RSI aşırı yüksek; long kovalamak riskli")
    if t15["rsi"]<=24:
        short-=6; risks.append("15dk RSI aşırı düşük; short kovalamak riskli")

    # Derivatives.
    price1h=t1h["change_1"]
    oic=oi["oi_change_1h"]
    deriv_source="BINANCE_FUTURES"
    deriv_funding=funding
    deriv_basis=None
    if DATA_MODE!="BINANCE_FUTURES" and cross and cross.get("status")=="OK":
        deriv_source="CROSS_VENUE_OKX"
        deriv_funding=cross.get("funding_pct") or 0.0
        deriv_basis=cross.get("basis_pct")
        # Cross-venue context is observational only. It can add reasons/risk,
        # but cannot unlock a trade while Binance Futures feed is unavailable.
        if deriv_funding>=0.05:
            risks.append(f"OKX funding yüksek pozitif %{deriv_funding:.3f}; long kalabalık olabilir")
        elif deriv_funding<=-0.05:
            risks.append(f"OKX funding yüksek negatif %{deriv_funding:.3f}; short kalabalık olabilir")
        if deriv_basis is not None and abs(deriv_basis)>=0.15:
            risks.append(f"OKX basis %{deriv_basis:+.2f}; spot-perp ayrışması yüksek")
    if price1h>0 and oic>1.5:
        long+=8; reasons.append(f"Fiyat↑ + OI↑ ({oic:+.1f}%): yeni kaldıraçlı talep")
    elif price1h<0 and oic>1.5:
        short+=8; reasons.append(f"Fiyat↓ + OI↑ ({oic:+.1f}%): yeni short baskısı")
    elif price1h>0 and oic<-1.5:
        long+=3; risks.append("Fiyat↑ + OI↓: short squeeze olabilir; devam teyidi zayıf")
    elif price1h<0 and oic<-1.5:
        short+=3; risks.append("Fiyat↓ + OI↓: long tasfiyesi olabilir; devam teyidi zayıf")

    if taker>=1.08: long+=7; reasons.append(f"Taker alım üstün ({taker:.2f}x)")
    elif taker<=0.92: short+=7; reasons.append(f"Taker satış üstün ({taker:.2f}x)")

    if depth>=0.08: long+=5; reasons.append(f"Order-book bid üstün ({depth:+.2f})")
    elif depth<=-0.08: short+=5; reasons.append(f"Order-book ask üstün ({depth:+.2f})")

    # Crowding / funding used contrarian as a risk modifier, not standalone signal.
    if funding>=0.05:
        long-=4; short+=2; risks.append(f"Funding yüksek pozitif %{funding:.3f}; long kalabalık olabilir")
    elif funding<=-0.05:
        short-=4; long+=2; risks.append(f"Funding yüksek negatif %{funding:.3f}; short kalabalık olabilir")
    if ls>=2.0:
        long-=3; risks.append(f"Long/short oranı {ls:.2f}; long tarafı kalabalık")
    elif ls<=0.5:
        short-=3; risks.append(f"Long/short oranı {ls:.2f}; short tarafı kalabalık")

    if market_regime=="UP": long+=4; short-=2
    elif market_regime=="DOWN": short+=4; long-=2

    # Multi-timeframe veto: do not fight both 4h and 1h trend.
    htf_bull = t4h["price"]>t4h["ema20"]>t4h["ema50"] and t1h["price"]>t1h["ema20"]>t1h["ema50"]
    htf_bear = t4h["price"]<t4h["ema20"]<t4h["ema50"] and t1h["price"]<t1h["ema20"]<t1h["ema50"]
    if htf_bull:
        short=max(0,short-12); risks.append("4s+1s ana trend yukarı; karşı-trend SHORT ağır cezalı")
    elif htf_bear:
        long=max(0,long-12); risks.append("4s+1s ana trend aşağı; karşı-trend LONG ağır cezalı")

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

    # Futures-data safety gate: chart-only fallback can watch, never issue entry.
    if DATA_MODE!="BINANCE_FUTURES" and status in ("LONG","SHORT"):
        risks.append("Binance Futures türev verisi erişilemiyor; grafik izleniyor ama giriş sinyali kilitli")
        status="WAIT"

    # Volatility risk gate.
    if t15["atr_pct"]>=4.0:
        risks.append(f"15dk ATR %{t15['atr_pct']:.1f}; aşırı oynaklık")
        if status in ("LONG","SHORT"):
            status="WAIT"

    confidence=min(99, max(0, int(best*0.75 + edge*0.25)))
    preferred_direction="LONG" if long>short else "SHORT"
    setup_plan=build_setup_plan(preferred_direction,price,k5,t15,chart)
    payload={
        "version":VERSION,"data_mode":DATA_MODE,"market_regime":market_regime,
        "day_change_pct":day_change_pct,"chart":chart,"setup_plan":setup_plan,
        "t1":t1,"t5":t5,"t15":t15,"t1h":t1h,"t4h":t4h,
        "oi":oi,"funding_pct":funding,"taker_ratio":taker,
        "long_short_ratio":ls,"depth_imbalance":depth,
        "derivatives_source":deriv_source,"cross_venue":cross,
        "derivatives_funding_pct":deriv_funding,"derivatives_basis_pct":deriv_basis,
    }
    return Analysis(symbol,status,long,short,confidence,price,entry_low,entry_high,
                    stop,tp1,tp2,rr1,reasons[:8],risks[:6],payload)


def init_db():
    with sqlite3.connect(DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS scans(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time_utc TEXT NOT NULL, version TEXT NOT NULL,
            btc_regime TEXT NOT NULL, universe_size INTEGER NOT NULL
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
            funding_pct_at_signal REAL, deriv_source TEXT
        )""")
        cols={r[1] for r in con.execute("PRAGMA table_info(paper_setups)")}
        additions={
            "signal_score":"INTEGER","expires_at_utc":"TEXT","gross_return_pct":"REAL",
            "net_return_pct":"REAL","r_multiple":"REAL","fee_bps_per_side":"REAL",
            "slippage_bps_per_side":"REAL","funding_pct_at_signal":"REAL","deriv_source":"TEXT",
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
            if a.status in ("LONG","SHORT") and a.stop and a.tp1 and a.tp2:
                exists=con.execute("""SELECT 1 FROM paper_setups
                    WHERE symbol=? AND direction=? AND (
                      status='OPEN' OR datetime(signal_time_utc) >= datetime(?, ?)
                    ) LIMIT 1""",
                    (a.symbol,a.status,ts,f"-{SIGNAL_COOLDOWN_MIN} minutes")).fetchone()
                if not exists:
                    from datetime import timedelta
                    exp=(datetime.fromisoformat(ts)+timedelta(minutes=SIGNAL_EXPIRY_MIN)).isoformat()
                    score=max(a.long_score,a.short_score)
                    con.execute("""INSERT INTO paper_setups(
                        symbol,direction,signal_time_utc,signal_price,signal_score,
                        stop,tp1,tp2,status,expires_at_utc,fee_bps_per_side,
                        slippage_bps_per_side,funding_pct_at_signal,deriv_source
                    ) VALUES(?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,?)""",
                    (a.symbol,a.status,ts,a.price,score,a.stop,a.tp1,a.tp2,exp,
                     FEE_BPS_PER_SIDE,SLIPPAGE_BPS_PER_SIDE,
                     a.payload.get("derivatives_funding_pct"),
                     a.payload.get("derivatives_source")))


def update_paper():
    with sqlite3.connect(DB) as con:
        rows=con.execute("""SELECT id,symbol,direction,signal_time_utc,signal_price,
                            signal_score,stop,tp1,tp2,expires_at_utc,
                            fee_bps_per_side,slippage_bps_per_side,
                            funding_pct_at_signal,deriv_source
                            FROM paper_setups WHERE status='OPEN'""").fetchall()
        now=now_iso()
        for row in rows:
            (rid,symbol,direction,signal_time,entry,score,stop,tp1,tp2,expires,
             fee_bps,slip_bps,funding_at_signal,deriv_source)=row
            try:
                if expires and now >= expires:
                    last=fetch_klines(symbol,"5m",2)["close"][-1]
                    outcome="TIMEOUT"
                    exit_price=last
                else:
                    k=fetch_klines(symbol,"5m",3)
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
                # Funding is charged only when native Binance funding is known.
                funding_cost=0.0
                if deriv_source=="BINANCE_FUTURES" and funding_at_signal is not None:
                    if direction=="LONG":
                        funding_cost=max(0.0,float(funding_at_signal))
                    else:
                        funding_cost=max(0.0,-float(funding_at_signal))
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
        rows=con.execute("""SELECT signal_score,outcome,net_return_pct,r_multiple
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
        summary={
            "closed":len(rows),
            "win_rate":100.0*wins/len(rows),
            "expectancy_r":mean(rs) if rs else 0.0,
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
        summary["text"]=(f"Paper: {summary['closed']} kapanış | Win %{summary['win_rate']:.1f} | "
                         f"Exp {summary['expectancy_r']:+.2f}R | Net ort %{summary['avg_net_pct']:+.2f} | "
                         f"Max DD %{summary['max_drawdown_pct']:.2f}")
        return summary


def fmtp(x):
    if x is None: return "-"
    if abs(x)>=1000: return f"{x:,.2f}"
    if abs(x)>=1: return f"{x:.4f}"
    return f"{x:.8f}".rstrip("0")


def turkish_status(s):
    return {"LONG":"🟢 LONG SETUP","SHORT":"🔴 SHORT SETUP",
            "WAIT":"🟡 TEYİT BEKLE","NO_TRADE":"⚪ İŞLEM YOK"}.get(s,s)


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
            lines.append("ŞU AN: " + ("✅ GİRİŞ ŞARTI OLUŞTU" if plan.get("triggered") else "⛔ ŞU AN GİRİŞ YOK — şart bekleniyor"))
            direction=plan.get("direction")
            trig=fmtp(plan.get("trigger_level"))
            rl=fmtp(plan.get("retest_low")); rh=fmtp(plan.get("retest_high"))
            if direction=="SHORT":
                lines.append(f"Ne bekliyoruz? Fiyat {trig} altına insin ve 5 dakikalık mum bu seviyenin altında kapansın.")
                lines.append(f"Sonra fiyat {rl}–{rh} bölgesine geri çıkıp burayı aşamaz ve yeniden aşağı dönerse SHORT düşünülebilir.")
                lines.append("Not: Sadece anlık fitil atması yeterli değil; 5 dakikalık mum kapanışı istiyoruz.")
            else:
                lines.append(f"Ne bekliyoruz? Fiyat {trig} üstüne çıksın ve 5 dakikalık mum bu seviyenin üstünde kapansın.")
                lines.append(f"Sonra fiyat {rl}–{rh} bölgesine geri gelip bu bölgenin üstünde tutunursa LONG düşünülebilir.")
                lines.append("Not: Sadece anlık yukarı değmesi yeterli değil; 5 dakikalık mum kapanışı istiyoruz.")
            lines.append(
                f"Retest bölgesi: {fmtp(plan.get('retest_low'))} – {fmtp(plan.get('retest_high'))}"
            )
            lines.append(
                f"❌ Bu fikir ne zaman bozulur? Fiyat {fmtp(plan.get('invalidation'))} seviyesinin karşı tarafında 5 dakikalık mum kapatırsa bu senaryoyu iptal ederiz."
            )
            lines.append(
                f"🎯 Kâr alınabilecek bölgeler: İlk hedef {fmtp(plan.get('target1'))} | İkinci hedef {fmtp(plan.get('target2'))}"
            )
        if a.status=="WAIT":
            if a.long_score>a.short_score:
                lines.append("⏳ Yükseliş tarafı daha güçlü görünüyor; ama mum kapanışı ve kırılan seviyenin tekrar korunması gelmeden giriş yok.")
            elif a.short_score>a.long_score:
                lines.append("⏳ Düşüş tarafı daha güçlü görünüyor; ama mum kapanışı ve kırılan seviyenin tekrar reddedilmesi gelmeden giriş yok.")
        for r in a.reasons[:4]: lines.append("• " + r)
        for r in a.risks[:2]: lines.append("⚠️ " + r)
        lines.append("")
    lines += [
        "Terimler: OI = açık vadeli pozisyon miktarı | Funding = long/short taraflarının birbirine ödediği ücret.",
        "Retest = kırılan seviyenin geri dönüp tekrar denenmesi | Fitil = mumun kısa süreli iğnesi.",
        "Not: Skor veriden hesaplanır; kesin fiyat tahmini değildir.",
        "Kaldıraç, kötü bir setup'ı iyi yapmaz. Stop ve pozisyon büyüklüğü ayrı karardır."
    ]
    return "\n".join(lines)[:TELEGRAM_LIMIT]


def resolve_telegram_chat(token, configured=""):
    # Reuse the Binance bot's persisted chat-id cache first. This avoids
    # depending on getUpdates being non-empty on every run.
    return resolve_chat_id(
        token,
        configured,
        "binance_avci2.db",
        "Long/Short Telegram",
    )


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


def main():
    init_db()
    update_paper()
    ts=now_iso()
    regime=btc_regime()
    uni=universe()
    results=[]; errors=0
    for symbol,_,_,day_change in uni:
        try:
            results.append(score_symbol(symbol,regime,day_change))
        except Exception as e:
            errors+=1
            print(f"{symbol}: {type(e).__name__}: {e}")
        time.sleep(0.03)
    save_scan(ts,regime,len(uni),results)
    perf=performance_summary()
    msg=build_message(ts,regime,results,errors,perf)
    print(msg)
    send_telegram(msg)


if __name__=="__main__":
    main()
