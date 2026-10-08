#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Locked setup lifecycle and append-only transitions for Long/Short V3.1.

No exchange requests, Telegram sends or order endpoints. This is an additive
state ledger; existing V3.1 qualification thresholds are intentionally untouched.
The only setup eligible for *primary* performance is a successfully delivered
final ACTIVE entry that has already been established within its locked retest
band. Observer/early/shadow/WATCH records are NEVER final signals.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone, timedelta

OPEN_STATES=("CANDIDATE","WATCH","CONFIRMED","ACTIVE","TP1")
TERMINAL_STATES=("TP2","STOP","INVALIDATED","TIMEOUT","DIRECTION_FLIP","WATCHLIST_EXPIRED")
STATES=OPEN_STATES+TERMINAL_STATES
ALLOWED={
    "CANDIDATE":{"WATCH","INVALIDATED","TIMEOUT","DIRECTION_FLIP"},
    "WATCH":{"CONFIRMED","INVALIDATED","TIMEOUT","DIRECTION_FLIP","WATCHLIST_EXPIRED"},
    "CONFIRMED":{"ACTIVE","INVALIDATED","TIMEOUT","DIRECTION_FLIP"},
    "ACTIVE":{"TP1","TP2","STOP","TIMEOUT","DIRECTION_FLIP"},
    "TP1":{"TP2","STOP","TIMEOUT","DIRECTION_FLIP"},
}
SETUP_TYPES=("BREAKOUT","PULLBACK","REVERSAL")
FINAL_DELIVERY_STAGE="ACTIVE"


def iso(value):
    if isinstance(value,datetime):
        d=value
    elif isinstance(value,str):
        d=datetime.fromisoformat(value.replace("Z","+00:00"))
    else:
        raise ValueError("timestamp must be datetime or ISO text")
    if d.tzinfo is None:
        raise ValueError("timestamp must be timezone aware")
    return d.astimezone(timezone.utc).isoformat()


def utc(value):
    return datetime.fromisoformat(iso(value))


def freeze_hash(raw):
    if isinstance(raw,bytes):
        return hashlib.sha256(raw).hexdigest()
    if isinstance(raw,str):
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def init_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS setups (
        setup_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
        setup_type TEXT NOT NULL CHECK(setup_type IN ('BREAKOUT','PULLBACK','REVERSAL')),
        trigger_level REAL NOT NULL CHECK(trigger_level>0),
        retest_low REAL NOT NULL,
        retest_high REAL NOT NULL,
        invalidation REAL NOT NULL CHECK(invalidation>0),
        tp1 REAL NOT NULL CHECK(tp1>0),
        tp2 REAL,
        created_at TEXT NOT NULL,
        source_scan TEXT NOT NULL,
        regime_at_create TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        price_source TEXT NOT NULL DEFAULT 'UNKNOWN',
        state TEXT NOT NULL DEFAULT 'CANDIDATE',
        state_changed_at TEXT NOT NULL,
        confirmed_at TEXT,
        active_at TEXT,
        entry_price REAL,
        final_telegram_event_id INTEGER,
        final_telegram_sent_at TEXT,
        terminal_reason TEXT
    )""")
    con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_setup_open_symbol
        ON setups(symbol) WHERE state IN ('CANDIDATE','WATCH','CONFIRMED','ACTIVE','TP1')""")
    con.execute("""CREATE INDEX IF NOT EXISTS ix_setups_direction_created
        ON setups(direction,created_at)""")
    con.execute("""CREATE TABLE IF NOT EXISTS setup_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        setup_id TEXT NOT NULL REFERENCES setups(setup_id),
        symbol TEXT NOT NULL,
        stage_from TEXT,
        stage_to TEXT NOT NULL,
        event_time_utc TEXT NOT NULL,
        observed_price REAL,
        reason TEXT NOT NULL,
        telegram_event_id INTEGER,
        source TEXT NOT NULL DEFAULT 'LIFECYCLE',
        UNIQUE(setup_id,stage_from,stage_to,event_time_utc,reason)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS setup_outcomes (
        setup_id TEXT NOT NULL REFERENCES setups(setup_id),
        horizon_min INTEGER NOT NULL CHECK(horizon_min>0),
        outcome_status TEXT NOT NULL,
        first_barrier TEXT,
        first_barrier_time TEXT,
        start_at TEXT NOT NULL,
        horizon_end_at TEXT NOT NULL,
        exit_at TEXT,
        entry_price REAL NOT NULL,
        exit_price REAL,
        gross_return_pct REAL,
        net_return_pct REAL,
        net_return_2x_cost_pct REAL,
        cost_pct REAL,
        mfe_pct REAL,
        mae_pct REAL,
        btc_excess_return_pct REAL,
        btc_return_pct REAL,
        time_in_trade_seconds REAL,
        price_source TEXT NOT NULL,
        same_chart_venue INTEGER NOT NULL DEFAULT 0,
        data_gap_reason TEXT,
        market_cluster TEXT NOT NULL,
        independence_key TEXT NOT NULL,
        measured_at TEXT NOT NULL,
        PRIMARY KEY(setup_id,horizon_min)
    )""")
    # Database-level protection is stronger than an application convention.
    con.execute("""CREATE TRIGGER IF NOT EXISTS trg_setup_levels_immutable
        BEFORE UPDATE OF setup_id,symbol,direction,setup_type,trigger_level,retest_low,
                         retest_high,invalidation,tp1,tp2,created_at,source_scan,
                         regime_at_create,config_hash,price_source ON setups
        BEGIN SELECT RAISE(ABORT,'locked setup levels/identity cannot change'); END""")
    con.execute("""CREATE TRIGGER IF NOT EXISTS trg_setup_event_append_only
        BEFORE UPDATE ON setup_events
        BEGIN SELECT RAISE(ABORT,'setup events are append-only'); END""")
    con.execute("""CREATE TRIGGER IF NOT EXISTS trg_setup_event_no_delete
        BEFORE DELETE ON setup_events
        BEGIN SELECT RAISE(ABORT,'setup events cannot be deleted'); END""")


