"""Append-only 451 observation snapshots and forward close-to-close labels.

Research only: no trade execution, no mutation of paper_setups.
Each horizon uses the first eligible later snapshot; missing data stays pending.
"""
from __future__ import annotations
import json
import math
import sqlite3
from datetime import datetime, timezone

HORIZONS = (15, 60, 240)
MAX_LABEL_LAG_MINUTES = 10


def _time(s):
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def record_observations(db_path, ts, analyses):
    with sqlite3.connect(db_path) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS watch_451_observations (
            scan_time_utc TEXT NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, signal_price REAL NOT NULL,
            PRIMARY KEY(scan_time_utc,symbol,direction))""")
        con.execute("""CREATE TABLE IF NOT EXISTS watch_451_labels (
            signal_time_utc TEXT NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, horizon_minutes INTEGER NOT NULL,
            observed_time_utc TEXT NOT NULL, observed_price REAL NOT NULL,
            directional_return_pct REAL NOT NULL,
            PRIMARY KEY(signal_time_utc,symbol,direction,horizon_minutes))""")
        for a in analyses:
            d = (getattr(a, "payload", None) or {}).get("direction_engine") or {}
            direction = d.get("direction")
            price = getattr(a, "price", None)
            if (not d.get("observation_only") or d.get("eligible")
                    or direction not in ("LONG", "SHORT")
                    or not isinstance(price, (float, int)) or not math.isfinite(price) or price <= 0):
                continue
            latest = con.execute(
                "SELECT scan_time_utc FROM watch_451_observations WHERE symbol=? AND direction=? ORDER BY scan_time_utc DESC LIMIT 1",
                (a.symbol, direction)
            ).fetchone()
            if latest and 0 <= (_time(ts)-_time(latest[0])).total_seconds() < 7200:
                continue
            con.execute("INSERT OR IGNORE INTO watch_451_observations VALUES (?,?,?,?)",
                        (ts, a.symbol, direction, price))
        # Resolve only using later stored market observations, never pre-signal data.
        # Only label a horizon when a snapshot is within 10 minutes of it.
        # Later observations cannot masquerade as the requested horizon.
        current = {a.symbol: a.price for a in analyses
                   if isinstance(getattr(a, "price", None), (float, int)) and math.isfinite(a.price) and a.price > 0}
        for sig_time, symbol, direction, entry in con.execute(
            "SELECT scan_time_utc,symbol,direction,signal_price FROM watch_451_observations"
        ).fetchall():
            if symbol not in current:
                continue
            elapsed = (_time(ts) - _time(sig_time)).total_seconds() / 60
            if elapsed < 0:
                continue
            for horizon in HORIZONS:
                if elapsed < horizon or elapsed > horizon + MAX_LABEL_LAG_MINUTES:
                    continue
                exists = con.execute(
                    "SELECT 1 FROM watch_451_labels WHERE signal_time_utc=? AND symbol=? AND direction=? AND horizon_minutes=?",
                    (sig_time, symbol, direction, horizon)
                ).fetchone()
                if exists:
                    continue
                ret = (current[symbol] / entry - 1) * 100
                if direction == "SHORT":
                    ret = -ret
                con.execute("INSERT INTO watch_451_labels VALUES (?,?,?,?,?,?,?)",
                            (sig_time, symbol, direction, horizon, ts, current[symbol], ret))
        con.execute("""CREATE TABLE IF NOT EXISTS watch_451_quality (
            signal_time_utc TEXT NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL,
            PRIMARY KEY(signal_time_utc,symbol,direction))""")
        # The current analyst candle router can mix venues and instrument types.
        # Preserve research outcomes, but do not mark them comparable/executable.
        con.executemany(
            "INSERT OR IGNORE INTO watch_451_quality VALUES (?,?,?,?,?)",
            [(sig, sym, direction, "PROVISIONAL", "ENTRY_EXIT_VENUE_NOT_VERIFIED")
             for sig, sym, direction in con.execute(
                 "SELECT scan_time_utc,symbol,direction FROM watch_451_observations"
             ).fetchall()]
        )
        con.commit()


def pending_symbols(db_path, now_utc, max_age_hours=8):
    """Return unresolved symbols, even if omitted from current deep scans."""
    with sqlite3.connect(db_path) as con:
        try:
            rows = con.execute(
                """SELECT o.scan_time_utc,o.symbol,o.direction
                   FROM watch_451_observations o
                   WHERE NOT EXISTS (
                     SELECT 1 FROM watch_451_labels l
                     WHERE l.signal_time_utc=o.scan_time_utc
                       AND l.symbol=o.symbol AND l.direction=o.direction
                       AND l.horizon_minutes=240
                   )"""
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    symbols = set()
    for ts, symbol, direction in rows:
        age = (_time(now_utc)-_time(ts)).total_seconds()/3600
        if 0 <= age <= max_age_hours:
            symbols.add(symbol)
    return sorted(symbols)


def backfill_pending(db_path, now_utc, price_fetcher, max_symbols=20):
    """Use an independent verified price fetcher for symbols no longer scanned.

    price_fetcher(symbol) -> (price, observed_utc) or None.
    No extrapolation: the actual quote timestamp is used for horizon labels.
    """
    symbols = pending_symbols(db_path, now_utc)[:max(0, int(max_symbols))]
    snapshots = {}
    for symbol in symbols:
        try:
            quote = price_fetcher(symbol)
            if not quote:
                continue
            price, quote_time = quote
            if (not isinstance(price, (int,float)) or price <= 0
                    or _time(quote_time) > _time(now_utc)):
                continue
            snapshots.setdefault(quote_time, []).append(
                type("Snapshot", (), {"symbol":symbol, "price":price, "payload":{}})()
            )
        except Exception:
            continue
    for quote_time, analyses in sorted(snapshots.items()):
        record_observations(db_path, quote_time, analyses)
    return sum(len(items) for items in snapshots.values())
