"""Market-flow observations for paper research; never changes candidate scoring."""

import json
import sqlite3
from datetime import datetime

from binance_scanner import spot_api_get


def imbalance(depth, levels=20):
    try:
        bids = sum(float(p) * float(q) for p, q in depth["bids"][:levels])
        asks = sum(float(p) * float(q) for p, q in depth["asks"][:levels])
        return (bids - asks) / (bids + asks) if bids + asks > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def tape(symbol, scan_time):
    end_ms = int(datetime.fromisoformat(scan_time).timestamp() * 1000)
    rows = spot_api_get("/api/v3/aggTrades", {
        "symbol": symbol, "startTime": end_ms - 180000,
        "endTime": end_ms, "limit": 1000,
    })
    if not isinstance(rows, list) or len(rows) >= 1000:
        # A partial window cannot establish a reliable buy streak.
        return None
    large = [row for row in rows if float(row["p"]) * float(row["q"]) >= 1000]
    buy = sum(not row["m"] for row in large)
    sell = sum(bool(row["m"]) for row in large)
    streak = max_streak = 0
    sell_streak = max_sell_streak = 0
    for row in large:
        if not row["m"]:
            streak += 1
            sell_streak = 0
        else:
            sell_streak += 1
            streak = 0
        max_streak = max(max_streak, streak)
        max_sell_streak = max(max_sell_streak, sell_streak)

    notionals = [float(row["p"]) * float(row["q"]) for row in rows]
    buy_notional = sum(
        float(row["p"]) * float(row["q"])
        for row in rows
        if not row["m"]
    )
    sell_notional = sum(
        float(row["p"]) * float(row["q"])
        for row in rows
        if row["m"]
    )
    total_notional = sum(notionals)
    top5_share = (sum(sorted(notionals, reverse=True)[:5]) / total_notional
                  if total_notional > 0 else None)

    # Repeated-size and rapid side-alternation are only manipulation proxies;
    # they are not proof of wash trading.
    rounded = [round(value, -1) if value >= 10 else round(value, 2)
               for value in notionals]
    counts = {}
    for value in rounded:
        counts[value] = counts.get(value, 0) + 1
    repeated = sum(count for count in counts.values() if count >= 3)
    repeated_ratio = repeated / len(rows) if rows else None

    alternations = 0
    if len(rows) >= 2:
        sides = [not row["m"] for row in rows]
        alternations = sum(sides[i] != sides[i-1] for i in range(1, len(sides)))
    alternation_ratio = alternations / (len(rows)-1) if len(rows) >= 2 else None

    return {"trade_count": len(rows), "large_trades": len(large),
            "large_buy_share": buy / len(large) if large else None,
            "large_sell_share": sell / len(large) if large else None,
            "large_buy_streak": max_streak,
            "large_sell_streak": max_sell_streak,
            "buy_notional": buy_notional,
            "sell_notional": sell_notional,
            "taker_buy_notional_share": (
                buy_notional / total_notional if total_notional > 0 else None
            ),
            "net_taker_notional": buy_notional - sell_notional,
            "top5_notional_share": top5_share,
            "repeated_notional_ratio": repeated_ratio,
            "side_alternation_ratio": alternation_ratio}


def main():
    conn = sqlite3.connect("binance_avci2.db")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS flow_observations (
            event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
            scan_time_utc TEXT NOT NULL, book_imbalance REAL,
            previous_imbalance REAL, imbalance_change REAL,
            funding_change REAL, quiet_oi_positioning INTEGER,
            tape_json TEXT, observation_error TEXT,
            event_class TEXT)""")
        cols={row[1] for row in conn.execute("PRAGMA table_info(flow_observations)")}
        if "event_class" not in cols:
            conn.execute("ALTER TABLE flow_observations ADD COLUMN event_class TEXT")
        latest = conn.execute("SELECT * FROM scans ORDER BY scan_time_utc DESC LIMIT 1").fetchone()
        if not latest or latest["health_status"] == "INVALID":
            return
        events = conn.execute("""SELECT
                COALESCE(
                    se.event_id,
                    'FLOW|' || f.scan_time_utc || '|' || f.symbol || '|' || f.selection_class
                ) AS event_id,
                f.symbol,
                f.selection_class AS event_class
            FROM features f
            LEFT JOIN signal_events se
              ON se.signal_time_utc=f.scan_time_utc
             AND se.symbol=f.symbol
             AND se.event_class=f.selection_class
            WHERE f.scan_time_utc=?
              AND f.selection_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
            ORDER BY CASE f.selection_class
                WHEN 'CANDIDATE' THEN 0
                WHEN 'NEAR_MISS' THEN 1
                ELSE 2 END, f.score DESC""",
            (latest["scan_time_utc"],)).fetchall()
        for event in events:
            symbol = event["symbol"]
            event_class=event["event_class"]
            books = conn.execute("""SELECT raw_json FROM orderbook_snap
                WHERE symbol=? AND scan_time_utc<=? AND event_class=?
                ORDER BY scan_time_utc DESC LIMIT 2""",
                (symbol, latest["scan_time_utc"], event_class)).fetchall()
            values = [imbalance(json.loads(row["raw_json"])) for row in books]
            current = values[0] if values else None
            previous = values[1] if len(values) > 1 else None
            feature = conn.execute("""SELECT oi_change_1h_pct, change_1h,
                funding_rate FROM features WHERE symbol=? AND scan_time_utc=?
                ORDER BY is_selected DESC LIMIT 1""",
                (symbol, latest["scan_time_utc"])).fetchone()
            old = conn.execute("""SELECT funding_rate FROM raw_derivs WHERE symbol=?
                AND scan_time_utc<? AND funding_rate IS NOT NULL
                ORDER BY scan_time_utc DESC LIMIT 1""",
                (symbol, latest["scan_time_utc"])).fetchone()
            funding_change = (feature["funding_rate"] - old["funding_rate"]
                              if feature and old and feature["funding_rate"] is not None else None)
            quiet = (int(abs(feature["change_1h"] or 0) <= 0.5
                         and (feature["oi_change_1h_pct"] or 0) >= 3)
                     if feature and latest["data_mode"] != "SPOT_ONLY" else None)
            trade_data, error = None, None
            try:
                trade_data = tape(symbol, latest["scan_time_utc"])
            except Exception as exc:
                error = str(exc)[:300]
            conn.execute("""INSERT OR IGNORE INTO flow_observations
                (event_id,symbol,scan_time_utc,book_imbalance,previous_imbalance,
                 imbalance_change,funding_change,quiet_oi_positioning,tape_json,
                 observation_error,event_class)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event["event_id"], symbol, latest["scan_time_utc"], current,
                 previous, current - previous if current is not None and previous is not None else None,
                 funding_change, quiet, json.dumps(trade_data), error, event_class))
            print(f"Akış gözlemi {event_class} {symbol}: defter {current}, değişim "
                  f"{current-previous if current is not None and previous is not None else None}, "
                  f"büyük işlemler {trade_data}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
