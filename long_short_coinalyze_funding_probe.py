"""Read-only Coinalyze Binance USD-M funding probe; never sends orders."""
import time
from long_short_coinalyze_adapter import _get, _market

def funding_snapshot(symbol, now=None):
    now = time.time() if now is None else float(now)
    out = {"source": "COINALYZE_BINANCE_A", "symbol": symbol.upper(),
           "funding_pct": None, "funding_fresh": False, "funding_age_seconds": None}
    market = _market(symbol)
    if not market:
        return out
    key, _ = market
    rows = _get("/funding-rate", {"symbols": key})
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
    return out
