#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long/Short Live Pool

Separate from Avci/Gate/Long-Short analyst scoring.
- Reads latest Long/Short analyst candidates from long_short_analyst.db.
- Keeps its own state in long_short_live_pool.db.
- Polls watched symbols every ~15 seconds during a short-lived worker window.
- Sends Telegram only on meaningful stage transitions.
- Never places orders.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from binance_notify import resolve_chat_id, load_cached_chat_id, save_cached_chat_id
from long_short_simple_notify import classify_move, format_alert, queue_alert, claim_ready_alert, ack_claimed_alert, retry_claimed_alert
from long_short_data_router import multi_venue_perp_klines, multi_venue_perp_universe

ANALYST_DB=os.getenv("LS_DB","long_short_analyst.db")
LIVE_DB=os.getenv("LS_LIVE_DB","long_short_live_pool.db")
NOTIFY_DB=os.getenv("LS_SIMPLE_NOTIFY_DB","long_short_simple_notify.db")
POLL_SECONDS=float(os.getenv("LS_LIVE_POLL_SECONDS","30"))
RUN_SECONDS=int(os.getenv("LS_LIVE_RUN_SECONDS","3600"))
ALIGN_TO_5M=os.getenv("LS_ALIGN_TO_5M","1").strip().lower() in ("1","true","yes","on")
ALIGN_GRACE_SECONDS=float(os.getenv("LS_ALIGN_GRACE_SECONDS","4"))
MAX_WATCH=int(os.getenv("LS_LIVE_MAX_WATCH","12"))
# Delivery policy only: frozen direction/structure/execution thresholds remain unchanged.
TELEGRAM_TRADE_ONLY=os.getenv("LS_TELEGRAM_TRADE_ONLY","1").strip().lower() in ("1","true","yes","on")
TELEGRAM_RUNTIME_NOTICES=os.getenv("LS_TELEGRAM_RUNTIME_NOTICES","0").strip().lower() in ("1","true","yes","on")
LIVE_FETCH_WORKERS=max(1,min(12,int(os.getenv("LS_LIVE_FETCH_WORKERS","6"))))
APPROACH_PCT=float(os.getenv("LS_LIVE_APPROACH_PCT","0.25"))
WATCHLIST_GRACE_SECONDS=int(os.getenv("LS_WATCHLIST_GRACE_SECONDS","900"))
SETUP_MAX_AGE_SECONDS=int(os.getenv("LS_SETUP_MAX_AGE_SECONDS","10800"))
RADAR_MIN_DAY_MOVE_PCT=float(os.getenv("LS_RADAR_MIN_DAY_MOVE_PCT","5.0"))
# Observational early-entry layer. It never changes the frozen continuation rules.
EARLY_APPROACH_PCT=float(os.getenv("LS_EARLY_APPROACH_PCT","0.18"))
EARLY_MAX_EXTENSION_PCT=float(os.getenv("LS_EARLY_MAX_EXTENSION_PCT","0.22"))
EARLY_MIN_VOLUME_MULT=float(os.getenv("LS_EARLY_MIN_VOLUME_MULT","1.20"))
EARLY_MIN_TAKER_SHARE=float(os.getenv("LS_EARLY_MIN_TAKER_SHARE","0.54"))
EARLY_MAX_COMPRESSION_PCT=float(os.getenv("LS_EARLY_MAX_COMPRESSION_PCT","0.90"))
EARLY_MIN_ROOM_PCT=float(os.getenv("LS_EARLY_MIN_ROOM_PCT","0.30"))
STRUCTURE_GATE_VERSION="LS_STRUCTURE_GATE_V3_1_2026-10-07"
STRUCTURE_MIN_VOLUME_MULT=float(os.getenv("LS_STRUCTURE_MIN_VOLUME_MULT","1.10"))
STRUCTURE_MIN_BODY_RATIO=float(os.getenv("LS_STRUCTURE_MIN_BODY_RATIO","0.45"))
STRUCTURE_MAX_REJECTION_WICK=float(os.getenv("LS_STRUCTURE_MAX_REJECTION_WICK","0.35"))
TELEGRAM_LIMIT=4096
HEALTH_INTERVAL_SECONDS=int(os.getenv("LS_TELEGRAM_HEALTH_SECONDS","3600"))

SPOT_BASES=("https://data-api.binance.vision","https://api.binance.com")
FUTURES_DEPTH_URL="https://fapi.binance.com/fapi/v1/depth"
EXECUTION_PROXY_NOTIONAL=float(os.getenv("LS_PAPER_NOTIONAL_USDT","250"))
EXECUTION_FEE_BPS_PER_SIDE=float(os.getenv("LS_FEE_BPS_PER_SIDE","5"))
EXECUTION_MIN_SLIPPAGE_BPS_PER_SIDE=float(os.getenv("LS_VALIDATION_MIN_SLIPPAGE_BPS_PER_SIDE","10"))
EXECUTION_MIN_NET_T1_R=float(os.getenv("LS_STRUCTURE_MIN_NET_T1_R","1.0"))
_PERP_STATS_CACHE={"ts":0.0,"rows":{}}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def spot_get(path,params=None):
    last=None
    for base in SPOT_BASES:
        try:
            r=requests.get(base+path,params=params or {},timeout=10,
                           headers={"User-Agent":"long-short-live-pool/1.0"})
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last=exc
    raise last or RuntimeError("Binance Spot unavailable")

def _book_vwap_quote(levels,quote_notional):
    remain=float(quote_notional)
    base_qty=0.0
    spent=0.0
    for p,q in levels:
        px=float(p); qty=float(q)
        level_quote=px*qty
        take=min(remain,level_quote)
        if take<=0:
            continue
        base_qty += take/px
        spent += take
        remain -= take
        if remain<=1e-9:
            break
    if remain>max(0.01,float(quote_notional)*0.001) or base_qty<=0:
        return None
    return spent/base_qty


def trigger_execution_proxy(symbol):
    """Capture one execution-cost snapshot after a TRIGGERED alert is sent."""
    source="BINANCE_FUTURES_BOOK"
    try:
        r=requests.get(
            FUTURES_DEPTH_URL,
            params={"symbol":symbol,"limit":100},
            timeout=5,
            headers={"User-Agent":"long-short-live-pool/1.0"},
        )
        r.raise_for_status()
        book=r.json()
    except Exception:
        source="BINANCE_SPOT_BOOK_PROXY"
        book=spot_get("/api/v3/depth",{"symbol":symbol,"limit":100})

    bids=book.get("bids",[]) or []
    asks=book.get("asks",[]) or []
    if not bids or not asks:
        return {"source":source,"observed_at_utc":now_iso(),"available":False}
    best_bid=float(bids[0][0]); best_ask=float(asks[0][0])
    mid=(best_bid+best_ask)/2.0 if best_bid and best_ask else 0.0
    spread_bps=((best_ask-best_bid)/mid*10000.0) if mid else None
    buy=_book_vwap_quote(asks,EXECUTION_PROXY_NOTIONAL)
    sell=_book_vwap_quote(bids,EXECUTION_PROXY_NOTIONAL)
    return {
        "source":source,
        "observed_at_utc":now_iso(),
        "available":True,
        "notional_usdt":EXECUTION_PROXY_NOTIONAL,
        "mid":mid,
        "spread_bps":spread_bps,
        "costs":{
            str(int(EXECUTION_PROXY_NOTIONAL)):{
                "buy_bps":((buy/mid-1.0)*10000.0) if buy is not None and mid else None,
                "sell_bps":((1.0-sell/mid)*10000.0) if sell is not None and mid else None,
            }
        },
    }

def live_execution_gate(row,price,proxy):
    """Fail closed if the trigger-time executable cost destroys net T1 R."""
    if not proxy or not proxy.get("available"):
        return {"qualified":False,"reason":"execution_proxy_unavailable"}
    size=str(int(float(proxy.get("notional_usdt") or EXECUTION_PROXY_NOTIONAL)))
    costs=(proxy.get("costs") or {}).get(size) or {}
    def finite_nonneg(x):
        try:
            x=float(x)
            return x if math.isfinite(x) and x>=0 else None
        except Exception:
            return None
    buy=finite_nonneg(costs.get("buy_bps"))
    sell=finite_nonneg(costs.get("sell_bps"))
    if buy is None or sell is None:
        return {"qualified":False,"reason":"execution_cost_missing","proxy":proxy}
    direction=str(row["direction"])
    entry_slip=max(EXECUTION_MIN_SLIPPAGE_BPS_PER_SIDE,buy if direction=="LONG" else sell)
    exit_slip=max(EXECUTION_MIN_SLIPPAGE_BPS_PER_SIDE,sell if direction=="LONG" else buy)
    total_cost_pct=(2.0*EXECUTION_FEE_BPS_PER_SIDE+entry_slip+exit_slip)/100.0
    inv=float(row["invalidation"] or 0.0)
    t1=float(row["target1"] or 0.0)
    entry=float(price)
    if direction=="LONG":
        geometry=bool(inv<entry<t1)
        reward=(t1/entry-1.0)*100.0 if geometry else -999.0
        risk=(entry/inv-1.0)*100.0 if geometry and inv>0 else 0.0
    else:
        geometry=bool(t1<entry<inv)
        reward=(entry/t1-1.0)*100.0 if geometry and t1>0 else -999.0
        risk=(inv/entry-1.0)*100.0 if geometry else 0.0
    net_r=((reward-total_cost_pct)/risk) if risk>0 else None
    qualified=bool(geometry and net_r is not None and net_r>=EXECUTION_MIN_NET_T1_R)
    return {
        "qualified":qualified,
        "reason":"ok" if qualified else "trigger_time_net_r_failed",
        "entry_slippage_bps":entry_slip,
        "exit_slippage_bps":exit_slip,
        "round_trip_cost_pct":total_cost_pct,
        "risk_pct":risk,
        "reward_pct":reward,
        "net_t1_r":net_r,
        "proxy":proxy,
    }


def fmtp(x):
    if x is None: return "-"
    x=float(x)
    if abs(x)>=1000: return f"{x:,.2f}"
    if abs(x)>=1: return f"{x:.4f}"
    return f"{x:.8f}".rstrip("0")

