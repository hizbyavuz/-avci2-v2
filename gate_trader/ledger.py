"""Sinyal defteri (SQLite). Her sinyal, sonucu ve dolar karşılığı burada tutulur."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from . import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  side TEXT NOT NULL,
  signal_ts INTEGER NOT NULL,
  entry REAL NOT NULL, sl REAL NOT NULL, tp REAL NOT NULL,
  level REAL, target_level REAL,
  rr REAL, net_rr REAL, stop_pct REAL,
  risk_usd REAL NOT NULL, notional REAL NOT NULL, cost_pct REAL NOT NULL,
  reasons TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN',
  exit_price REAL, exit_ts INTEGER, pnl_usd REAL,
  sent INTEGER NOT NULL DEFAULT 0,
  version TEXT,
  UNIQUE(contract, signal_ts)
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS scans(
  ts INTEGER PRIMARY KEY, coins INTEGER, funnel TEXT, errors INTEGER, signals INTEGER
);
"""

# Process-local SQLite RAM only: no disk files, no backups, lost on restart.
_RAM_DB = None

@contextmanager
def connect(path: str | None = None):
    global _RAM_DB
    if _RAM_DB is None:
        _RAM_DB = sqlite3.connect(":memory:", timeout=30)
        _RAM_DB.row_factory = sqlite3.Row
        _RAM_DB.executescript(SCHEMA)
    try:
        yield _RAM_DB
        _RAM_DB.commit()
    except Exception:
        _RAM_DB.rollback()
        raise


def get_meta(con, key: str, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(con, key: str, value) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))


def open_signals(con):
    return con.execute("SELECT * FROM signals WHERE status='OPEN' ORDER BY signal_ts").fetchall()


def last_signal_ts(con, contract: str) -> int | None:
    r = con.execute("SELECT MAX(COALESCE(exit_ts, signal_ts)) m FROM signals WHERE contract=?",
                    (contract,)).fetchone()
    return r["m"] if r and r["m"] is not None else None


def is_open(con, contract: str) -> bool:
    return con.execute("SELECT 1 FROM signals WHERE contract=? AND status='OPEN'",
                       (contract,)).fetchone() is not None


def insert_signal(con, contract, d, notional, cost_pct) -> int | None:
    try:
        cur = con.execute(
            """INSERT INTO signals(contract,side,signal_ts,entry,sl,tp,level,target_level,rr,net_rr,
               stop_pct,risk_usd,notional,cost_pct,reasons,version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (contract, d.side, d.signal_ts, d.entry, d.sl, d.tp, d.level, d.target_level, d.rr,
             d.net_rr, d.stop_pct, C.RISK_USD, notional, cost_pct,
             json.dumps(d.reasons, ensure_ascii=False), C.VERSION))
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def close_signal(con, sid: int, status: str, exit_price: float, exit_ts: int, pnl: float):
    con.execute("UPDATE signals SET status=?, exit_price=?, exit_ts=?, pnl_usd=? WHERE id=?",
                (status, exit_price, exit_ts, pnl, sid))


def stats(con, since_ts: int | None = None) -> dict:
    q = "SELECT status, side, pnl_usd FROM signals WHERE status!='OPEN'"
    args: tuple = ()
    if since_ts is not None:
        q += " AND exit_ts>=?"
        args = (since_ts,)
    rows = con.execute(q, args).fetchall()
    n = len(rows)
    wins = sum(1 for r in rows if (r["pnl_usd"] or 0) > 0)
    total = sum(r["pnl_usd"] or 0 for r in rows)
    gross_w = sum(r["pnl_usd"] for r in rows if (r["pnl_usd"] or 0) > 0)
    gross_l = -sum(r["pnl_usd"] for r in rows if (r["pnl_usd"] or 0) < 0)
    by = {}
    for side in ("LONG", "SHORT"):
        rs = [r for r in rows if r["side"] == side]
        by[side] = {"n": len(rs), "pnl": sum(r["pnl_usd"] or 0 for r in rs),
                    "wins": sum(1 for r in rs if (r["pnl_usd"] or 0) > 0)}
    return {"n": n, "wins": wins, "pnl": total,
            "pf": (gross_w / gross_l) if gross_l > 0 else None,
            "tp": sum(1 for r in rows if r["status"] == "TP"),
            "sl": sum(1 for r in rows if r["status"] == "SL"),
            "timeout": sum(1 for r in rows if r["status"] == "TIMEOUT"),
            "by_side": by,
            "open": con.execute("SELECT COUNT(*) n FROM signals WHERE status='OPEN'").fetchone()["n"]}


def now() -> int:
    return int(time.time())
