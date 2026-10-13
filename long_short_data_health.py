#!/usr/bin/env python3
"""Read-only data & signal health report for the Binance LONG/SHORT motor.

Answers, with numbers: which scanned coins have complete native Binance UM
Futures candles, why the others fail, how long until they can pass, and what
the delivered signals did afterwards. No writes to engine DBs, no Telegram,
no network, no orders.
"""
import argparse
import json
import os
import sqlite3
import time

PERIOD_MS = {"1m": 60000, "5m": 300000, "15m": 900000, "30m": 1800000,
             "1h": 3600000, "4h": 14400000}
# Bars the analyst actually requests (prefilter 5m/15m x120; deep 1m x120,
# 30m x160, 1h/4h x220). Mirrors the code; not a new rule.
REQUIRED = {"1m": 120, "5m": 120, "15m": 120, "30m": 160, "1h": 220, "4h": 220}
VALID = {"BINANCE_UM_FUTURES_WEBSOCKET", "BINANCE_UM_FUTURES_ARCHIVE", "BINANCE_UM_FUTURES_REST"}


def ro(path):
    return sqlite3.connect("file:" + os.path.abspath(path) + "?mode=ro", uri=True, timeout=5)


def interval_status(con, symbol, interval, now_ms, required=None):
    required = required or REQUIRED[interval]
    period = PERIOD_MS[interval]
    rows = con.execute("""SELECT open_ms,close_ms,source,quote_volume,trade_count,taker_buy_quote
        FROM futures_closed_klines WHERE symbol=? AND interval=?
        ORDER BY open_ms DESC LIMIT ?""", (symbol, interval, required)).fetchall()
    rows.reverse()
    out = {"bars": len(rows), "required": required}
    if not rows:
        out.update(ready=False, reason="no_data", eta_minutes=None)
        return out
    age = now_ms - rows[-1][1]
    out["last_close_age_s"] = round(age / 1000)
    tail = 1
    gap = None
    for a, b in zip(reversed(rows[:-1]), reversed(rows[1:])):
        if b[0] - a[0] != period:
            gap = {"first_missing_open_ms": a[0] + period, "missing_bars": (b[0] - a[0]) // period - 1}
            break
        tail += 1
    out["contiguous_tail_bars"] = tail
    null_rows = sum(any(v is None for v in r[3:6]) for r in rows)
    bad_rows = sum(r[2] not in VALID for r in rows)
    out["sources"] = sorted({r[2] for r in rows})
    if gap:
        out.update(latest_gap=gap)
    # Same order as long_short_analyst.native_futures_shadow_klines().
    if len(rows) < required:
        reason = "short_history"
    elif age > 2 * period or age < 0:
        reason = "stale"
    elif bad_rows or null_rows:
        reason = "invalid_source_or_fields"
        out.update(null_field_rows=null_rows, bad_source_rows=bad_rows)
    elif gap:
        reason = "gap"
    else:
        reason = "ok"
    out["reason"] = reason
    out["ready"] = reason == "ok"
    out["eta_minutes"] = 0 if out["ready"] else (
        (required - tail) * period // 60000 if reason in ("gap", "short_history") else None)
    return out


def scan_window(analyst_db):
    if not os.path.isfile(analyst_db):
        return None, [], {}
    try:
        with ro(analyst_db) as con:
            latest = con.execute("SELECT MAX(scan_time_utc) FROM full_universe_stage_audit").fetchone()[0]
            if not latest:
                return None, [], {}
            rows = con.execute("""SELECT symbol,stage FROM full_universe_stage_audit
                WHERE scan_time_utc=? ORDER BY quote_volume DESC""", (latest,)).fetchall()
    except sqlite3.Error:
        return None, [], {}
    stages = {}
    for _, stage in rows:
        stages[stage] = stages.get(stage, 0) + 1
    return latest, [r[0] for r in rows], stages


def signal_summary(live_db, now_s):
    out = {"available": False}
    if not os.path.isfile(live_db):
        return out
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now_s - 86400))
    try:
        with ro(live_db) as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "events" not in tables:
                return out
            transitions = {}
            for stage, status, payload in con.execute("""SELECT stage_to,COALESCE(telegram_status,''),payload_json
                    FROM events WHERE event_time_utc>=?""", (since,)):
                try:
                    venue = (json.loads(payload or "{}") or {}).get("_live_price_source") or "UNKNOWN"
                except ValueError:
                    venue = "UNKNOWN"
                key = f"{stage}|{status or 'NONE'}|{venue}"
                transitions[key] = transitions.get(key, 0) + 1
            out = {"available": True, "window_hours": 24,
                   "transitions_stage|telegram|price_source": transitions}
            if 'confirmed_outcomes_v2' in tables:
                values=[json.loads(r[0]) for r in con.execute('SELECT payload FROM confirmed_outcomes_v2')]
                out['v2_outcomes']={'completed':len(values),'by_result':{
                    k:sum(v['result']==k for v in values) for k in ('TP1','STOP','TIMEOUT')},
                    'mean_net_pct':sum(v['net_return_pct'] for v in values)/len(values) if values else None}
                out['v2_pending']=con.execute("""SELECT COUNT(*) FROM events e LEFT JOIN confirmed_outcomes_v2 o
                    ON o.event_id=e.id WHERE e.stage_to='TRIGGERED' AND e.telegram_status='SENT' AND o.event_id IS NULL""").fetchone()[0]
            if "confirmed_trade_outcomes" in tables:
                by_venue = {}
                for venue, result, n, net in con.execute("""SELECT venue,result,COUNT(*),AVG(net_return_pct)
                        FROM confirmed_trade_outcomes GROUP BY venue,result"""):
                    by_venue.setdefault(venue or "UNKNOWN", {})[result] = {"n": n, "avg_net_pct": round(net or 0, 4)}
                pending = con.execute("""SELECT COUNT(*) FROM events e LEFT JOIN confirmed_trade_outcomes o
                        ON o.event_id=e.id WHERE e.stage_to='TRIGGERED' AND e.telegram_status='SENT'
                        AND o.event_id IS NULL""").fetchone()[0]
                out["confirmed_outcomes_by_venue"] = by_venue  # Spot-era and Futures cohorts kept apart
                out["confirmed_pending"] = pending
    except sqlite3.Error as exc:
        out = {"available": False, "error": type(exc).__name__}
    return out


