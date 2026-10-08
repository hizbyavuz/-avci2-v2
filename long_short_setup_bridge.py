#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V3.1 -> immutable setup ledger, observational adapter only.

Legacy V3.1 TRIGGERED is not automatically an ACTIVE paper entry: today it
can occur AFTER the retest, whereas an executable ACTIVE requires an in-band
price and a delivered final alert at/before the entry timestamp. Never
backdate an imaginary fill or reinterpret prior Telegram events as trades.
"""
from __future__ import annotations

import os
from pathlib import Path

from long_short_setup_lifecycle import (
    create_candidate, actionable_setup_for_symbol,freeze_hash,transition,iso
)

CONFIG_PATH=Path(__file__).with_name("LONG_SHORT_V3_FROZEN_CONFIG.json")


def observe_candidate(con,x,at):
    """Call only after current V3.1 has admitted the item to its watchlist."""
    if x.get("radar_only"):
        return None
    levels={
        "symbol":x["symbol"],"direction":x["direction"],
        "setup_type":x.get("setup_type") or "BREAKOUT",
        "trigger_level":x["trigger_level"],
        "retest_low":x["retest_low"],"retest_high":x["retest_high"],
        "invalidation":x["invalidation"],"tp1":x["target1"],
        "tp2":x.get("target2") if x.get("target2") else None,
        "source_scan":x["scan_time"],
        "regime_at_create":str(
            (x.get("structure_gate") or {}).get("_btc_regime")
            or (x.get("structure_gate") or {}).get("btc_regime") or "UNKNOWN"
        ),
        "config_hash":freeze_hash(CONFIG_PATH.read_bytes()),
        "price_source":str(x.get("price_source") or "UNKNOWN"),
    }
    sid,created=create_candidate(con,levels,at)
    if created:
        transition(con,sid,"WATCH",at,reason="V3_1_ANALYST_WATCH")
    return sid


def record_retest_observation(con,setup_id,at,price,reason="V3_1_RETEST_ZONE"):
    """The legacy retest is an observation, not a notified ACTIVE paper fill."""
    row=con.execute("SELECT symbol,state FROM setups WHERE setup_id=?",(setup_id,)).fetchone()
    if row is None or row[1]!="CONFIRMED":
        return False
    prev=con.execute("""SELECT 1 FROM setup_events WHERE setup_id=?
        AND reason=? AND event_time_utc=?""",(setup_id,reason,iso(at))).fetchone()
    if prev:
        return False
    con.execute("""INSERT INTO setup_events (
        setup_id,symbol,stage_from,stage_to,event_time_utc,observed_price,
        reason,source
    ) VALUES(?,?,?,?,?,?,?,'V3_1_ADAPTER')""",
    (setup_id,row[0],"CONFIRMED","CONFIRMED",iso(at),float(price),reason))
    return True


def observe_live_stage(con,symbol,old,new,at,price,*,telegram_status=None):
    """Mirror only proven V3 state; never convert an old alert to delivered ACTIVE."""
    setup=actionable_setup_for_symbol(con,symbol)
    if setup is None:
        return None
    sid=setup["setup_id"]; state=setup["state"]
    if new=="CLOSE_CONFIRMED" and state=="WATCH":
        transition(con,sid,"CONFIRMED",at,observed_price=price,
                   reason="V3_1_CLOSED_CANDLE_QUALIFIED")
    elif new=="RETESTING" and state=="CONFIRMED":
        if float(setup["retest_low"])<=float(price)<=float(setup["retest_high"]):
            record_retest_observation(con,sid,at,price)
    elif new=="TRIGGERED" and state=="CONFIRMED":
        # The existing Telegram is sent AFTER legacy retest/FAST confirmation.
        # It cannot be used to manufacture an earlier in-band entry.
        note="LEGACY_FINAL_NOT_EXECUTABLE_ACTIVE"
        record_retest_observation(con,sid,at,price,note)
    elif new=="INVALIDATED" and state in ("CANDIDATE","WATCH","CONFIRMED"):
        transition(con,sid,"INVALIDATED",at,observed_price=price,
                   reason="V3_1_LEVEL_INVALIDATED")
    return sid


def retire_from_watchlist(con,symbol,reason,at):
    setup=actionable_setup_for_symbol(con,symbol)
    if setup is None:
        return False
    if reason=="RESET_ACTIONABLE_DIRECTION_FLIP":
        target="DIRECTION_FLIP"
    elif setup["state"] in ("CANDIDATE","WATCH"):
        target="WATCHLIST_EXPIRED"
    else:
        target="TIMEOUT"
    try:
        transition(con,setup["setup_id"],target,at,reason=reason)
    except ValueError:
        return False
    return True
