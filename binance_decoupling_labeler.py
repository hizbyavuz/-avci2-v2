#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Outcome labeling for BTC-decoupling research events.

This is observational only. It does not alter frozen Avci scoring or alerts.
"""

import json
import sqlite3
from datetime import datetime, timezone

from binance_outcome_labeler import fetch_klines, pct_change

DB = "binance_avci2.db"
TARGETS = (3.0, 5.0, 7.0, 10.0, 15.0)
HORIZONS = (4, 24, 72)


def ensure_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS btc_decoupling_outcomes (
        event_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        signal_time_utc TEXT NOT NULL,
        signal_price REAL NOT NULL,
        measured_at_utc TEXT NOT NULL,
        age_hours REAL NOT NULL,
        mfe_pct REAL,
        mae_pct REAL,
        reach_json TEXT NOT NULL,
        hit_time_json TEXT NOT NULL,
        horizon_return_json TEXT NOT NULL,
        btc_horizon_return_json TEXT NOT NULL,
        excess_vs_btc_json TEXT NOT NULL,
        status TEXT NOT NULL)""")


def event_start_ms(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def analyze_path(signal_price, rows, signal_ms):
    highs, lows = [], []
    first_hits = {str(int(t)): None for t in TARGETS}
    horizon_returns = {}
    last_close = None
    for row in rows:
        open_ms = int(row[0])
        high, low, close = float(row[2]), float(row[3]), float(row[4])
        highs.append(high)
        lows.append(low)
        last_close = close
        gain = pct_change(signal_price, high)
        for target in TARGETS:
            key = str(int(target))
            if first_hits[key] is None and gain >= target:
                first_hits[key] = (open_ms - signal_ms) / 60000.0
        elapsed_h = (open_ms - signal_ms) / 3600000.0
        for horizon in HORIZONS:
            key = str(horizon)
            if key not in horizon_returns and elapsed_h >= horizon:
                horizon_returns[key] = pct_change(signal_price, close)
    mfe = pct_change(signal_price, max(highs)) if highs else None
    mae = pct_change(signal_price, min(lows)) if lows else None
    reached = {k: v is not None for k, v in first_hits.items()}
    return mfe, mae, reached, first_hits, horizon_returns, last_close


def main(path=DB):
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        ensure_schema(con)
        now = datetime.now(timezone.utc)
        events = con.execute("""SELECT * FROM btc_decoupling_events
            WHERE outcome_status!='CLOSED'
            ORDER BY signal_time_utc ASC LIMIT 40""").fetchall()
        if not events:
            print("BTC ayrisma outcome: acik event yok")
            return 0

        written = 0
        for event in events:
            signal_ms = event_start_ms(event["signal_time_utc"])
            age_hours = max(0.0, (now.timestamp() * 1000 - signal_ms) / 3600000.0)
            end_ms = min(int(now.timestamp() * 1000), signal_ms + 72 * 3600000)
            try:
                rows = fetch_klines(event["symbol"], signal_ms, end_ms, 1000)
                btc_rows = fetch_klines("BTCUSDT", signal_ms, end_ms, 1000)
            except Exception as exc:
                print(f"BTC ayrisma outcome {event['symbol']}: veri hatasi {type(exc).__name__}")
                continue
            if not rows or not btc_rows:
                continue

            mfe, mae, reached, hit_times, hret, _ = analyze_path(
                float(event["signal_price"]), rows, signal_ms)
            btc_start = float(btc_rows[0][1])
            _, _, _, _, btc_hret, _ = analyze_path(btc_start, btc_rows, signal_ms)
            excess = {}
            for horizon in HORIZONS:
                key = str(horizon)
                if key in hret and key in btc_hret:
                    excess[key] = hret[key] - btc_hret[key]

            status = "CLOSED" if age_hours >= 72 else "OPEN"
            con.execute("""INSERT OR REPLACE INTO btc_decoupling_outcomes
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event["event_id"], event["symbol"], event["signal_time_utc"],
                 event["signal_price"], now.isoformat(), age_hours, mfe, mae,
                 json.dumps(reached), json.dumps(hit_times),
                 json.dumps(hret), json.dumps(btc_hret), json.dumps(excess), status))
            if status == "CLOSED":
                con.execute("""UPDATE btc_decoupling_events
                    SET outcome_status='CLOSED' WHERE event_id=?""",
                    (event["event_id"],))
            written += 1
        con.commit()
        print(f"BTC ayrisma outcome: {written} event guncellendi")
        return written
    finally:
        con.close()


if __name__ == "__main__":
    main()
