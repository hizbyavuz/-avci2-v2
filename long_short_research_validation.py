#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent statistical validator for Long/Short V1.9.

This module NEVER changes live scoring, thresholds, Telegram states, or trading
decisions. It reads the analyst/live databases, builds a conservative execution
shadow, matches same-scan controls, and reports episode-clustered evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests

ANALYST_DB = os.getenv("LS_DB", "long_short_analyst.db")
LIVE_DB = os.getenv("LS_LIVE_DB", "long_short_live_pool.db")
REVERSAL_DB = os.getenv("LS_REVERSAL_DB", "long_short_reversal_live.db")
RESEARCH_DB = os.getenv("LS_RESEARCH_DB", "long_short_research.db")
REPORT_PATH = os.getenv("LS_RESEARCH_REPORT", "long_short_research_report.json")
LIVE_CONFIG_PATH = os.getenv("LS_FROZEN_CONFIG_PATH", "LONG_SHORT_V1_9_FROZEN_CONFIG.json")

PROTOCOL_VERSION = "LS_V1_9_STAT_PROTOCOL_2026_10_05"
PRIMARY_STAGE = "TRIGGERED"
PRIMARY_HORIZON_MIN = 60
PRIMARY_COHORT = "BINANCE_FUTURES_NATIVE"
HUMAN_DELAY_SECONDS = int(os.getenv("LS_RESEARCH_HUMAN_DELAY_SECONDS", "30"))
FEE_BPS_PER_SIDE = float(os.getenv("LS_FEE_BPS_PER_SIDE", "5"))
MIN_SLIPPAGE_BPS_PER_SIDE = float(os.getenv("LS_RESEARCH_MIN_SLIPPAGE_BPS", "10"))
EPISODE_SECONDS = int(os.getenv("LS_RESEARCH_EPISODE_SECONDS", "7200"))
MIN_PRIMARY_EPISODES = int(os.getenv("LS_RESEARCH_MIN_PRIMARY_EPISODES", "100"))
PREFERRED_PRIMARY_EPISODES = int(os.getenv("LS_RESEARCH_PREFERRED_PRIMARY_EPISODES", "200"))
MAX_PRIMARY_PER_RUN = int(os.getenv("LS_RESEARCH_MAX_PRIMARY_PER_RUN", "24"))
MAX_CALIBRATION_PER_RUN = int(os.getenv("LS_RESEARCH_MAX_CALIBRATION_PER_RUN", "36"))
BOOTSTRAP_DRAWS = int(os.getenv("LS_RESEARCH_BOOTSTRAP_DRAWS", "4000"))
PAPER_NOTIONAL_USDT = float(os.getenv("LS_PAPER_NOTIONAL_USDT", "250"))
TIMEOUT = 12
BASES = ("https://data-api.binance.vision", "https://api.binance.com")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(x: str | None) -> datetime | None:
    if not x:
        return None
    d = datetime.fromisoformat(str(x))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def mean(xs):
    vals = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.fmean(vals) if vals else None


def file_sha256(path: str) -> str:
    h=hashlib.sha256()
    with open(path,"rb") as fh:
        for chunk in iter(lambda: fh.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def ceil_minute(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc)
    base = dt.replace(second=0, microsecond=0)
    return base if dt == base else base + timedelta(minutes=1)


def episode_id(signal_time: datetime, direction: str) -> str:
    bucket = int(signal_time.timestamp() // EPISODE_SECONDS)
    return f"{bucket}|{direction}"


def deterministic_seed(label: str) -> int:
    return int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:8], "big")


def bootstrap_episode_ci(rows, value_key: str):
    """Bootstrap episode means, not raw correlated signals."""
    groups = {}
    for r in rows:
        v = r.get(value_key)
        if v is None:
            continue
        groups.setdefault(r["episode_id"], []).append(float(v))
    episode_vals = [mean(v) for v in groups.values() if v]
    episode_vals = [x for x in episode_vals if x is not None]
    if not episode_vals:
        return {"episodes": 0, "mean": None, "ci95": [None, None]}
    point = mean(episode_vals)
    if len(episode_vals) == 1:
        return {"episodes": 1, "mean": point, "ci95": [None, None]}
    rng = random.Random(deterministic_seed(PROTOCOL_VERSION + "|" + value_key))
    draws = []
    n = len(episode_vals)
    for _ in range(max(500, BOOTSTRAP_DRAWS)):
        sample = [episode_vals[rng.randrange(n)] for _ in range(n)]
        draws.append(statistics.fmean(sample))
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    return {"episodes": n, "mean": point, "ci95": [lo, hi]}


