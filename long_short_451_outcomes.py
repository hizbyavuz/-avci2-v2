"""Append-only 451 observation snapshots and forward close-to-close labels.

Research only: no trade execution, no mutation of paper_setups.
Each horizon uses the first eligible later snapshot; missing data stays pending.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone

HORIZONS = (15, 60, 240)


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
                    or not isinstance(price, (float, int)) or price <= 0):
                continue
            con.execute("INSERT OR IGNORE INTO watch_451_observations VALUES (?,?,?,?)",
                        (ts, a.symbol, direction, price))
        # Resolve only using later stored market observations, never pre-signal data.
        # First available snapshot at/after the horizon is a coarse approximation,
        # not a precise horizon close; retain its timestamp for audit.
        current = {a.symbol: a.price for a in analyses
                   if isinstance(getattr(a, "price", None), (float, int)) and a.price > 0}
        for sig_time, symbol, direction, entry in con.execute(
            "SELECT scan_time_utc,symbol,direction,signal_price FROM watch_451_observations"
        ).fetchall():
            if symbol not in current:
                continue
            elapsed = (_time(ts) - _time(sig_time)).total_seconds() / 60
            if elapsed < 0:
                continue
            for horizon in HORIZONS:
                if elapsed < horizon:
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
        con.commit()
