#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Multi-venue derivatives data router for the Long/Short motor.

Purpose
-------
GitHub-hosted runners can receive HTTP 451 from Binance Futures. The trading
model must not silently replace missing derivatives features with neutral
numbers. This module provides an explicit, public-data fallback using Bybit and
OKX and returns field-level provenance/coverage.

It never places orders and requires no exchange API keys.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

import requests

REQUEST_TIMEOUT = 10
BYBIT_BASES = (
    "https://api.bybit.com",
)
OKX_BASES = (
    "https://www.okx.com",
)
GATE_BASES = (
    "https://api.gateio.ws/api/v4",
    "https://fx-api.gateio.ws/api/v4",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fv(value, default=None):
    try:
        x = float(value)
        if math.isfinite(x):
            return x
    except (TypeError, ValueError):
        pass
    return default


def _pct(a, b):
    a = _fv(a)
    b = _fv(b)
    if a in (None, 0.0) or b is None:
        return None
    return (b / a - 1.0) * 100.0


def _get_json(base: str, path: str, params: dict | None = None) -> dict:
    last = None
    for attempt in range(2):
        try:
            r = requests.get(
                base + path,
                params=params or {},
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "lsa-multi-venue-router/2.0"},
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict):
                raise RuntimeError("non-object JSON response")
            return body
        except Exception as exc:
            last = exc
            if attempt == 0:
                time.sleep(0.20)
    raise last or RuntimeError("market data request failed")


def _bybit(path: str, params: dict | None = None) -> dict:
    last = None
    for base in BYBIT_BASES:
        try:
            body = _get_json(base, path, params)
            if int(body.get("retCode", -1)) != 0:
                raise RuntimeError(body.get("retMsg") or "Bybit error")
            result = body.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Bybit result missing")
            return {"result": result, "server_time_ms": body.get("time")}
        except Exception as exc:
            last = exc
    raise last or RuntimeError("Bybit unavailable")


def _okx(path: str, params: dict | None = None) -> list[dict]:
    last = None
    for base in OKX_BASES:
        try:
            body = _get_json(base, path, params)
            if str(body.get("code", "-1")) != "0":
                raise RuntimeError(body.get("msg") or "OKX error")
            data = body.get("data") or []
            if not isinstance(data, list):
                raise RuntimeError("OKX data missing")
            return data
        except Exception as exc:
            last = exc
    raise last or RuntimeError("OKX unavailable")


def _gate(path: str, params: dict | None = None):
    last = None
    for base in GATE_BASES:
        try:
            r = requests.get(
                base + path,
                params=params or {},
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "lsa-multi-venue-router/2.0", "Accept": "application/json"},
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
    raise last or RuntimeError("Gate unavailable")


def _depth_imbalance(bids, asks, levels=20):
    def notional(rows):
        total = 0.0
        for row in (rows or [])[:levels]:
            try:
                total += float(row[0]) * float(row[1])
            except (TypeError, ValueError, IndexError):
                continue
        return total

    b = notional(bids)
    a = notional(asks)
    total = a + b
    return None if total <= 0 else (b - a) / total


