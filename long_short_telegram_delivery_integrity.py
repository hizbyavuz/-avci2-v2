#!/usr/bin/env python3
"""Fail-closed Telegram delivery integrity policy for Long/Short V3.1.

This guards what reaches the user's phone, NOT the frozen direction or entry
algorithm. All suppressed state transitions remain recorded for validation.
"""
from __future__ import annotations
import os
from datetime import datetime,timezone

VERSION="LS_TELEGRAM_TIME_INTEGRITY_2026_10_08_V1"
MAX_CLOSED_CANDLE_AGE_SECONDS=int(os.getenv("LS_TELEGRAM_CLOSED_CANDLE_MAX_AGE_SECONDS","75"))

def _utc(value):
    if not value:raise ValueError("MISSING_TIMESTAMP")
    t=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if t.tzinfo is None:raise ValueError("NAIVE_TIMESTAMP")
    return t.astimezone(timezone.utc)

def policy(stage, observed_at, latest_closed_candle_at, prior_visible=False, max_age_seconds=MAX_CLOSED_CANDLE_AGE_SECONDS):
    """Return a strict, auditable delivery decision.

    A close-confirmed or triggered message is not timely if its last underlying
    5m CLOSED candle predates the notice by >75s. Historical stage/outcomes are
    *not* rewritten to make this look like a better trading model.
    """
    result={"version":VERSION,"stage":str(stage),"allowed":True,
            "reason":"OK","closed_candle_age_seconds":None,
            "max_closed_candle_age_seconds":int(max_age_seconds)}
    if stage=="INVALIDATED":
        if not prior_visible:
            result.update(allowed=False,reason="UNANNOUNCED_SETUP_INVALIDATED")
        return result
    if stage not in ("CLOSE_CONFIRMED","TRIGGERED"):
        return result
    try:
        diff=(_utc(observed_at)-_utc(latest_closed_candle_at)).total_seconds()
    except (ValueError,TypeError,OverflowError):
        result.update(allowed=False,reason="INVALID_CLOSE_TIMESTAMP")
        return result
    result["closed_candle_age_seconds"]=round(diff,3)
    if not (-5 <= diff <= max_age_seconds):
        result.update(allowed=False,reason="STALE_OR_FUTURE_5M_CLOSE")
    return result
