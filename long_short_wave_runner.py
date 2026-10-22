"""Opt-in one-shot wave lab over an existing Binance candle SQLite database.

Source databases are opened mode=ro. Writes go to a separate research database.
Never imported by the daemon; no scheduling, HTTP, Telegram or order capability.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from long_short_wave_lab import (CONFIG_HASH, MINUTE, RULES, SOURCES, TERMINAL,
                                 VERSION, paper_outcome, prepare, proposals,
                                 render_watch, transition)


def pack(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False)


def readonly(path):
    c = sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    return c


def schema(c):
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("futures_closed_klines", "closed_klines"):
        if table in names:
            return table
    raise ValueError("No supported Binance closed candle table")


def bars(c, table, symbol, interval, end, limit=None, start=0, available_by=None):
    if table not in ("futures_closed_klines", "closed_klines"):
        raise ValueError("Unsupported source table")
    query = f"SELECT * FROM {table} WHERE symbol=? AND interval=? AND close_ms<=? AND open_ms>=?"
    params = [symbol, interval, end, start]
    if available_by is not None:
        query += " AND received_ms<=?"
        params.append(available_by)
    query += " ORDER BY open_ms DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = [dict(r) for r in c.execute(query, params)][::-1]
    return rows


def initialize(out):
    out.row_factory = sqlite3.Row
    out.execute("PRAGMA journal_mode=WAL")
    out.execute("PRAGMA busy_timeout=20000")
    out.executescript("""
    CREATE TABLE IF NOT EXISTS wave_config(
      hash TEXT PRIMARY KEY, version TEXT NOT NULL, rules_json TEXT NOT NULL,
      first_git_commit TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wave_scans(
      scan_id TEXT PRIMARY KEY, observed_ms INTEGER NOT NULL, asof_ms INTEGER NOT NULL,
      mode TEXT NOT NULL, config_hash TEXT NOT NULL, git_commit TEXT NOT NULL,
      scope TEXT NOT NULL, symbols INTEGER NOT NULL, healthy INTEGER NOT NULL,
      new_events INTEGER NOT NULL, errors_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wave_snapshots(
      scan_id TEXT NOT NULL, symbol TEXT NOT NULL, health TEXT NOT NULL,
      payload_json TEXT NOT NULL, PRIMARY KEY(scan_id,symbol));
    CREATE TABLE IF NOT EXISTS wave_raw_bars(
      fingerprint TEXT PRIMARY KEY, symbol TEXT NOT NULL, interval TEXT NOT NULL,
      open_ms INTEGER NOT NULL, source TEXT NOT NULL, payload_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wave_events(
      event_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, scan_id TEXT NOT NULL,
      symbol TEXT NOT NULL, strategy TEXT NOT NULL, direction TEXT NOT NULL,
      cohort TEXT NOT NULL, mode TEXT NOT NULL, config_hash TEXT NOT NULL,
      signal_ms INTEGER NOT NULL, payload_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS wave_event_lookup ON wave_events
      (symbol,strategy,direction,mode,config_hash,signal_ms);
    CREATE TABLE IF NOT EXISTS wave_states(
      id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, observed_ms INTEGER NOT NULL,
      last_bar_ms INTEGER NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wave_labels(
      id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, horizon_min INTEGER NOT NULL,
      observed_ms INTEGER NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS wave_label_lookup ON wave_labels(event_id,horizon_min,id);
    """)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--",
            "long_short_wave_lab.py", "long_short_wave_runner.py"],
            cwd=Path(__file__).parent, text=True).strip()
        if dirty:
            commit += "+DIRTY"
    except (OSError, subprocess.CalledProcessError):
        commit = "UNKNOWN"
    out.execute("INSERT OR IGNORE INTO wave_config VALUES(?,?,?,?)",
                (CONFIG_HASH, VERSION, pack(asdict(RULES)), commit))
    out.commit()
    return commit


def archive(out, symbol, interval, rows):
    fingerprints = []
    for row in rows:
        try:
            body = pack(row)
        except ValueError:
            body = pack({"invalid_raw_repr": repr(row)})
        fingerprint = hashlib.sha256((symbol+interval+body).encode()).hexdigest()
        out.execute("INSERT OR IGNORE INTO wave_raw_bars VALUES(?,?,?,?,?,?)",
                    (fingerprint, symbol, interval, row["open_ms"], row["source"], body))
        fingerprints.append(fingerprint)
    return hashlib.sha256(pack(fingerprints).encode()).hexdigest()


def new_event(out, scan_id, p, now, asof, mode, cohort="WATCH", parent=None):
    eid = hashlib.sha256(pack([CONFIG_HASH, mode, p["symbol"], p["strategy"],
                              p["direction"], cohort, now]).encode()).hexdigest()
    payload = {**p, "signal_ms": now, "signal_bar_ms": asof,
               "cohort": cohort, "execution_authorized": False}
    if cohort == "WATCH":
        payload["message_preview"] = render_watch(payload)
    out.execute("INSERT INTO wave_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (eid, parent or eid, scan_id, p["symbol"], p["strategy"],
                 p["direction"], cohort, mode, CONFIG_HASH, now, pack(payload)))
    out.execute("INSERT INTO wave_states(event_id,observed_ms,last_bar_ms,state,reason) VALUES(?,?,?,?,?)",
                (eid, now, asof, cohort, "INITIAL_OBSERVATION"))
    return eid


def scan(source, out, now, commit, universe=None, replay=False):
    table = schema(source)
    asof = (now//(5*MINUTE))*(5*MINUTE)-1
    mode = "PRICE_REPLAY" if replay else "FORWARD"
    scan_id = hashlib.sha256(pack([CONFIG_HASH, mode, now]).encode()).hexdigest()
    if out.execute("SELECT 1 FROM wave_scans WHERE scan_id=?", (scan_id,)).fetchone():
        return {"status": "ALREADY_RECORDED", "scan_id": scan_id}
    # One read transaction ensures all symbols see the same source snapshot.
    source.execute("BEGIN")
    try:
        symbols = [r[0] for r in source.execute(f"SELECT DISTINCT symbol FROM {table} WHERE interval='5m'")]
        scope, universe_errors = "CANDLE_DB_ONLY_NOT_FULL_EXCHANGE", {}
        if universe is not None:
            tickers = universe.execute("SELECT symbol,source,received_ms FROM futures_tickers").fetchall()
            symbols = [r[0] for r in tickers]
            scope = "ALL_SYMBOLS_IN_UNIVERSE_DB"
            for r in tickers:
                if r[1] not in SOURCES or not 0 <= now-r[2] <= 120_000:
                    universe_errors[r[0]] = "STALE_OR_WRONG_UNIVERSE_SOURCE"
        symbols = sorted(set(symbols))
        out.execute("BEGIN IMMEDIATE")
        healthy = new = 0
        errors = Counter()
        for symbol in symbols:
            rows = bars(source, table, symbol, "5m", asof, RULES.history_bars,
                        available_by=None if replay else now)
            raw_hash = archive(out, symbol, "5m", rows)
            feat = prepare(rows, asof, now)
            if symbol in universe_errors:
                feat = {"health": "INVALID", "reason": universe_errors[symbol]}
            candidates = proposals(symbol, feat)
            summary = {k: v for k, v in feat.items() if k != "timeframes"}
            summary.update({"proposals": candidates, "raw_window_hash": raw_hash,
                            "input_bars": len(rows), "tradability_verified": False,
                            "derivatives": "NOT_CONNECTED", "sector": "UNKNOWN"})
            out.execute("INSERT INTO wave_snapshots VALUES(?,?,?,?)",
                        (scan_id, symbol, feat["health"], pack(summary)))
            if feat["health"] != "OK":
                errors[feat["reason"]] += 1
                continue
            healthy += 1
            # Existing episodes keep their original trigger/stop/target.
            existing = out.execute("""SELECT e.*,s.last_bar_ms,s.state FROM wave_events e
                JOIN wave_states s ON s.id=(SELECT MAX(id) FROM wave_states WHERE event_id=e.event_id)
                WHERE e.symbol=? AND e.mode=? AND e.config_hash=? AND e.cohort='WATCH'""",
                (symbol, mode, CONFIG_HASH)).fetchall()
            for old in existing:
                if old["state"] in TERMINAL or old["last_bar_ms"] >= asof:
                    continue
                event = {**json.loads(old["payload_json"]), "state": old["state"],
                         "last_bar_ms": old["last_bar_ms"]}
                path = [b for b in rows if b["open_ms"] > old["last_bar_ms"]]
                state, reason = transition(event, path, now, feat["volume_ratio"])
                if reason == "PATH_UNAVAILABLE":
                    errors[reason] += 1
                    # Preserve last observed bar; never silently skip a gap.
                    last_bar = old["last_bar_ms"]
                else:
                    last_bar = asof
                out.execute("INSERT INTO wave_states(event_id,observed_ms,last_bar_ms,state,reason) VALUES(?,?,?,?,?)",
                            (old["event_id"], now, last_bar, state, reason))
                if state == "CONFIRMED" and old["state"] != "CONFIRMED":
                    sign = 1 if event["direction"] == "LONG" else -1
                    price = rows[-1]["close"]
                    confirmation = {**event, "signal_price": price,
                                    "watch_signal_ms": event["signal_ms"],
                                    "watch_signal_price": event["signal_price"],
                                    "gain_before_confirmation_pct": sign*(price/event["signal_price"]-1)*100,
                                    "time_to_confirmation_ms": now-event["signal_ms"]}
                    confirmation.pop("message_preview", None)
                    new_event(out, scan_id, confirmation, now, asof, mode, "CONFIRMED", old["episode_id"])
                    new += 1
            for p in candidates:
                if not p["qualified_watch"]:
                    continue
                previous = out.execute("""SELECT 1 FROM wave_events WHERE symbol=? AND strategy=?
                    AND direction=? AND mode=? AND config_hash=? AND cohort='WATCH'
                    AND signal_ms>? LIMIT 1""",
                    (symbol, p["strategy"], p["direction"], mode, CONFIG_HASH,
                     now-RULES.cooldown_hours*3_600_000)).fetchone()
                if previous:
                    continue
                new_event(out, scan_id, p, now, asof, mode)
                new += 1
        if not symbols:
            errors["EMPTY_UNIVERSE"] += 1
        out.execute("INSERT INTO wave_scans VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (scan_id, now, asof, mode, CONFIG_HASH, commit, scope,
                     len(symbols), healthy, new, pack(dict(errors))))
        out.commit()
        return {"status": "RECORDED", "scan_id": scan_id, "scope": scope,
                "symbols": len(symbols), "healthy": healthy, "new_events": new,
                "errors": dict(errors)}
    except Exception:
        out.rollback()
        raise
    finally:
        source.rollback()


def label(source, out, now):
    table = schema(source)
    count = 0
    for row in out.execute("SELECT * FROM wave_events WHERE config_hash=?", (CONFIG_HASH,)).fetchall():
        event = json.loads(row["payload_json"])
        for horizon in RULES.horizons_minutes:
            old = out.execute("SELECT status,payload_json FROM wave_labels WHERE event_id=? AND horizon_min=? ORDER BY id DESC LIMIT 1",
                              (row["event_id"], horizon)).fetchone()
            if old and old["status"] in ("LABELED", "OBSERVATION_ONLY"):
                continue
            due = ((event["signal_ms"]+RULES.entry_delay_ms+MINUTE-1)//MINUTE)*MINUTE
            end = due+horizon*MINUTE-1
            if now <= end:
                continue
            path = bars(source, table, row["symbol"], "1m", end, start=due,
                        available_by=None if row["mode"] == "PRICE_REPLAY" else now)
            result = paper_outcome(event, path, horizon, now)
            result["raw_window_hash"] = archive(out, row["symbol"], "1m", path)
            body = pack(result)
            if old and old["payload_json"] == body:
                continue
            out.execute("INSERT INTO wave_labels(event_id,horizon_min,observed_ms,status,payload_json) VALUES(?,?,?,?,?)",
                        (row["event_id"], horizon, now, result["status"], body))
            count += 1
    out.commit()
    return count


def report(out):
    scans = out.execute("SELECT * FROM wave_scans ORDER BY observed_ms DESC LIMIT 1").fetchone()
    groups = {}
    for row in out.execute("""SELECT e.strategy,e.direction,e.cohort,e.mode,e.config_hash,l.*
       FROM wave_labels l JOIN wave_events e ON e.event_id=l.event_id
       WHERE l.id=(SELECT MAX(x.id) FROM wave_labels x
         WHERE x.event_id=l.event_id AND x.horizon_min=l.horizon_min)"""):
        key = "|".join(str(row[k]) for k in ("config_hash", "mode", "strategy", "direction", "cohort", "horizon_min"))
        g = groups.setdefault(key, {"count": 0, "data_failures": 0, "observation_only": 0,
                                   "labeled": 0, "targets": 0, "stops": 0, "timeouts": 0,
                                   "modeled_sum_excluding_funding_pct": 0, "net_known": 0})
        g["count"] += 1
        if row["status"] == "DATA_FAILURE":
            g["data_failures"] += 1
        elif row["status"] == "OBSERVATION_ONLY":
            g["observation_only"] += 1
        elif row["status"] == "LABELED":
            p = json.loads(row["payload_json"])
            g["labeled"] += 1
            g[{"TARGET": "targets", "STOP": "stops", "TIMEOUT": "timeouts"}[p["first_barrier"]]] += 1
            g["modeled_sum_excluding_funding_pct"] += p["modeled_net_excluding_funding_pct"]
            g["net_known"] += int(p["net_pct"] is not None)
    for g in groups.values():
        g["modeled_mean_excluding_funding_pct"] = g.pop("modeled_sum_excluding_funding_pct")/g["labeled"] if g["labeled"] else None
        # This only counts target hits; it is deliberately not called win rate.
        g["target_rate_resolved"] = g["targets"]/g["labeled"] if g["labeled"] else None
        den = g["labeled"]+g["data_failures"]
        g["target_rate_missing_as_failure"] = g["targets"]/den if den else None
    return {"version": VERSION, "config_hash": CONFIG_HASH,
            "latest_scan": dict(scans) if scans else None, "groups": groups,
            "events_total": out.execute("SELECT COUNT(*) FROM wave_events").fetchone()[0],
            "edge_proven": False, "live_execution": False, "telegram_delivery": False,
            "limitations": ["Provisional research rules; no performance claim",
                            "Funding unknown: net_pct/net_r remain null",
                            "Fixed-cost candle simulation; no live depth/size verification",
                            "No matched controls or sector benchmark yet",
                            "Candle-only scope can be smaller than Binance universe"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db")
    parser.add_argument("--research-db", required=True)
    parser.add_argument("--universe-db")
    parser.add_argument("--action", choices=("scan", "label", "report", "all"), default="all")
    parser.add_argument("--replay-ms", type=int, help="Explicit historical candle replay, never forward evidence")
    args = parser.parse_args()
    if args.action != "report" and not args.source_db:
        parser.error("--source-db is required for scan/label/all")
    output = Path(args.research_db).resolve()
    for name in (args.source_db, args.universe_db):
        if name and (Path(name).resolve() == output or (output.exists() and Path(name).samefile(output))):
            parser.error("Research database must be separate from every source database")
    now = args.replay_ms if args.replay_ms is not None else int(time.time()*1000)
    with sqlite3.connect(output, timeout=20) as out:
        commit = initialize(out)
        source = readonly(args.source_db) if args.source_db else None
        universe = readonly(args.universe_db) if args.universe_db else None
        try:
            if args.action in ("scan", "all"):
                scan(source, out, now, commit, universe, args.replay_ms is not None)
            if args.action in ("label", "all"):
                label(source, out, now)
            print(pack(report(out)))
        finally:
            if source is not None:
                source.close()
            if universe is not None:
                universe.close()


if __name__ == "__main__":
    main()