def bybit_derivatives(symbol: str) -> dict[str, Any]:
    """Return a standardized USDT-perpetual snapshot from Bybit public APIs."""
    out: dict[str, Any] = {
        "provider": "BYBIT_LINEAR",
        "symbol": symbol,
        "observed_at_utc": _now_iso(),
        "source_age_seconds": None,
        "oi_change_1h": None,
        "oi_now": None,
        "funding_pct": None,
        "next_funding_time_ms": None,
        "funding_interval_hours": None,
        "taker_ratio": None,
        "long_short_ratio": None,
        "depth_imbalance": None,
        "mark_price": None,
        "index_price": None,
        "basis_pct": None,
        "field_source": {},
        "errors": [],
    }

    try:
        x = _bybit("/v5/market/tickers", {"category": "linear", "symbol": symbol})
        rows = x["result"].get("list") or []
        row = rows[0] if rows else {}
        out["mark_price"] = _fv(row.get("markPrice"))
        out["index_price"] = _fv(row.get("indexPrice"))
        fr = _fv(row.get("fundingRate"))
        if fr is not None:
            out["funding_pct"] = fr * 100.0
            out["field_source"]["funding_pct"] = "BYBIT_LINEAR"
        nft=row.get("nextFundingTime")
        try:
            out["next_funding_time_ms"]=int(nft) if nft is not None else None
        except (TypeError,ValueError):
            pass
        fih=_fv(row.get("fundingIntervalHour"))
        if fih is not None and fih>0:
            out["funding_interval_hours"]=fih
        if out["mark_price"] is not None and out["index_price"] not in (None, 0.0):
            out["basis_pct"] = 100.0 * (out["mark_price"] / out["index_price"] - 1.0)
            out["field_source"]["basis_pct"] = "BYBIT_LINEAR"
    except Exception as exc:
        out["errors"].append("bybit_ticker:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        x = _bybit(
            "/v5/market/open-interest",
            {"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 13},
        )
        rows = list(x["result"].get("list") or [])
        rows.sort(key=lambda r: int(r.get("timestamp") or 0))
        if rows:
            latest_ms=int(rows[-1].get("timestamp") or 0)
            if latest_ms>0:
                out["source_age_seconds"]=max(0.0,time.time()-latest_ms/1000.0)
        vals = [_fv(r.get("openInterest")) for r in rows]
        vals = [v for v in vals if v is not None]
        if vals:
            out["oi_now"] = vals[-1]
            out["field_source"]["oi_now"] = "BYBIT_LINEAR"
        if len(vals) >= 2:
            out["oi_change_1h"] = _pct(vals[0], vals[-1])
            out["field_source"]["oi_change_1h"] = "BYBIT_LINEAR"
    except Exception as exc:
        out["errors"].append("bybit_oi:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        x = _bybit(
            "/v5/market/account-ratio",
            {"category": "linear", "symbol": symbol, "period": "5min", "limit": 12},
        )
        rows = list(x["result"].get("list") or [])
        rows.sort(key=lambda r: int(r.get("timestamp") or 0))
        if rows:
            buy = _fv(rows[-1].get("buyRatio"))
            sell = _fv(rows[-1].get("sellRatio"))
            if buy is not None and sell not in (None, 0.0):
                out["long_short_ratio"] = buy / sell
                out["field_source"]["long_short_ratio"] = "BYBIT_LINEAR"
    except Exception as exc:
        out["errors"].append("bybit_ls:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        x = _bybit(
            "/v5/market/recent-trade",
            {"category": "linear", "symbol": symbol, "limit": 500},
        )
        rows = x["result"].get("list") or []
        buy = 0.0
        sell = 0.0
        for r in rows:
            px = _fv(r.get("price"), 0.0) or 0.0
            sz = _fv(r.get("size"), 0.0) or 0.0
            notional = px * sz
            if str(r.get("side", "")).lower() == "buy":
                buy += notional
            elif str(r.get("side", "")).lower() == "sell":
                sell += notional
        if buy > 0 or sell > 0:
            out["taker_ratio"] = buy / max(sell, 1e-12)
            out["field_source"]["taker_ratio"] = "BYBIT_LINEAR_RECENT_TRADES"
    except Exception as exc:
        out["errors"].append("bybit_trades:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        x = _bybit(
            "/v5/market/orderbook",
            {"category": "linear", "symbol": symbol, "limit": 50},
        )
        res = x["result"]
        imb = _depth_imbalance(res.get("b"), res.get("a"), levels=20)
        if imb is not None:
            out["depth_imbalance"] = imb
            out["field_source"]["depth_imbalance"] = "BYBIT_LINEAR"
    except Exception as exc:
        out["errors"].append("bybit_book:" + type(exc).__name__ + ":" + str(exc)[:100])

    return _finish(out)


def okx_derivatives(symbol: str) -> dict[str, Any]:
    """Partial second-source context from OKX public SWAP endpoints."""
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    inst = f"{base}-USDT-SWAP"
    out: dict[str, Any] = {
        "provider": "OKX_SWAP",
        "symbol": symbol,
        "inst_id": inst,
        "observed_at_utc": _now_iso(),
        "source_age_seconds": None,
        "oi_change_1h": None,
        "oi_now": None,
        "funding_pct": None,
        "next_funding_time_ms": None,
        "funding_interval_hours": None,
        "taker_ratio": None,
        "long_short_ratio": None,
        "depth_imbalance": None,
        "mark_price": None,
        "index_price": None,
        "basis_pct": None,
        "field_source": {},
        "errors": [],
    }

    try:
        rows = _okx("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst})
        row = rows[0] if rows else {}
        oi = _fv(row.get("oiUsd"))
        if oi is not None:
            out["oi_now"] = oi
            out["field_source"]["oi_now"] = "OKX_SWAP_USD"
    except Exception as exc:
        out["errors"].append("okx_oi:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        rows = _okx("/api/v5/public/funding-rate", {"instId": inst})
        row = rows[0] if rows else {}
        fr = _fv(row.get("fundingRate"))
        if fr is not None:
            out["funding_pct"] = fr * 100.0
            out["field_source"]["funding_pct"] = "OKX_SWAP"
    except Exception as exc:
        out["errors"].append("okx_funding:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        rows = _okx("/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst})
        row = rows[0] if rows else {}
        out["mark_price"] = _fv(row.get("markPx"))
    except Exception as exc:
        out["errors"].append("okx_mark:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        rows = _okx("/api/v5/market/index-tickers", {"instId": f"{base}-USDT"})
        row = rows[0] if rows else {}
        out["index_price"] = _fv(row.get("idxPx"))
        if out["mark_price"] is not None and out["index_price"] not in (None, 0.0):
            out["basis_pct"] = 100.0 * (out["mark_price"] / out["index_price"] - 1.0)
            out["field_source"]["basis_pct"] = "OKX_SWAP"
    except Exception as exc:
        out["errors"].append("okx_index:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        rows = _okx("/api/v5/market/books", {"instId": inst, "sz": "50"})
        row = rows[0] if rows else {}
        imb = _depth_imbalance(row.get("bids"), row.get("asks"), levels=20)
        if imb is not None:
            out["depth_imbalance"] = imb
            out["field_source"]["depth_imbalance"] = "OKX_SWAP"
    except Exception as exc:
        out["errors"].append("okx_book:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        rows = _okx("/api/v5/market/trades", {"instId": inst, "limit": "100"})
        buy = 0.0
        sell = 0.0
        for r in rows:
            px = _fv(r.get("px"), 0.0) or 0.0
            sz = _fv(r.get("sz"), 0.0) or 0.0
            n = px * sz
            if str(r.get("side", "")).lower() == "buy":
                buy += n
            elif str(r.get("side", "")).lower() == "sell":
                sell += n
        if buy > 0 or sell > 0:
            out["taker_ratio"] = buy / max(sell, 1e-12)
            out["field_source"]["taker_ratio"] = "OKX_SWAP_RECENT_TRADES"
    except Exception as exc:
        out["errors"].append("okx_trades:" + type(exc).__name__ + ":" + str(exc)[:100])

    return _finish(out)


def gate_derivatives(symbol: str) -> dict[str, Any]:
    """Full public USDT-perpetual fallback from Gate contract statistics."""
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    contract = f"{base}_USDT"
    out: dict[str, Any] = {
        "provider": "GATE_FUTURES",
        "symbol": symbol,
        "contract": contract,
        "observed_at_utc": _now_iso(),
        "source_age_seconds": None,
        "oi_change_1h": None,
        "oi_now": None,
        "funding_pct": None,
        "next_funding_time_ms": None,
        "funding_interval_hours": None,
        "taker_ratio": None,
        "long_short_ratio": None,
        "depth_imbalance": None,
        "mark_price": None,
        "index_price": None,
        "basis_pct": None,
        "field_source": {},
        "errors": [],
    }

    try:
        rows = _gate(
            "/futures/usdt/contract_stats",
            {"contract": contract, "interval": "5m", "limit": 13},
        )
        rows = list(rows or [])
        rows.sort(key=lambda r: int(r.get("time") or 0))
        if rows:
            latest = rows[-1]
            latest_ts=int(latest.get("time") or 0)
            if latest_ts>0:
                if latest_ts>10_000_000_000:
                    latest_ts=latest_ts/1000.0
                out["source_age_seconds"]=max(0.0,time.time()-float(latest_ts))
            # V3: prefer contract/coin OI, not USD OI. USD OI mechanically
            # rises when price rises and can create a false "new positioning" signal.
            oi_vals = [_fv(r.get("open_interest")) for r in rows]
            oi_unit = "GATE_FUTURES_CONTRACT_OI"
            if not any(v is not None and v > 0 for v in oi_vals):
                oi_vals = [_fv(r.get("open_interest_usd")) for r in rows]
                oi_unit = "GATE_FUTURES_USD_OI_FALLBACK"
            oi_vals = [v for v in oi_vals if v is not None and v > 0]
            if oi_vals:
                out["oi_now"] = oi_vals[-1]
                out["field_source"]["oi_now"] = oi_unit
            if len(oi_vals) >= 2:
                out["oi_change_1h"] = _pct(oi_vals[0], oi_vals[-1])
                out["field_source"]["oi_change_1h"] = oi_unit

            taker = _fv(latest.get("lsr_taker"))
            if taker is None:
                long_taker = _fv(latest.get("long_taker_size"))
                short_taker = _fv(latest.get("short_taker_size"))
                if long_taker is not None and short_taker not in (None, 0.0):
                    taker = long_taker / short_taker
            if taker is not None and taker > 0:
                out["taker_ratio"] = taker
                out["field_source"]["taker_ratio"] = "GATE_FUTURES"

            lsr = _fv(latest.get("lsr_account"))
            if lsr is None:
                long_users = _fv(latest.get("long_users"))
                short_users = _fv(latest.get("short_users"))
                if long_users is not None and short_users not in (None, 0.0):
                    lsr = long_users / short_users
            if lsr is not None and lsr > 0:
                out["long_short_ratio"] = lsr
                out["field_source"]["long_short_ratio"] = "GATE_FUTURES"
    except Exception as exc:
        out["errors"].append("gate_stats:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        c = _gate(f"/futures/usdt/contracts/{contract}")
        fr = _fv(c.get("funding_rate"))
        if fr is not None:
            out["funding_pct"] = fr * 100.0
            out["field_source"]["funding_pct"] = "GATE_FUTURES"
        nxt=c.get("funding_next_apply")
        try:
            if nxt is not None:
                nv=int(float(nxt))
                out["next_funding_time_ms"]=nv if nv>10_000_000_000 else nv*1000
        except (TypeError,ValueError):
            pass
        fi=_fv(c.get("funding_interval"))
        if fi is not None and fi>0:
            out["funding_interval_hours"]=fi/3600.0
        out["mark_price"] = _fv(c.get("mark_price"))
        out["index_price"] = _fv(c.get("index_price"))
        if out["mark_price"] is not None and out["index_price"] not in (None, 0.0):
            out["basis_pct"] = 100.0 * (out["mark_price"] / out["index_price"] - 1.0)
            out["field_source"]["basis_pct"] = "GATE_FUTURES"
    except Exception as exc:
        out["errors"].append("gate_contract:" + type(exc).__name__ + ":" + str(exc)[:100])

    try:
        book = _gate(
            "/futures/usdt/order_book",
            {"contract": contract, "limit": 50, "with_id": "true"},
        )
        bids = [[r.get("p"), r.get("s")] for r in (book.get("bids") or [])]
        asks = [[r.get("p"), r.get("s")] for r in (book.get("asks") or [])]
        imb = _depth_imbalance(bids, asks, levels=20)
        if imb is not None:
            out["depth_imbalance"] = imb
            out["field_source"]["depth_imbalance"] = "GATE_FUTURES"
    except Exception as exc:
        out["errors"].append("gate_book:" + type(exc).__name__ + ":" + str(exc)[:100])

    return _finish(out)


def _finish(out: dict[str, Any]) -> dict[str, Any]:
    critical = (
        "oi_change_1h",
        "funding_pct",
        "taker_ratio",
        "long_short_ratio",
        "depth_imbalance",
    )
    # V3 deliberately does not treat one REST order-book snapshot as a
    # decision-critical field. Core positioning/flow must still come from one
    # coherent venue; depth remains diagnostic only.
    v3_core = (
        "oi_change_1h",
        "funding_pct",
        "taker_ratio",
        "long_short_ratio",
    )
    available = sum(out.get(k) is not None for k in critical)
    coverage = available / float(len(critical))
    v3_available = sum(out.get(k) is not None for k in v3_core)
    out["critical_fields"] = list(critical)
    out["available_critical"] = available
    out["coverage"] = coverage
    out["v3_core_fields"] = list(v3_core)
    out["v3_core_available"] = v3_available
    out["v3_core_coverage"] = v3_available / float(len(v3_core))
    age=out.get("source_age_seconds")
    stale=age is not None and float(age)>900.0
    out["stale"]=bool(stale)
    out["v3_core_ready"]=bool(v3_available==len(v3_core) and not stale)
    if available == len(critical) and not stale:
        out["quality"]="FULL"
    elif out["v3_core_ready"]:
        out["quality"]="V3_CORE_FULL"
    elif available >= 2:
        out["quality"]="STALE" if stale else "PARTIAL"
    else:
        out["quality"]="UNAVAILABLE"
    return out


def multi_venue_derivatives(symbol: str) -> dict[str, Any]:
    """Return a complete single-venue derivatives bundle or fail closed.

    Priority:
      1) Bybit linear perpetuals
      2) Gate USDT perpetuals
      3) OKX only as diagnostic field-level corroboration

    Actionable quality is FULL only when one venue alone supplies every critical
    field. We never manufacture a trading-ready bundle by mixing OI from one
    venue with crowding/order-flow from another venue.
    """
    def empty(provider: str, err: Exception) -> dict[str, Any]:
        return _finish({
            "provider": provider,
            "symbol": symbol,
            "observed_at_utc": _now_iso(),
            "oi_change_1h": None,
            "oi_now": None,
            "funding_pct": None,
            "next_funding_time_ms": None,
            "funding_interval_hours": None,
            "taker_ratio": None,
            "long_short_ratio": None,
            "depth_imbalance": None,
            "mark_price": None,
            "index_price": None,
            "basis_pct": None,
            "field_source": {},
            "errors": [provider.lower() + "_bundle:" + type(err).__name__ + ":" + str(err)[:100]],
        })

    try:
        by = bybit_derivatives(symbol)
    except Exception as exc:
        by = empty("BYBIT_LINEAR", exc)

    fields = (
        "oi_change_1h",
        "oi_now",
        "funding_pct",
        "next_funding_time_ms",
        "funding_interval_hours",
        "taker_ratio",
        "long_short_ratio",
        "depth_imbalance",
        "mark_price",
        "index_price",
        "basis_pct",
    )
    critical = (
        "oi_change_1h",
        "funding_pct",
        "taker_ratio",
        "long_short_ratio",
        "depth_imbalance",
    )

    def selected_bundle(chosen: dict[str, Any], sources: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "provider": "MULTI_VENUE_PUBLIC",
            "selected_provider": chosen.get("provider"),
            "symbol": symbol,
            "observed_at_utc": _now_iso(),
            "source_age_seconds":chosen.get("source_age_seconds"),
            "field_source": dict(chosen.get("field_source") or {}),
            "errors": [e for src in sources.values() for e in (src.get("errors") or [])],
            "sources": sources,
        }
        for key in fields:
            out[key] = chosen.get(key)
        out["critical_fields"] = list(critical)
        out["available_critical"] = sum(out.get(k) is not None for k in critical)
        out["coverage"] = out["available_critical"] / float(len(critical))
        out["v3_core_fields"] = list(chosen.get("v3_core_fields") or [])
        out["v3_core_available"] = int(chosen.get("v3_core_available") or 0)
        out["v3_core_coverage"] = float(chosen.get("v3_core_coverage") or 0.0)
        out["v3_core_ready"] = bool(chosen.get("v3_core_ready"))
        out["quality"] = str(chosen.get("quality") or "UNAVAILABLE")
        out["source_consistent"] = True
        return out

    # Short-circuit on a complete primary source. This is both faster and more
    # semantically coherent than unconditional three-venue mixing.
    if by.get("v3_core_ready"):
        return selected_bundle(by, {"bybit": by})

    try:
        gate = gate_derivatives(symbol)
    except Exception as exc:
        gate = empty("GATE_FUTURES", exc)

    if gate.get("v3_core_ready"):
        return selected_bundle(gate, {"bybit": by, "gate": gate})

    try:
        ok = okx_derivatives(symbol)
    except Exception as exc:
        ok = empty("OKX_SWAP", exc)

    # Diagnostic fusion only. This may show that all five facts exist somewhere,
    # but it is deliberately never marked FULL because the semantics are
    # cross-venue. The analyst therefore cannot issue an actionable alert from it.
    sources = {"bybit": by, "gate": gate, "okx": ok}
    out: dict[str, Any] = {
        "provider": "MULTI_VENUE_PUBLIC",
        "selected_provider": None,
        "symbol": symbol,
        "observed_at_utc": _now_iso(),
        "field_source": {},
        "errors": [e for src in sources.values() for e in (src.get("errors") or [])],
        "sources": sources,
        "source_consistent": False,
    }
    for key in fields:
        val = None
        src_name = None
        for src in (by, gate, ok):
            if src.get(key) is not None:
                val = src.get(key)
                src_name = (src.get("field_source") or {}).get(key) or src.get("provider")
                break
        out[key] = val
        if val is not None and src_name:
            out["field_source"][key] = src_name

    available = sum(out.get(k) is not None for k in critical)
    out["critical_fields"] = list(critical)
    out["available_critical"] = available
    out["coverage"] = available / float(len(critical))
    out["diagnostic_full"] = available == len(critical)
    out["v3_core_fields"] = ["oi_change_1h","funding_pct","taker_ratio","long_short_ratio"]
    out["v3_core_available"] = sum(out.get(k) is not None for k in out["v3_core_fields"])
    out["v3_core_coverage"] = out["v3_core_available"] / 4.0
    out["v3_core_ready"] = False  # mixed venues are diagnostic, never actionable
    out["quality"] = "PARTIAL" if available >= 2 else "UNAVAILABLE"
    return out


def multi_venue_perp_universe() -> list[dict[str, Any]]:
    """Broad USDT-perpetual discovery feed for geo-blocked Binance runners.

    This is a discovery layer only. It never claims that a symbol is Binance-native.
    Bybit/Gate turnover is used to avoid collapsing the candidate universe to
    Binance Spot volume when Binance Futures REST returns HTTP 451.
    """
    merged: dict[str, dict[str, Any]] = {}

    def add(symbol, quote_volume, last_price, day_change_pct, provider):
        sym = str(symbol or "").upper().replace("_", "")
        if not sym.endswith("USDT"):
            return
        qv = _fv(quote_volume, 0.0) or 0.0
        px = _fv(last_price, 0.0) or 0.0
        ch = _fv(day_change_pct, 0.0) or 0.0
        if qv <= 0 or px <= 0:
            return
        row = merged.setdefault(sym, {
            "symbol": sym,
            "quote_volume": 0.0,
            "last_price": px,
            "day_change_pct": ch,
            "providers": [],
            "provider_stats": {},
        })
        row["providers"].append(provider) if provider not in row["providers"] else None
        row["provider_stats"][provider] = {
            "quote_volume": qv,
            "last_price": px,
            "day_change_pct": ch,
        }
        # For ranking, retain the most liquid venue's change/price snapshot.
        if qv >= float(row.get("quote_volume") or 0.0):
            row["quote_volume"] = qv
            row["last_price"] = px
            row["day_change_pct"] = ch

    try:
        x = _bybit("/v5/market/tickers", {"category": "linear"})
        for r in (x["result"].get("list") or []):
            sym = str(r.get("symbol") or "")
            if not sym.endswith("USDT"):
                continue
            turnover = _fv(r.get("turnover24h"), 0.0) or 0.0
            change = (_fv(r.get("price24hPcnt"), 0.0) or 0.0) * 100.0
            add(sym, turnover, r.get("lastPrice"), change, "BYBIT_LINEAR")
    except Exception:
        pass

    try:
        rows = _gate("/futures/usdt/tickers")
        for r in (rows or []):
            contract = str(r.get("contract") or "")
            if not contract.endswith("_USDT"):
                continue
            sym = contract.replace("_", "")
            last = _fv(r.get("last"), 0.0) or 0.0
            qv = (
                _fv(r.get("volume_24h_quote"))
                or _fv(r.get("volume_24h_settle"))
                or 0.0
            )
            if not qv:
                base_vol = _fv(r.get("volume_24h_base")) or _fv(r.get("volume_24h")) or 0.0
                qv = base_vol * last
            add(sym, qv, last, r.get("change_percentage"), "GATE_FUTURES")
    except Exception:
        pass

    return sorted(
        merged.values(),
        key=lambda r: (abs(float(r.get("day_change_pct") or 0.0)), float(r.get("quote_volume") or 0.0)),
        reverse=True,
    )


def multi_venue_perp_klines(symbol: str, interval: str, limit: int = 220, *, start_ms: int | None = None, provider: str | None = None) -> dict[str, Any]:
    """Return normalized perpetual candles from Bybit, then Gate.

    Rows follow Binance-kline positions used by long_short_analyst.parse_klines:
    open time, OHLC, base volume, close time, quote volume, trades, taker fields.
    """
    interval_ms = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
    }
    bybit_interval = {
        "1m": "1", "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "4h": "240", "1d": "D",
    }
    gate_interval = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "4h": "4h", "1d": "1d",
    }
    if interval not in interval_ms:
        raise ValueError(f"unsupported interval: {interval}")

    errors = []
    if provider not in (None, "BYBIT_LINEAR", "GATE_FUTURES"):
        raise ValueError("unsupported historical venue")
    if provider in (None, "BYBIT_LINEAR"):
      try:
        x = _bybit(
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": bybit_interval[interval],
                "limit": min(int(limit), 1000),
                **({"start": int(start_ms), "end": int(start_ms) + min(int(limit),1000)*interval_ms[interval]-1} if start_ms is not None else {}),
            },
        )
        raw = list(x["result"].get("list") or [])
        rows = []
        for r in raw:
            if len(r) < 7:
                continue
            ts = int(float(r[0]))
            quote_volume = _fv(r[6], 0.0) or 0.0
            rows.append([
                ts, r[1], r[2], r[3], r[4], r[5],
                ts + interval_ms[interval] - 1,
                str(quote_volume), None, "0", None, "0",
            ])
        rows.sort(key=lambda r: int(r[0]))
        if rows:
            return {"provider": "BYBIT_LINEAR", "rows": rows}
      except Exception as exc:
        errors.append("bybit:" + type(exc).__name__ + ":" + str(exc)[:100])

    if provider in (None, "GATE_FUTURES"):
      try:
        base = symbol[:-4] if symbol.endswith("USDT") else symbol
        contract = f"{base}_USDT"
        raw = _gate(
            "/futures/usdt/candlesticks",
            {
                "contract": contract,
                "interval": gate_interval[interval],
                **({"from": int(start_ms)//1000, "to": (int(start_ms)+min(int(limit),2000)*interval_ms[interval]-1)//1000} if start_ms is not None else {"limit": min(int(limit), 2000)}),
            },
        )
        rows = []
        for r in (raw or []):
            ts = int(float(r.get("t") or 0)) * 1000
            if ts <= 0:
                continue
            quote_volume = _fv(r.get("sum"), 0.0) or 0.0
            base_volume = _fv(r.get("v"), 0.0) or 0.0
            rows.append([
                ts, r.get("o"), r.get("h"), r.get("l"), r.get("c"), str(base_volume),
                ts + interval_ms[interval] - 1,
                str(quote_volume), None, "0", None, "0",
            ])
        rows.sort(key=lambda r: int(r[0]))
        if rows:
            return {"provider": "GATE_FUTURES", "rows": rows}
      except Exception as exc:
        errors.append("gate:" + type(exc).__name__ + ":" + str(exc)[:100])

    raise RuntimeError(f"{symbol} {interval}: no external perp candles; " + " | ".join(errors))


def bybit_linear_execution_book(symbol: str, notional_usdt: float = 500.0) -> dict[str, Any]:
    """Research-only Bybit linear order-book snapshot; never authorizes trades."""
    if notional_usdt <= 0:
        raise ValueError("notional must be positive")
    data = _bybit("/v5/market/orderbook", {
        "category": "linear", "symbol": symbol, "limit": 50,
    })
    book = data.get("result") or {}
    bids = book.get("b") or []
    asks = book.get("a") or []
    if not bids or not asks:
        return {"provider": "BYBIT_LINEAR", "available": False}
    bid = float(bids[0][0])
    ask = float(asks[0][0])
    if not (0 < bid <= ask):
        return {"provider": "BYBIT_LINEAR", "available": False}
    mid = (bid + ask) / 2
    def walk(levels):
        remaining = float(notional_usdt)
        spent = 0.0
        quantity = 0.0
        for price_raw, quantity_raw in levels:
            price = float(price_raw)
            size = float(quantity_raw)
            if price <= 0 or size <= 0:
                continue
            take = min(size, remaining / price)
            quantity += take
            spent += take * price
            remaining -= take * price
            if remaining <= 1e-8:
                break
        if remaining > notional_usdt * 0.001 or quantity <= 0:
            return None
        return spent / quantity
    buy = walk(asks)
    sell = walk(bids)
    return {
        "provider": "BYBIT_LINEAR",
        "available": buy is not None and sell is not None,
        "observed_at_utc": _now_iso(),
        "notional_usdt": float(notional_usdt),
        "spread_bps": (ask - bid) / mid * 10000,
        "buy_bps": (buy / mid - 1) * 10000 if buy is not None else None,
        "sell_bps": (1 - sell / mid) * 10000 if sell is not None else None,
        "research_only": True,
    }


def bybit_linear_paper_snapshot(symbol: str, interval: str = "5m",
                                limit: int = 120, notional_usdt: float = 500.0) -> dict[str, Any]:
    """One-venue, read-only paper observation. No trading approval."""
    candles = multi_venue_perp_klines(symbol, interval, limit, provider="BYBIT_LINEAR")
    rows = candles["rows"]
    now_ms = int(time.time() * 1000)
    closed = [r for r in rows if int(r[6]) < now_ms - 250]
    if len(closed) < 20:
        return {"qualified": False, "reason": "insufficient_closed_bybit_candles",
                "venue": "BYBIT_LINEAR", "symbol": symbol}
    book = bybit_linear_execution_book(symbol, notional_usdt)
    if not book.get("available"):
        return {"qualified": False, "reason": "bybit_orderbook_unavailable",
                "venue": "BYBIT_LINEAR", "symbol": symbol, "book": book}
    last_close = float(closed[-1][4])
    spread = book.get("spread_bps")
    if spread is None or not math.isfinite(float(spread)):
        return {"qualified": False, "reason": "invalid_bybit_spread",
                "venue": "BYBIT_LINEAR", "symbol": symbol}
    return {"qualified": False, "reason": "research_only_no_directional_confirmation",
            "venue": "BYBIT_LINEAR", "symbol": symbol,
            "chart_venue": "BYBIT_LINEAR", "book_venue": book["provider"],
            "closed_candle_count": len(closed), "last_closed_price": last_close,
            "last_closed_at_ms": int(closed[-1][6]), "book": book}


def gate_linear_paper_snapshot(symbol: str, interval: str = "5m",
                               limit: int = 120) -> dict[str, Any]:
    """Read-only same-venue Gate futures data-health check; never a trade signal."""
    contract = symbol[:-4] + "_USDT" if symbol.endswith("USDT") else symbol
    out = {"qualified": False, "venue": "GATE_FUTURES", "symbol": symbol}
    try:
        candles = multi_venue_perp_klines(symbol, interval, limit,
                                           provider="GATE_FUTURES")
        now_ms = int(time.time() * 1000)
        closed = [row for row in candles["rows"] if int(row[6]) < now_ms - 250]
        if len(closed) < 20:
            return {**out, "reason": "insufficient_closed_gate_candles",
                    "closed_candle_count": len(closed)}
        book = _gate("/futures/usdt/order_book",
                     {"contract": contract, "limit": 50, "interval": 0})
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if not bids or not asks:
            return {**out, "reason": "gate_orderbook_empty"}
        bid, ask = float(bids[0]["p"]), float(asks[0]["p"])
        if not (0 < bid <= ask):
            return {**out, "reason": "invalid_gate_book"}
        return {**out, "reason": "research_only_no_directional_confirmation",
                "closed_candle_count": len(closed),
                "last_closed_price": float(closed[-1][4]),
                "last_closed_at_ms": int(closed[-1][6]),
                "spread_bps": (ask - bid) / ((ask + bid) / 2) * 10000,
                "book_venue": "GATE_FUTURES", "chart_venue": "GATE_FUTURES"}
    except Exception as exc:
        return {**out, "reason": "gate_data_unavailable",
                "error": type(exc).__name__ + ": " + str(exc)[:300]}


if __name__ == "__main__":
    import json
    import sys

    sym = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    mode = sys.argv[2] if len(sys.argv) > 2 else "derivatives"
    if mode == "bybit-paper":
        output = bybit_linear_paper_snapshot(sym)
    elif mode == "gate-paper":
        output = gate_linear_paper_snapshot(sym)
    elif mode == "gate-derivatives":
        output = gate_derivatives(sym)
    elif mode == "derivatives":
        output = multi_venue_derivatives(sym)
    else:
        raise SystemExit("mode must be derivatives, bybit-paper, gate-paper or gate-derivatives")
    print(json.dumps(output, ensure_ascii=False, indent=2))
