#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Survivorship-bias backfill for Gate History Miner.

This layer is deliberately separate from frozen V4/V5 rules.

What it does:
- builds a registry of historical/non-tradable Gate USDT spot pairs from
  (a) Gate's current currency/pair API, including untradable/delisted flags, and
  (b) pairs previously observed in avci2.db but no longer in the current tradable set
- downloads official Gate monthly spot 1d candlestick archives for those known pairs
- writes recovered bars into the existing History Miner raw archive
- applies the existing broad event/control labelling without changing its thresholds
- records explicit coverage so "known historical pairs" is never confused with the
  complete historical Gate universe

Research-only. Never trades and never changes live Avci candidate rules.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from urllib import error, request

import history_event_miner as hem

DB = os.getenv("HISTORY_DB", "history_miner.db")
SOURCE_DB = os.getenv("AVCI_DB", "avci2.db")
GATE_SPOT = "https://api.gateio.ws/api/v4/spot"
ARCHIVE = "https://download.gatedata.org/spot/candlesticks_1d"
LOOKBACK_DAYS = int(os.getenv("HISTORY_LOOKBACK_DAYS", "730"))
PAIR_BUDGET = int(os.getenv("HISTORY_DELIST_PAIR_BUDGET", "6"))
SLEEP = float(os.getenv("HISTORY_DELIST_SLEEP", "0.20"))
VERSION = "history-survivorship-backfill-v0.1-20260925"

STABLES = hem.STABLES


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def con():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory = sqlite3.Row
    return c