def init_db():
    with sqlite3.connect(LIVE_DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS watch_state(
            symbol TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            trigger_level REAL NOT NULL,
            retest_low REAL,
            retest_high REAL,
            invalidation REAL,
            target1 REAL,
            target2 REAL,
            analyst_scan_time TEXT,
            analyst_confidence INTEGER,
            data_mode TEXT,
            data_cohort TEXT,
            derivatives_provider TEXT,
            derivatives_quality TEXT,
            htf_direction TEXT,
            htf_score INTEGER,
            htf_reasons_json TEXT,
            stage TEXT NOT NULL DEFAULT 'WATCH',
            close_confirmed_time TEXT,
            retest_seen INTEGER NOT NULL DEFAULT 0,
            last_price REAL,
            last_closed_5m REAL,
            early_state TEXT NOT NULL DEFAULT 'NONE',
            early_signal_price REAL,
            early_signal_time TEXT,
            confirmed_signal_price REAL,
            confirmed_signal_time TEXT,
            gain_before_confirmation REAL,
            time_early_to_confirmed_seconds REAL,
            early_short_signal_price REAL,
            confirmed_short_signal_price REAL,
            gain_before_short_confirmation REAL,
            time_early_short_to_confirmed_seconds REAL,
            analyst_active INTEGER NOT NULL DEFAULT 1,
            last_seen_watchlist_utc TEXT,
            last_actionable_scan_time TEXT,
            setup_locked_at_utc TEXT,
            last_update_utc TEXT NOT NULL
        )""")
        cols={r[1] for r in con.execute("PRAGMA table_info(watch_state)")}
        if "trigger_level" not in cols and "entry_level" in cols:
            con.execute("ALTER TABLE watch_state RENAME COLUMN entry_level TO trigger_level")
        if "data_mode" not in cols:
            con.execute("ALTER TABLE watch_state ADD COLUMN data_mode TEXT")
        for name,typ in [
            ("data_cohort","TEXT"),("derivatives_provider","TEXT"),("derivatives_quality","TEXT"),
            ("htf_direction","TEXT"),("htf_score","INTEGER"),("htf_reasons_json","TEXT"),
            ("structure_gate_version","TEXT"),("structure_gate_json","TEXT")
        ]:
            if name not in cols:
                con.execute(f"ALTER TABLE watch_state ADD COLUMN {name} {typ}")
        for name,typ,default in [
            ("early_state","TEXT","'NONE'"),
            ("early_signal_price","REAL",None),("early_signal_time","TEXT",None),
            ("confirmed_signal_price","REAL",None),("confirmed_signal_time","TEXT",None),
            ("gain_before_confirmation","REAL",None),("time_early_to_confirmed_seconds","REAL",None),
            ("early_short_signal_price","REAL",None),("confirmed_short_signal_price","REAL",None),
            ("gain_before_short_confirmation","REAL",None),("time_early_short_to_confirmed_seconds","REAL",None),
            ("analyst_active","INTEGER","1"),("last_seen_watchlist_utc","TEXT",None),
            ("last_actionable_scan_time","TEXT",None),("setup_locked_at_utc","TEXT",None),
        ]:
            if name not in cols:
                clause=f" DEFAULT {default}" if default is not None else ""
                con.execute(f"ALTER TABLE watch_state ADD COLUMN {name} {typ}{clause}")
        con.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            stage_from TEXT,
            stage_to TEXT NOT NULL,
            price REAL,
            closed_5m REAL,
            condition_time_utc TEXT,
            telegram_sent_time_utc TEXT,
            telegram_status TEXT,
            telegram_error TEXT,
            delay_seconds REAL,
            payload_json TEXT
        )""")
        event_cols={r[1] for r in con.execute("PRAGMA table_info(events)")}
        for name,typ in {
            "condition_time_utc":"TEXT",
            "telegram_sent_time_utc":"TEXT",
            "telegram_status":"TEXT",
            "telegram_error":"TEXT",
            "delay_seconds":"REAL",
        }.items():
            if name not in event_cols:
                con.execute(f"ALTER TABLE events ADD COLUMN {name} {typ}")

        con.execute("""CREATE TABLE IF NOT EXISTS watch_episodes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            started_at_utc TEXT NOT NULL,
            ended_at_utc TEXT,
            end_reason TEXT,
            analyst_scan_time TEXT,
            start_price REAL,
            trigger_level REAL NOT NULL,
            invalidation REAL,
            target1 REAL,
            target2 REAL,
            data_cohort TEXT,
            structure_gate_version TEXT,
            max_stage TEXT NOT NULL DEFAULT 'WATCH',
            close_confirmed_time TEXT,
            triggered_time TEXT,
            last_price REAL
        )""")
        con.execute("""CREATE INDEX IF NOT EXISTS ix_watch_episodes_open
                       ON watch_episodes(symbol,ended_at_utc)""")

def load_watchlist():
    if not os.path.exists(ANALYST_DB):
        return []
    with sqlite3.connect(ANALYST_DB) as con:
        con.row_factory=sqlite3.Row
        scan=con.execute("SELECT MAX(scan_time_utc) AS ts FROM analyses").fetchone()
        if not scan or not scan["ts"]:
            return []
        rows=con.execute("""SELECT symbol,status,long_score,short_score,confidence,price,payload_json
                           FROM analyses WHERE scan_time_utc=?""",(scan["ts"],)).fetchall()
        out=[]
        for r in rows:
            try:
                p=json.loads(r["payload_json"] or "{}")
                plan=p.get("setup_plan") or {}
                gate=p.get("htf_gate") or {}
                v3=p.get("v3") or {}
                v3_discovery=p.get("v3_discovery") or {}
                structure_gate=p.get("structure_gate") or {}
                derivatives_ready=bool(p.get("derivatives_ready"))
                setup_type=str(plan.get("setup_type") or v3.get("setup_type") or "BREAKOUT")
                if not plan.get("direction") or plan.get("trigger_level") is None:
                    continue
                # 1D/4H gate belongs only to the observational EARLY layer.
                # Do not let it suppress the normal confirmed continuation watcher:
                # frozen V1.9 already scores/vetoes higher-timeframe trend, while
                # early_observation() below still requires explicit HTF agreement.
                # V2 distinction:
                # - admission to the LIVE WATCHLIST only needs a versioned structural plan;
                # - CLOSE_CONFIRMED/TRIGGERED still fail closed unless the full
                #   Structure Gate precheck + live candle-quality checks pass.
                # Keeping qualified_precheck here used to produce an empty live pool
                # whenever every WAIT candidate was merely close-but-not-yet-qualified.
                if structure_gate.get("version")!=STRUCTURE_GATE_VERSION:
                    continue
                if structure_gate.get("direction")!=plan.get("direction"):
                    continue
                if setup_type=="BREAKOUT" and not structure_gate.get("trigger_zone"):
                    continue
                # Normal candidates are WAIT/LONG/SHORT. Strong daily movers that are
                # still NO_TRADE may enter a separate radar-only pool; radar can
                # never advance to CLOSE_CONFIRMED/TRIGGERED until a later analyst
                # scan upgrades the frozen status.
                analyst_status=str(r["status"] or "")
                day_change_pct=float(p.get("day_change_pct") or 0.0)
                radar_only=bool(
                    analyst_status=="NO_TRADE"
                    and abs(day_change_pct)>=RADAR_MIN_DAY_MOVE_PCT
                )
                if analyst_status not in ("WAIT","LONG","SHORT") and not radar_only:
                    continue
                if not radar_only and ("v3" in p) and not bool(v3.get("eligible")):
                    continue
                # Fail closed for every actionable candidate. Radar-only movers may
                # still be observed with incomplete derivatives because the state
                # machine below cannot advance radar to confirmation/trigger.
                if not derivatives_ready and not radar_only:
                    continue
                deriv_source=str(p.get("derivatives_source") or p.get("data_mode") or "UNKNOWN")
                provider=str(p.get("derivatives_selected_provider") or "")
                quality=str(p.get("derivatives_quality") or "UNKNOWN")
                if deriv_source=="BINANCE_FUTURES":
                    cohort="BINANCE_FUTURES_NATIVE"
                elif provider=="BYBIT_LINEAR":
                    cohort="SPOT_PLUS_BYBIT"
                elif provider=="GATE_FUTURES":
                    cohort="SPOT_PLUS_GATE"
                elif deriv_source=="MULTI_VENUE_PUBLIC":
                    cohort="SPOT_PLUS_OTHER_OR_PARTIAL"
                else:
                    cohort="UNKNOWN"

                # Treat support/resistance as a band, but NEVER replace the
                # analyst's raw breakout level with an easier level inside a nearby
                # zone. That previously created inverted retest ranges and, worse,
                # stops only a few basis points from the live trigger.
                zone=structure_gate.get("trigger_zone") or {}
                raw_trigger=float(plan["trigger_level"])
                zone_low=float(zone.get("low") or raw_trigger)
                zone_high=float(zone.get("high") or raw_trigger)
                invalidation=float(plan.get("invalidation") or 0.0)
                target1=float(plan.get("target1") or 0.0)
                target2=float(plan.get("target2") or 0.0)

                # The analyst's structural trigger is the contract for this setup.
                # Do not harden it a second time by moving it to a trigger-zone edge:
                # the structure gate already validated zone strength/confluence and
                # room/R using this trigger. The zone remains useful for retest
                # context, while the locked breakout/breakdown level stays stable.
                live_trigger=raw_trigger
                if plan["direction"]=="LONG":
                    zone_retest_low=min(zone_low,live_trigger)
                    live_retest_low=min(
                        live_trigger,
                        max(float(plan.get("retest_low") or raw_trigger),zone_retest_low),
                    )
                    live_retest_high=live_trigger
                    level_order_ok=bool(
                        invalidation>0 and target1>0 and
                        invalidation < live_trigger < target1
                    )
                    risk_pct=((live_trigger-invalidation)/live_trigger*100.0) if level_order_ok else 0.0
                    reward_pct=((target1/live_trigger-1.0)*100.0) if level_order_ok else 0.0
                else:
                    zone_retest_high=max(zone_high,live_trigger)
                    live_retest_low=live_trigger
                    live_retest_high=max(
                        live_trigger,
                        min(float(plan.get("retest_high") or raw_trigger),zone_retest_high),
                    )
                    level_order_ok=bool(
                        invalidation>0 and target1>0 and
                        target1 < live_trigger < invalidation
                    )
                    risk_pct=((invalidation-live_trigger)/live_trigger*100.0) if level_order_ok else 0.0
                    reward_pct=((live_trigger/target1-1.0)*100.0) if level_order_ok else 0.0

                # Re-check the exact levels that Telegram will show. The analyst
                # structure gate was calculated from raw_trigger; if the zone edge
                # shifts the final trigger, its old R calculation is no longer enough.
                min_cost_pct=float(structure_gate.get("minimum_round_trip_cost_pct") or 0.30)
                thresholds=structure_gate.get("thresholds") or {}
                min_net_r=float(thresholds.get("min_net_t1_r") or 1.0)
                required_room_pct=float(structure_gate.get("required_room_pct") or 0.0)
                effective_net_r=((reward_pct-min_cost_pct)/risk_pct) if risk_pct>0 else None
                effective_levels_ok=bool(
                    level_order_ok
                    and reward_pct>=required_room_pct
                    and effective_net_r is not None
                    and effective_net_r>=min_net_r
                    and live_retest_low<=live_retest_high
                )

                # Radar-only entries are observational. A real WATCH message must
                # be capable of becoming a confirmation with the *locked* setup.
                # Do not tell the user to watch an entry whose structural room/R
                # precheck already says it can never pass.
                structure_precheck_ok=bool(
                    structure_gate.get("qualified_precheck")
                    if setup_type=="BREAKOUT"
                    else (structure_gate.get("room_ok") and structure_gate.get("rr_ok"))
                )
                if not radar_only and (not effective_levels_ok or not structure_precheck_ok):
                    continue

                stored_gate=dict(structure_gate)
                stored_gate["_effective_trigger_level"]=live_trigger
                stored_gate["_effective_risk_pct"]=risk_pct
                stored_gate["_effective_reward_pct"]=reward_pct
                stored_gate["_effective_net_t1_r"]=effective_net_r
                stored_gate["_effective_levels_ok"]=effective_levels_ok
                stored_gate["_radar_only"]=bool(radar_only)
                stored_gate["_analyst_status"]=analyst_status
                stored_gate["_day_change_pct"]=day_change_pct
                stored_gate["_derivatives_ready"]=bool(derivatives_ready)
                stored_gate["_derivatives_quality"]=quality
                stored_gate["_v3"]=v3
                stored_gate["_v3_discovery"]=v3_discovery
                stored_gate["_v3_setup_type"]=setup_type
                stored_gate["_v3_precheck_ok"]=bool(
                    structure_gate.get("qualified_precheck")
                    if setup_type=="BREAKOUT"
                    else (structure_gate.get("room_ok") and structure_gate.get("rr_ok"))
                )
                out.append({
                    "symbol":r["symbol"],"direction":plan["direction"],
                    "reference_price":float(r["price"] or 0.0),
                    "trigger_level":live_trigger,
                    "retest_low":live_retest_low,
                    "retest_high":live_retest_high,
                    "invalidation":invalidation,
                    "target1":target1,
                    "target2":target2,
                    "confidence":int(r["confidence"] or 0),
                    "day_change_pct":day_change_pct,
                    "radar_only":bool(radar_only),
                    "data_mode":deriv_source,
                    "data_cohort":cohort,
                    "derivatives_provider":provider,
                    "derivatives_quality":quality,
                    "htf_direction":str(plan.get("direction") or "NONE"),
                    "htf_score":0,
                    "htf_reasons":[f"V3 {setup_type}", f"phase={v3.get('phase','NONE')}"],
                    "structure_gate_version":str(structure_gate.get("version") or ""),
                    "structure_gate":stored_gate,
                    "setup_type":setup_type,
                    "discovery_rank":float(p.get("discovery_rank") or 0.0),
                    "scan_time":scan["ts"],
                })
            except Exception:
                continue
        # Keep real WAIT/LONG/SHORT candidates first. Fill remaining capacity with
        # the fastest radar-only movers, not with mega-cap names by confidence.
        out.sort(key=lambda x:(
            1 if x.get("radar_only") else 0,
            -(abs(float(x.get("day_change_pct") or 0.0)) if x.get("radar_only") else float(x.get("discovery_rank") or 0.0)),
            str(x.get("symbol") or ""),
        ))
        return out[:MAX_WATCH]

