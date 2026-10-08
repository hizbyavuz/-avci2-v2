#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V4 fail-closed autonomous execution *readiness* and paper-trading engine.

No live account access, no real-money order placement. Testnet only is available
through a separate guarded adapter. Explicit immutable plans, idempotent signal
IDs, closed candles, source/freshness, notional/risk caps and circuit breaker.
"""
from __future__ import annotations
import dataclasses
import hashlib
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

VERSION = "LS_V4_EXECUTION_GATE_2026_10_08"
ALLOWED_MODES = frozenset(("PAPER", "TESTNET"))
VALID_DIRECTIONS = frozenset(("LONG", "SHORT"))
MIN_CLOSED_CANDLE_AGE_MS = -5000

@dataclass(frozen=True)
class RiskPolicy:
    # Conservative TESTNET research caps, not final user-approved live settings.
    max_notional_usdt: float = 50.0
    max_risk_usdt: float = 1.0
    max_risk_equity_pct: float = 0.25
    max_daily_loss_usdt: float = 3.0
    max_concurrent_positions: int = 1
    max_signal_age_ms: int = 90000
    max_feed_age_ms: int = 25000
    max_spread_bps: float = 15.0
    max_slippage_bps: float = 20.0
    min_net_r: float = 1.5
    fee_bps_roundtrip: float = 10.0
    max_leverage: int = 2
    min_equity_usdt: float = 100.0

@dataclass(frozen=True)
class Decision:
    approved: bool
    reasons: tuple[str, ...]
    intent_id: str
    planned_qty: str | None = None
    risk_usdt: float | None = None
    notional_usdt: float | None = None

def _num(x):
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError,OverflowError):
        return None

def _millis(x):
    if type(x) is bool:return None
    try:return int(x)
    except (ValueError,TypeError,OverflowError):return None

def intent_id(plan:dict[str,Any]) -> str:
    """Stable idempotency key; identical strategy setup never trades twice."""
    raw={k:plan.get(k) for k in ("setup_id","symbol","direction","signal_time_ms",
                                 "entry","stop","tp1","tp2","source")}
    return "ls4"+hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:23]

def quantity_round_down(notional_usdt,entry_price,step_size):
    """Never round quantity up beyond the approved notional cap."""
    p=Decimal(str(entry_price)); n=Decimal(str(notional_usdt)); s=Decimal(str(step_size))
    if p<=0 or n<=0 or s<=0:return "0"
    return format((n/p//s*s).quantize(s,rounding=ROUND_DOWN),"f")

def assess(plan:dict[str,Any],market:dict[str,Any],account:dict[str,Any],
           *, mode:str="PAPER", now_ms:int|None=None,
           policy:RiskPolicy=RiskPolicy()) -> Decision:
    reasons=[]
    ident=intent_id(plan)
    if mode not in ALLOWED_MODES:reasons.append("MODE_NOT_ALLOWED")
    if not isinstance(plan,dict) or not isinstance(market,dict) or not isinstance(account,dict):
        return Decision(False,("INVALID_INPUT",),ident)
    if plan.get("state")!="TRIGGERED":reasons.append("NOT_A_TRIGGERED_SIGNAL")
    if plan.get("research_only") is True:reasons.append("RESEARCH_ONLY_NOT_EXECUTABLE")
    if not plan.get("setup_id"):reasons.append("MISSING_SETUP_ID")
    if plan.get("direction") not in VALID_DIRECTIONS:reasons.append("INVALID_DIRECTION")
    sym=str(plan.get("symbol") or "")
    if not sym.endswith("USDT") or not sym.isalnum():reasons.append("INVALID_SYMBOL")
    if plan.get("source")!="BINANCE_FUTURES_UM_WS":reasons.append("NOT_NATIVE_FUTURES_PRICE")
    if market.get("source")!="BINANCE_FUTURES_UM_WS":reasons.append("PRICE_SOURCE_MISMATCH")
    if market.get("symbol")!=sym:reasons.append("SYMBOL_MISMATCH")
    if not bool(market.get("data_health_ok")):reasons.append("DATA_HEALTH_INVALID")
    if not bool(market.get("complete_history")):reasons.append("HISTORY_HAS_GAPS")
    if not bool(market.get("closed_candle_only")):reasons.append("UNCONFIRMED_CANDLE")
    if not bool(plan.get("analyst_authorized")):reasons.append("ANALYST_NOT_AUTHORIZED")
    if not bool(plan.get("stop_and_tp_locked")):reasons.append("UNLOCKED_LEVELS")
    if not bool(plan.get("derivatives_quality_ok")):reasons.append("DERIVATIVES_NOT_READY")
    if plan.get("data_cohort") not in ("BINANCE_FUTURES_NATIVE","VENUE_LABELED_EXTERNAL"):
        reasons.append("UNVERIFIED_DATA_COHORT")
    if not bool(account.get("one_way_mode")):reasons.append("REQUIRE_ONE_WAY_MODE")
    if not bool(account.get("isolated_margin")):reasons.append("REQUIRE_ISOLATED_MARGIN")
    if not bool(account.get("account_reconciled")):reasons.append("ACCOUNT_NOT_RECONCILED")
    if bool(account.get("kill_switch")):reasons.append("KILL_SWITCH_ON")
    if bool(account.get("unresolved_order")):reasons.append("UNRESOLVED_ORDER")
    if bool(account.get("unprotected_position")):reasons.append("UNPROTECTED_POSITION")
    if _num(account.get("open_positions")) is None or _num(account.get("open_positions"))>=policy.max_concurrent_positions:
        reasons.append("MAX_CONCURRENT_POSITIONS")
    if _num(account.get("realized_loss_today_usdt")) is None or _num(account.get("realized_loss_today_usdt"))>=policy.max_daily_loss_usdt:
        reasons.append("DAILY_LOSS_LIMIT")
    equity=_num(account.get("equity_usdt"))
    if equity is None or equity<policy.min_equity_usdt:reasons.append("EQUITY_INVALID")
    leverage=_num(account.get("leverage"))
    if leverage is None or leverage<1 or leverage>policy.max_leverage:reasons.append("LEVERAGE_NOT_APPROVED")
    if not bool(account.get("venue_permitted")):reasons.append("VENUE_ACCESS_NOT_VERIFIED")
    now=int(now_ms if now_ms is not None else time.time()*1000)
    signal_time=_millis(plan.get("signal_time_ms"))
    feed_time=_millis(market.get("event_ms"))
    closed_at=_millis(market.get("closed_candle_time_ms"))
    if signal_time is None or not 0<=now-signal_time<=policy.max_signal_age_ms:
        reasons.append("STALE_SIGNAL")
    if feed_time is None or not 0<=now-feed_time<=policy.max_feed_age_ms:
        reasons.append("STALE_FEED")
    if closed_at is None or now-closed_at < MIN_CLOSED_CANDLE_AGE_MS:
        reasons.append("INVALID_CANDLE_TIME")
    spread=_num(market.get("spread_bps"))
    if spread is None or spread<0 or spread>policy.max_spread_bps:
        reasons.append("SPREAD_TOO_WIDE")
    slip=_num(market.get("estimated_slippage_bps"))
    if slip is None or slip<0 or slip>policy.max_slippage_bps:
        reasons.append("SLIPPAGE_TOO_HIGH")
    entry=_num(plan.get("entry"))
    stop=_num(plan.get("stop"))
    tp1=_num(plan.get("tp1"))
    tp2=_num(plan.get("tp2"))
    side=plan.get("direction")
    valid_levels=(entry is not None and stop is not None and tp1 is not None
                  and tp2 is not None and
                  (0<stop<entry<tp1<tp2 if side=="LONG" else
                   0<tp2<tp1<entry<stop if side=="SHORT" else False))
    if not valid_levels:reasons.append("INVALID_LEVELS")
    qty=None;risk=None;notional=None
    step=_num(market.get("step_size"))
    min_qty=_num(market.get("min_qty"))
    min_notional=_num(market.get("min_notional"))
    if step is None or step<=0 or min_qty is None or min_notional is None:
        reasons.append("SYMBOL_FILTERS_UNVERIFIED")
    if valid_levels and step and min_qty is not None and min_notional is not None and equity is not None:
        risk_ratio=abs(entry-stop)/entry
        # Risk uses conservative fill+stop execution cost buffers.
        worst_risk_pct=risk_ratio+(policy.fee_bps_roundtrip+2*policy.max_slippage_bps)/10000.0
        allowable_risk=min(policy.max_risk_usdt,equity*policy.max_risk_equity_pct/100.0)
        notional=min(policy.max_notional_usdt,allowable_risk/worst_risk_pct)
        qty=quantity_round_down(notional,entry,step)
        q=float(qty)
        notional=q*entry
        risk=q*entry*worst_risk_pct
        if q<min_qty or notional<min_notional:reasons.append("BELOW_EXCHANGE_MIN_SIZE")
        gross_reward=abs(tp1-entry)/entry
        expected_cost=(policy.fee_bps_roundtrip+2*max(slip or 0,0))/10000.0
        rr=(gross_reward-expected_cost)/worst_risk_pct
        if rr<policy.min_net_r:reasons.append("REWARD_RISK_TOO_LOW")
        if risk>allowable_risk+0.000001:reasons.append("RISK_CAP")
        if notional>policy.max_notional_usdt+0.000001:reasons.append("NOTIONAL_CAP")
    return Decision(not reasons,tuple(dict.fromkeys(reasons)),ident,qty,risk,notional)

def init_journal(c:sqlite3.Connection):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS v4_intents(
      intent_id TEXT PRIMARY KEY, setup_id TEXT NOT NULL, symbol TEXT NOT NULL,
      direction TEXT NOT NULL, created_ms INTEGER NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('RESERVED','PAPER_OPEN','TESTNET_PENDING','TESTNET_PROTECTED',
      'TESTNET_REJECTED','CLOSED','UNKNOWN','QUARANTINED')),
      mode TEXT NOT NULL CHECK(mode IN ('PAPER','TESTNET')), payload_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS v4_execution_events(
      seq INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT NOT NULL,
      observed_ms INTEGER NOT NULL, status TEXT NOT NULL, details_json TEXT NOT NULL
    );
    """)
    c.commit()

