"""V4.0-GATE research-only universe discovery. No production wiring or orders."""
from __future__ import annotations
import math
import requests

BASE = "https://api.gateio.ws/api/v4/futures/usdt"
MIN_QUOTE_VOLUME_USDT = 25_000_000  # unchanged production liquidity floor
TIMEOUT = 12

class GateDataError(RuntimeError):
    pass

def _get(path: str):
    try:
        response = requests.get(BASE + path, timeout=TIMEOUT, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise GateDataError(f"{path}: expected list")
        return data
    except (requests.RequestException, ValueError) as exc:
        raise GateDataError(f"{path}: {type(exc).__name__}") from exc

def gate_usdt_perpetual_universe(min_quote_volume=MIN_QUOTE_VOLUME_USDT):
    """Return eligible Gate USDT perpetuals; fail closed on missing/invalid liquidity data."""
    contracts = _get("/contracts")
    tickers = _get("/tickers")
    by_name = {t.get("contract"): t for t in tickers if isinstance(t, dict)}
    eligible = []
    rejected = {"inactive": 0, "missing_ticker_or_volume": 0, "below_liquidity_floor": 0}
    for c in contracts:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "")
        if not isinstance(name, str) or not name.endswith("_USDT"):
            continue
        if c.get("in_delisting") or c.get("enable_bonus") or c.get("type") == "inverse":
            rejected["inactive"] += 1
            continue
        t = by_name.get(name)
        try:
            vol = float(t["volume_24h_quote"]) if t else float("nan")
        except (ValueError, TypeError, KeyError):
            vol = float("nan")
        if not math.isfinite(vol) or vol < 0:
            rejected["missing_ticker_or_volume"] += 1
            continue
        if vol < min_quote_volume:
            rejected["below_liquidity_floor"] += 1
            continue
        eligible.append({"gate_contract": name, "symbol": name.replace("_", ""), "quote_volume_24h_usdt": vol})
    eligible.sort(key=lambda x: x["quote_volume_24h_usdt"], reverse=True)
    return {"venue": "GATE_FUTURES_USDT", "eligible": eligible, "rejected": rejected, "liquidity_floor_usdt": min_quote_volume}

if __name__ == "__main__":
    import json
    data = gate_usdt_perpetual_universe()
    print(json.dumps({"venue": data["venue"], "eligible_count": len(data["eligible"]), "rejected": data["rejected"], "sample": data["eligible"][:10]}, ensure_ascii=False))
