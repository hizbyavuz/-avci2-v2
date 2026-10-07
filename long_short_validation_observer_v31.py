#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only V3.1 signal validation: paired baselines, source health and cluster CI.

Does not change score, signal, risk gates, Telegram or automated execution.
Uses only delivered TRIGGERED events whose measured horizons already exist.
All baseline directions are fixed at signal time (no future price leakage).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sqlite3
from collections import defaultdict
from datetime import timedelta

from long_short_outcome_tracker_v21 import (
    _barrier_path, _dt, _direction_return, _rows_1m, _slippage_pair,
    FEE_BPS_PER_SIDE,
)

VERSION = "LS_V31_VALIDATION_OBSERVER_2026-10-08"
DB = os.getenv("LS_LIVE_DB", "long_short_v31_live_pool.db")
MAX_EVENTS = int(os.getenv("LS_VALIDATION_MAX_NEW_EVENTS", "8"))


def _payload(raw):
    try:
        return json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}


def _source_health(event):
    p = _payload(event["payload_json"])
    gate = _payload(p.get("structure_gate_json"))
    cohort = str(p.get("data_cohort") or "UNKNOWN")
    provider = str(p.get("derivatives_provider") or "UNKNOWN")
    quality = str(p.get("derivatives_quality") or "UNKNOWN")
    ready = bool(gate.get("_derivatives_ready"))
    native = cohort == "BINANCE_FUTURES_NATIVE"
    alternate = provider in ("BYBIT_LINEAR", "GATE_FUTURES")
    core_single_venue = ready and (native or alternate)
    # The fallback router may mark a 4/4 core packet "V3_CORE_FULL" even
    # when 5th depth field is absent; do not inflate FULL health coverage.
    full_single_venue = core_single_venue and (native or quality == "FULL")
    return {
        "data_cohort": cohort, "derivatives_provider": provider,
        "derivatives_quality": quality, "ready": ready,
        "core_single_venue": bool(core_single_venue),
        "full_single_venue": bool(full_single_venue),
        "native": bool(native),
        # Outcome tracker always fetches Binance SPOT 1m, even for perp signals.
        "outcome_venue": "BINANCE_SPOT_1M",
        "outcome_matches_perp_venue": False,
        "config_hash": str(p.get("_frozen_config_hash") or "HISTORICAL_MISSING"),
        "code_sha": str(p.get("_signal_code_sha") or "HISTORICAL_MISSING"),
    }


def _random_direction(event_id, symbol):
    # Same draw for a trade's 15m, 60m and 180m outcomes.
    digest = hashlib.sha256(f"{VERSION}|{event_id}|{symbol}".encode()).digest()
    return "LONG" if digest[0] % 2 == 0 else "SHORT"


def _momentum_direction(candles, condition_time):
    """15 CLOSED 1m candles preceding the decision; never use a forming candle."""
    last_close_ms = int(_dt(condition_time).timestamp() * 1000)
    preceding = [r for r in candles if int(r[6]) < last_close_ms]
    preceding.sort(key=lambda r: int(r[0]))
    preceding = preceding[-15:]
    if len(preceding) < 6:
        return None
    start = float(preceding[0][4])
    end = float(preceding[-1][4])
    if not start or not end or start == end:
        return None
    return "LONG" if end > start else "SHORT"


def _mirror_levels(direction, entry, original_stop, original_target, original_direction):
    """Keep the EXACT original entry-relative stop and TP1 distances.

    A flip mirrors both levels around the same executable entry. This prevents
    the chosen model from receiving more favorable risk geometry than baseline.
    """
    if direction == original_direction:
        return original_stop, original_target
    sd = abs(original_stop - entry)
    td = abs(original_target - entry)
    if direction == "LONG":
        return entry - sd, entry + td
    return entry + sd, entry - td


def _simulate(row, candles, direction):
    entry = float(row["entry_price"])
    live_dir = str(row["direction"])
    stop, target = _mirror_levels(
        direction, entry, float(row["invalidation"]), float(row["target1"]), live_dir
    )
    if min(entry, stop, target) <= 0:
        return None
    if (direction == "LONG" and not stop < entry < target) or (
        direction == "SHORT" and not target < entry < stop
    ):
        return None
    first, first_ts, _, mfe, mae = _barrier_path(direction, entry, stop, target, 0.0, candles)
    endpoint = float(candles[-1][4])
    exit_price = target if first == "TP1" else stop if first == "STOP" else endpoint
    payload = _payload(row["event_payload_json"])
    buy_slip, sell_slip = _slippage_pair(payload, direction)
    cost = (2.0 * FEE_BPS_PER_SIDE + buy_slip + sell_slip) / 100.0
    gross = _direction_return(direction, entry, exit_price)
    net = gross - cost
    risk = abs(entry - stop) / entry * 100.0
    return {
        "stop": stop, "target1": target, "exit_price": exit_price,
        "barrier": first or "TIMEOUT", "barrier_time": first_ts,
        "gross_pct": gross, "net_pct": net,
        "r_multiple": net / risk if risk > 0 else None,
        "cost_pct": cost, "mfe_pct": mfe, "mae_pct": mae,
    }


