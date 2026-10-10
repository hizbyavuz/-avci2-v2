"""Research-only full-universe forward outcomes from successive 24h ticker snapshots.
Tracks every eligible symbol, including those excluded from candle/deep scans.
These are price-to-price observational outcomes, NOT executable trade PnL.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

HORIZONS=(15,30,60,180)
MAX_LAG_SECONDS=12*60

def track(rows, metadata=None):
    root=Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
    root.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(root/"full_universe_forward.sqlite"),timeout=20)
    now=int(time.time())
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, symbol TEXT NOT NULL,
            price REAL NOT NULL, change24 REAL NOT NULL, volume24 REAL NOT NULL,
            UNIQUE(ts,symbol))""")
        db.execute("""CREATE TABLE IF NOT EXISTS outcomes (
            snapshot_id INTEGER NOT NULL, horizon INTEGER NOT NULL,
            observed_ts INTEGER NOT NULL, future_price REAL NOT NULL,
            return_pct REAL NOT NULL, abs_return_pct REAL NOT NULL,
            PRIMARY KEY(snapshot_id,horizon))""")
        db.execute("CREATE INDEX IF NOT EXISTS ix_full_symbol_ts ON snapshots(symbol,ts)")
        resolved=0
        inserted=0
        for sym,qv,px,ch in rows:
            try:
                px=float(px)
                if px<=0: continue
                previous=db.execute("""SELECT s.id,s.price,h.h
                    FROM snapshots s CROSS JOIN
                    (SELECT 15 h UNION ALL SELECT 30 UNION ALL SELECT 60 UNION ALL SELECT 180) h
                    LEFT JOIN outcomes o ON o.snapshot_id=s.id AND o.horizon=h.h
                    WHERE s.symbol=? AND o.snapshot_id IS NULL AND s.ts<?
                    AND s.ts+h.h*60<=? AND s.ts+h.h*60+?>=?""",
                    (sym,now,now,MAX_LAG_SECONDS,now)).fetchall()
                for oid,old,h in previous:
                    pct=(px/old-1)*100
                    db.execute("INSERT OR IGNORE INTO outcomes VALUES (?,?,?,?,?,?)",
                               (oid,h,now,px,pct,abs(pct)))
                    resolved+=1
                cur=db.execute("""INSERT OR IGNORE INTO snapshots
                    (ts,symbol,price,change24,volume24) VALUES (?,?,?,?,?)""",
                    (now,sym,px,float(ch),float(qv)))
                inserted+=max(0,cur.rowcount)
            except Exception as exc:
                print("FULL_FORWARD_ROW_ERROR",str(sym),type(exc).__name__,str(exc)[:100],flush=True)
        db.commit()
        summary=db.execute("""SELECT horizon,COUNT(*),ROUND(AVG(return_pct),4),
            SUM(CASE WHEN return_pct>=3 THEN 1 ELSE 0 END),
            SUM(CASE WHEN return_pct<=-3 THEN 1 ELSE 0 END),
            SUM(CASE WHEN return_pct>=5 THEN 1 ELSE 0 END),
            SUM(CASE WHEN return_pct<=-5 THEN 1 ELSE 0 END),
            SUM(CASE WHEN return_pct>=10 THEN 1 ELSE 0 END),
            SUM(CASE WHEN return_pct<=-10 THEN 1 ELSE 0 END)
            FROM outcomes GROUP BY horizon ORDER BY horizon""").fetchall()
        # Observational volume cohorts: show whether the frozen 25M threshold
        # excludes subsequent large price moves. This is NOT a trading signal,
        # directional prediction, or executable profit calculation.
        cohort_rows=db.execute("""SELECT
            CASE WHEN s.volume24 < 25000000 THEN '8M_TO_25M'
                 ELSE '25M_PLUS' END AS volume_cohort,
            o.horizon,COUNT(*),ROUND(AVG(o.return_pct),4),
            SUM(CASE WHEN o.return_pct>=3 THEN 1 ELSE 0 END),
            SUM(CASE WHEN o.return_pct<=-3 THEN 1 ELSE 0 END),
            SUM(CASE WHEN o.return_pct>=5 THEN 1 ELSE 0 END),
            SUM(CASE WHEN o.return_pct<=-5 THEN 1 ELSE 0 END)
            FROM outcomes o JOIN snapshots s ON s.id=o.snapshot_id
            GROUP BY volume_cohort,o.horizon
            ORDER BY volume_cohort,o.horizon""").fetchall()
        print("FULL_UNIVERSE_VOLUME_COHORT_AUDIT",json.dumps([
            {"volume_cohort":x[0],"minutes":x[1],"resolved":x[2],
             "avg_return_pct":x[3],"up_3":x[4],"down_3":x[5],
             "up_5":x[6],"down_5":x[7]} for x in cohort_rows
        ]),flush=True)
        pending=db.execute("""SELECT COUNT(*) FROM snapshots s
            WHERE EXISTS (SELECT 1 FROM
            (SELECT 15 h UNION ALL SELECT 30 UNION ALL SELECT 60 UNION ALL SELECT 180) h
            LEFT JOIN outcomes o ON o.snapshot_id=s.id AND o.horizon=h.h
            WHERE o.snapshot_id IS NULL AND s.ts+(h.h*60)+?>=?)""",
            (MAX_LAG_SECONDS,now)).fetchone()[0]
        print("FULL_UNIVERSE_FORWARD",json.dumps({
            "symbols":len(rows),"inserted":inserted,"new_resolved":resolved,
            "pending_snapshots":pending,
            "horizons":[{"minutes":x[0],"resolved":x[1],"avg_return_pct":x[2],
                "up_3":x[3],"down_3":x[4],"up_5":x[5],"down_5":x[6],
                "up_10":x[7],"down_10":x[8]} for x in summary],
            "note":"ticker-snapshot observation, no intraperiod MFE/MAE or executable PnL"
        }),flush=True)
    finally:
        db.close()