def _close_open_watch_episode(con,symbol,reason,last_price=None):
    ts=now_iso()
    con.execute("""UPDATE watch_episodes SET ended_at_utc=?,end_reason=?,
                   last_price=COALESCE(?,last_price)
                   WHERE symbol=? AND ended_at_utc IS NULL""",
                (ts,reason,last_price,symbol))


def _open_watch_episode(con,x):
    con.execute("""INSERT INTO watch_episodes(
        symbol,direction,started_at_utc,analyst_scan_time,start_price,
        trigger_level,invalidation,target1,target2,data_cohort,
        structure_gate_version,max_stage,last_price
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'WATCH',?)""",
    (x["symbol"],x["direction"],now_iso(),x["scan_time"],x.get("reference_price"),
     x["trigger_level"],x["invalidation"],x["target1"],x["target2"],
     x["data_cohort"],x["structure_gate_version"],x.get("reference_price")))


def sync_watchlist(items):
    """Synchronize analyst observations without moving an active setup's goalposts.

    Core rule:
    - analysis may refresh every cycle;
    - an actionable setup keeps its original trigger/retest/SL/TP/gate until it
      is invalidated, expires, or a fresh actionable setup flips direction/type;
    - radar or temporary watchlist absence can pause a setup, but cannot silently
      rewrite it or confirm a trade.
    """
    seen_at=now_iso()
    with sqlite3.connect(LIVE_DB) as con:
        con.row_factory=sqlite3.Row
        keep={x["symbol"] for x in items}

        for x in items:
            old=con.execute("SELECT * FROM watch_state WHERE symbol=?",(x["symbol"],)).fetchone()
            new_radar=bool(x.get("radar_only"))
            new_actionable=not new_radar
            new_setup_type=str(x.get("setup_type") or "BREAKOUT")

            old_gate={}
            if old:
                try:
                    old_gate=json.loads(old["structure_gate_json"] or "{}")
                except Exception:
                    old_gate={}
            old_radar=bool(old_gate.get("_radar_only")) if old else False
            old_setup_type=str(old_gate.get("_v3_setup_type") or "BREAKOUT") if old else "NONE"
            direction_changed=bool(old and str(old["direction"])!=str(x["direction"]))

            # Only a fresh ACTIONABLE hypothesis may replace an actionable setup.
            # Same-direction recalculation is metadata, not a new trade idea.
            reset=bool(
                not old
                or (new_actionable and old_radar)
                or (new_actionable and direction_changed)
                or (new_actionable and (not old_radar) and old_setup_type!=new_setup_type)
            )

            if reset:
                if old:
                    if direction_changed:
                        reason="RESET_ACTIONABLE_DIRECTION_FLIP"
                    elif old_radar and new_actionable:
                        reason="UPGRADE_RADAR_TO_ACTIONABLE"
                    elif old_setup_type!=new_setup_type:
                        reason="RESET_SETUP_TYPE_CHANGE"
                    else:
                        reason="RESET_NEW_SETUP"
                    _close_open_watch_episode(con,x["symbol"],reason,old["last_price"])
                _open_watch_episode(con,x)
                con.execute("""INSERT OR REPLACE INTO watch_state(
                    symbol,direction,trigger_level,retest_low,retest_high,invalidation,target1,target2,
                    analyst_scan_time,analyst_confidence,data_mode,data_cohort,derivatives_provider,derivatives_quality,
                    htf_direction,htf_score,htf_reasons_json,structure_gate_version,structure_gate_json,
                    stage,close_confirmed_time,retest_seen,last_price,last_closed_5m,
                    early_state,early_signal_price,early_signal_time,confirmed_signal_price,confirmed_signal_time,
                    gain_before_confirmation,time_early_to_confirmed_seconds,early_short_signal_price,
                    confirmed_short_signal_price,gain_before_short_confirmation,time_early_short_to_confirmed_seconds,
                    analyst_active,last_seen_watchlist_utc,last_actionable_scan_time,setup_locked_at_utc,last_update_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'WATCH',NULL,0,NULL,NULL,
                         'NONE',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?)""",
                (x["symbol"],x["direction"],x["trigger_level"],x["retest_low"],x["retest_high"],
                 x["invalidation"],x["target1"],x["target2"],x["scan_time"],x["confidence"],x["data_mode"],
                 x["data_cohort"],x["derivatives_provider"],x["derivatives_quality"],
                 x["htf_direction"],x["htf_score"],json.dumps(x["htf_reasons"],ensure_ascii=False),
                 x["structure_gate_version"],json.dumps(x["structure_gate"],ensure_ascii=False),
                 1 if new_actionable else 0,seen_at,x["scan_time"] if new_actionable else None,seen_at,seen_at))
                continue

            # Radar-to-radar is observational, so its moving level may refresh.
            if old_radar and new_radar:
                con.execute("""UPDATE watch_state SET
                    direction=?,trigger_level=?,retest_low=?,retest_high=?,invalidation=?,target1=?,target2=?,
                    analyst_scan_time=?,analyst_confidence=?,data_mode=?,data_cohort=?,
                    derivatives_provider=?,derivatives_quality=?,htf_direction=?,htf_score=?,htf_reasons_json=?,
                    structure_gate_version=?,structure_gate_json=?,analyst_active=0,
                    last_seen_watchlist_utc=?,last_update_utc=? WHERE symbol=?""",
                    (x["direction"],x["trigger_level"],x["retest_low"],x["retest_high"],x["invalidation"],
                     x["target1"],x["target2"],x["scan_time"],x["confidence"],x["data_mode"],x["data_cohort"],
                     x["derivatives_provider"],x["derivatives_quality"],x["htf_direction"],x["htf_score"],
                     json.dumps(x["htf_reasons"],ensure_ascii=False),x["structure_gate_version"],
                     json.dumps(x["structure_gate"],ensure_ascii=False),seen_at,seen_at,x["symbol"]))
                continue

            # Existing actionable episode: LOCK trigger/retest/SL/TP/structure_gate.
            # Fresh actionable analysis only refreshes context/freshness. A radar
            # downgrade pauses confirmation but does not destroy the setup.
            con.execute("""UPDATE watch_state SET
                analyst_scan_time=?,analyst_confidence=?,data_mode=?,data_cohort=?,
                derivatives_provider=?,derivatives_quality=?,htf_direction=?,htf_score=?,htf_reasons_json=?,
                analyst_active=?,last_seen_watchlist_utc=?,
                last_actionable_scan_time=CASE WHEN ?=1 THEN ? ELSE last_actionable_scan_time END,
                setup_locked_at_utc=COALESCE(setup_locked_at_utc,last_update_utc,?),
                last_update_utc=? WHERE symbol=?""",
                (x["scan_time"],x["confidence"],x["data_mode"],x["data_cohort"],x["derivatives_provider"],
                 x["derivatives_quality"],x["htf_direction"],x["htf_score"],
                 json.dumps(x["htf_reasons"],ensure_ascii=False),
                 1 if new_actionable else 0,seen_at,1 if new_actionable else 0,
                 x["scan_time"],seen_at,seen_at,x["symbol"]))

        # Missing for one analyst cycle is not an invalidation. Pause it first:
        # no confirmation while absent, but preserve the locked setup for a short
        # grace window so top-K churn cannot erase a nearly confirmed idea.
        if keep:
            q=",".join("?" for _ in keep)
            con.execute(f"""UPDATE watch_state SET analyst_active=0,last_update_utc=?
                            WHERE symbol NOT IN ({q})""",(seen_at,*tuple(keep)))
        else:
            con.execute("UPDATE watch_state SET analyst_active=0,last_update_utc=?",(seen_at,))

        # Truly stale paused setups are retired; they cannot live forever.
        stale=con.execute("""SELECT symbol,last_price FROM watch_state
            WHERE analyst_active=0
              AND (julianday(?) - julianday(COALESCE(last_seen_watchlist_utc,last_update_utc)))*86400.0 > ?""",
            (seen_at,float(WATCHLIST_GRACE_SECONDS))).fetchall()
        for r in stale:
            _close_open_watch_episode(con,r["symbol"],"WATCHLIST_GRACE_EXPIRED",r["last_price"])
            con.execute("DELETE FROM watch_state WHERE symbol=?",(r["symbol"],))

