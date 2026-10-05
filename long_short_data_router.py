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
        "oi_change_1h": None,
        "oi_now": None,
        "funding_pct": None,
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
        "oi_change_1h": None,
        "oi_now": None,
        "funding_pct": None,
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
        "oi_change_1h": None,
        "oi_now": None,
        "funding_pct": None,
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
            oi_vals = [_fv(r.get("open_interest_usd")) for r in rows]
            if not any(v is not None and v > 0 for v in oi_vals):
                oi_vals = [_fv(r.get("open_interest")) for r in rows]
            oi_vals = [v for v in oi_vals if v is not None and v > 0]
            if oi_vals:
                out["oi_now"] = oi_vals[-1]
                out["field_source"]["oi_now"] = "GATE_FUTURES"
            if len(oi_vals) >= 2:
                out["oi_change_1h"] = _pct(oi_vals[0], oi_vals[-1])
                out["field_source"]["oi_change_1h"] = "GATE_FUTURES"

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
    available = sum(out.get(k) is not None for k in critical)
    coverage = available / float(len(critical))
    out["critical_fields"] = list(critical)
    out["available_critical"] = available
    out["coverage"] = coverage
    out["quality"] = "FULL" if available == len(critical) else ("PARTIAL" if available >= 2 else "UNAVAILABLE")
    return out


def multi_venue_derivatives(symbol: str) -> dict[str, Any]:
    """Fuse Bybit + OKX without hiding provenance.

    Bybit is the primary public fallback. Gate is the second full public
    fallback because contract_stats exposes OI history, taker and account ratios.
    OKX remains a corroborating/field-level fallback. A field is never replaced
    with a neutral 0/1 just because an exchange call failed.
    """
    try:
        by = bybit_derivatives(symbol)
    except Exception as exc:
        by = _finish({
            "provider": "BYBIT_LINEAR",
            "symbol": symbol,
            "observed_at_utc": _now_iso(),
            "oi_change_1h": None,
            "oi_now": None,
            "funding_pct": None,
            "taker_ratio": None,
            "long_short_ratio": None,
            "depth_imbalance": None,
            "mark_price": None,
            "index_price": None,
            "basis_pct": None,
            "field_source": {},
            "errors": ["bybit_bundle:" + type(exc).__name__ + ":" + str(exc)[:100]],
        })

    try:
        ok = okx_derivatives(symbol)
    except Exception as exc:
        ok = _finish({
            "provider": "OKX_SWAP",
            "symbol": symbol,
            "observed_at_utc": _now_iso(),
            "oi_change_1h": None,
            "oi_now": None,
            "funding_pct": None,
            "taker_ratio": None,
            "long_short_ratio": None,
            "depth_imbalance": None,
            "mark_price": None,
            "index_price": None,
            "basis_pct": None,
            "field_source": {},
            "errors": ["okx_bundle:" + type(exc).__name__ + ":" + str(exc)[:100]],
        })

    try:
        gate = gate_derivatives(symbol)
    except Exception as exc:
        gate = _finish({
            "provider": "GATE_FUTURES",
            "symbol": symbol,
            "observed_at_utc": _now_iso(),
            "oi_change_1h": None,
            "oi_now": None,
            "funding_pct": None,
            "taker_ratio": None,
            "long_short_ratio": None,
            "depth_imbalance": None,
            "mark_price": None,
            "index_price": None,
            "basis_pct": None,
            "field_source": {},
            "errors": ["gate_bundle:" + type(exc).__name__ + ":" + str(exc)[:100]],
        })

    fields = (
        "oi_change_1h",
        "oi_now",
        "funding_pct",
        "taker_ratio",
        "long_short_ratio",
        "depth_imbalance",
        "mark_price",
        "index_price",
        "basis_pct",
    )
    fused: dict[str, Any] = {
        "provider": "MULTI_VENUE_PUBLIC",
        "symbol": symbol,
        "observed_at_utc": _now_iso(),
        "field_source": {},
        "errors": list(by.get("errors") or []) + list(ok.get("errors") or []) + list(gate.get("errors") or []),
        "sources": {"bybit": by, "okx": ok, "gate": gate},
    }

    # Prefer Bybit for semantically complete derivatives features; use OKX only
    # when the equivalent field is unavailable.
    for key in fields:
        val = by.get(key)
        source = (by.get("field_source") or {}).get(key)
        if val is None:
            val = gate.get(key)
            source = (gate.get("field_source") or {}).get(key)
        if val is None:
            val = ok.get(key)
            source = (ok.get("field_source") or {}).get(key)
        fused[key] = val
        if val is not None and source:
            fused["field_source"][key] = source

    critical = (
        "oi_change_1h",
        "funding_pct",
        "taker_ratio",
        "long_short_ratio",
        "depth_imbalance",
    )
    available = sum(fused.get(k) is not None for k in critical)
    fused["critical_fields"] = list(critical)
    fused["available_critical"] = available
    fused["coverage"] = available / float(len(critical))
    fused["quality"] = "FULL" if available == len(critical) else ("PARTIAL" if available >= 2 else "UNAVAILABLE")
    return fused


if __name__ == "__main__":
    import json
    import sys

    sym = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    print(json.dumps(multi_venue_derivatives(sym), ensure_ascii=False, indent=2))
