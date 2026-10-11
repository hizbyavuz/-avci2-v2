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
    # Anti-chase: detect the first abnormal-volume candle within the recent window.
    # Measure directional price travel since that candle, not just distance to breakout.
    sample = candles[-21:]
    first_anomaly = None
    for j in range(1, len(sample)):
        baseline_j = sum(float(x["volume"]) for x in sample[:j]) / j
        if j >= 5 and baseline_j > 0 and float(sample[j]["volume"]) >= 1.8 * baseline_j:
            first_anomaly = j
            break
    anchor_price = float(sample[first_anomaly]["open"]) if first_anomaly is not None and "open" in sample[first_anomaly] else None
    if anchor_price is None:
        # Conservative fallback: earlier range boundary, not a fabricated first anomaly.
        anchor_price = float(history[0].get("open", history[0]["close"]))
    travel_pct = (price / anchor_price - 1) * 100 * (1 if side == "LONG" else -1)
    recent_prices = [float(x["close"]) for x in candles[-9:]]
    last_move_pct = (price / recent_prices[0] - 1) * 100 * (1 if side == "LONG" else -1)
    # Observational conservative guard; later tune only after forward validation.
    late = extended or travel_pct >= 4.0 or last_move_pct >= 3.0

    return {
        "side": side,
        "state": "GEC_KOVALAMA" if late else "ERKEN_IZLE",
        "travel_pct": round(travel_pct, 2),
        "last_move_pct": round(last_move_pct, 2),
        "anomaly_detected": first_anomaly is not None,
        "price": price,
        "trigger": trigger,
        "volume_ratio": round(volume_ratio, 2),
        "confirmed": False,
        "note": "Gozlem; kapanis, alan ve risk/odul teyidi gerekli",
    }