def _ema(values,period=7):
    if not values:
        return 0.0
    alpha=2.0/(period+1.0)
    out=float(values[0])
    for v in values[1:]:
        out=alpha*float(v)+(1.0-alpha)*out
    return out

def _true_range(rows):
    if len(rows)<2:
        return 0.0
    vals=[]
    for prev,cur in zip(rows[:-1],rows[1:]):
        ph=float(cur[2]); pl=float(cur[3]); pc=float(prev[4])
        vals.append(max(ph-pl,abs(ph-pc),abs(pl-pc)))
    return sum(vals[-14:])/max(1,len(vals[-14:]))

def _perp_stats(symbol):
    """One-minute cached all-market perp stats for futures-only live symbols."""
    now=time.time()
    if now-float(_PERP_STATS_CACHE.get("ts") or 0.0)>60.0:
        try:
            rows=multi_venue_perp_universe()
            _PERP_STATS_CACHE["rows"]={str(x.get("symbol") or ""):x for x in rows}
            _PERP_STATS_CACHE["ts"]=now
        except Exception:
            # Preserve the last good cache on transient provider errors.
            _PERP_STATS_CACHE["ts"]=now
    return (_PERP_STATS_CACHE.get("rows") or {}).get(symbol) or {}


def market_snapshot(symbol):
    # 1m data lets the observational layer see a breakout while it is forming.
    # The frozen continuation engine still uses the last completed 5m close below.
    price_source="BINANCE_SPOT"
    try:
        kl5=spot_get("/api/v3/klines",{"symbol":symbol,"interval":"5m","limit":30})
        kl1=spot_get("/api/v3/klines",{"symbol":symbol,"interval":"1m","limit":30})
        ticker=spot_get("/api/v3/ticker/price",{"symbol":symbol})
        stats=spot_get("/api/v3/ticker/24hr",{"symbol":symbol})
        price=float(ticker["price"])
    except Exception:
        # Futures-only listings can be absent from Binance Spot. Use the same
        # public perpetual chart router as the analyst instead of silently losing
        # the coin from the near-live watcher.
        ext5=multi_venue_perp_klines(symbol,"5m",30)
        ext1=multi_venue_perp_klines(symbol,"1m",30)
        price_source=str(ext1.get("provider") or "UNKNOWN")
        if price_source not in ("GATE_FUTURES","BYBIT_LINEAR") or ext5.get("provider")!=price_source:
            raise RuntimeError(f"{symbol}: mixed live candle venues cannot qualify")
        kl5=ext5.get("rows") or []
        kl1=ext1.get("rows") or []
        if len(kl5)<3 or len(kl1)<3:
            raise RuntimeError(f"{symbol}: insufficient external live candles")
        price=float(kl1[-1][4])
        ps=_perp_stats(symbol)
        stats={
            "quoteVolume":str(float(ps.get("quote_volume") or 0.0)),
            "priceChangePercent":str(float(ps.get("day_change_pct") or 0.0)),
        }
    row=kl5[-2] if len(kl5)>=2 else kl5[-1]
    closed=float(row[4])
    close_ms=int(row[6])
    close_time=datetime.fromtimestamp(close_ms/1000.0,tz=timezone.utc).isoformat()

    prev5=kl5[-7:-1] if len(kl5)>=7 else kl5[:-1]
    highs=[float(x[2]) for x in prev5]
    lows=[float(x[3]) for x in prev5]
    local_high=max(highs) if highs else price
    local_low=min(lows) if lows else price
    compression_pct=((max(highs)-min(lows))/price*100.0) if highs and lows and price else 999.0

    c1=[float(x[4]) for x in kl1]
    ema7_now=_ema(c1[-12:],7)
    ema7_prev=_ema(c1[-13:-1],7) if len(c1)>=13 else ema7_now
    ema7_slope=(ema7_now-ema7_prev)/price*100.0 if price else 0.0

    forming=kl1[-1]
    qvol=float(forming[7] or 0)
    base=[float(x[7] or 0) for x in kl1[-12:-2]]
    vol_base=(sum(base)/len(base)) if base else 0.0
    vol_mult=qvol/vol_base if vol_base>0 else 1.0
    taker_buy=float(forming[10] or 0)
    taker_share=taker_buy/qvol if qvol>0 else 0.5
    atr1=_true_range(kl1[-16:])

    short_range_pct=((max(highs)-min(lows))/price*100.0) if highs and lows and price else 0.0

    co=float(row[1]); ch=float(row[2]); cl=float(row[3]); cc=float(row[4])
    cr=max(ch-cl,1e-12)
    cbody=abs(cc-co)/cr
    cclose_loc=(cc-cl)/cr
    cqvol=float(row[7] or 0.0)
    cbase_rows=kl5[-22:-2] if len(kl5)>=22 else kl5[:-2]
    cbase=(sum(float(x[7] or 0.0) for x in cbase_rows)/len(cbase_rows)) if cbase_rows else 0.0
    cvol_mult=cqvol/cbase if cbase>0 else 1.0

    early={
        "live_price_source":price_source,
        "local_high":local_high,"local_low":local_low,
        "closed_5m_open":co,"closed_5m_high":ch,"closed_5m_low":cl,
        "closed_5m_close":cc,"closed_5m_body_ratio":cbody,
        "closed_5m_close_location":cclose_loc,
        "closed_5m_volume_mult":cvol_mult,
        "compression_pct":compression_pct,"ema7_slope_pct":ema7_slope,
        "vol_mult":vol_mult,"taker_buy_share":taker_share,"atr1":atr1,
        "quote_volume_24h":float(stats.get("quoteVolume") or 0.0),
        "day_change_pct":float(stats.get("priceChangePercent") or 0.0),
        "short_range_pct":short_range_pct,
        "recent_closed_5m_closes":[float(x[4]) for x in kl5[-4:-1]] if len(kl5)>=4 else [closed],
    }
    return price,closed,close_time,early

def early_observation(row,price,early):
    """Observational only: detects a breakout/breakdown beginning before 5m confirmation."""
    d=row["direction"]
    trig=float(row["trigger_level"])
    t1=float(row["target1"] or 0)
    if not trig or not price:
        return "NONE",{}
    htf_direction=(row["htf_direction"] or "NONE") if "htf_direction" in row.keys() else "NONE"
    if htf_direction!=d:
        return "NONE",{"htf_gate_ok":False,"htf_direction":htf_direction}

    dist_pct=(price/trig-1.0)*100.0
    compression_ok=float(early["compression_pct"])<=EARLY_MAX_COMPRESSION_PCT
    vol_ok=float(early["vol_mult"])>=EARLY_MIN_VOLUME_MULT
    ema_slope=float(early["ema7_slope_pct"])
    taker=float(early["taker_buy_share"])
    atr1=float(early["atr1"] or 0.0)

    if d=="LONG":
        approach=(-EARLY_APPROACH_PCT)<=dist_pct
        directional=(ema_slope>0 and taker>=EARLY_MIN_TAKER_SHARE)
        started=price>=trig*(1.0-0.0005)
        extension=max(0.0,dist_pct)
        room=((t1/price-1.0)*100.0) if t1>price else 0.0
    else:
        approach=dist_pct<=EARLY_APPROACH_PCT
        directional=(ema_slope<0 and taker<=(1.0-EARLY_MIN_TAKER_SHARE))
        started=price<=trig*(1.0+0.0005)
        extension=max(0.0,-dist_pct)
        room=((price/t1-1.0)*100.0) if 0<t1<price else 0.0

    # Anti-chase: do not call a move "early" after it has already stretched or
    # when the first structural target is too close.
    atr_extension=(extension/100.0*price/atr1) if atr1>0 else 0.0
    chase=(extension>EARLY_MAX_EXTENSION_PCT or atr_extension>1.10 or room<EARLY_MIN_ROOM_PCT)
    metrics={
        "dist_trigger_pct":dist_pct,"compression_ok":compression_ok,
        "volume_ok":vol_ok,"directional_flow_ok":directional,
        "started":started,"extension_pct":extension,"room_pct":room,
        "htf_gate_ok":True,"htf_direction":htf_direction,
        "htf_score":int(row["htf_score"] or 0) if "htf_score" in row.keys() else 0,
        **early,
    }
    if chase and approach and started:
        return "CHASE",metrics
    if compression_ok and approach and started and directional and vol_ok:
        return ("EARLY_LONG" if d=="LONG" else "EARLY_SHORT"),metrics
    if approach and (directional or vol_ok):
        return "PENDING",metrics
    return "NONE",metrics

