#!/usr/bin/env python3
"""Read-only Gate USDT perpetual order-book observation; never approves trades."""
from datetime import datetime, timezone
import math
import requests

GATE_URL = "https://api.gateio.ws/api/v4/futures/usdt/order_book"
CONTRACT_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts/"

def _vwap(levels, multiplier, notional):
    remaining = float(notional)
    spent = 0.0
    quantity = 0.0
    for price, contracts in levels:
        base_quantity = contracts * multiplier
        take_base = min(base_quantity, remaining / price)
        spent += take_base * price
        quantity += take_base
        remaining -= take_base * price
        if remaining <= max(0.01, notional * 0.001):
            break
    return spent / quantity if remaining <= max(0.01, notional * 0.001) and quantity > 0 else None


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
        metadata_response = requests.get(CONTRACT_URL + contract, timeout=timeout)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        multiplier = float(metadata["quanto_multiplier"])
        if not math.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("invalid_contract_multiplier")
        buy_vwap = _vwap(ask_levels, multiplier, float(notional_usdt))
        sell_vwap = _vwap(bid_levels, multiplier, float(notional_usdt))
        mid = (best_bid + best_ask) / 2
        return {
            "available": True, "execution_verified": False,
            "source": "GATE_USDT_PERP_BOOK_SHADOW", "symbol": symbol,
            "contract": contract, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "best_bid": best_bid, "best_ask": best_ask,
            "spread_bps": (best_ask-best_bid)/((best_bid+best_ask)/2)*10000,
            "bid_levels": len(bid_levels), "ask_levels": len(ask_levels),
            "requested_notional_usdt": float(notional_usdt),
            "quanto_multiplier": multiplier,
            "buy_vwap": buy_vwap, "sell_vwap": sell_vwap,
            "buy_bps": (buy_vwap / mid - 1) * 10000 if buy_vwap else None,
            "sell_bps": (1 - sell_vwap / mid) * 10000 if sell_vwap else None,
            "depth_sufficient": buy_vwap is not None and sell_vwap is not None,
            "reason": "shadow_only_no_freshness_or_same_venue_signal_validation",
        }
    except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
        return {"available": False, "execution_verified": False,
                "source": "GATE_USDT_PERP_BOOK_SHADOW", "symbol": symbol,
                "reason": type(exc).__name__ + ":" + str(exc)[:160]}
