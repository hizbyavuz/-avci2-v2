#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate Avci History Miner.

Builds a reconstructable token life-story timeline without changing frozen V5.
The miner is intentionally checkpointed, rate-limited and append-only.

v0.1 scope:
- seed exact Gate contracts from avci2.db when available
- retain contract identity (network + address)
- discover current pools from GeckoTerminal
- record pool birth/current market observations as timeline events
- keep a queue/checkpoint so every run continues where the previous run stopped
- maintain API counters so free-tier usage can be measured
- leave room for historical candles, holder history and CEX listing events

This is a research collector. It never places trades.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

DB=os.getenv("HISTORY_DB","history_miner.db")
SOURCE_DB=os.getenv("AVCI_DB","avci2.db")
GECKO_BASE="https://api.geckoterminal.com/api/v2"
BATCH=int(os.getenv("HISTORY_BATCH","24"))
SLEEP=float(os.getenv("HISTORY_SLEEP_SECONDS","6.2"))
LOOKBACK_DAYS=int(os.getenv("HISTORY_LOOKBACK_DAYS","730"))
MAX_RUN_SECONDS=int(os.getenv("HISTORY_MAX_RUN_SECONDS","1200"))
UA="GateAvci-HistoryMiner/0.1"


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def conn():
    c=sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory=sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tokens(
          network_id TEXT NOT NULL,
          contract TEXT NOT NULL,
          symbol TEXT,
          gate_pair TEXT,
          first_seen_utc TEXT NOT NULL,
          source TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'QUEUED',
          last_error TEXT,
          PRIMARY KEY(network_id,contract)
        );
        CREATE TABLE IF NOT EXISTS queue(
          network_id TEXT NOT NULL,
          contract TEXT NOT NULL,
          priority INTEGER NOT NULL DEFAULT 100,
          attempts INTEGER NOT NULL DEFAULT 0,
          next_run_utc TEXT,
          last_run_utc TEXT,
          PRIMARY KEY(network_id,contract)
        );
        CREATE TABLE IF NOT EXISTS pools(
          network_id TEXT NOT NULL,
          contract TEXT NOT NULL,
          pool_address TEXT NOT NULL,
          dex_id TEXT,
          pool_created_at TEXT,
          base_symbol TEXT,
          quote_symbol TEXT,
          first_observed_utc TEXT NOT NULL,
          raw_json TEXT,
          PRIMARY KEY(network_id,contract,pool_address)
        );
        CREATE TABLE IF NOT EXISTS timeline(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          network_id TEXT NOT NULL,
          contract TEXT NOT NULL,
          event_time_utc TEXT NOT NULL,
          event_type TEXT NOT NULL,
          price_usd REAL,
          liquidity_usd REAL,
          volume_24h_usd REAL,
          fdv_usd REAL,
          market_cap_usd REAL,
          change_24h_pct REAL,
          source TEXT NOT NULL,
          details_json TEXT,
          UNIQUE(network_id,contract,event_time_utc,event_type,source)
        );
        CREATE TABLE IF NOT EXISTS milestones(
          network_id TEXT NOT NULL,
          contract TEXT NOT NULL,
          milestone TEXT NOT NULL,
          event_time_utc TEXT NOT NULL,
          value REAL,
          details_json TEXT,
          PRIMARY KEY(network_id,contract,milestone)
        );
        CREATE TABLE IF NOT EXISTS api_usage(
          day_utc TEXT NOT NULL,
          provider TEXT NOT NULL,
          endpoint TEXT NOT NULL,
          calls INTEGER NOT NULL DEFAULT 0,
          errors INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(day_utc,provider,endpoint)
        );
        CREATE TABLE IF NOT EXISTS run_stats(
          run_id TEXT PRIMARY KEY,
          started_utc TEXT NOT NULL,
          finished_utc TEXT,
          tokens_attempted INTEGER NOT NULL DEFAULT 0,
          tokens_ok INTEGER NOT NULL DEFAULT 0,
          api_calls INTEGER NOT NULL DEFAULT 0,
          notes TEXT
        );
        """)


def norm_contract(network: str, contract: str) -> str:
    return contract if network=="solana" else contract.lower()


def seed_from_gate():
    if not os.path.exists(SOURCE_DB):
        return 0
    added=0
    try:
        src=sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
        src.row_factory=sqlite3.Row
        cols={r["name"] for r in src.execute("PRAGMA table_info(gate_spot_contracts)")}
        if not {"network_id","token_contract"}.issubset(cols):
            src.close(); return 0
        pair_col="pair" if "pair" in cols else "NULL AS pair"
        rows=src.execute(f"""SELECT network_id,token_contract,{pair_col}
            FROM gate_spot_contracts
            WHERE network_id IS NOT NULL AND token_contract IS NOT NULL""").fetchall()
        with conn() as c:
            for r in rows:
                n=str(r["network_id"]).strip().lower()
                a=norm_contract(n,str(r["token_contract"]).strip())
                if not a: continue
                before=c.total_changes
                c.execute("""INSERT OR IGNORE INTO tokens
                    (network_id,contract,gate_pair,first_seen_utc,source)
                    VALUES (?,?,?,?,?)""",(n,a,r["pair"],utcnow(),"gate_spot_contracts"))
                c.execute("""INSERT OR IGNORE INTO queue(network_id,contract)
                    VALUES (?,?)""",(n,a))
                if c.total_changes>before: added+=1
        src.close()
    except sqlite3.Error as e:
        print("seed warning:",type(e).__name__)
    return added


def usage(provider, endpoint, ok=True):
    day=datetime.now(timezone.utc).date().isoformat()
    with conn() as c:
        c.execute("""INSERT INTO api_usage(day_utc,provider,endpoint,calls,errors)
          VALUES (?,?,?,?,?) ON CONFLICT(day_utc,provider,endpoint) DO UPDATE SET
          calls=calls+1, errors=errors+excluded.errors""",
          (day,provider,endpoint,1,0 if ok else 1))


def get_json(path: str) -> dict[str,Any] | None:
    try:
        r=requests.get(GECKO_BASE+path,headers={"User-Agent":UA},
                       timeout=25)
        ok=r.status_code==200
        usage("geckoterminal",path.split("?")[0],ok)
        if not ok:
            print("gecko",r.status_code,path[:80])
            return None
        return r.json()
    except requests.RequestException as e:
        usage("geckoterminal",path.split("?")[0],False)
        print("gecko error",type(e).__name__)
        return None


def fnum(v):
    try: return float(v)
    except (TypeError,ValueError): return None


def record_pool_snapshot(network,contract,payload):
    data=payload.get("data") or []
    if not isinstance(data,list) or not data:
        return 0
    now=utcnow(); count=0
    with conn() as c:
        for row in data:
            attrs=row.get("attributes") or {}
            rel=row.get("relationships") or {}
            pool_id=str(row.get("id") or "")
            address=str(attrs.get("address") or pool_id.split("_")[-1])
            if not address: continue
            created=attrs.get("pool_created_at")
            dex=((rel.get("dex") or {}).get("data") or {}).get("id")
            base=((rel.get("base_token") or {}).get("data") or {}).get("id")
            quote=((rel.get("quote_token") or {}).get("data") or {}).get("id")
            c.execute("""INSERT OR REPLACE INTO pools
                (network_id,contract,pool_address,dex_id,pool_created_at,
                 base_symbol,quote_symbol,first_observed_utc,raw_json)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (network,contract,address,dex,created,base,quote,now,
                 json.dumps(row,separators=(",",":"))))
            vol=attrs.get("volume_usd") or {}
            chg=attrs.get("price_change_percentage") or {}
            liq=attrs.get("reserve_in_usd")
            c.execute("""INSERT OR IGNORE INTO timeline
                (network_id,contract,event_time_utc,event_type,price_usd,
                 liquidity_usd,volume_24h_usd,fdv_usd,market_cap_usd,
                 change_24h_pct,source,details_json)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (network,contract,now,"POOL_SNAPSHOT",
                 fnum(attrs.get("base_token_price_usd")),fnum(liq),
                 fnum(vol.get("h24")),fnum(attrs.get("fdv_usd")),
                 fnum(attrs.get("market_cap_usd")),fnum(chg.get("h24")),
                 "geckoterminal",json.dumps({"pool":address,"created":created,
                    "transactions":attrs.get("transactions")},separators=(",",":"))))
            if created:
                c.execute("""INSERT OR IGNORE INTO milestones
                  (network_id,contract,milestone,event_time_utc,details_json)
                  VALUES (?,?,?,?,?)""",(network,contract,"EARLIEST_POOL",created,
                    json.dumps({"pool":address,"dex":dex},separators=(",",":"))))
            count+=1
    return count


def process_one(network,contract):
    payload=get_json(f"/networks/{network}/tokens/{contract}/pools"
                     "?include=base_token,quote_token")
    if payload is None:
        return False,"api_error"
    n=record_pool_snapshot(network,contract,payload)
    return True,f"pools={n}"


def due_rows(limit):
    now=utcnow()
    with conn() as c:
        return c.execute("""SELECT q.network_id,q.contract,q.attempts
          FROM queue q WHERE q.next_run_utc IS NULL OR q.next_run_utc<=?
          ORDER BY q.priority ASC,q.attempts ASC,q.contract LIMIT ?""",
          (now,limit)).fetchall()


def reschedule(network,contract,ok,reason):
    nxt=(datetime.now(timezone.utc)+timedelta(hours=24 if ok else 6)).isoformat()
    with conn() as c:
        c.execute("""UPDATE queue SET attempts=attempts+1,last_run_utc=?,
          next_run_utc=? WHERE network_id=? AND contract=?""",
          (utcnow(),nxt,network,contract))
        c.execute("""UPDATE tokens SET status=?,last_error=? WHERE
          network_id=? AND contract=?""",
          ("OBSERVED" if ok else "RETRY",None if ok else reason,network,contract))


def main():
    init_db()
    seeded=seed_from_gate()
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with conn() as c:
        c.execute("INSERT INTO run_stats(run_id,started_utc,notes) VALUES (?,?,?)",
                  (run_id,utcnow(),f"seeded={seeded};lookback={LOOKBACK_DAYS}d"))
    attempted=okn=0
    started_monotonic=time.monotonic()
    for row in due_rows(BATCH):
        if time.monotonic() - started_monotonic >= MAX_RUN_SECONDS:
            print(f"History Miner time budget reached after {attempted} tokens")
            break
        attempted+=1
        ok,reason=process_one(row["network_id"],row["contract"])
        reschedule(row["network_id"],row["contract"],ok,reason)
        okn+=int(ok)
        print(row["network_id"],row["contract"][:12],ok,reason)
        time.sleep(SLEEP)
    with conn() as c:
        calls=c.execute("""SELECT COALESCE(SUM(calls),0) FROM api_usage
          WHERE day_utc=?""",(datetime.now(timezone.utc).date().isoformat(),)).fetchone()[0]
        c.execute("""UPDATE run_stats SET finished_utc=?,tokens_attempted=?,
          tokens_ok=?,api_calls=? WHERE run_id=?""",
          (utcnow(),attempted,okn,calls,run_id))
        q=c.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        t=c.execute("SELECT COUNT(*) FROM timeline").fetchone()[0]
        print(f"History Miner: attempted={attempted} ok={okn} queue={q} timeline={t} today_calls={calls}")


if __name__=="__main__":
    main()