def _query_one(con,sql,params=()):
    cur=con.execute(sql,params)
    row=cur.fetchone()
    return dict(zip((x[0] for x in cur.description),row)) if row is not None else None


def _active_for(con,symbol):
    return _query_one(con,"""SELECT * FROM setups WHERE symbol=?
        AND state IN ('CANDIDATE','WATCH','CONFIRMED','ACTIVE','TP1')""",(symbol,))


def _validate(candidate):
    d=str(candidate["direction"])
    ty=str(candidate["setup_type"])
    if d not in ("LONG","SHORT") or ty not in SETUP_TYPES:
        raise ValueError("unsupported direction/setup_type")
    a,b=[float(candidate[k]) for k in ("retest_low","retest_high")]
    trigger,stop,tp1=[float(candidate[k]) for k in ("trigger_level","invalidation","tp1")]
    tp2=candidate.get("tp2")
    if tp2 is not None:
        tp2=float(tp2)
    if not all(math.isfinite(x) and x>0 for x in (a,b,trigger,stop,tp1)):
        raise ValueError("invalid positive levels")
    if a>b or not a<=trigger<=b:
        raise ValueError("retest band must contain trigger")
    if d=="LONG" and not (stop<trigger<tp1) or d=="SHORT" and not (tp1<trigger<stop):
        raise ValueError("reversed stop/target")
    if tp2 is not None and not (tp2>=tp1 if d=="LONG" else tp2<=tp1):
        raise ValueError("TP2 must extend beyond TP1")


def create_candidate(con,candidate,at):
    """Idempotent for same-direction live idea. New flip closes old, never edits levels.

    Only caller-qualified ACTIONABLE plans can enter. Radar/shadow must not call.
    Return (setup_id,was_created) and leave original levels untouched on rescans.
    """
    init_schema(con)
    _validate(candidate)
    symbol=str(candidate["symbol"]).strip().upper()
    direction=str(candidate["direction"]).upper()
    if not symbol:
        raise ValueError("empty symbol")
    at=iso(at)
    existing=_active_for(con,symbol)
    if existing:
        if existing["direction"]==direction:
            return existing["setup_id"],False
        transition(con,existing["setup_id"],"DIRECTION_FLIP",at,
                   reason="FRESH_OPPOSITE_ACTIONABLE_SETUP")
    source_scan=iso(candidate["source_scan"])
    config_hash=str(candidate["config_hash"])
    if len(config_hash)!=64 or any(c not in "0123456789abcdef" for c in config_hash):
        raise ValueError("config hash must be SHA256")
    setup_id=hashlib.sha256(
        f"{symbol}|{direction}|{source_scan}|{config_hash}".encode()
    ).hexdigest()[:32]
    vals=(setup_id,symbol,direction,candidate["setup_type"],float(candidate["trigger_level"]),
          float(candidate["retest_low"]),float(candidate["retest_high"]),
          float(candidate["invalidation"]),float(candidate["tp1"]),
          float(candidate["tp2"]) if candidate.get("tp2") is not None else None,
          at,source_scan,str(candidate.get("regime_at_create") or "UNKNOWN"),
          config_hash,str(candidate.get("price_source") or "UNKNOWN"),at)
    con.execute("""INSERT INTO setups(
       setup_id,symbol,direction,setup_type,trigger_level,retest_low,retest_high,
       invalidation,tp1,tp2,created_at,source_scan,regime_at_create,config_hash,
       price_source,state,state_changed_at)
       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'CANDIDATE',?)""",vals)
    con.execute("""INSERT INTO setup_events(
        setup_id,symbol,stage_from,stage_to,event_time_utc,reason)
        VALUES(?,? ,NULL,'CANDIDATE',?,'ANALYST_ACTIONABLE_ADMISSION')""",
        (setup_id,symbol,at))
    return setup_id,True