def live_structure_confirmation(row,closed,early):
    """Require volume/candle agreement and reject wick-only fake breakouts."""
    try:
        gate=json.loads(row["structure_gate_json"] or "{}") if "structure_gate_json" in row.keys() else {}
    except Exception:
        gate={}
    precheck_ok=bool(gate.get("_v3_precheck_ok",gate.get("qualified_precheck")))
    if gate.get("version")!=STRUCTURE_GATE_VERSION or not precheck_ok:
        return {"qualified":False,"reason":"structure_precheck_failed"}

    d=row["direction"]; trig=float(row["trigger_level"])
    o=float(early.get("closed_5m_open") or closed)
    h=float(early.get("closed_5m_high") or closed)
    l=float(early.get("closed_5m_low") or closed)
    c=float(early.get("closed_5m_close") or closed)
    rng=max(h-l,1e-12)
    body_ratio=float(early.get("closed_5m_body_ratio") or 0.0)
    close_loc=float(early.get("closed_5m_close_location") or 0.5)
    vol_mult=float(early.get("closed_5m_volume_mult") or 0.0)

    if d=="LONG":
        direction_ok=c>o
        close_location_ok=close_loc>=0.65
        rejection_wick=(h-max(o,c))/rng
        fake_breakout=bool(h>trig and c<=trig)
        beyond=bool(c>trig)
    else:
        direction_ok=c<o
        close_location_ok=close_loc<=0.35
        rejection_wick=(min(o,c)-l)/rng
        fake_breakout=bool(l<trig and c>=trig)
        beyond=bool(c<trig)

    volume_ok=vol_mult>=STRUCTURE_MIN_VOLUME_MULT
    body_ok=body_ratio>=STRUCTURE_MIN_BODY_RATIO
    wick_ok=rejection_wick<=STRUCTURE_MAX_REJECTION_WICK

    # Entry quality is combined evidence, not an all-or-nothing checklist.
    # The actual close beyond the locked trigger remains mandatory. Among the
    # five quality observations (candle direction, volume, body, close location,
    # rejection wick), four are enough. One imperfect soft feature cannot erase
    # an otherwise clean breakout/breakdown.
    quality_checks={
        "direction":bool(direction_ok),
        "volume":bool(volume_ok),
        "body":bool(body_ok),
        "close_location":bool(close_location_ok),
        "wick":bool(wick_ok),
    }
    quality_score=sum(1 for ok in quality_checks.values() if ok)
    quality_required=4
    qualified=bool(
        beyond and not fake_breakout and quality_score>=quality_required
    )
    recent=[float(x) for x in (early.get("recent_closed_5m_closes") or [])]
    if d=="LONG":
        acceptance_count=sum(1 for x in recent[-3:] if x>trig)
    else:
        acceptance_count=sum(1 for x in recent[-3:] if x<trig)
    return {
        "version":STRUCTURE_GATE_VERSION,
        "qualified":qualified,
        "precheck_ok":True,
        "beyond_trigger":beyond,
        "direction_ok":direction_ok,
        "volume_ok":volume_ok,
        "body_ok":body_ok,
        "close_location_ok":close_location_ok,
        "rejection_wick_ok":wick_ok,
        "fake_breakout":fake_breakout,
        "quality_score":quality_score,
        "quality_required":quality_required,
        "quality_checks":quality_checks,
        "volume_mult":vol_mult,
        "body_ratio":body_ratio,
        "close_location":close_loc,
        "rejection_wick_ratio":rejection_wick,
        "acceptance_bars_beyond_last3":acceptance_count,
        "setup_type":str(gate.get("_v3_setup_type") or "BREAKOUT"),
        "room_pct":float(gate.get("room_pct") or 0.0),
        "timeframe_confluence":int(gate.get("timeframe_confluence") or 0),
        "level_touches":int(gate.get("level_touches") or 0),
    }


def _row_is_radar(row):
    try:
        raw=row["structure_gate_json"] if "structure_gate_json" in row.keys() else None
        gate=json.loads(raw or "{}")
        return bool(gate.get("_radar_only"))
    except Exception:
        return False


def _row_is_analyst_active(row):
    try:
        return bool(int(row["analyst_active"])) if "analyst_active" in row.keys() else True
    except Exception:
        return True


def _setup_age_seconds(row):
    try:
        ts=row["setup_locked_at_utc"] if "setup_locked_at_utc" in row.keys() else None
        if not ts:
            return 0.0
        dt=datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return max(0.0,(datetime.now(timezone.utc)-dt).total_seconds())
    except Exception:
        return 0.0


def next_stage(row,price,closed,structure_quality=None):
    direction=row["direction"]
    trig=float(row["trigger_level"])
    rl=float(row["retest_low"])
    rh=float(row["retest_high"])
    inv=float(row["invalidation"] or 0)
    stage=row["stage"]
    dist=abs(price/trig-1.0)*100.0 if trig else 999

    # Radar-only movers are observational. They may surface as APPROACHING but
    # can never become a confirmed/triggered trade setup until the analyst later
    # upgrades them out of NO_TRADE.
    if _row_is_radar(row):
        return "APPROACHING" if dist<=APPROACH_PCT else "WATCH"

    if direction=="LONG":
        if inv and closed < inv:
            return "INVALIDATED"
        close_ok=closed>trig
        in_retest=(rl<=price<=rh)
        moving_away=price>rh
    else:
        if inv and closed > inv:
            return "INVALIDATED"
        close_ok=closed<trig
        in_retest=(rl<=price<=rh)
        moving_away=price<rl

    # A locked setup may be paused when it temporarily disappears from the
    # analyst's active watchlist. Keep observing/invalidation, but NEVER confirm
    # or trigger while the latest analyst cycle is not actively endorsing it.
    if stage not in ("TRIGGERED","EXECUTION_BLOCKED","CLUSTER_BLOCKED") and _setup_age_seconds(row)>SETUP_MAX_AGE_SECONDS:
        return "INVALIDATED"
    if not _row_is_analyst_active(row):
        return stage

    sq=structure_quality or {}
    setup_type=str(sq.get("setup_type") or "BREAKOUT")
    if stage in ("WATCH","APPROACHING"):
        if close_ok and bool(sq.get("qualified")):
            return "CLOSE_CONFIRMED"
        if dist<=APPROACH_PCT:
            return "APPROACHING"
        return "WATCH"
    if stage=="CLOSE_CONFIRMED":
        # V3 has two legitimate paths:
        # FAST: strong acceptance, no forced retest.
        # RETEST: controlled revisit then renewed directional close.
        if int(sq.get("acceptance_bars_beyond_last3") or 0)>=2 and bool(sq.get("qualified")):
            return "TRIGGERED"
        if in_retest:
            return "RETESTING"
        # Do not wait forever for a retest on a 3h research horizon.
        try:
            t0=datetime.fromisoformat(row["close_confirmed_time"]) if row["close_confirmed_time"] else None
            ttl=45*60 if setup_type=="BREAKOUT" else 90*60
            if t0 and (datetime.now(timezone.utc)-t0).total_seconds()>ttl:
                return "INVALIDATED"
        except Exception:
            pass
        return "CLOSE_CONFIRMED"
    if stage=="RETESTING":
        # A touch is not enough. Re-entry requires a fresh qualified close back
        # in the trade direction.
        if moving_away and close_ok and bool(sq.get("qualified")):
            return "TRIGGERED"
        return "RETESTING"
    return stage

def early_message(row,estate,price,metrics):
    sym=row["symbol"]; d=row["direction"]
    if estate=="EARLY_LONG":
        return (f"🟢 ERKEN LONG | {sym}\n"
                f"1D/4H zemin LONG ({int(row['htf_score'] or 0)}/11).\n"
                f"Kırılım yeni başlıyor. Fiyat: {fmtp(price)}\n"
                f"Direnç: {fmtp(row['trigger_level'])} | Hacim: {metrics.get('vol_mult',1):.2f}x\n"
                f"⚠️ Gözlemsel sinyal; frozen ana giriş kuralını değiştirmez.")
    if estate=="EARLY_SHORT":
        return (f"🔻 ERKEN SHORT | {sym}\n"
                f"1D/4H zemin SHORT ({int(row['htf_score'] or 0)}/11).\n"
                f"Aşağı kırılım yeni başlıyor. Fiyat: {fmtp(price)}\n"
                f"Destek: {fmtp(row['trigger_level'])} | Hacim: {metrics.get('vol_mult',1):.2f}x\n"
                f"⚠️ Gözlemsel sinyal; frozen ana giriş kuralını değiştirmez.")
    if estate=="PENDING":
        return (f"🟡 TEYİT BEKLİYOR | {sym}\n"
                f"Erken {d} görüldü; ana 5dk teyidi henüz tamamlanmadı. Fiyat: {fmtp(price)}")
    if estate=="CHASE":
        return (f"⚪ GEÇ/KOVALAMA | {sym}\n"
                f"Hareket başladı ama erken giriş avantajı azaldı. Fiyat: {fmtp(price)}")
    if estate=="BROKEN":
        return (f"🔴 BOZULDU | {sym}\nErken {d} gözlemi geçersizleşti.")
    return None

