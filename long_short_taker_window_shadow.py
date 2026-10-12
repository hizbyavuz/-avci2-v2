"""Read-only one-minute taker flow summaries for the selected deep-scan symbols.

Shadow-only: does not alter V3 signals or stored raw events. Missing minutes
remain missing; they are never filled with zero or treated as continuous data.
"""
import os
import sqlite3
import time

SOURCE = "BINANCE_UM_FUTURES_WEBSOCKET"
MINUTE_MS = 60_000

def summarize(db_path, symbols, end_ms=None, minutes=5):
    if not 1 <= minutes <= 60:
        raise ValueError("minutes must be 1..60")
    end_ms = int(time.time() * 1000) if end_ms is None else int(end_ms)
    end_ms = end_ms // MINUTE_MS * MINUTE_MS
    start_ms = end_ms - minutes * MINUTE_MS
    names = list(dict.fromkeys(s.upper() for s in symbols))
    if not names or not os.path.isfile(db_path):
        return {s: {"ready": False, "reason": "DB_MISSING_OR_EMPTY_SELECTION"} for s in names}
    results = {}
    try:
        with sqlite3.connect("file:" + os.path.abspath(db_path) + "?mode=ro", uri=True) as conn:
            for symbol in names:
                rows = conn.execute(
                    """SELECT (event_ms / 60000) * 60000 AS minute,
                              COUNT(*),
                              SUM(CASE WHEN signed_quote > 0 THEN signed_quote ELSE 0 END),
                              SUM(CASE WHEN signed_quote < 0 THEN -signed_quote ELSE 0 END)
                       FROM taker_trades
                       WHERE symbol=? AND source=? AND event_ms>=? AND event_ms<?
                       GROUP BY minute ORDER BY minute""",
                    (symbol, SOURCE, start_ms, end_ms),
                ).fetchall()
                buckets = {int(t): (int(n), float(b or 0), float(s or 0))
                           for t, n, b, s in rows}
                missing = [t for t in range(start_ms, end_ms, MINUTE_MS)
                           if t not in buckets]
                buy = sum(v[1] for v in buckets.values())
                sell = sum(v[2] for v in buckets.values())
                ready = not missing and buy > 0 and sell > 0
                results[symbol] = {
                    "ready": ready,
                    "reason": None if ready else ("MISSING_MINUTE_COVERAGE" if missing
                        else "ONE_SIDED_OR_INSUFFICIENT_FLOW"),
                    "source": SOURCE,
                    "window_start_ms": start_ms,
                    "window_end_ms": end_ms,
                    "minutes_with_trades": len(buckets),
                    "missing_minutes": len(missing),
                    "trades": sum(v[0] for v in buckets.values()),
                    "buy_quote": buy,
                    "sell_quote": sell,
                    "taker_buy_sell_ratio": buy / sell if ready else None,
                    "coverage_note": "Trade presence is not proof of gap-free websocket connectivity",
                }
    except (sqlite3.Error, OSError):
        return {s: {"ready": False, "reason": "DB_READ_ERROR"} for s in names}
    return results
