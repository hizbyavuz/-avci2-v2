#!/usr/bin/env python3
"""Read-only Gate USDT perpetual order-book observation; never approves trades."""
from datetime import datetime, timezone
import math
import requests

GATE_URL = "https://api.gateio.ws/api/v4/futures/usdt/order_book"

def observe(symbol, notional_usdt=250.0, timeout=8):
    """Return a cost snapshot or an explicit unavailable reason; fail closed."""
    symbol = str(symbol).upper()
    if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
        return {"available": False, "reason": "invalid_symbol"}
    contract = symbol[:-4] + "_USDT"
    try:
        response = requests.get(GATE_URL, params={"contract": contract, "limit": 50, "with_id": "true"}, timeout=timeout)
        response.raise_for_status()
        book = response.json()
        bids, asks = book.get("bids"), book.get("asks")
        if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
            raise ValueError("empty_book")
        def levels(items, reverse):
            result = []
            for item in items:
                price = float(item["p"])
                contracts = float(item["s"])
                if not math.isfinite(price) or not math.isfinite(contracts) or price <= 0 or contracts <= 0:
                    raise ValueError("invalid_level")
                result.append((price, contracts))
            return sorted(result, key=lambda x: x[0], reverse=reverse)
        bid_levels, ask_levels = levels(bids, True), levels(asks, False)
        best_bid, best_ask = bid_levels[0][0], ask_levels[0][0]
        if best_bid >= best_ask:
            raise ValueError("crossed_book")
        # Gate futures order-book sizes are CONTRACT counts, not necessarily base-asset units.
        # Without the contract quanto_multiplier, VWAP and slippage are not executable.
        return {
            "available": True, "execution_verified": False,
            "source": "GATE_USDT_PERP_BOOK_SHADOW", "symbol": symbol,
            "contract": contract, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "best_bid": best_bid, "best_ask": best_ask,
            "spread_bps": (best_ask-best_bid)/((best_bid+best_ask)/2)*10000,
            "bid_levels": len(bid_levels), "ask_levels": len(ask_levels),
            "requested_notional_usdt": float(notional_usdt),
            "reason": "contract_multiplier_and_executable_vwap_not_validated",
        }
    except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
        return {"available": False, "execution_verified": False,
                "source": "GATE_USDT_PERP_BOOK_SHADOW", "symbol": symbol,
                "reason": type(exc).__name__ + ":" + str(exc)[:160]}