def init_db():
    hem.init_db()
    with con() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS historical_pair_registry(
          pair TEXT PRIMARY KEY,
          symbol TEXT NOT NULL,
          quote TEXT NOT NULL,
          first_source TEXT NOT NULL,
          current_trade_status TEXT,
          currency_delisted INTEGER,
          trade_disabled INTEGER,
          first_seen_utc TEXT NOT NULL,
          last_seen_utc TEXT NOT NULL,
          coverage_status TEXT NOT NULL DEFAULT 'QUEUED',
          attempts INTEGER NOT NULL DEFAULT 0,
          recovered_bars INTEGER NOT NULL DEFAULT 0,
          first_bar_ts INTEGER,
          last_bar_ts INTEGER,
          last_error TEXT,
          version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS survivorship_runs(
          run_id TEXT PRIMARY KEY,
          started_utc TEXT NOT NULL,
          finished_utc TEXT,
          registry_pairs INTEGER NOT NULL DEFAULT 0,
          queued_pairs INTEGER NOT NULL DEFAULT 0,
          attempted_pairs INTEGER NOT NULL DEFAULT 0,
          recovered_pairs INTEGER NOT NULL DEFAULT 0,
          no_archive_pairs INTEGER NOT NULL DEFAULT 0,
          error_pairs INTEGER NOT NULL DEFAULT 0,
          recovered_bars INTEGER NOT NULL DEFAULT 0,
          events INTEGER NOT NULL DEFAULT 0,
          controls INTEGER NOT NULL DEFAULT 0,
          notes TEXT,
          version TEXT NOT NULL
        );
        """)


def fetch_json(path):
    req = request.Request(
        GATE_SPOT + path,
        headers={"Accept": "application/json", "User-Agent": "avci-history-research/1.0"},
    )
    with request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if not isinstance(data, list):
        raise ValueError("invalid_gate_payload:" + path)
    return data


def _upsert_registry(pair, symbol, source, trade_status=None, delisted=None, disabled=None):
    if not pair or not symbol or symbol.upper() in STABLES:
        return 0
    now = utcnow()
    with con() as c:
        before = c.total_changes
        c.execute("""
        INSERT INTO historical_pair_registry(
          pair,symbol,quote,first_source,current_trade_status,currency_delisted,
          trade_disabled,first_seen_utc,last_seen_utc,coverage_status,version
        ) VALUES(?,?,?,?,?,?,?,?,?,'QUEUED',?)
        ON CONFLICT(pair) DO UPDATE SET
          symbol=excluded.symbol,
          current_trade_status=COALESCE(excluded.current_trade_status,historical_pair_registry.current_trade_status),
          currency_delisted=COALESCE(excluded.currency_delisted,historical_pair_registry.currency_delisted),
          trade_disabled=COALESCE(excluded.trade_disabled,historical_pair_registry.trade_disabled),
          last_seen_utc=excluded.last_seen_utc
        """, (
            pair, symbol.upper(), "USDT", source, trade_status,
            None if delisted is None else int(bool(delisted)),
            None if disabled is None else int(bool(disabled)),
            now, now, VERSION,
        ))
        return 1 if c.total_changes > before else 0


def seed_current_gate():
    currencies = fetch_json("/currencies")
    pairs = fetch_json("/currency_pairs")
    cur = {str(x.get("currency") or ""): x for x in currencies if isinstance(x, dict)}
    tradable = set()
    seeded = 0
    for p in pairs:
        if not isinstance(p, dict) or p.get("quote") != "USDT" or p.get("type") != "normal":
            continue
        pair = str(p.get("id") or "")
        sym = str(p.get("base") or "").upper()
        if not pair or not sym or sym in STABLES:
            continue
        ccy = cur.get(sym) or {}
        status = str(p.get("trade_status") or "UNKNOWN")
        is_delisted = ccy.get("delisted")
        disabled = ccy.get("trade_disabled")
        if status == "tradable" and is_delisted is False and disabled is False:
            tradable.add(pair)
        else:
            seeded += _upsert_registry(
                pair, sym, "gate_current_nontradable",
                status, is_delisted, disabled,
            )
    return tradable, seeded


def seed_observed_history(current_tradable):
    if not os.path.exists(SOURCE_DB):
        return 0
    seeded = 0
    try:
        src = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "gate_spot_history" not in tables:
            src.close()
            return 0
        rows = src.execute("SELECT pair,MAX(symbol) symbol FROM gate_spot_history GROUP BY pair").fetchall()
        for r in rows:
            pair = str(r["pair"] or "")
            if pair in current_tradable:
                continue
            sym = str(r["symbol"] or "").upper()
            seeded += _upsert_registry(pair, sym, "avci_observed_then_missing", "MISSING_NOW")
        src.close()
    except sqlite3.Error:
        return seeded
    return seeded


def archive_months(days):
    now = datetime.now(timezone.utc)
    start_ts = int(now.timestamp()) - days * 86400
    a = datetime.fromtimestamp(start_ts, timezone.utc)
    out = []
    y, m = a.year, a.month
    while (y, m) <= (now.year, now.month):
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m == 13:
            y += 1
            m = 1
    return out


def fetch_archive_daily(pair):
    rows = []
    errors = []
    found_months = 0
    for ym in archive_months(LOOKBACK_DAYS):
        url = f"{ARCHIVE}/{ym}/{pair}-{ym}.csv.gz"
        try:
            req = request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
            with request.urlopen(req, timeout=30) as r:
                raw = r.read()
            text = gzip.decompress(raw).decode("utf-8", "replace")
            month = []
            for rec in csv.reader(io.StringIO(text)):
                if len(rec) < 6:
                    continue
                try:
                    ts = int(float(rec[0]))
                    base_v = float(rec[1])
                    close = float(rec[2])
                    high = float(rec[3])
                    low = float(rec[4])
                    op = float(rec[5])
                except (TypeError, ValueError):
                    continue
                vals = (op, high, low, close, base_v)
                if any(not math.isfinite(x) for x in vals) or min(op, high, low, close) <= 0 or base_v < 0:
                    continue
                qv = base_v * close
                month.append((ts, op, high, low, close, base_v, qv))
            if month:
                found_months += 1
                rows.extend(month)
        except error.HTTPError as e:
            if e.code not in (403, 404):
                errors.append(f"{ym}:http{e.code}")
        except Exception as e:
            errors.append(f"{ym}:{type(e).__name__}:{str(e)[:60]}")
        time.sleep(SLEEP)
    dedup = {int(r[0]): r for r in rows}
    out = [dedup[k] for k in sorted(dedup)]
    return out, found_months, errors


def due_pairs(limit):
    with con() as c:
        return c.execute("""
          SELECT pair,symbol FROM historical_pair_registry
          WHERE coverage_status IN ('QUEUED','RETRY')
          ORDER BY attempts ASC, first_seen_utc ASC, pair ASC
          LIMIT ?
        """, (limit,)).fetchall()


def mark(pair, status, bars=0, err=None, first_ts=None, last_ts=None):
    with con() as c:
        c.execute("""
          UPDATE historical_pair_registry
          SET coverage_status=?,attempts=attempts+1,recovered_bars=?,
              first_bar_ts=?,last_bar_ts=?,last_error=?,last_seen_utc=?
          WHERE pair=?
        """, (status, bars, first_ts, last_ts, err, utcnow(), pair))


def persist_recovered_pair(pair, symbol, bars):
    hem.save_bars(pair, bars)
    events, controls = hem.build_labels(pair, bars)
    hem.save_labels(pair, bars, events, controls)
    with con() as c:
        c.execute("""
          INSERT INTO cex_pairs(pair,symbol,source,priority,status,attempts,last_run_utc,last_error)
          VALUES(?,?,?,10,'DONE',1,?,NULL)
          ON CONFLICT(pair) DO UPDATE SET
            source='gate_historical_archive',
            status='DONE',
            last_run_utc=excluded.last_run_utc,
            last_error=NULL
        """, (pair, symbol, "gate_historical_archive", utcnow()))
    return len(events), len(controls)


def main():
    init_db()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with con() as c:
        c.execute("""
          INSERT OR REPLACE INTO survivorship_runs(run_id,started_utc,notes,version)
          VALUES(?,?,?,?)
        """, (
            run_id, utcnow(),
            "Known-pair backfill only. Full historical Gate pair enumeration remains a separate coverage target.",
            VERSION,
        ))

    current_tradable, current_seeded = seed_current_gate()
    observed_seeded = seed_observed_history(current_tradable)

    attempted = recovered = no_archive = errors = bars_n = events_n = controls_n = 0
    for row in due_pairs(PAIR_BUDGET):
        pair, symbol = row["pair"], row["symbol"]
        attempted += 1
        try:
            bars, found_months, errs = fetch_archive_daily(pair)
            if len(bars) < 120:
                status = "NO_ARCHIVE" if found_months == 0 else "SHORT_HISTORY"
                mark(pair, status, len(bars), "|".join(errs)[:500],
                     bars[0][0] if bars else None, bars[-1][0] if bars else None)
                no_archive += 1
                print(f"{pair}: {status} bars={len(bars)} months={found_months}")
                continue
            ev, ctl = persist_recovered_pair(pair, symbol, bars)
            mark(pair, "RECOVERED", len(bars), None, bars[0][0], bars[-1][0])
            recovered += 1
            bars_n += len(bars)
            events_n += ev
            controls_n += ctl
            print(f"{pair}: RECOVERED bars={len(bars)} events={ev} controls={ctl}")
        except Exception as e:
            mark(pair, "RETRY", 0, type(e).__name__ + ":" + str(e)[:450])
            errors += 1
            print(f"{pair}: ERROR {type(e).__name__} {str(e)[:100]}")

    with con() as c:
        reg = c.execute("SELECT COUNT(*) FROM historical_pair_registry").fetchone()[0]
        queued = c.execute("""
          SELECT COUNT(*) FROM historical_pair_registry
          WHERE coverage_status IN ('QUEUED','RETRY')
        """).fetchone()[0]
        c.execute("""
          UPDATE survivorship_runs SET finished_utc=?,registry_pairs=?,queued_pairs=?,
            attempted_pairs=?,recovered_pairs=?,no_archive_pairs=?,error_pairs=?,
            recovered_bars=?,events=?,controls=? WHERE run_id=?
        """, (
            utcnow(), reg, queued, attempted, recovered, no_archive, errors,
            bars_n, events_n, controls_n, run_id,
        ))

    print(
        "Survivorship backfill:",
        f"current_nontradable_seeded={current_seeded}",
        f"observed_then_missing_seeded={observed_seeded}",
        f"registry={reg}",
        f"queued={queued}",
        f"attempted={attempted}",
        f"recovered={recovered}",
        f"no_archive_or_short={no_archive}",
        f"errors={errors}",
        f"bars={bars_n}",
        f"events={events_n}",
        f"controls={controls_n}",
    )


if __name__ == "__main__":
    main()
