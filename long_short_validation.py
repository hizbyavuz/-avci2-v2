#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Forward validation for the Long/Short motor.

This file is deliberately write-only with respect to the trading rules: it
measures what already happened and never changes live thresholds.

It evaluates every deep-scan candidate and every prefilter shadow observation at
fixed 15m / 1h / 4h horizons, in directional return and R units. The same-scan
non-shortlisted observations form a control pool. It also records whether the
existing model and the simpler breakout-only baseline would have been eligible.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

import requests

DB = os.getenv("LS_DB", "long_short_analyst.db")
TIMEOUT = 12
HORIZONS = (15, 60, 240)
FEE_BPS_PER_SIDE = float(os.getenv("LS_FEE_BPS_PER_SIDE", "5"))
SLIPPAGE_BPS_PER_SIDE = float(os.getenv("LS_SLIPPAGE_BPS_PER_SIDE", "5"))
SPOT_BASES = ("https://data-api.binance.vision", "https://api.binance.com")


def now_utc():
    return datetime.now(timezone.utc)


def parse_iso(x: str) -> datetime:
    dt = datetime.fromisoformat(x)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def spot_get(path: str, params=None):
    last = None
    for base in SPOT_BASES:
        try:
            r = requests.get(
                base + path,
                params=params or {},
                timeout=TIMEOUT,
                headers={"User-Agent": "lsa-forward-validation/1.0"},
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
    raise last or RuntimeError("Binance Spot unavailable")


def future_path(symbol: str, start: datetime, horizon_min: int):
    # Start with the first full minute after the recorded scan to avoid using a
    # candle that partly predates the signal timestamp.
    start_ms = int((start + timedelta(minutes=1)).timestamp() * 1000)
    end = start + timedelta(minutes=horizon_min)
    end_ms = int(end.timestamp() * 1000)
    rows = spot_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": "1m",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": min(1000, horizon_min + 5),
        },
    )
    if not rows:
        return None
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]
    return {"high": max(highs), "low": min(lows), "close": closes[-1], "bars": len(rows)}


def directional_metrics(direction: str, entry: float, risk_pct: float, path: dict):
    close = float(path["close"])
    hi = float(path["high"])
    lo = float(path["low"])
    if direction == "SHORT":
        gross = (entry / close - 1.0) * 100.0 if close else 0.0
        mfe = (entry / lo - 1.0) * 100.0 if lo else 0.0
        mae = (entry / hi - 1.0) * 100.0 if hi else 0.0
    else:
        gross = (close / entry - 1.0) * 100.0 if entry else 0.0
        mfe = (hi / entry - 1.0) * 100.0 if entry else 0.0
        mae = (lo / entry - 1.0) * 100.0 if entry else 0.0

    round_trip_cost = 2.0 * (FEE_BPS_PER_SIDE + SLIPPAGE_BPS_PER_SIDE) / 100.0
    net = gross - round_trip_cost
    r = net / risk_pct if risk_pct and risk_pct > 0 else None
    mfe_r = mfe / risk_pct if risk_pct and risk_pct > 0 else None
    mae_r = mae / risk_pct if risk_pct and risk_pct > 0 else None
    return gross, net, r, mfe, mae, mfe_r, mae_r


def random_direction(scan_time: str, symbol: str) -> str:
    # Deterministic so reruns never change the control assignment.
    h = hashlib.sha256((scan_time + "|" + symbol).encode("utf-8")).digest()
    return "LONG" if (h[0] & 1) == 0 else "SHORT"


