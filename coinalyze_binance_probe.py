"""Read-only Binance USD-M derivatives via Coinalyze. No exchange fallback or orders.

Run inside the existing Railway service:
    python coinalyze_binance_probe.py BTCUSDT ETHUSDT
Requires COINALYZE_API_KEY in the service environment. Never logs the key.
"""
import json
import os
import sys
import time
import requests

BASE = "https://api.coinalyze.net/v1"

def fetch(path, key, params=None):
    r = requests.get(BASE + path, params=params or {}, headers={"api_key": key}, timeout=12)
    r.raise_for_status()
    return r.json()

def probe(symbols):
    key = os.getenv("COINALYZE_API_KEY")
    if not key:
        raise RuntimeError("COINALYZE_API_KEY is missing")
    markets = fetch("/future-markets", key)
    if not isinstance(markets, list):
        raise RuntimeError("Invalid markets response")
    index = {m.get("symbol"): m for m in markets if isinstance(m, dict)}
    selected = {}
    for symbol in symbols:
        symbol = symbol.upper()
        cz = f"{symbol}_PERP.A"
        m = index.get(cz)
        if m and m.get("exchange") == "A" and m.get("symbol_on_exchange") == symbol and m.get("is_perpetual") is True and m.get("quote_asset") == "USDT" and m.get("margined") == "STABLE":
            selected[symbol] = cz
    now = int(time.time())
    out = {s: {"market_verified": s in selected, "source": "coinalyze_binance_usdm", "oi_ok": False, "ls_ok": False} for s in symbols}
    for symbol, cz in selected.items():
        try:
            oi_rows = fetch("/open-interest", key, {"symbols": cz})
            oi = next((x for x in oi_rows if x.get("symbol") == cz), None)
            if oi:
                stamp = float(oi["update"])
                if stamp > 1e12: stamp /= 1000
                age = time.time() - stamp
                out[symbol].update(oi_age_seconds=round(age, 1), oi_value=oi.get("value"), oi_unit=index[cz].get("oi_lq_vol_denominated_in"), oi_ok=0 <= age <= 180)
        except (requests.RequestException, ValueError, TypeError, KeyError, StopIteration) as exc:
            out[symbol]["oi_error"] = type(exc).__name__
        try:
            rows = fetch("/long-short-ratio-history", key, {"symbols": cz, "interval": "5min", "from": now - 3600, "to": now})
            row = next((x for x in rows if x.get("symbol") == cz), None)
            hist = row.get("history") if row else None
            if hist:
                point = max(hist, key=lambda x: float(x["t"]))
                stamp = float(point["t"])
                if stamp > 1e12: stamp /= 1000
                age = time.time() - stamp
                out[symbol].update(ls_age_seconds=round(age, 1), ls_ratio=point.get("r"), ls_ok=0 <= age <= 660)
        except (requests.RequestException, ValueError, TypeError, KeyError, StopIteration) as exc:
            out[symbol]["ls_error"] = type(exc).__name__
    return out

if __name__ == "__main__":
    try:
        print(json.dumps(probe([s.upper() for s in (sys.argv[1:] or ["BTCUSDT", "ETHUSDT"])]), indent=2))
    except Exception as exc:
        print(json.dumps({"status": "PROBE_FAILED", "error_type": type(exc).__name__, "details": str(exc)[:150]}))
        sys.exit(1)