def spot_get(path: str, params=None):
    last = None
    for base in BASES:
        try:
            r = requests.get(
                base + path,
                params=params or {},
                timeout=TIMEOUT,
                headers={"User-Agent": "lsa-research-validator/1.0"},
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
    raise last or RuntimeError("Binance Spot unavailable")


def minute_path(symbol: str, execution_time: datetime, horizon_min: int = 60):
    """Use first full 1m bar after the executable timestamp; no pre-signal candle."""
    entry_minute = ceil_minute(execution_time)
    start_ms = int(entry_minute.timestamp() * 1000)
    end_ms = int((entry_minute + timedelta(minutes=horizon_min + 1)).timestamp() * 1000)
    rows = spot_get("/api/v3/klines", {
        "symbol": symbol,
        "interval": "1m",
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": min(1000, horizon_min + 3),
    })
    if not rows or len(rows) < max(2, horizon_min - 3):
        return None
    first = rows[0]
    used = rows[:min(horizon_min, len(rows))]
    last = used[-1]
    return {
        "entry_time": datetime.fromtimestamp(int(first[0]) / 1000.0, tz=timezone.utc),
        "exit_time": datetime.fromtimestamp(int(last[6]) / 1000.0, tz=timezone.utc),
        "entry_open": float(first[1]),
        "entry_high": float(first[2]),
        "entry_low": float(first[3]),
        "entry_close": float(first[4]),
        "exit_open": float(last[1]),
        "exit_high": float(last[2]),
        "exit_low": float(last[3]),
        "exit_close": float(last[4]),
        "bars": len(used),
        "bar_path": [
            {
                "open_time_ms": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]), "close_time_ms": int(r[6]),
            }
            for r in used
        ],
    }


def range_buffer_bps(o: float, h: float, l: float) -> float:
    if not o:
        return 0.0
    rbps = abs(h - l) / o * 10000.0
    return min(50.0, 0.10 * rbps)


def cohort_from_payload(p: dict) -> str:
    source = str(p.get("derivatives_source") or p.get("data_mode") or "")
    provider = str(p.get("derivatives_selected_provider") or p.get("derivatives_provider") or "")
    if source == "BINANCE_FUTURES":
        return "BINANCE_FUTURES_NATIVE"
    if provider == "BYBIT_LINEAR":
        return "SPOT_PLUS_BYBIT"
    if provider == "GATE_FUTURES":
        return "SPOT_PLUS_GATE"
    if source == "MULTI_VENUE_PUBLIC":
        return "SPOT_PLUS_OTHER_OR_PARTIAL"
    return "UNKNOWN"


