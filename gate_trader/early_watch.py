"""Observation-only early-move detector; never an execution signal."""
from __future__ import annotations

def classify(candles, side, lookback=12):
    """Return early-watch evidence from CLOSED OHLCV candles, or None.

    candles: oldest-first dicts with open/high/low/close/volume.
    This observational layer does not bypass the main risk gates.
    """
    if side not in ("LONG", "SHORT") or len(candles) < lookback + 21:
        return None
    history = candles[-(lookback + 1):-1]
    current = candles[-1]
    previous = candles[-21:-1]
    price = float(current["close"])
    volumes = [float(x["volume"]) for x in previous]
    baseline = sum(volumes) / len(volumes)
    if baseline <= 0:
        return None
    volume_ratio = float(current["volume"]) / baseline
    high = max(float(x["high"]) for x in history)
    low = min(float(x["low"]) for x in history)
    width = high - low
    if width <= 0:
        return None
    position = (price - low) / width
    if side == "LONG":
        approaching = position >= 0.85
        extended = price > high + 0.30 * width
        trigger = high
    else:
        approaching = position <= 0.15
        extended = price < low - 0.30 * width
        trigger = low
    if not approaching or volume_ratio < 1.20:
        return None
    return {
        "side": side,
        "state": "GEC_KOVALAMA" if extended else "ERKEN_IZLE",
        "price": price,
        "trigger": trigger,
        "volume_ratio": round(volume_ratio, 2),
        "confirmed": False,
        "note": "Gozlem; kapanis, alan ve risk/odul teyidi gerekli",
    }