def reserve(c,plan,decision,mode="PAPER",now_ms=None):
    """Atomic idempotent reservation across restarted workers."""
    if not decision.approved:return False
    if mode not in ALLOWED_MODES:raise ValueError("real orders are not supported")
    init_journal(c)
    ts=int(now_ms or time.time()*1000)
    c.execute("BEGIN IMMEDIATE")
    try:
        cur=c.execute("""INSERT OR IGNORE INTO v4_intents
        (intent_id,setup_id,symbol,direction,created_ms,state,mode,payload_json)
        VALUES(?,?,?,?,?,?,?,?)""",(decision.intent_id,str(plan["setup_id"]),
        plan["symbol"],plan["direction"],ts,"RESERVED",mode,
        json.dumps({"plan":plan,"decision":dataclasses.asdict(decision)},sort_keys=True)))
        success=cur.rowcount==1
        if success:
            c.execute("""INSERT INTO v4_execution_events(intent_id,observed_ms,status,details_json)
            VALUES(?,?,?,?)""",(decision.intent_id,ts,"RESERVED","{}"))
        c.commit()
        return success
    except Exception:
        c.rollback()
        raise

def change_state(c,intent_id,from_state,to_state,details=None):
    allowed={"RESERVED":{"PAPER_OPEN","TESTNET_PENDING","TESTNET_REJECTED","QUARANTINED"},
             "PAPER_OPEN":{"CLOSED","QUARANTINED"},
             "TESTNET_PENDING":{"TESTNET_PROTECTED","UNKNOWN","TESTNET_REJECTED","QUARANTINED"},
             "TESTNET_PROTECTED":{"CLOSED","UNKNOWN","QUARANTINED"},
             "UNKNOWN":{"CLOSED","QUARANTINED"},
             "QUARANTINED":{"CLOSED"}}
    if to_state not in allowed.get(from_state,set()):
        raise ValueError("invalid state transition")
    now=int(time.time()*1000)
    c.execute("BEGIN IMMEDIATE")
    try:
        result=c.execute("UPDATE v4_intents SET state=? WHERE intent_id=? AND state=?",
                         (to_state,intent_id,from_state))
        if result.rowcount!=1:
            c.rollback()
            return False
        c.execute("""INSERT INTO v4_execution_events(intent_id,observed_ms,status,details_json)
          VALUES(?,?,?,?)""",(intent_id,now,to_state,json.dumps(details or {},sort_keys=True)))
        c.commit()
        return True
    except Exception:
        c.rollback()
        raise