def side_proxy_bps(payload: dict, direction: str, entry: bool) -> float:
    proxy = payload.get("execution_proxy") or {}
    costs = (proxy.get("costs") or {}).get(str(int(PAPER_NOTIONAL_USDT))) or {}
    if direction == "LONG":
        key = "buy_bps" if entry else "sell_bps"
    else:
        key = "sell_bps" if entry else "buy_bps"
    try:
        v = float(costs.get(key))
        return max(0.0, v) if math.isfinite(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


def cost_adjusted_result(direction: str, path: dict, risk_pct: float, payload: dict | None = None):
    payload = payload or {}
    raw_entry = float(path["entry_open"])
    raw_exit = float(path["exit_close"])
    entry_slip = max(
        MIN_SLIPPAGE_BPS_PER_SIDE,
        side_proxy_bps(payload, direction, True),
        range_buffer_bps(raw_entry, path["entry_high"], path["entry_low"]),
    )
    exit_slip = max(
        MIN_SLIPPAGE_BPS_PER_SIDE,
        side_proxy_bps(payload, direction, False),
        range_buffer_bps(raw_exit, path["exit_high"], path["exit_low"]),
    )
    if direction == "LONG":
        entry = raw_entry * (1.0 + (FEE_BPS_PER_SIDE + entry_slip) / 10000.0)
        exit_px = raw_exit * (1.0 - (FEE_BPS_PER_SIDE + exit_slip) / 10000.0)
        net_pct = (exit_px / entry - 1.0) * 100.0
    else:
        entry = raw_entry * (1.0 - (FEE_BPS_PER_SIDE + entry_slip) / 10000.0)
        exit_px = raw_exit * (1.0 + (FEE_BPS_PER_SIDE + exit_slip) / 10000.0)
        net_pct = (entry / exit_px - 1.0) * 100.0 if exit_px else 0.0

    funding_cost_pct = 0.0
    try:
        funding = payload.get("derivatives_funding_pct")
        nxt = payload.get("next_funding_time_ms")
        if funding is not None and nxt and path.get("entry_time") and path.get("exit_time"):
            nf = float(nxt)
            st = path["entry_time"].timestamp() * 1000.0
            en = path["exit_time"].timestamp() * 1000.0
            if st < nf <= en:
                rate = float(funding)
                funding_cost_pct = rate if direction == "LONG" else -rate
                net_pct -= funding_cost_pct
    except Exception:
        funding_cost_pct = 0.0

    return {
        "entry_price": entry,
        "exit_price": exit_px,
        "entry_slippage_bps": entry_slip,
        "exit_slippage_bps": exit_slip,
        "funding_cost_pct": funding_cost_pct,
        "net_return_pct": net_pct,
        "r_multiple": net_pct / risk_pct if risk_pct and risk_pct > 0 else None,
    }


def barrier_outcome(direction: str, path: dict, stop: float, target1: float, target2: float):
    """Resolve path on 1m bars; if stop and target are touched in one bar, stop wins."""
    bars = path.get("bar_path") or []
    if not bars or not stop:
        return None
    for b in bars:
        hi=float(b["high"]); lo=float(b["low"])
        if direction=="LONG":
            if lo<=stop:
                return {"outcome":"STOP","price":float(stop),"time_ms":int(b["close_time_ms"])}
            if target2 and hi>=target2:
                return {"outcome":"TP2","price":float(target2),"time_ms":int(b["close_time_ms"])}
            if target1 and hi>=target1:
                return {"outcome":"TP1","price":float(target1),"time_ms":int(b["close_time_ms"])}
        else:
            if hi>=stop:
                return {"outcome":"STOP","price":float(stop),"time_ms":int(b["close_time_ms"])}
            if target2 and lo<=target2:
                return {"outcome":"TP2","price":float(target2),"time_ms":int(b["close_time_ms"])}
            if target1 and lo<=target1:
                return {"outcome":"TP1","price":float(target1),"time_ms":int(b["close_time_ms"])}
    last=bars[-1]
    return {"outcome":"TIMEOUT","price":float(last["close"]),"time_ms":int(last["close_time_ms"])}


def init_db(con: sqlite3.Connection):
    con.execute("""CREATE TABLE IF NOT EXISTS protocol_meta(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS primary_events(
        source_event_id INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        signal_time_utc TEXT NOT NULL,
        telegram_time_utc TEXT NOT NULL,
        execution_time_utc TEXT NOT NULL,
        analyst_scan_time TEXT,
        data_cohort TEXT NOT NULL,
        derivatives_provider TEXT,
        model_version TEXT,
        model_config_hash TEXT,
        score INTEGER,
        entry_price REAL,
        exit_price REAL,
        risk_pct REAL,
        entry_slippage_bps REAL,
        exit_slippage_bps REAL,
        net_return_pct REAL,
        r_multiple REAL,
        matched_control_r REAL,
        delta_r REAL,
        control_symbols_json TEXT,
        episode_id TEXT NOT NULL,
        funding_cost_pct REAL,
        barrier_outcome TEXT,
        barrier_exit_price REAL,
        barrier_time_ms INTEGER,
        evaluated_at_utc TEXT NOT NULL
    )""")
    pcols={r[1] for r in con.execute("PRAGMA table_info(primary_events)")}
    for name,typ in {
        "model_config_hash":"TEXT",
        "funding_cost_pct":"REAL",
        "barrier_outcome":"TEXT",
        "barrier_exit_price":"REAL",
        "barrier_time_ms":"INTEGER",
    }.items():
        if name not in pcols:
            con.execute(f"ALTER TABLE primary_events ADD COLUMN {name} {typ}")
    con.execute("""CREATE TABLE IF NOT EXISTS score_calibration(
        scan_time_utc TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        score INTEGER NOT NULL,
        data_cohort TEXT NOT NULL,
        execution_time_utc TEXT NOT NULL,
        net_return_pct REAL,
        r_multiple REAL,
        episode_id TEXT NOT NULL,
        evaluated_at_utc TEXT NOT NULL,
        PRIMARY KEY(scan_time_utc,symbol)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS conflicts(
        observed_at_utc TEXT NOT NULL,
        symbol TEXT NOT NULL,
        continuation_direction TEXT NOT NULL,
        reversal_direction TEXT NOT NULL,
        analyst_scan_time TEXT,
        PRIMARY KEY(observed_at_utc,symbol)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS research_reports(
        report_time_utc TEXT NOT NULL,
        report_type TEXT NOT NULL,
        report_key TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY(report_time_utc,report_type,report_key)
    )""")

    frozen = {
        "protocol_version": PROTOCOL_VERSION,
        "primary_stage": PRIMARY_STAGE,
        "primary_horizon_min": PRIMARY_HORIZON_MIN,
        "primary_cohort": PRIMARY_COHORT,
        "live_config_manifest": LIVE_CONFIG_PATH,
        "live_config_hash": file_sha256(LIVE_CONFIG_PATH),
        "human_delay_seconds": HUMAN_DELAY_SECONDS,
        "fee_bps_per_side": FEE_BPS_PER_SIDE,
        "min_slippage_bps_per_side": MIN_SLIPPAGE_BPS_PER_SIDE,
        "episode_seconds": EPISODE_SECONDS,
        "min_primary_episodes": MIN_PRIMARY_EPISODES,
        "preferred_primary_episodes": PREFERRED_PRIMARY_EPISODES,
    }
    config_hash = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    frozen["research_config_hash"] = config_hash
    for k, v in frozen.items():
        encoded=json.dumps(v, ensure_ascii=False)
        old=con.execute("SELECT value FROM protocol_meta WHERE key=?",(k,)).fetchone()
        if old and old[0] != encoded:
            raise RuntimeError(f"FROZEN_PROTOCOL_MISMATCH {k}: stored={old[0]} current={encoded}")
        con.execute(
            "INSERT OR IGNORE INTO protocol_meta(key,value) VALUES(?,?)",
            (k, encoded),
        )
    return frozen


def analyst_context(acon: sqlite3.Connection, scan_time: str | None, symbol: str):
    if not scan_time:
        return {}
    acon.row_factory = sqlite3.Row
    row = acon.execute(
        """SELECT status,long_score,short_score,price,stop,payload_json
           FROM analyses WHERE scan_time_utc=? AND symbol=?
           ORDER BY id DESC LIMIT 1""",
        (scan_time, symbol),
    ).fetchone()
    if not row:
        return {}
    try:
        p = json.loads(row["payload_json"] or "{}")
    except Exception:
        p = {}
    plan = p.get("setup_plan") or {}
    return {
        "status": row["status"],
        "score": max(int(row["long_score"] or 0), int(row["short_score"] or 0)),
        "price": float(row["price"] or 0.0),
        "stop": float(row["stop"] or 0.0),
        "invalidation": float(plan.get("invalidation") or 0.0),
        "target1": float(plan.get("target1") or 0.0),
        "target2": float(plan.get("target2") or 0.0),
        "payload": p,
        "cohort": cohort_from_payload(p),
        "provider": str(p.get("derivatives_selected_provider") or ""),
        "version": str(p.get("version") or ""),
        "frozen_config_hash": str(p.get("frozen_config_hash") or ""),
    }


def target_observation(acon: sqlite3.Connection, scan_time: str, symbol: str):
    acon.row_factory = sqlite3.Row
    return acon.execute(
        """SELECT * FROM universe_observations
           WHERE scan_time_utc=? AND symbol=?""",
        (scan_time, symbol),
    ).fetchone()


def matched_controls(acon: sqlite3.Connection, scan_time: str, symbol: str, direction: str, target, limit=3):
    acon.row_factory = sqlite3.Row
    rows = acon.execute(
        """SELECT * FROM universe_observations
           WHERE scan_time_utc=? AND symbol<>? AND shortlisted=0 AND direction_hint=?""",
        (scan_time, symbol, direction),
    ).fetchall()
    if not rows:
        return []
    tq = float(target["quote_volume"] or 1.0) if target else 1.0
    td = abs(float(target["day_change_pct"] or 0.0)) if target else 0.0
    ta = float(target["t15_atr_pct"] or 0.0) if target else 0.0
    tr = float(target["prefilter_rank"] or 0.0) if target else 0.0

    def distance(r):
        q = max(float(r["quote_volume"] or 1.0), 1.0)
        d = abs(float(r["day_change_pct"] or 0.0))
        a = float(r["t15_atr_pct"] or 0.0)
        rank = float(r["prefilter_rank"] or 0.0)
        return (
            abs(math.log(q / max(tq, 1.0)))
            + abs(d - td) / 10.0
            + abs(a - ta) / 2.0
            + abs(rank - tr) / 20.0
        )
    return sorted(rows, key=distance)[:limit]


def evaluate_control(r, direction: str, execution_time: datetime):
    path = minute_path(str(r["symbol"]), execution_time, PRIMARY_HORIZON_MIN)
    if not path:
        return None
    risk_pct = float(r["risk_pct"] or 0.0)
    if risk_pct <= 0:
        return None
    result = cost_adjusted_result(direction, path, risk_pct, {})
    if result["r_multiple"] is None:
        return None
    return {"symbol": str(r["symbol"]), **result}


def evaluate_primary(rcon: sqlite3.Connection, acon: sqlite3.Connection, lcon: sqlite3.Connection):
    lcon.row_factory = sqlite3.Row
    now = now_utc()
    rows = lcon.execute(
        """SELECT * FROM events
           WHERE stage_to='TRIGGERED' AND telegram_sent_time_utc IS NOT NULL
           ORDER BY id"""
    ).fetchall()
    inserted = 0
    skipped_no_control = 0
    for row in rows:
        if inserted >= MAX_PRIMARY_PER_RUN:
            break
        if rcon.execute("SELECT 1 FROM primary_events WHERE source_event_id=?", (row["id"],)).fetchone():
            continue
        telegram_time = parse_dt(row["telegram_sent_time_utc"])
        condition_time = parse_dt(row["condition_time_utc"] or row["event_time_utc"])
        if not telegram_time or not condition_time:
            continue
        decision_base = max(telegram_time, condition_time)
        execution_time = decision_base + timedelta(seconds=HUMAN_DELAY_SECONDS)
        if now < ceil_minute(execution_time) + timedelta(minutes=PRIMARY_HORIZON_MIN + 2):
            continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        scan_time = str(payload.get("analyst_scan_time") or "")
        ctx = analyst_context(acon, scan_time, row["symbol"])
        cohort = str(payload.get("data_cohort") or ctx.get("cohort") or "UNKNOWN")
        provider = str(payload.get("derivatives_provider") or ctx.get("provider") or "")
        model_payload = dict(ctx.get("payload") or {})
        trigger_proxy=payload.get("_trigger_execution_proxy")
        if isinstance(trigger_proxy,dict) and trigger_proxy.get("available"):
            model_payload["execution_proxy"]=trigger_proxy
        invalidation = float(payload.get("invalidation") or ctx.get("invalidation") or 0.0)

        try:
            path = minute_path(row["symbol"], execution_time, PRIMARY_HORIZON_MIN)
            if not path:
                continue
            raw_entry = float(path["entry_open"])
            risk_pct = abs(raw_entry - invalidation) / raw_entry * 100.0 if raw_entry and invalidation else 0.0
            if risk_pct <= 0:
                continue
            model = cost_adjusted_result(row["direction"], path, risk_pct, model_payload)
            target1=float(payload.get("target1") or ctx.get("target1") or 0.0)
            target2=float(payload.get("target2") or ctx.get("target2") or 0.0)
            barrier=barrier_outcome(row["direction"],path,invalidation,target1,target2)

            target = target_observation(acon, scan_time, row["symbol"]) if scan_time else None
            controls = matched_controls(acon, scan_time, row["symbol"], row["direction"], target) if scan_time else []
            control_results = []
            for ctrl in controls:
                try:
                    cr = evaluate_control(ctrl, row["direction"], execution_time)
                    if cr:
                        control_results.append(cr)
                except Exception as exc:
                    print("control evaluation error", ctrl["symbol"], type(exc).__name__, str(exc)[:100])
            control_r = mean([x["r_multiple"] for x in control_results])
            if control_r is None:
                skipped_no_control += 1
            delta = (float(model["r_multiple"]) - control_r) if model["r_multiple"] is not None and control_r is not None else None
            eid = episode_id(condition_time, row["direction"])

            rcon.execute(
                """INSERT INTO primary_events(
                    source_event_id,symbol,direction,signal_time_utc,telegram_time_utc,
                    execution_time_utc,analyst_scan_time,data_cohort,derivatives_provider,
                    model_version,model_config_hash,score,entry_price,exit_price,risk_pct,
                    entry_slippage_bps,exit_slippage_bps,net_return_pct,r_multiple,
                    matched_control_r,delta_r,control_symbols_json,episode_id,
                    funding_cost_pct,barrier_outcome,barrier_exit_price,barrier_time_ms,
                    evaluated_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["id"], row["symbol"], row["direction"], iso(condition_time), iso(telegram_time),
                    iso(execution_time), scan_time or None, cohort, provider,
                    ctx.get("version"), ctx.get("frozen_config_hash"), ctx.get("score"), model["entry_price"], model["exit_price"],
                    risk_pct, model["entry_slippage_bps"], model["exit_slippage_bps"],
                    model["net_return_pct"], model["r_multiple"], control_r, delta,
                    json.dumps([x["symbol"] for x in control_results], ensure_ascii=False),
                    eid, model.get("funding_cost_pct"),
                    (barrier or {}).get("outcome"),(barrier or {}).get("price"),(barrier or {}).get("time_ms"),
                    iso(now_utc()),
                ),
            )
            inserted += 1
        except Exception as exc:
            print("primary evaluation error", row["symbol"], type(exc).__name__, str(exc)[:140])
    return {"inserted": inserted, "skipped_no_control": skipped_no_control}


def evaluate_score_calibration(rcon: sqlite3.Connection, acon: sqlite3.Connection):
    acon.row_factory = sqlite3.Row
    now = now_utc()
    rows = acon.execute(
        """SELECT scan_time_utc,symbol,status,long_score,short_score,price,stop,payload_json
           FROM analyses
           WHERE MAX(COALESCE(long_score,0),COALESCE(short_score,0)) >= 55
           ORDER BY scan_time_utc,symbol"""
    ).fetchall()
    inserted = 0
    for row in rows:
        if inserted >= MAX_CALIBRATION_PER_RUN:
            break
        if rcon.execute(
            "SELECT 1 FROM score_calibration WHERE scan_time_utc=? AND symbol=?",
            (row["scan_time_utc"], row["symbol"]),
        ).fetchone():
            continue
        st = parse_dt(row["scan_time_utc"])
        if not st:
            continue
        execution_time = st + timedelta(seconds=HUMAN_DELAY_SECONDS)
        if now < ceil_minute(execution_time) + timedelta(minutes=PRIMARY_HORIZON_MIN + 2):
            continue
        try:
            p = json.loads(row["payload_json"] or "{}")
        except Exception:
            p = {}
        try:
            path = minute_path(row["symbol"], execution_time, PRIMARY_HORIZON_MIN)
            if not path:
                continue
            inv = float((p.get("setup_plan") or {}).get("invalidation") or row["stop"] or 0.0)
            raw_entry = float(path["entry_open"])
            risk_pct = abs(raw_entry - inv) / raw_entry * 100.0 if raw_entry and inv else 0.0
            if risk_pct <= 0:
                continue
            long_score=int(row["long_score"] or 0)
            short_score=int(row["short_score"] or 0)
            direction="LONG" if long_score>short_score else "SHORT"
            result = cost_adjusted_result(direction, path, risk_pct, p)
            score = max(long_score, short_score)
            rcon.execute(
                """INSERT INTO score_calibration(
                    scan_time_utc,symbol,direction,score,data_cohort,execution_time_utc,
                    net_return_pct,r_multiple,episode_id,evaluated_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["scan_time_utc"], row["symbol"], direction, score,
                    cohort_from_payload(p), iso(execution_time), result["net_return_pct"],
                    result["r_multiple"], episode_id(st, direction), iso(now_utc()),
                ),
            )
            inserted += 1
        except Exception as exc:
            print("calibration error", row["symbol"], type(exc).__name__, str(exc)[:120])
    return inserted


def record_conflicts(rcon: sqlite3.Connection, lcon: sqlite3.Connection):
    if not os.path.exists(REVERSAL_DB):
        return 0
    try:
        xcon = sqlite3.connect(f"file:{os.path.abspath(REVERSAL_DB)}?mode=ro", uri=True)
        xcon.row_factory = sqlite3.Row
        lcon.row_factory = sqlite3.Row
        cont = {r["symbol"]: r for r in lcon.execute("SELECT symbol,direction,analyst_scan_time FROM watch_state").fetchall()}
        rev = {r["symbol"]: r for r in xcon.execute("SELECT symbol,direction FROM watch_state").fetchall()}
        ts = iso(now_utc())
        n = 0
        for sym, a in cont.items():
            b = rev.get(sym)
            if b and b["direction"] != a["direction"]:
                rcon.execute(
                    """INSERT OR IGNORE INTO conflicts(
                        observed_at_utc,symbol,continuation_direction,reversal_direction,analyst_scan_time
                    ) VALUES(?,?,?,?,?)""",
                    (ts, sym, a["direction"], b["direction"], a["analyst_scan_time"]),
                )
                n += 1
        xcon.close()
        return n
    except Exception as exc:
        print("conflict tagging error", type(exc).__name__, str(exc)[:120])
        return 0


def primary_report(rcon: sqlite3.Connection):
    rcon.row_factory = sqlite3.Row
    rows = [dict(r) for r in rcon.execute("SELECT * FROM primary_events ORDER BY source_event_id").fetchall()]
    overall_net = bootstrap_episode_ci(rows, "r_multiple")
    with_control = [r for r in rows if r.get("delta_r") is not None]
    overall_delta = bootstrap_episode_ci(with_control, "delta_r")

    cohorts = {}
    for cohort in sorted({r["data_cohort"] for r in rows}):
        sub = [r for r in rows if r["data_cohort"] == cohort]
        cohorts[cohort] = {
            "events": len(sub),
            "net_r": bootstrap_episode_ci(sub, "r_multiple"),
            "delta_r": bootstrap_episode_ci([x for x in sub if x.get("delta_r") is not None], "delta_r"),
        }

    current_live_hash=file_sha256(LIVE_CONFIG_PATH)
    confirmatory_rows=[
        r for r in rows
        if r["data_cohort"]==PRIMARY_COHORT
        and str(r.get("model_config_hash") or "")==current_live_hash
    ]
    prefreeze_or_other_config=[
        r for r in rows
        if r["data_cohort"]==PRIMARY_COHORT
        and str(r.get("model_config_hash") or "")!=current_live_hash
    ]
    confirmatory_control=[r for r in confirmatory_rows if r.get("delta_r") is not None]
    confirmatory_net=bootstrap_episode_ci(confirmatory_rows,"r_multiple")
    confirmatory_delta=bootstrap_episode_ci(confirmatory_control,"delta_r")

    episodes = int(confirmatory_delta["episodes"])
    ci_low = confirmatory_delta["ci95"][0]
    status = "INSUFFICIENT_EVIDENCE"
    if episodes >= MIN_PRIMARY_EPISODES:
        if (
            confirmatory_net["mean"] is not None and confirmatory_net["mean"] > 0
            and confirmatory_delta["mean"] is not None and confirmatory_delta["mean"] > 0
            and ci_low is not None and ci_low > 0
        ):
            status = "PRIMARY_PASS"
        else:
            status = "PRIMARY_NOT_CONFIRMED"

    delivery_total = 0
    delivery_sent = 0
    if os.path.exists(LIVE_DB):
        lro = sqlite3.connect(f"file:{os.path.abspath(LIVE_DB)}?mode=ro", uri=True)
        try:
            delivery_total = int(lro.execute("SELECT COUNT(*) FROM events WHERE stage_to='TRIGGERED'").fetchone()[0])
            delivery_sent = int(lro.execute(
                "SELECT COUNT(*) FROM events WHERE stage_to='TRIGGERED' AND telegram_sent_time_utc IS NOT NULL"
            ).fetchone()[0])
        finally:
            lro.close()

    return {
        "protocol_version": PROTOCOL_VERSION,
        "primary_stage": PRIMARY_STAGE,
        "primary_horizon_min": PRIMARY_HORIZON_MIN,
        "primary_cohort": PRIMARY_COHORT,
        "live_config_hash": current_live_hash,
        "status": status,
        "confirmatory_events": len(confirmatory_rows),
        "excluded_native_events_wrong_or_missing_config_hash": len(prefreeze_or_other_config),
        "confirmatory_events_with_matched_control": len(confirmatory_control),
        "independent_episodes": episodes,
        "minimum_episodes": MIN_PRIMARY_EPISODES,
        "preferred_episodes": PREFERRED_PRIMARY_EPISODES,
        "confirmatory_net_r": confirmatory_net,
        "confirmatory_matched_control_delta_r": confirmatory_delta,
        "descriptive_all_cohorts": {
            "events_evaluated": len(rows),
            "events_with_matched_control": len(with_control),
            "net_r": overall_net,
            "matched_control_delta_r": overall_delta,
        },
        "cohorts": cohorts,
        "telegram_delivery": {
            "triggered_events_total": delivery_total,
            "triggered_events_sent": delivery_sent,
            "triggered_events_not_sent": max(0, delivery_total - delivery_sent),
        },
    }


def calibration_report(rcon: sqlite3.Connection):
    rcon.row_factory = sqlite3.Row
    rows = [dict(r) for r in rcon.execute("SELECT * FROM score_calibration ORDER BY scan_time_utc,symbol").fetchall()]
    buckets = {}
    defs = ((55, 64, "55-64"), (65, 74, "65-74"), (75, 100, "75+"))
    ordered_means = []
    for lo, hi, label in defs:
        sub = [r for r in rows if lo <= int(r["score"]) <= hi]
        ci = bootstrap_episode_ci(sub, "r_multiple")
        win = mean([1.0 if float(r["r_multiple"]) > 0 else 0.0 for r in sub if r["r_multiple"] is not None])
        buckets[label] = {
            "events": len(sub),
            "episodes": ci["episodes"],
            "mean_r": ci["mean"],
            "ci95": ci["ci95"],
            "win_rate": (100.0 * win) if win is not None else None,
        }
        ordered_means.append(ci["mean"])
    valid = [x for x in ordered_means if x is not None]
    monotonic = None
    if len(valid) == 3:
        monotonic = ordered_means[0] <= ordered_means[1] <= ordered_means[2]
    cohort_counts = {}
    cohort_buckets = {}
    for r in rows:
        cohort_counts[r["data_cohort"]] = cohort_counts.get(r["data_cohort"], 0) + 1
    for cohort in sorted(cohort_counts):
        crows=[r for r in rows if r["data_cohort"]==cohort]
        cb={}
        for lo,hi,label in defs:
            sub=[r for r in crows if lo<=int(r["score"])<=hi]
            ci=bootstrap_episode_ci(sub,"r_multiple")
            win=mean([1.0 if float(r["r_multiple"])>0 else 0.0 for r in sub if r["r_multiple"] is not None])
            cb[label]={
                "events":len(sub),"episodes":ci["episodes"],"mean_r":ci["mean"],
                "ci95":ci["ci95"],"win_rate":(100.0*win) if win is not None else None,
            }
        cohort_buckets[cohort]=cb
    return {
        "exploratory": True,
        "horizon_min": PRIMARY_HORIZON_MIN,
        "mixed_cohort_buckets_descriptive_only": buckets,
        "mixed_cohort_monotonic_point_estimate_descriptive_only": monotonic,
        "cohort_counts": cohort_counts,
        "cohort_buckets": cohort_buckets,
    }


def write_reports(rcon: sqlite3.Connection, frozen: dict, run_stats: dict):
    primary = primary_report(rcon)
    calibration = calibration_report(rcon)
    report = {
        "generated_at_utc": iso(now_utc()),
        "frozen_protocol": frozen,
        "run_stats": run_stats,
        "primary": primary,
        "score_calibration": calibration,
        "warning": "Exploratory outputs cannot replace the frozen primary metric.",
    }
    report_time = report["generated_at_utc"]
    for typ, key, payload in (
        ("PRIMARY", "TRIGGERED_60M_MATCHED_CONTROL", primary),
        ("EXPLORATORY", "SCORE_CALIBRATION_60M", calibration),
    ):
        rcon.execute(
            "INSERT INTO research_reports(report_time_utc,report_type,report_key,payload_json) VALUES(?,?,?,?)",
            (report_time, typ, key, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return report


def main():
    if not os.path.exists(ANALYST_DB) or not os.path.exists(LIVE_DB):
        print("research validation skipped: source DB missing")
        return
    acon = sqlite3.connect(f"file:{os.path.abspath(ANALYST_DB)}?mode=ro", uri=True)
    lcon = sqlite3.connect(f"file:{os.path.abspath(LIVE_DB)}?mode=ro", uri=True)
    rcon = sqlite3.connect(RESEARCH_DB)
    try:
        frozen = init_db(rcon)
        pstats = evaluate_primary(rcon, acon, lcon)
        cstats = evaluate_score_calibration(rcon, acon)
        conflicts = record_conflicts(rcon, lcon)
        report = write_reports(rcon, frozen, {
            "primary": pstats,
            "score_calibration_inserted": cstats,
            "conflicts_observed": conflicts,
        })
        rcon.commit()
    finally:
        acon.close()
        lcon.close()
        rcon.close()

    p = report["primary"]
    d = p["confirmatory_matched_control_delta_r"]
    print(
        "PRIMARY",
        p["status"],
        "events=", p["confirmatory_events"],
        "episodes=", p["independent_episodes"],
        "deltaR=", d["mean"],
        "CI95=", d["ci95"],
    )
    print("COHORTS", json.dumps(p["cohorts"], ensure_ascii=False, sort_keys=True))
    print("SCORE_CALIBRATION", json.dumps(report["score_calibration"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