def transition(con,setup_id,to_state,at,*,observed_price=None,reason=None,
               final_telegram_event_id=None,final_telegram_sent_at=None):
    init_schema(con)
    if to_state not in STATES:
        raise ValueError("invalid state")
    row=_query_one(con,"SELECT * FROM setups WHERE setup_id=?",(setup_id,))
    if row is None:
        raise KeyError(setup_id)
    current=row["state"]
    if current==to_state:
        return False
    if to_state not in ALLOWED.get(current,set()):
        raise ValueError(f"illegal transition: {current}->{to_state}")
    at=iso(at)
    if utc(at)<utc(row["state_changed_at"]):
        raise ValueError("event cannot precede previous transition")
    price=None if observed_price is None else float(observed_price)
    if price is not None and (not math.isfinite(price) or price<=0):
        raise ValueError("invalid observed price")
    if to_state=="ACTIVE":
        # Confirmation alone is never an entry. Price must actually revisit
        # the locked retest band, AND final Telegram must be successfully sent.
        if price is None or not float(row["retest_low"])<=price<=float(row["retest_high"]):
            raise ValueError("ACTIVE requires an executable retest-band observation")
        if final_telegram_event_id is None or final_telegram_sent_at is None:
            raise ValueError("ACTIVE requires delivered final Telegram event")
        sent=iso(final_telegram_sent_at)
        if utc(sent)>utc(at):
            raise ValueError("ACTIVE cannot precede the sent message")
        # No pre-notification phantom fills: this timestamp is first usable entry.
    if to_state in ("TP1","TP2","STOP") and row["active_at"] is None:
        raise ValueError("cannot close an unentered trade")
    con.execute("""UPDATE setups SET state=?,state_changed_at=?,
        confirmed_at=CASE WHEN ?='CONFIRMED' THEN ? ELSE confirmed_at END,
        active_at=CASE WHEN ?='ACTIVE' THEN ? ELSE active_at END,
        entry_price=CASE WHEN ?='ACTIVE' THEN ? ELSE entry_price END,
        final_telegram_event_id=CASE WHEN ?='ACTIVE' THEN ? ELSE final_telegram_event_id END,
        final_telegram_sent_at=CASE WHEN ?='ACTIVE' THEN ? ELSE final_telegram_sent_at END,
        terminal_reason=CASE WHEN ? IN ('TP2','STOP','INVALIDATED','TIMEOUT',
            'DIRECTION_FLIP','WATCHLIST_EXPIRED') THEN ? ELSE terminal_reason END
        WHERE setup_id=?""",
        (to_state,at,to_state,at,to_state,at,to_state,price,to_state,
         final_telegram_event_id,to_state,
         iso(final_telegram_sent_at) if final_telegram_sent_at is not None else None,
         to_state,reason or to_state,setup_id))
    con.execute("""INSERT INTO setup_events(
        setup_id,symbol,stage_from,stage_to,event_time_utc,observed_price,
        reason,telegram_event_id) VALUES(?,?,?,?,?,?,?,?)""",
        (setup_id,row["symbol"],current,to_state,at,price,reason or to_state,
         final_telegram_event_id))
    return True


def actionable_setup_for_symbol(con,symbol):
    init_schema(con)
    return _active_for(con,symbol)


def market_cluster(at):
    return str(int(utc(at).timestamp()//900))


def independence_key(con,setup_id):
    """Coin+direction within 2h: one event; 15m market wave: one cluster."""
    row=_query_one(con,"SELECT * FROM setups WHERE setup_id=?",(setup_id,))
    if row is None:raise KeyError(setup_id)
    at=row["active_at"]
    if not at or row["final_telegram_event_id"] is None:
        raise ValueError("independence applies only to delivered ACTIVE setups")
    cutoff=iso(utc(at)-timedelta(hours=2))
    first=con.execute("""SELECT setup_id FROM setups
        WHERE symbol=? AND direction=?
        AND active_at IS NOT NULL AND final_telegram_event_id IS NOT NULL
        AND active_at BETWEEN ? AND ?
        ORDER BY active_at,setup_id LIMIT 1""",
        (row["symbol"],row["direction"],cutoff,at)).fetchone()
    return str(first[0]) if first else setup_id
