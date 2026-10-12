"""Binance-only Coinalyze derivatives reader. Read-only; never creates orders.

This adapter intentionally returns None for missing/stale fields. Coinalyze's
long/short series must not be relabeled Binance global account ratio.
"""
import os
import time
import requests

BASE = "https://api.coinalyze.net/v1"
_cache = {}

def _get(path, params=None):
    key = os.getenv("COINALYZE_API_KEY", "")
    if not key:
        raise RuntimeError("COINALYZE_API_KEY missing")
    r = requests.get(BASE + path, headers={"api_key": key}, params=params or {}, timeout=12)
    r.raise_for_status()
    return r.json()

def _market(symbol):
    symbol = symbol.upper()
    key = symbol + "_PERP.A"
    now = time.monotonic()
    cached = _cache.get("markets")
    if not cached or now - cached[0] > 3600:
        rows = _get("/future-markets")
        if not isinstance(rows, list):
            raise ValueError("invalid markets")
        _cache["markets"] = (now, {r.get("symbol"): r for r in rows if isinstance(r, dict)})
    m = _cache["markets"][1].get(key)
    if not m or any((m.get("exchange") != "A", m.get("symbol_on_exchange") != symbol,
                     m.get("is_perpetual") is not True, m.get("quote_asset") != "USDT",
                     m.get("margined") != "STABLE")):
        return None
    return key, m

def snapshot(symbol):
    """Get OI current + 1h change + independent Coinalyze L/S observation.

    Cache 5 minutes per symbol; <= 3 requests/symbol per refresh. 40/min
    provider quota means caller must limit refreshed symbols per minute.
    """
    symbol = symbol.upper()
    now = time.monotonic()
    hit = _cache.get(("snapshot", symbol))
    if hit and now - hit[0] < 300:
        return hit[1]
    out = {"source": "COINALYZE_BINANCE_A", "symbol": symbol,
           "oi_now": None, "oi_change_1h": None, "long_short_ratio": None,
           "oi_fresh": False, "ls_fresh": False}
    try:
        market = _market(symbol)
        if not market:
            return out
        key, meta = market
        unit = meta.get("oi_lq_vol_denominated_in")
        out["oi_unit"] = unit
        current = _get("/open-interest", {"symbols": key})
        row = next((r for r in current if isinstance(r, dict) and r.get("symbol") == key), None)
        if row:
            stamp = float(row["update"])
            if stamp > 1e12: stamp /= 1000
            value = float(row["value"])
            if 0 <= time.time() - stamp <= 180 and value > 0:
                out["oi_now"] = value
                out["oi_fresh"] = True
        if out["oi_fresh"] and unit == "BASE_ASSET":
            end = int(time.time())
            hist = _get("/open-interest-history", {"symbols": key, "interval": "5min",
                        "from": end - 4200, "to": end})
            row = next((r for r in hist if isinstance(r, dict) and r.get("symbol") == key), None)
            candles = row.get("history", []) if row else []
            candles = sorted((x for x in candles if isinstance(x, dict)), key=lambda x: x["t"])
            if len(candles) >= 12:
                first = float(candles[-12]["c"])
                last = float(candles[-1]["c"])
                if first > 0 and last > 0 and 0 <= time.time()-float(candles[-1]["t"]) <= 660:
                    out["oi_change_1h"] = (last / first - 1) * 100
        if meta.get("has_long_short_ratio_data"):
            end = int(time.time())
            ls = _get("/long-short-ratio-history", {"symbols": key, "interval": "5min",
                      "from": end - 3600, "to": end})
            row = next((r for r in ls if isinstance(r, dict) and r.get("symbol") == key), None)
            candles = row.get("history", []) if row else []
            if candles:
                point = max(candles, key=lambda x: x["t"])
                stamp = float(point["t"])
                ratio = float(point["r"])
                if 0 <= time.time() - stamp <= 660 and ratio > 0:
                    out["long_short_ratio"] = ratio
                    out["ls_fresh"] = True
    except (requests.RequestException, ValueError, TypeError, KeyError, StopIteration) as exc:
        out["error_type"] = type(exc).__name__
    _cache[("snapshot", symbol)] = (time.monotonic(), out)
    return out
