"""Read-only Coinalyze Binance USD-M funding probe; never sends orders."""
import time
import requests
import sqlite3
_CACHE = {}
from long_short_coinalyze_adapter import _cached_request, _market

def funding_snapshot(symbol, now=None):
    now = time.time() if now is None else float(now)
    cached = _CACHE.get(symbol.upper())
    if cached and now - cached[0] < 120:
        return dict(cached[1])
    out = {"source": "COINALYZE_BINANCE_A", "symbol": symbol.upper(),
           "funding_pct": None, "funding_fresh": False, "funding_age_seconds": None}
    try:
        market = _market(symbol)
        if not market:
            return out
        key, _ = market
        rows = _cached_request("/funding-rate", {"symbols": key}, ttl=600)
    except (requests.RequestException, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        out["error_type"] = type(exc).__name__
        return out
    if not isinstance(rows, list):
        return out
    row = next((x for x in rows if isinstance(x, dict) and x.get("symbol") == key), None)
    if not row:
        return out
    stamp = float(row["update"])
    if stamp > 1e12:
        stamp /= 1000
    age = now - stamp
    out["funding_age_seconds"] = round(age, 1)
    if 0 <= age <= 600:
        # Coinalyze documents its funding rates in percentage units.
        out["funding_pct"] = float(row["value"])
        out["funding_fresh"] = True
        _CACHE[symbol.upper()] = (now, dict(out))
    return out
