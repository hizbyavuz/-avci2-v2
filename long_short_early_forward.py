"""Forward-only outcome tracker for broad early-watch observations.

Only evaluates snapshots arriving after the observation; no historical lookahead.
SQLite is persistent and idempotent, with missing horizons left unresolved.
This module does not influence trade decisions or Telegram.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HORIZONS = (15, 30, 60, 180)
MAX_LAG_MIN = 10


def track(observations):
    if not observations:
        return
    root = Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(root / "early_forward_outcomes.sqlite"), timeout=20)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, state TEXT NOT NULL, price REAL NOT NULL,
            deep_selected INTEGER NOT NULL, meta_json TEXT NOT NULL,
            UNIQUE(ts,symbol))""")
        db.execute("""CREATE TABLE IF NOT EXISTS outcomes (
            observation_id INTEGER NOT NULL, horizon_min INTEGER NOT NULL,
            observed_ts INTEGER NOT NULL, future_price REAL NOT NULL,
            signed_return_pct REAL NOT NULL, PRIMARY KEY(observation_id,horizon_min))""")
        db.execute("CREATE INDEX IF NOT EXISTS ix_obs_pending ON observations(symbol,ts)")
        now = int(datetime.now(timezone.utc).timestamp())
        for item in observations:
            try:
                symbol = str(item["symbol"])
                price = float(item["price"])
                direction = str(item["direction"])
                state = str(item["state"])
                if price <= 0 or direction not in ("LONG", "SHORT") or state != "EARLY_WATCH":
                    continue
                # Resolve only earlier observations, using first eligible subsequent scan
                # within the 10-minute window after each target horizon.
                pending = db.execute("""SELECT o.id,o.ts,o.price,o.direction,h.horizon_min
                    FROM observations o CROSS JOIN
                    (SELECT 15 AS horizon_min UNION ALL SELECT 30 UNION ALL SELECT 60 UNION ALL SELECT 180) h
                    LEFT JOIN outcomes z ON z.observation_id=o.id AND z.horizon_min=h.horizon_min
                    WHERE o.symbol=? AND z.observation_id IS NULL
                      AND o.ts+h.horizon_min*60<=?
                      AND o.ts+(h.horizon_min+?)*60>=?
                      AND o.ts<?""", (symbol, now, MAX_LAG_MIN, now, now)).fetchall()
                for oid, old_ts, old_price, old_dir, horizon in pending:
                    signed = (price / old_price - 1.0) * 100.0 * (1 if old_dir == "LONG" else -1)
                    db.execute("INSERT OR IGNORE INTO outcomes VALUES (?,?,?,?,?)",
                               (oid, horizon, now, price, signed))
                db.execute("""INSERT OR IGNORE INTO observations
                    (ts,symbol,direction,state,price,deep_selected,meta_json)
                    VALUES (?,?,?,?,?,?,?)""",
                    (now, symbol, direction, state, price, int(bool(item.get("deep_selected"))),
                     json.dumps(item, ensure_ascii=False, default=str)))
            except Exception as exc:
                print("EARLY_FORWARD_ROW_ERROR", str(item.get("symbol")), type(exc).__name__, str(exc)[:100], flush=True)
        # Expired observations remain visible in database, never mislabeled as losses.
        db.commit()
        counts = db.execute("""SELECT horizon_min,COUNT(*),
            ROUND(AVG(signed_return_pct),3),
            SUM(CASE WHEN signed_return_pct>0 THEN 1 ELSE 0 END)
            FROM outcomes GROUP BY horizon_min ORDER BY horizon_min""").fetchall()
        print("EARLY_FORWARD_SUMMARY", json.dumps({
            "horizons": [{"minutes":h,"resolved":n,"mean_signed_pct":avg,
                          "positive":positive} for h,n,avg,positive in counts],
            "pending": db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "database": str(root / "early_forward_outcomes.sqlite")
        }), flush=True)
    finally:
        db.close()