def episode_id(scan_time: str, direction: str, regime: str | None) -> str:
    dt=parse_iso(scan_time)
    bucket=int(dt.timestamp()//1800)
    return f"{bucket}|{regime or 'UNKNOWN'}|{direction}"


def init_db(con: sqlite3.Connection):
    con.execute(
        """CREATE TABLE IF NOT EXISTS universe_observations(
            scan_time_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quote_volume REAL,
            day_change_pct REAL,
            prefilter_rank REAL,
            shortlisted INTEGER NOT NULL,
            direction_hint TEXT NOT NULL,
            reference_price REAL NOT NULL,
            risk_pct REAL NOT NULL,
            t5_structure INTEGER,
            t15_structure INTEGER,
            t15_change_4 REAL,
            t15_vol_mult REAL,
            t15_atr_pct REAL,
            breakout20 INTEGER,
            breakdown20 INTEGER,
            payload_json TEXT,
            PRIMARY KEY(scan_time_utc,symbol)
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS forward_validation(
            scan_time_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            horizon_min INTEGER NOT NULL,
            group_name TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            risk_pct REAL NOT NULL,
            model_status TEXT,
            model_long_score INTEGER,
            model_short_score INTEGER,
            model_eligible INTEGER,
            breakout_only_eligible INTEGER,
            gross_return_pct REAL,
            net_return_pct REAL,
            r_multiple REAL,
            mfe_pct REAL,
            mae_pct REAL,
            mfe_r REAL,
            mae_r REAL,
            random_direction TEXT,
            random_net_return_pct REAL,
            random_r_multiple REAL,
            model_direction TEXT,
            model_net_return_pct REAL,
            model_r_multiple REAL,
            breakout_direction TEXT,
            breakout_net_return_pct REAL,
            breakout_r_multiple REAL,
            ablation_r_json TEXT,
            episode_id TEXT,
            bars INTEGER,
            evaluated_at_utc TEXT NOT NULL,
            PRIMARY KEY(scan_time_utc,symbol,horizon_min)
        )"""
    )
    cols={r[1] for r in con.execute("PRAGMA table_info(forward_validation)")}
    for name,typ in {
        "model_direction":"TEXT","model_net_return_pct":"REAL","model_r_multiple":"REAL",
        "breakout_direction":"TEXT","breakout_net_return_pct":"REAL","breakout_r_multiple":"REAL",
        "ablation_r_json":"TEXT","episode_id":"TEXT",
    }.items():
        if name not in cols:
            con.execute(f"ALTER TABLE forward_validation ADD COLUMN {name} {typ}")
    con.execute(
        """CREATE TABLE IF NOT EXISTS validation_reports(
            report_time_utc TEXT PRIMARY KEY,
            horizon_min INTEGER NOT NULL,
            model_n INTEGER,
            model_expectancy_r REAL,
            breakout_n INTEGER,
            breakout_expectancy_r REAL,
            control_n INTEGER,
            control_expectancy_r REAL,
            random_n INTEGER,
            random_expectancy_r REAL,
            payload_json TEXT
        )"""
    )


def lookup_analysis(con: sqlite3.Connection, scan_time: str, symbol: str):
    row = con.execute(
        """SELECT status,long_score,short_score,payload_json
           FROM analyses WHERE scan_time_utc=? AND symbol=?
           ORDER BY id DESC LIMIT 1""",
        (scan_time, symbol),
    ).fetchone()
    if not row:
        return None
    payload = {}
    try:
        payload = json.loads(row[3] or "{}")
    except Exception:
        pass
    plan = payload.get("setup_plan") or {}
    return {
        "status": row[0],
        "long_score": row[1],
        "short_score": row[2],
        "breakout_only": bool(plan.get("triggered")),
        "setup_direction": plan.get("direction"),
        "ablations": payload.get("ablations") or {},
        "market_regime": payload.get("market_regime"),
    }


def evaluate_due(con: sqlite3.Connection):
    rows = con.execute(
        """SELECT scan_time_utc,symbol,shortlisted,direction_hint,reference_price,risk_pct
           FROM universe_observations ORDER BY scan_time_utc,symbol"""
    ).fetchall()
    now = now_utc()
    inserted = 0
    for scan_time, symbol, shortlisted, direction, entry, risk_pct in rows:
        start = parse_iso(scan_time)
        analysis = lookup_analysis(con, scan_time, symbol)
        for horizon in HORIZONS:
            due = start + timedelta(minutes=horizon + 2)
            if now < due:
                continue
            exists = con.execute(
                """SELECT 1 FROM forward_validation
                   WHERE scan_time_utc=? AND symbol=? AND horizon_min=?""",
                (scan_time, symbol, horizon),
            ).fetchone()
            if exists:
                continue
            try:
                path = future_path(symbol, start, horizon)
                if not path:
                    continue
                gross, net, r, mfe, mae, mfe_r, mae_r = directional_metrics(
                    direction, float(entry), float(risk_pct), path
                )
                rd = random_direction(scan_time, symbol)
                _, random_net, random_r, *_ = directional_metrics(
                    rd, float(entry), float(risk_pct), path
                )
                model_status = analysis["status"] if analysis else None
                model_eligible = int(bool(analysis and model_status in ("LONG", "SHORT")))
                breakout_only = int(bool(analysis and analysis["breakout_only"]))
                model_direction=model_status if model_eligible else None
                model_net=model_r=None
                if model_direction:
                    _,model_net,model_r,*_=directional_metrics(
                        model_direction,float(entry),float(risk_pct),path
                    )
                breakout_direction=(analysis.get("setup_direction") if analysis and breakout_only else None)
                breakout_net=breakout_r=None
                if breakout_direction in ("LONG","SHORT"):
                    _,breakout_net,breakout_r,*_=directional_metrics(
                        breakout_direction,float(entry),float(risk_pct),path
                    )
                ablation_rs={}
                if analysis:
                    for name,meta in (analysis.get("ablations") or {}).items():
                        decision=(meta or {}).get("decision")
                        if decision in ("LONG","SHORT"):
                            _,an,ar,*_=directional_metrics(
                                decision,float(entry),float(risk_pct),path
                            )
                            ablation_rs[name]={"direction":decision,"net_return_pct":an,"r_multiple":ar}
                eid=episode_id(
                    scan_time,
                    model_direction or direction,
                    analysis.get("market_regime") if analysis else None,
                )
                group = "SHORTLIST" if shortlisted else "CONTROL_NOT_SHORTLISTED"
                con.execute(
                    """INSERT INTO forward_validation(
                        scan_time_utc,symbol,horizon_min,group_name,direction,
                        entry_price,risk_pct,model_status,model_long_score,
                        model_short_score,model_eligible,breakout_only_eligible,
                        gross_return_pct,net_return_pct,r_multiple,mfe_pct,mae_pct,
                        mfe_r,mae_r,random_direction,random_net_return_pct,
                        random_r_multiple,model_direction,model_net_return_pct,
                        model_r_multiple,breakout_direction,breakout_net_return_pct,
                        breakout_r_multiple,ablation_r_json,episode_id,bars,evaluated_at_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        scan_time, symbol, horizon, group, direction, float(entry),
                        float(risk_pct), model_status,
                        analysis["long_score"] if analysis else None,
                        analysis["short_score"] if analysis else None,
                        model_eligible, breakout_only, gross, net, r, mfe, mae,
                        mfe_r, mae_r, rd, random_net, random_r,
                        model_direction,model_net,model_r,
                        breakout_direction,breakout_net,breakout_r,
                        json.dumps(ablation_rs,ensure_ascii=False,separators=(",",":")),
                        eid,int(path["bars"]),now_utc().isoformat(),
                    ),
                )
                inserted += 1
            except Exception as exc:
                print("validation error", symbol, horizon, type(exc).__name__, str(exc)[:120])
    return inserted


def _mean(xs):
    vals = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.fmean(vals) if vals else None


def report(con: sqlite3.Connection):
    reports = []
    for horizon in HORIZONS:
        rows = con.execute(
            """SELECT group_name,model_eligible,breakout_only_eligible,
                      r_multiple,random_r_multiple,model_r_multiple,
                      breakout_r_multiple,ablation_r_json,episode_id
               FROM forward_validation WHERE horizon_min=?""",
            (horizon,),
        ).fetchall()
        model = [r[5] for r in rows if r[1] and r[5] is not None]
        breakout = [r[6] for r in rows if r[2] and r[6] is not None]
        control = [r[3] for r in rows if r[0] == "CONTROL_NOT_SHORTLISTED" and r[3] is not None]
        randoms = [r[4] for r in rows if r[4] is not None]
        episode_model={}
        for row in rows:
            if row[1] and row[5] is not None:
                episode_model.setdefault(row[8] or "UNASSIGNED",[]).append(float(row[5]))
        episode_means=[_mean(v) for v in episode_model.values() if v]
        ablations={}
        for row in rows:
            try:
                obj=json.loads(row[7] or "{}")
            except Exception:
                obj={}
            for name,meta in obj.items():
                rv=(meta or {}).get("r_multiple")
                if rv is not None:
                    ablations.setdefault(name,[]).append(float(rv))
        payload = {
            "horizon_min": horizon,
            "model": {
                "n": len(model),
                "effective_episodes":len(episode_model),
                "expectancy_r": _mean(model),
                "episode_expectancy_r":_mean(episode_means),
            },
            "breakout_only": {"n": len(breakout), "expectancy_r": _mean(breakout)},
            "control_not_shortlisted": {"n": len(control), "expectancy_r": _mean(control)},
            "random_direction": {"n": len(randoms), "expectancy_r": _mean(randoms)},
            "ablations":{k:{"n":len(v),"expectancy_r":_mean(v)} for k,v in ablations.items()},
        }
        con.execute(
            """INSERT OR REPLACE INTO validation_reports(
                report_time_utc,horizon_min,model_n,model_expectancy_r,
                breakout_n,breakout_expectancy_r,control_n,control_expectancy_r,
                random_n,random_expectancy_r,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now_utc().isoformat() + f"|{horizon}",
                horizon,
                len(model), _mean(model),
                len(breakout), _mean(breakout),
                len(control), _mean(control),
                len(randoms), _mean(randoms),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        reports.append(payload)
    return reports


def fmt(x):
    return "-" if x is None else f"{x:+.3f}R"


def main():
    if not os.path.exists(DB):
        print("validation: analyst DB missing")
        return
    with sqlite3.connect(DB) as con:
        init_db(con)
        n = evaluate_due(con)
        reps = report(con)
        con.commit()
    print(f"FORWARD_VALIDATION inserted={n}")
    for r in reps:
        print(
            f"{r['horizon_min']}m | "
            f"model n={r['model']['n']} exp={fmt(r['model']['expectancy_r'])} | "
            f"breakout n={r['breakout_only']['n']} exp={fmt(r['breakout_only']['expectancy_r'])} | "
            f"control n={r['control_not_shortlisted']['n']} exp={fmt(r['control_not_shortlisted']['expectancy_r'])} | "
            f"random n={r['random_direction']['n']} exp={fmt(r['random_direction']['expectancy_r'])}"
        )


if __name__ == "__main__":
    main()
