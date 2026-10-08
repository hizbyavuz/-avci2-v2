#!/usr/bin/env python3
"""V4 independent, venue-aware paper math. Never edits V3.1 or sends orders."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

VERSION = "LS_V4_PAPER_MATH_2026_10_08"

def linear_return_pct(direction: str, entry: float, exit_price: float) -> float:
    """USDT linear perpetual, return on initial notional; not inverse contracts."""
    if direction not in ("LONG", "SHORT") or entry <= 0 or exit_price <= 0:
        raise ValueError("invalid direction or price")
    sign = 1 if direction == "LONG" else -1
    return sign * (exit_price - entry) / entry * 100.0

def valid_levels(direction: str, entry: float, stop: float, tp1: float, tp2: float) -> bool:
    if direction == "LONG":
        return 0 < stop < entry < tp1 < tp2
    if direction == "SHORT":
        return 0 < tp2 < tp1 < entry < stop
    return False

def path_result(direction: str, entry: float, stop: float, tp1: float, tp2: float,
                bars: Iterable[dict], fee_bps_per_side: float = 5.0,
                slippage_bps_per_side: float = 10.0, funding_pct: float | None = None,
                expected_step_ms: int = 60000) -> dict:
    """Chronological closed 1m candles, stop-first on intrabar ambiguity.
    TP2 only credited if reached AFTER TP1 and before a stop. We assume
    full TP1 exit for realized PnL; TP2 is a separate *path* statistic.
    No forward-fill, no venue blending and no invented funding.
    """
    if not valid_levels(direction, entry, stop, tp1, tp2):
        return {"status": "INVALID_LEVELS"}
    rows = list(bars)
    if not rows:
        return {"status": "DATA_FAILURE", "reason": "NO_BARS"}
    if (fee_bps_per_side < 0 or slippage_bps_per_side < 0 or
            expected_step_ms <= 0):
        return {"status": "INVALID_COSTS"}
    times = [int(r["open_ms"]) for r in rows]
    if times != sorted(times) or len(times) != len(set(times)):
        return {"status": "DATA_FAILURE", "reason": "ORDER_OR_DUPLICATE"}
    if any(b - a != expected_step_ms for a, b in zip(times, times[1:])):
        return {"status": "DATA_FAILURE", "reason": "MISSING_CANDLE"}
    if any(not bool(r.get("closed", False)) for r in rows):
        return {"status": "DATA_FAILURE", "reason": "UNFINISHED_CANDLE"}
    if any(r.get("source") != rows[0].get("source") for r in rows):
        return {"status": "DATA_FAILURE", "reason": "MIXED_VENUE"}
    source = rows[0].get("source")
    if not source:
        return {"status": "DATA_FAILURE", "reason": "NO_SOURCE"}
    tp1_at = None
    tp2_at = None
    stop_at = None
    exit_price = float(rows[-1]["close"])
    first = "TIMEOUT"
    mfe = 0.0
    mae = 0.0
    for r in rows:
        lo, hi, op, cl = (float(r[k]) for k in ("low", "high", "open", "close"))
        if not (0 < lo <= min(op, cl) <= max(op, cl) <= hi):
            return {"status": "DATA_FAILURE", "reason": "BAD_OHLC"}
        favored = hi if direction == "LONG" else lo
        adverse = lo if direction == "LONG" else hi
        mfe = max(mfe, linear_return_pct(direction, entry, favored))
        mae = min(mae, linear_return_pct(direction, entry, adverse))
        stop_hit = lo <= stop if direction == "LONG" else hi >= stop
        first_hit = hi >= tp1 if direction == "LONG" else lo <= tp1
        second_hit = hi >= tp2 if direction == "LONG" else lo <= tp2
        # First barrier: stop-first in a candle that crosses both levels.
        if first == "TIMEOUT":
            if stop_hit:
                first, stop_at = "STOP", r["open_ms"]
                exit_price = min(op, stop) if direction == "LONG" else max(op, stop)
            elif first_hit:
                first, tp1_at = "TP1", r["open_ms"]
                exit_price = tp1
            if first != "TIMEOUT":
                continue
        # This is research-only continuation AFTER first TP1, not realized PnL.
        if first == "TP1" and tp2_at is None and stop_at is None:
            if stop_hit:
                stop_at = r["open_ms"]
            elif second_hit:
                tp2_at = r["open_ms"]
    gross = linear_return_pct(direction, entry, exit_price)
    costs = 2 * (float(fee_bps_per_side) + float(slippage_bps_per_side)) / 100.0
    # Null funding is UNKNOWN, not forced to zero.
    net_without_funding = gross - costs
    net = None if funding_pct is None else net_without_funding - float(funding_pct)
    risk_pct = abs(entry - stop) / entry * 100.0
    return {
        "status": "LABELED", "version": VERSION, "source": source,
        "first_barrier": first, "tp1_time_ms": tp1_at,
        "tp2_after_tp1_without_stop": tp2_at is not None,
        "tp2_time_ms": tp2_at, "stop_time_ms": stop_at,
        "entry": entry, "realized_exit_price": exit_price,
        "gross_pct": gross, "cost_pct": costs,
        "net_before_funding_pct": net_without_funding,
        "net_pct": net, "funding_known": funding_pct is not None,
        "risk_pct": risk_pct, "net_r": net / risk_pct if net is not None else None,
        "mfe_pct": mfe, "mae_pct": mae,
        "n_closed_bars": len(rows), "first_open_ms": times[0],
        "last_open_ms": times[-1],
    }