def queue_approaching_alert(row,price,structure_quality=None):
    """Queue one non-actionable watch message when price first approaches the trigger.

    This fixes the gap where APPROACHING was stored in DB but never surfaced to
    Telegram. Confirmation rules remain unchanged.
    """
    sym=str(row["symbol"]); d=str(row["direction"])
    level=float(row["trigger_level"])
    inv=float(row["invalidation"] or 0.0)
    t1=float(row["target1"] or 0.0)
    t2=float(row["target2"] or 0.0)
    side_ball="🟢" if d=="LONG" else "🔴"
    if _row_is_radar(row):
        try:
            gate=json.loads(row["structure_gate_json"] or "{}")
        except Exception:
            gate={}
        day_change=float(gate.get("_day_change_pct") or 0.0)
        derivatives_ready=bool(gate.get("_derivatives_ready"))
        data_line=(
            "Türev teyidi: tamam."
            if derivatives_ready else
            "Türev teyidi: eksik/uyumsuz; yalnızca radar."
        )
        msg=(f"🟡 OYNAK RADAR | {sym}\n"
             f"24s hareket: %{day_change:+.1f} | Yön eğilimi: {side_ball} {d}\n"
             f"5 dk izleme seviyesi: {fmtp(level)} | Şu an: {fmtp(price)}\n"
             f"{data_line}\n"
             "Durum: V3 hard-gate şartları tamamlanmadı; bu bir işlem teyidi değildir.\n"
             "Güvenlik filtresi ve normal LONG/SHORT teyidi aynen korunuyor.")
        payload={
            "stage":"RADAR_ALERT",
            "radar_only":True,
            "price":float(price),
            "trigger_level":level,
            "data_cohort":str(row["data_cohort"] or "UNKNOWN") if "data_cohort" in row.keys() else "UNKNOWN",
            "analyst_scan_time":str(row["analyst_scan_time"] or ""),
            "analyst_confidence":int(row["analyst_confidence"] or 0),
            "day_change_pct":day_change,
        }
        # Separate fingerprint from a later real LONG/SHORT watch message so a
        # radar ping can never suppress the actionable alert via cooldown.
        return queue_alert(sym,"RADAR_"+d,level,msg,1,payload=payload)
    relation="üstünde" if d=="LONG" else "altında"
    expectation=(f"{fmtp(level)} üstü kapanış → ardından seviyeyi koruması."
                 if d=="LONG" else
                 f"{fmtp(level)} altı kapanış → ardından seviyenin altında kalması.")
    sq=structure_quality or {}
    full_gate=bool(sq.get("qualified"))
    status_line=("Yapı + hacim + fake-breakout kapısı geçti."
                 if full_gate else
                 "Henüz işlem teyidi değil; yapı/alan/R ve kapanış teyidi bekleniyor.")
    msg=(f"{side_ball} {d} İÇİN İZLE | {sym}\n"
         f"5 dk mum {fmtp(level)} {relation} kapanırsa {d} güçlenir.\n"
         f"Şu an fiyat: {fmtp(price)}\n"
         f"Beklenen: {expectation}\n"
         f"Durum: 🟡 {status_line}\n"
         f"🛡️ SL: {fmtp(inv)}\n"
         f"🎯 TP1: {fmtp(t1)}\n"
         f"🎯 TP2: {fmtp(t2)}")
    payload={
        "stage":"WATCH_ALERT",
        "early_state":"APPROACHING",
        "price":float(price),
        "trigger_level":level,
        "invalidation":inv,
        "target1":t1,
        "target2":t2,
        "data_cohort":str(row["data_cohort"] or "UNKNOWN") if "data_cohort" in row.keys() else "UNKNOWN",
        "structure_gate_version":str(row["structure_gate_version"] or "") if "structure_gate_version" in row.keys() else "",
        "analyst_scan_time":str(row["analyst_scan_time"] or ""),
        "analyst_confidence":int(row["analyst_confidence"] or 0),
        "source":"APPROACHING_STAGE",
        "structure_qualified":full_gate,
    }
    priority=3 if int(row["analyst_confidence"] or 0)>=55 else 2
    return queue_alert(sym,d,level,msg,priority,payload=payload)


def message_for(row,stage,price,closed):
    sym=row["symbol"]; d=row["direction"]
    trig=float(row["trigger_level"]); rl=float(row["retest_low"]); rh=float(row["retest_high"])
    inv=float(row["invalidation"] or 0); t1=float(row["target1"] or 0); t2=float(row["target2"] or 0)
    side_ball="🟢" if d=="LONG" else "🔴"
    side_word=f"{side_ball} {d}"
    coin=f"{side_ball} {sym}"

    # Only one Telegram alert at a *trade-qualified* transition. Earlier
    # radar/watch/early/close-confirmed/invalidation stages stay in events DB.
    # This is a presentation policy, never a weaker signal or execution gate.
    if TELEGRAM_TRADE_ONLY:
        if stage!="TRIGGERED":
            return None
        data_mode=(row["data_mode"] or "UNKNOWN") if "data_mode" in row.keys() else "UNKNOWN"
        data_note=("Binance Futures" if data_mode=="BINANCE_FUTURES"
                   else "Spot grafik + çoklu-venue türev doğrulaması")
        try:
            gate=json.loads(row["structure_gate_json"] or "{}") if "structure_gate_json" in row.keys() else {}
        except Exception:
            gate={}
        mclass=classify_move(sym,float(gate.get("_day_change_pct") or 0.0))
        coin_type={"STABLE":"🟦 Güçlü/Stabil","FAST_FRESH":"⚡ Hızlı/Taze",
                   "FAST_EXTENDED":"🔥 Hızlı/Uzamış","FAST_QUIET":"⚪ Hızlı/Sakin"}[mclass]
        return (f"{side_ball} {d} SİNYALİ | {sym}\n"
                f"5 dk kapanış + devam koşulları tamamlandı.\n"
                f"Tetik seviyesi: {fmtp(trig)} | Gözlenen fiyat: {fmtp(price)}\n"
                f"🛡️ SL: {fmtp(inv)}\n"
                f"🎯 TP1: {fmtp(t1)}\n"
                f"🎯 TP2: {fmtp(t2)}\n"
                f"Tip: {coin_type}\n"
                f"Veri: {data_note}\n"
                "⚠️ Analiz uyarısı; otomatik emir açılmadı.")

    if stage=="APPROACHING":
        # Approach alerts are handled by the shared anti-spam queue.
        return None

    if stage=="CLOSE_CONFIRMED":
        relation="üstünde" if d=="LONG" else "altında"
        next_step="seviyeyi koruması / retestten güç alması" if d=="LONG" else "seviyenin altında kalması / retestten reddedilmesi"
        return (f"{side_ball} {d} TEYİT GELDİ | {sym}\n"
                f"5 dk mum {fmtp(trig)} {relation} kapandı.\n"
                f"✅ Yapı filtresi geçti: destek/direnç + hacim/mum + fake breakout kontrolü.\n"
                f"Kapanış: {fmtp(closed)} | Şu an: {fmtp(price)}\n"
                f"Şimdi beklenen: {next_step}.\n"
                f"🛡️ SL: {fmtp(inv)}\n"
                f"🎯 TP1: {fmtp(t1)}\n"
                f"🎯 TP2: {fmtp(t2)}")

    if stage=="RETESTING":
        # Retest itself is recorded but not messaged; the next actionable state
        # is TRIGGERED or INVALIDATED.
        return None

    if stage=="TRIGGERED":
        data_mode=(row["data_mode"] or "UNKNOWN") if "data_mode" in row.keys() else "UNKNOWN"
        warn="" if data_mode=="BINANCE_FUTURES" else "\nℹ️ Binance native Futures akışı yok; spot grafik + çoklu-venue türev teyidi kullanılıyor."
        return (f"➡️ DEVAM MOTORU\n"
                f"{coin} — {side_word} ŞARTLARI TAMAM\n"
                f"{side_ball} {d} DEĞERLENDİRİLEBİLİR{warn}\n"
                f"✅ Destek/direnç bölgesi + hacim/mum uyumu + fake breakout filtresi geçti.\n"
                f"Fiyat: {fmtp(price)}\n"
                f"🛡️ SL: {fmtp(inv)}\n"
                f"🎯 TP1: {fmtp(t1)}\n"
                f"🎯 TP2: {fmtp(t2)}")

    if stage=="INVALIDATED":
        return (f"➡️ DEVAM MOTORU\n"
                f"⚪ {sym} — ESKİ {side_word} FİKRİ İPTAL\n"
                f"Bu setup artık kullanılmamalı.")

    return None

def resolve_live_chat_id(token, configured=""):
    """Resolve V3.1 Telegram chat id, falling back to restored Binance cache."""
    configured=str(configured or "").strip()
    if configured:
        return resolve_chat_id(token, configured, NOTIFY_DB, "Long/Short Live Pool")

    cached=load_cached_chat_id(NOTIFY_DB)
    if cached:
        return cached

    # V3.1 uses a fresh notify DB, while the workflow already restores
    # binance_avci2.db specifically to preserve the previously working chat id.
    legacy=load_cached_chat_id("binance_avci2.db")
    if legacy:
        save_cached_chat_id(legacy, NOTIFY_DB)
        print("Long/Short Live Pool Chat ID restored from Binance cache", flush=True)
        return legacy

    return resolve_chat_id(token, "", NOTIFY_DB, "Long/Short Live Pool")


def send_telegram(msg):
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing: alert was not delivered")
    last=None
    for attempt in range(3):
        try:
            chat=resolve_live_chat_id(token,configured)
            r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                            json={"chat_id":chat,"text":msg[:TELEGRAM_LIMIT],"disable_web_page_preview":True},
                            timeout=10)
            r.raise_for_status()
            return now_iso()
        except Exception as exc:
            last=exc
            if attempt<2:
                time.sleep(2.0*(attempt+1))
    raise last or RuntimeError("Telegram send failed")