def init_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS v31_validation_baselines(
        event_id INTEGER NOT NULL,
        horizon_min INTEGER NOT NULL,
        comparator TEXT NOT NULL,
        comparator_direction TEXT NOT NULL,
        net_pct REAL NOT NULL,
        r_multiple REAL,
        barrier TEXT NOT NULL,
        entry_price REAL NOT NULL,
        stop REAL NOT NULL,
        target1 REAL NOT NULL,
        cost_pct REAL NOT NULL,
        source_health_json TEXT NOT NULL,
        version TEXT NOT NULL,
        calculated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(event_id, horizon_min, comparator)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS v31_validation_issues(
        event_id INTEGER NOT NULL,
        version TEXT NOT NULL,
        reason TEXT NOT NULL,
        observed_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(event_id,version,reason)
    )""")


def _pending_events(conn, limit):
    return conn.execute("""SELECT DISTINCT o.event_id FROM delivered_signal_outcomes AS o
        WHERE o.stage_name='TRIGGERED'
          AND NOT EXISTS (
            SELECT 1 FROM v31_validation_baselines AS v
            WHERE v.event_id=o.event_id AND v.horizon_min=o.horizon_min
              AND v.comparator='RANDOM_DIRECTION'
          )
        ORDER BY o.event_id LIMIT ?""", (limit,)).fetchall()


def evaluate(conn, fetcher=_rows_1m, max_events=MAX_EVENTS):
    conn.row_factory = sqlite3.Row
    completed, missing = 0, 0
    for item in _pending_events(conn, max_events):
        event_id = int(item["event_id"])
        horizons = conn.execute("""SELECT o.*,e.payload_json AS event_payload_json,
                e.condition_time_utc AS event_condition_time_utc
            FROM delivered_signal_outcomes AS o
            JOIN events AS e ON e.id=o.event_id
            WHERE o.event_id=? AND o.stage_name='TRIGGERED'
            ORDER BY o.horizon_min""", (event_id,)).fetchall()
        if not horizons:
            continue
        first = horizons[0]
        cond = first["event_condition_time_utc"] or first["condition_time_utc"]
        entry_at = _dt(first["execution_time_utc"])
        start = min(_dt(cond) - timedelta(minutes=17), entry_at)
        end = max(_dt(r["execution_time_utc"]) + timedelta(minutes=int(r["horizon_min"]))
                  for r in horizons)
        try:
            candles = fetcher(str(first["symbol"]), start, end)
        except Exception as exc:
            conn.execute("""INSERT OR IGNORE INTO v31_validation_issues(event_id,version,reason)
                VALUES(?,?,?)""", (event_id, VERSION, "FETCH_ERROR:"+type(exc).__name__))
            missing += 1
            continue
        mom = _momentum_direction(candles, cond)
        if mom is None:
            conn.execute("""INSERT OR IGNORE INTO v31_validation_issues(event_id,version,reason)
                VALUES(?,?,?)""", (event_id, VERSION, "MOMENTUM_HISTORY_MISSING"))
        random_dir = _random_direction(event_id, str(first["symbol"]))
        health = _source_health(_event_by_id(conn, event_id))
        for horizon in horizons:
            h = int(horizon["horizon_min"])
            if conn.execute("""SELECT 1 FROM v31_validation_baselines
                WHERE event_id=? AND horizon_min=? AND comparator='RANDOM_DIRECTION'""",
                (event_id, h)).fetchone():
                continue
            execute = _dt(horizon["execution_time_utc"])
            cutoff = execute + timedelta(minutes=h)
            forward = [r for r in candles
                if int(r[0]) >= int(execute.timestamp()*1000)
                and int(r[0]) < int(cutoff.timestamp()*1000)]
            if not forward:
                conn.execute("""INSERT OR IGNORE INTO v31_validation_issues(event_id,version,reason)
                    VALUES(?,?,?)""", (event_id, VERSION, f"NO_FORWARD_CANDLES_{h}M"))
                missing += 1
                continue
            for name, direction in (("RANDOM_DIRECTION", random_dir),
                                    ("MOMENTUM_15M", mom)):
                if direction is None:
                    continue
                result = _simulate(horizon, forward, direction)
                if result is None:
                    conn.execute("""INSERT OR IGNORE INTO v31_validation_issues(event_id,version,reason)
                        VALUES(?,?,?)""", (event_id, VERSION, "INVALID_BASELINE_GEOMETRY"))
                    continue
                conn.execute("""INSERT OR IGNORE INTO v31_validation_baselines(
                    event_id,horizon_min,comparator,comparator_direction,net_pct,
                    r_multiple,barrier,entry_price,stop,target1,cost_pct,
                    source_health_json,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (event_id,h,name,direction,result["net_pct"],result["r_multiple"],
                     result["barrier"],horizon["entry_price"],result["stop"],
                     result["target1"],result["cost_pct"],json.dumps(health),VERSION))
                completed += 1
        conn.commit()
    return {"baseline_rows_written": completed, "data_issues": missing}


def _event_by_id(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()


def _percentile(xs, p):
    xs=sorted(xs)
    if not xs:
        return None
    pos=(len(xs)-1)*p
    a=int(pos); b=min(a+1,len(xs)-1)
    return xs[a]*(1-(pos-a))+xs[b]*(pos-a)


def _cluster_ci(pairs, seed=20261008, repeats=1000):
    """Paired cluster bootstrap: CI for mean model-minus-baseline net pct.

    Each market-wave cluster has equal weight; signals within one wave are not
    incorrectly treated as independent observations.
    """
    clusters=defaultdict(list)
    for cluster, live_net, baseline_net in pairs:
        clusters[str(cluster)].append(float(live_net)-float(baseline_net))
    vals=[sum(v)/len(v) for v in clusters.values()]
    if len(vals)<2:
        return {"clusters":len(vals),"mean_diff_pct":sum(vals)/len(vals) if vals else None,
                "ci95_pct":None}
    rng=random.Random(seed)
    n=len(vals)
    draws=[sum(vals[rng.randrange(n)] for _ in range(n))/n for _ in range(repeats)]
    return {"clusters":n,"mean_diff_pct":sum(vals)/n,
            "ci95_pct":[_percentile(draws,0.025),_percentile(draws,0.975)]}


def report(conn):
    conn.row_factory=sqlite3.Row
    events=conn.execute("""SELECT e.* FROM events AS e
        WHERE stage_to='TRIGGERED' AND telegram_status='SENT'""").fetchall()
    sources=defaultdict(lambda:{"sent":0,"single_venue_full":0,"historical_missing_hash":0})
    full=0
    core=0
    for event in events:
        h=_source_health(event)
        k=h["data_cohort"]+"|"+h["derivatives_provider"]+"|"+h["derivatives_quality"]
        sources[k]["sent"]+=1
        sources[k]["single_venue_full"]+=int(h["full_single_venue"])
        sources[k]["historical_missing_hash"]+=int(h["config_hash"]=="HISTORICAL_MISSING")
        full+=int(h["full_single_venue"])
        core+=int(h["core_single_venue"])
    comparisons=[]
    for h in (15,60,180):
        for method in ("RANDOM_DIRECTION","MOMENTUM_15M"):
            rows=conn.execute("""SELECT o.event_id,o.cluster_id,
                o.net_return_pct AS live_net,v.net_pct AS baseline_net
                FROM delivered_signal_outcomes AS o
                JOIN v31_validation_baselines AS v ON
                    o.event_id=v.event_id AND o.horizon_min=v.horizon_min
                WHERE o.stage_name='TRIGGERED' AND o.horizon_min=? AND v.comparator=?""",
                (h,method)).fetchall()
            pairs=[(r["cluster_id"] or "NO_CLUSTER_"+str(r["event_id"]),
                    r["live_net"],r["baseline_net"]) for r in rows
                   if r["live_net"] is not None and r["baseline_net"] is not None]
            ci=_cluster_ci(pairs)
            comparisons.append({
                "horizon_min":h,"baseline":method,"paired_events":len(pairs),
                "independent_market_clusters":ci["clusters"],
                "model_minus_baseline_cluster_mean_net_pct":ci["mean_diff_pct"],
                "cluster_bootstrap_95_ci_pct":ci["ci95_pct"],
                "live_average_net_pct":sum(p[1] for p in pairs)/len(pairs) if pairs else None,
                "baseline_average_net_pct":sum(p[2] for p in pairs)/len(pairs) if pairs else None,
            })
    issues=conn.execute("""SELECT reason,COUNT(*) AS n FROM v31_validation_issues
        WHERE version=? GROUP BY reason ORDER BY n DESC""", (VERSION,)).fetchall()
    return {
        "version":VERSION,
        "data_health":{"sent_trade_signals":len(events),
                       "full_single_venue":full,
                       "four_field_core_single_venue":core,
                       "four_field_core_ratio":core/len(events) if events else None,
                       "full_single_venue_ratio":full/len(events) if events else None,
                       "outcome_market":"BINANCE_SPOT_1M",
                       "perp_venue_matched_outcomes":0,
                       "by_source":dict(sources)},
        "comparisons":comparisons,
        "data_issues":{r["reason"]:r["n"] for r in issues},
        "limitations":[
            "Random direction is fixed by deterministic hash per event, not optimized.",
            "Momentum uses only closed 1m candles before signal.",
            "Entry/stop/TP distances and fee/slippage assumptions match model events.",
            "All realized outcomes still use Binance Spot 1m proxy, not same-venue perpetual fills.",
            "Confidence intervals assume market clusters are independent; small n is inconclusive.",
            "Missing market data is an explicit issue, never a fabricated losing trade."
        ],
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--max-events", type=int, default=MAX_EVENTS)
    args=ap.parse_args()
    if not os.path.exists(DB):
        print(json.dumps({"error":"live database unavailable","path":DB}))
        return
    with sqlite3.connect(DB,timeout=15) as con:
        init_db(con)
        added={"baseline_rows_written":0,"data_issues":0}
        if not args.report_only:
            added=evaluate(con,max_events=max(0,args.max_events))
        result=report(con)
        result["evaluation"]=added
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))


if __name__=="__main__":
    main()
