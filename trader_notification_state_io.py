#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import/export Telegram notification state without touching signal logic.

Used by lightweight workflows whose research database is restored from a
separate authoritative artifact. The sidecar contains presentation-only state:
which candidate/early-watch rows have already been shown to the user.

Commands:
  python trader_notification_state_io.py import <sidecar.db> <main.db>
  python trader_notification_state_io.py export <main.db> <sidecar.db>
"""
from __future__ import annotations

import os
import sqlite3
import sys

DDL = """CREATE TABLE IF NOT EXISTS trader_notification_state(
  source TEXT NOT NULL,lane TEXT NOT NULL,asset_key TEXT NOT NULL,
  last_label TEXT,last_score REAL,last_reignition INTEGER NOT NULL DEFAULT 0,
  last_seen_utc TEXT NOT NULL,last_notified_utc TEXT,
  PRIMARY KEY(source,lane,asset_key)
)"""

UPSERT = """INSERT INTO trader_notification_state
(source,lane,asset_key,last_label,last_score,last_reignition,last_seen_utc,last_notified_utc)
VALUES(?,?,?,?,?,?,?,?)
ON CONFLICT(source,lane,asset_key) DO UPDATE SET
  last_label=excluded.last_label,
  last_score=excluded.last_score,
  last_reignition=excluded.last_reignition,
  last_seen_utc=excluded.last_seen_utc,
  last_notified_utc=excluded.last_notified_utc
WHERE excluded.last_seen_utc >= trader_notification_state.last_seen_utc"""


def read_rows(path: str):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with sqlite3.connect(path, timeout=30) as con:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trader_notification_state'"
        ).fetchone()
        if not exists:
            return []
        return con.execute(
            """SELECT source,lane,asset_key,last_label,last_score,last_reignition,
                      last_seen_utc,last_notified_utc
               FROM trader_notification_state"""
        ).fetchall()


def merge(source: str, destination: str) -> int:
    rows = read_rows(source)
    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    with sqlite3.connect(destination, timeout=30) as con:
        con.execute(DDL)
        if rows:
            con.executemany(UPSERT, rows)
        con.commit()
    print(f"notification-state merge: {source} -> {destination}; rows={len(rows)}")
    return len(rows)


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] not in ("import", "export"):
        print("usage: trader_notification_state_io.py import|export <source.db> <destination.db>")
        return 2
    mode, first, second = sys.argv[1:]
    if mode == "import":
        merge(first, second)
    else:
        merge(first, second)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