def send_recovery_notice_once(watch_count=0):
    """Send one deployment confirmation after the repaired runtime actually starts.

    Stored in the shared Telegram DB so short-lived GitHub runners do not repeat
    the notice on every 5-minute handoff. If delivery fails, the marker is not
    written and the next live cycle can retry.
    """
    key="long_short_v31_production_notice_2026_10_07"
    try:
        with sqlite3.connect(NOTIFY_DB,timeout=10) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS runtime_settings(
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            row=con.execute("SELECT value FROM runtime_settings WHERE key=?",(key,)).fetchone()
            if row:
                return False
        sent_at=send_telegram(
            "🟢 LONG/SHORT MOTOR AKTİF | V2.1\\n"
            f"Canlı havuz {int(watch_count)} coin izliyor.\\n"
            "Production: teyitli devam motoru. Dönüş motoru kapalı.\\n"
            "Sinyaller maliyet + alan + gerçek stop/T1/T2 ile kaydedilecek."
        )
        with sqlite3.connect(NOTIFY_DB,timeout=10) as con:
            con.execute("""INSERT OR REPLACE INTO runtime_settings(key,value,updated_at)
                           VALUES(?,?,?)""",(key,str(sent_at or now_iso()),now_iso()))
        print("Recovery Telegram notice sent",flush=True)
        return True
    except Exception as exc:
        print("Recovery Telegram notice failed",type(exc).__name__,str(exc)[:200],flush=True)
        return False

def send_health_if_due(watch_count=0):
    """At most one health heartbeat per interval; does not pretend a trade signal exists."""
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return False
    key="long_short_health_v31"
    now=time.time()
    try:
        with sqlite3.connect(NOTIFY_DB,timeout=10) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS runtime_settings(
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            row=con.execute("SELECT value FROM runtime_settings WHERE key=?",(key,)).fetchone()
            if row:
                try:
                    if now-float(row[0])<HEALTH_INTERVAL_SECONDS:
                        return False
                except Exception:
                    pass
        counts={"WATCH":0,"APPROACHING":0,"CLOSE_CONFIRMED":0,"RETESTING":0,"TRIGGERED":0}
        with sqlite3.connect(LIVE_DB) as con:
            for stage,n in con.execute("SELECT stage,COUNT(*) FROM watch_state GROUP BY stage").fetchall():
                counts[str(stage)]=int(n)
        msg=(
            "🟢 LONG/SHORT MOTOR ÇALIŞIYOR | V3.1\n"
            f"İzlenen: {int(watch_count)} coin | Yaklaşan: {counts.get('APPROACHING',0)} | "
            f"Teyit: {counts.get('CLOSE_CONFIRMED',0)} | Retest: {counts.get('RETESTING',0)}\n"
            "Bu sağlık mesajıdır; işlem sinyali değildir."
        )
        send_telegram(msg)
        with sqlite3.connect(NOTIFY_DB,timeout=10) as con:
            con.execute("""INSERT OR REPLACE INTO runtime_settings(key,value,updated_at)
                           VALUES(?,?,?)""",(key,str(now),now_iso()))
        return True
    except Exception as exc:
        print("Health Telegram failed",type(exc).__name__,str(exc)[:180],flush=True)
        return False


def cluster_trigger_allowed(con,direction,observed_time,max_per_cluster=2):
    """Cap user-facing correlated risk, while preserving blocked events as shadow data."""
    try:
        now_dt=datetime.fromisoformat(observed_time)
        if now_dt.tzinfo is None:
            now_dt=now_dt.replace(tzinfo=timezone.utc)
    except Exception:
        now_dt=datetime.now(timezone.utc)
    cutoff=(now_dt-timedelta(minutes=15)).isoformat()
    n=con.execute("""SELECT COUNT(*) FROM events
                     WHERE stage_to='TRIGGERED'
                       AND direction=?
                       AND event_time_utc>=?
                       AND telegram_status='SENT'""",
                  (direction,cutoff)).fetchone()[0]
    return int(n)<int(max_per_cluster),int(n)


def loop_once():
    with sqlite3.connect(LIVE_DB) as con:
        con.row_factory=sqlite3.Row
        rows=con.execute("SELECT * FROM watch_state ORDER BY analyst_confidence DESC").fetchall()
        # Fetch external market snapshots concurrently; DB state writes and
        # Telegram sends remain serialized and deterministic in watchlist order.
        with ThreadPoolExecutor(max_workers=LIVE_FETCH_WORKERS) as workers:
            snapshots={r["symbol"]:workers.submit(market_snapshot,r["symbol"]) for r in rows}
            for row in rows:
                try:
                    price,closed,closed_candle_time,early=snapshots[row["symbol"]].result()
                    old=row["stage"]
                    structure_quality=live_structure_confirmation(row,closed,early)
                    new=next_stage(row,price,closed,structure_quality)
                    observed_time=now_iso()

                    # Separate observational early layer: never mutates frozen continuation stage.
                    old_early=(row["early_state"] or "NONE") if "early_state" in row.keys() else "NONE"
                    if _row_is_radar(row) or not _row_is_analyst_active(row):
                        estate,emetrics="NONE",{
                            "radar_only":_row_is_radar(row),
                            "analyst_active":_row_is_analyst_active(row),
                        }
                    else:
                        estate,emetrics=early_observation(row,price,early)
                    inv=float(row["invalidation"] or 0)
                    if old_early in ("EARLY_LONG","EARLY_SHORT","PENDING","CHASE"):
                        broken=(row["direction"]=="LONG" and inv and price<inv) or (row["direction"]=="SHORT" and inv and price>inv)
                        if broken:
                            estate="BROKEN"
                        elif old_early in ("EARLY_LONG","EARLY_SHORT") and estate=="NONE":
                            estate="PENDING"
                        elif old_early=="PENDING" and estate=="NONE":
                            estate="PENDING"
                        elif old_early=="CHASE" and estate in ("NONE","PENDING"):
                            estate="CHASE"
                    if estate!=old_early:
                        emsg=None
                        if estate in ("PENDING","EARLY_LONG","EARLY_SHORT","CHASE"):
                            level=float(row["trigger_level"])
                            day_change=float(emetrics.get("day_change_pct") or 0.0)
                            mclass=classify_move(row["symbol"],day_change)
                            if not TELEGRAM_TRADE_ONLY and mclass!="FAST_QUIET":
                                queued_msg=format_alert(row["symbol"],row["direction"],level,mclass,day_change,price,
                                                        row["invalidation"],row["target1"],row["target2"])
                                priority=4 if mclass=="FAST_FRESH" else 3 if mclass=="STABLE" else 2
                                watch_payload={
                                    "stage":"WATCH_ALERT",
                                    "early_state":estate,
                                    "price":float(price),
                                    "trigger_level":level,
                                    "invalidation":float(row["invalidation"] or 0.0),
                                    "target1":float(row["target1"] or 0.0),
                                    "target2":float(row["target2"] or 0.0),
                                    "data_cohort":str(row["data_cohort"] or "UNKNOWN") if "data_cohort" in row.keys() else "UNKNOWN",
                                    "structure_gate_version":str(row["structure_gate_version"] or "") if "structure_gate_version" in row.keys() else "",
                                    "analyst_scan_time":str(row["analyst_scan_time"] or ""),
                                    "analyst_confidence":int(row["analyst_confidence"] or 0),
                                    "day_change_pct":day_change,
                                    "move_class":mclass,
                                }
                                queue_alert(row["symbol"],row["direction"],level,queued_msg,priority,payload=watch_payload)
                        first_signal=estate in ("EARLY_LONG","EARLY_SHORT") and old_early not in ("EARLY_LONG","EARLY_SHORT")
                        con.execute("""UPDATE watch_state SET early_state=?,
                            early_signal_price=CASE WHEN ? THEN ? ELSE early_signal_price END,
                            early_signal_time=CASE WHEN ? THEN ? ELSE early_signal_time END,
                            early_short_signal_price=CASE WHEN ? AND direction='SHORT' THEN ? ELSE early_short_signal_price END,
                            last_price=?,last_closed_5m=?,last_update_utc=? WHERE symbol=?""",
                            (estate,1 if first_signal else 0,price,1 if first_signal else 0,observed_time,
                             1 if first_signal else 0,price,price,closed,observed_time,row["symbol"]))
                        con.commit()
                        if emsg:
                            print(emsg); send_telegram(emsg)
                        con.execute("""INSERT INTO events(event_time_utc,symbol,direction,stage_from,stage_to,
                            price,closed_5m,condition_time_utc,payload_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                            (observed_time,row["symbol"],row["direction"],"EARLY:"+old_early,"EARLY:"+estate,
                             price,closed,observed_time,json.dumps({
                                 **emetrics,
                                 "_live_structure_quality":structure_quality,
                                 "_live_alert_version":STRUCTURE_GATE_VERSION,
                                 "_setup":{
                                     "trigger_level":float(row["trigger_level"]),
                                     "invalidation":float(row["invalidation"] or 0.0),
                                     "target1":float(row["target1"] or 0.0),
                                     "target2":float(row["target2"] or 0.0),
                                     "analyst_confidence":int(row["analyst_confidence"] or 0),
                                     "data_mode":str(row["data_mode"] or "UNKNOWN"),
                                     "data_cohort":str(row["data_cohort"] or "UNKNOWN") if "data_cohort" in row.keys() else "UNKNOWN",
                                     "derivatives_provider":str(row["derivatives_provider"] or "") if "derivatives_provider" in row.keys() else "",
                                     "derivatives_quality":str(row["derivatives_quality"] or "") if "derivatives_quality" in row.keys() else "",
                                     "analyst_scan_time":str(row["analyst_scan_time"] or ""),
                                 },
                             },ensure_ascii=False)))
                        con.commit()

                    if new!=old:
                        # V3 correlated-risk cap: at most two user-facing TRIGGERED
                        # signals per direction in a rolling 15m market wave. Extra
                        # qualifying setups are preserved as shadow events, not sent.
                        if new=="TRIGGERED":
                            try:
                                trigger_proxy=trigger_execution_proxy(row["symbol"])
                            except Exception as exc:
                                trigger_proxy={"available":False,"error":type(exc).__name__+":"+str(exc)[:120]}
                            execution_check=live_execution_gate(row,price,trigger_proxy)
                            if not execution_check.get("qualified"):
                                blocked_payload=dict(row)
                                blocked_payload["_live_price_source"]=str(early.get("live_price_source") or "UNKNOWN")
                                blocked_payload["_live_structure_quality"]=structure_quality
                                blocked_payload["_live_alert_version"]=STRUCTURE_GATE_VERSION
                                blocked_payload["_trigger_execution_gate"]=execution_check
                                con.execute("""INSERT INTO events(
                                    event_time_utc,symbol,direction,stage_from,stage_to,price,closed_5m,
                                    condition_time_utc,telegram_status,payload_json
                                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                    (observed_time,row["symbol"],row["direction"],old,"EXECUTION_BLOCKED",
                                     price,closed,observed_time,"SHADOW",
                                     json.dumps(blocked_payload,ensure_ascii=False)))
                                con.execute("""UPDATE watch_state
                                               SET stage='EXECUTION_BLOCKED',last_price=?,last_closed_5m=?,
                                                   last_update_utc=? WHERE symbol=?""",
                                            (price,closed,observed_time,row["symbol"]))
                                con.execute("""UPDATE watch_episodes SET max_stage='EXECUTION_BLOCKED',
                                               ended_at_utc=?,end_reason='TRIGGER_EXECUTION_GATE',
                                               last_price=? WHERE symbol=? AND ended_at_utc IS NULL""",
                                            (observed_time,price,row["symbol"]))
                                con.commit()
                                continue

                            allowed,cluster_count=cluster_trigger_allowed(con,row["direction"],observed_time,2)
                            if not allowed:
                                blocked_payload=dict(row)
                                blocked_payload["_live_structure_quality"]=structure_quality
                                blocked_payload["_live_alert_version"]=STRUCTURE_GATE_VERSION
                                blocked_payload["_live_price_source"]=str(early.get("live_price_source") or "UNKNOWN")
                                blocked_payload["_cluster_blocked_count"]=cluster_count
                                con.execute("""INSERT INTO events(
                                    event_time_utc,symbol,direction,stage_from,stage_to,price,closed_5m,
                                    condition_time_utc,telegram_status,payload_json
                                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                    (observed_time,row["symbol"],row["direction"],old,"CLUSTER_BLOCKED",
                                     price,closed,observed_time,"SHADOW",
                                     json.dumps(blocked_payload,ensure_ascii=False)))
                                con.execute("""UPDATE watch_state
                                               SET stage='CLUSTER_BLOCKED',last_price=?,last_closed_5m=?,
                                                   last_update_utc=? WHERE symbol=?""",
                                            (price,closed,observed_time,row["symbol"]))
                                con.execute("""UPDATE watch_episodes SET max_stage='CLUSTER_BLOCKED',
                                               ended_at_utc=?,end_reason='CORRELATED_CLUSTER_CAP',
                                               last_price=? WHERE symbol=? AND ended_at_utc IS NULL""",
                                            (observed_time,price,row["symbol"]))
                                con.commit()
                                continue

                        # APPROACHING is a user-facing watch state. Previously it was
                        # stored in DB but never queued to Telegram.
                        if new=="APPROACHING" and not TELEGRAM_TRADE_ONLY:
                            queued=queue_approaching_alert(row,price,structure_quality)
                            if queued:
                                print(f"WATCH_ALERT_QUEUED {row['symbol']} {row['direction']} {float(row['trigger_level'])}",flush=True)
                        # For a 5m close confirmation, the market condition time is the
                        # completed candle close. For intrabar states, first observation
                        # is the most honest timestamp available without websocket trades.
                        condition_time = closed_candle_time if new=="CLOSE_CONFIRMED" else observed_time
                        msg=message_for(row,new,price,closed)

                        con.execute("""UPDATE watch_state SET stage=?,last_price=?,last_closed_5m=?,last_update_utc=?,
                                       close_confirmed_time=CASE WHEN ?='CLOSE_CONFIRMED' THEN ? ELSE close_confirmed_time END,
                                       retest_seen=CASE WHEN ?='RETESTING' THEN 1 ELSE retest_seen END
                                       WHERE symbol=?""",
                            (new,price,closed,observed_time,new,condition_time,new,row["symbol"]))
                        if new=="TRIGGERED":
                            fresh=con.execute("SELECT early_signal_price,early_signal_time,direction FROM watch_state WHERE symbol=?",(row["symbol"],)).fetchone()
                            if fresh and fresh[0] is not None:
                                ep=float(fresh[0]); et=fresh[1]
                                gain=((price/ep-1.0)*100.0) if fresh[2]=="LONG" else ((ep/price-1.0)*100.0)
                                try:
                                    dt=(datetime.fromisoformat(observed_time)-datetime.fromisoformat(et)).total_seconds() if et else None
                                except Exception:
                                    dt=None
                                con.execute("""UPDATE watch_state SET confirmed_signal_price=?,confirmed_signal_time=?,
                                    gain_before_confirmation=?,time_early_to_confirmed_seconds=?,
                                    confirmed_short_signal_price=CASE WHEN direction='SHORT' THEN ? ELSE confirmed_short_signal_price END,
                                    gain_before_short_confirmation=CASE WHEN direction='SHORT' THEN ? ELSE gain_before_short_confirmation END,
                                    time_early_short_to_confirmed_seconds=CASE WHEN direction='SHORT' THEN ? ELSE time_early_short_to_confirmed_seconds END
                                    WHERE symbol=?""",(price,observed_time,gain,dt,price,gain,dt,row["symbol"]))
                        con.execute("""UPDATE watch_episodes SET
                            max_stage=?,
                            close_confirmed_time=CASE WHEN ?='CLOSE_CONFIRMED' THEN ? ELSE close_confirmed_time END,
                            triggered_time=CASE WHEN ?='TRIGGERED' THEN ? ELSE triggered_time END,
                            last_price=?
                            WHERE symbol=? AND ended_at_utc IS NULL""",
                            (new,new,condition_time,new,condition_time,price,row["symbol"]))
                        con.commit()

                        sent_time=None
                        delay=None
                        telegram_status="NOT_APPLICABLE" if not msg else "PENDING"
                        telegram_error=None
                        event_payload=dict(row)
                        event_payload["_live_structure_quality"]=structure_quality
                        event_payload["_live_price_source"]=str(early.get("live_price_source") or "UNKNOWN")
                        event_payload["_live_alert_version"]=STRUCTURE_GATE_VERSION
                        if msg:
                            print(msg)
                            try:
                                sent_time=send_telegram(msg)
                                telegram_status="SENT"
                            except Exception as exc:
                                telegram_status="FAILED"
                                telegram_error=type(exc).__name__+":"+str(exc)[:180]
                                print("telegram direct send failed",row["symbol"],new,telegram_error)
                            if sent_time:
                                try:
                                    delay=(datetime.fromisoformat(sent_time)-datetime.fromisoformat(condition_time)).total_seconds()
                                except Exception:
                                    delay=None
                                if delay is not None:
                                    print(f"ALERT_DELAY {row['symbol']} {new}: {delay:.1f}s")
                        if new=="TRIGGERED":
                            event_payload["_trigger_execution_proxy"]=trigger_proxy
                            event_payload["_trigger_execution_gate"]=execution_check

                        con.execute("""INSERT INTO events(
                            event_time_utc,symbol,direction,stage_from,stage_to,price,closed_5m,
                            condition_time_utc,telegram_sent_time_utc,telegram_status,telegram_error,delay_seconds,payload_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (observed_time,row["symbol"],row["direction"],old,new,price,closed,
                             condition_time,sent_time,telegram_status,telegram_error,delay,
                             json.dumps(event_payload,ensure_ascii=False)))
                        con.commit()
                    else:
                        con.execute("UPDATE watch_state SET last_price=?,last_closed_5m=?,last_update_utc=? WHERE symbol=?",
                                    (price,closed,observed_time,row["symbol"]))
                        con.commit()
                except Exception as exc:
                    print("live error",row["symbol"],type(exc).__name__,str(exc)[:120])

    # Retain old pending observational alerts in DB; never release them to Telegram.
    ready=None if TELEGRAM_TRADE_ONLY else claim_ready_alert()
    if ready:
        print(ready["message"])
        try:
            send_telegram(ready["message"])
        except Exception as exc:
            retry_claimed_alert(ready)
            print("telegram send failed; requeued",type(exc).__name__,str(exc)[:160])
        else:
            ack_claimed_alert(ready)


def watch_window_end(start_epoch, run_seconds, align_to_5m=ALIGN_TO_5M, grace_seconds=ALIGN_GRACE_SECONDS):
    """Run a full observation window even when a candle boundary occurs early.

    Aligning to the next 5-minute timestamp is useful for deciding when to
    inspect closed candles, NOT for shortening the watcher job. Old code used
    min(start+RUN_SECONDS, next_boundary+grace), often only running ~1 minute.
    The live loop already fetches only closed candles and its 15s polling
    naturally observes the first post-boundary candle.
    """
    return float(start_epoch) + max(0.0, float(run_seconds))


def main():
    init_db()
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if token:
        try:
            resolve_live_chat_id(token,configured)
        except Exception as exc:
            print("Telegram chat cache prime failed",type(exc).__name__,str(exc)[:160])
    items=load_watchlist()
    sync_watchlist(items)
    if token and TELEGRAM_RUNTIME_NOTICES and not TELEGRAM_TRADE_ONLY:
        deployment_notice=send_recovery_notice_once(len(items))
        if not deployment_notice:
            send_health_if_due(len(items))
    now=time.time()
    end=watch_window_end(now,RUN_SECONDS)
    # The previous early 5m boundary stop created substantial unmonitored
    # windows between analyst/live GitHub Actions executions.
    print(f"Live pool started: {len(items)} symbols, poll={POLL_SECONDS}s, "
          f"full_window_seconds={RUN_SECONDS}, "
          f"until={datetime.fromtimestamp(end,tz=timezone.utc).isoformat()}",flush=True)
    while time.time()<end:
        loop_once()
        time.sleep(POLL_SECONDS)

if __name__=="__main__":
    main()