def build(state_dir, now_ms=None, symbols=None):
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    kline_db = os.path.join(state_dir, "binance_futures_kline_shadow.db")
    analyst_db = os.getenv("LS_ANALYST_DB") or os.path.join(state_dir, "long_short_analyst.db")
    live_db = os.getenv("LS_LIVE_DB") or os.path.join(state_dir, "long_short_live_pool.db")
    scan_time, window, stages = scan_window(analyst_db)
    symbols = list(symbols or window)
    report = {"time_ms": now_ms, "scan_time_utc": scan_time, "scan_window": len(window),
              "scan_stage_counts": stages, "symbols_checked": len(symbols)}
    per_symbol = {}
    reasons = {}
    ready_by_interval = {iv: 0 for iv in PERIOD_MS}
    receiving_live_5m = 0
    if os.path.isfile(kline_db):
        with ro(kline_db) as con:
            for sym in symbols:
                st = {iv: interval_status(con, sym, iv, now_ms) for iv in PERIOD_MS}
                per_symbol[sym] = st
                if st["5m"].get("last_close_age_s") is not None and st["5m"]["last_close_age_s"] <= 600:
                    receiving_live_5m += 1
                for iv, s in st.items():
                    ready_by_interval[iv] += int(s["ready"])
                    if not s["ready"]:
                        key = f"{iv}:{s['reason']}"
                        reasons[key] = reasons.get(key, 0) + 1
            try:
                lp = con.execute("SELECT COUNT(*) FROM futures_last_price WHERE event_ms>=?",
                                 (now_ms - 10000,)).fetchone()[0]
            except sqlite3.Error:
                lp = None
        report["kline_db"] = "present"
        report["fresh_last_price_symbols"] = lp
    else:
        report["kline_db"] = "missing"
    prefilter_ready = [s for s, st in per_symbol.items() if st["5m"]["ready"] and st["15m"]["ready"]]
    deep_ready = [s for s in prefilter_ready if all(per_symbol[s][iv]["ready"] for iv in ("1m", "30m", "1h", "4h"))]
    etas = sorted((max((v["eta_minutes"] or 0) for v in st.values() if not v["ready"]), s)
                  for s, st in per_symbol.items() if s not in deep_ready
                  and all(v["eta_minutes"] is not None for v in st.values() if not v["ready"]))
    report.update({
        "receiving_live_5m": receiving_live_5m,
        "ready_by_interval": ready_by_interval,
        "prefilter_data_ready": len(prefilter_ready),
        "deep_data_ready": len(deep_ready),
        "deep_ready_symbols": deep_ready[:30],
        "not_ready_reasons": reasons,
        "next_ready_eta_minutes": [{"symbol": s, "eta_minutes": m} for m, s in etas[:10]],
        "signals": signal_summary(live_db, now_ms // 1000),
    })
    report["per_symbol"] = {s: {iv: {k: v for k, v in x.items() if k in
                                     ("ready", "reason", "bars", "contiguous_tail_bars", "eta_minutes",
                                      "latest_gap", "last_close_age_s", "null_field_rows")}
                                for iv, x in st.items()} for s, st in per_symbol.items()}
    return report


def summary_line(report):
    keys = ("scan_time_utc", "scan_window", "symbols_checked", "receiving_live_5m",
            "prefilter_data_ready", "deep_data_ready", "ready_by_interval", "not_ready_reasons",
            "next_ready_eta_minutes", "fresh_last_price_symbols", "scan_stage_counts")
    return {k: report.get(k) for k in keys}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--full", action="store_true", help="print per-symbol detail")
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    report = build(args.state_dir, symbols=symbols)
    print(json.dumps(report if args.full else summary_line(report), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
