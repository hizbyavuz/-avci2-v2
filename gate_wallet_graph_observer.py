#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate Web3 wallet-quality observer.

Research-only. Does not alter frozen V5 membership or thresholds.
For recent Solana candidates it samples recent DEX buyers, then uses Helius
archival RPC (when configured) to estimate wallet age and first inbound funders.
Results are explicitly labelled as sampled/proxy evidence, never proof of Sybil.
"""

import json
import os
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone

import requests

DB = os.getenv("AVCI_DB", "avci2.db")
VALIDATION_DB = os.getenv("AVCI_VALIDATION_DB", "avci_validation_v5.db")
HELIUS_API_KEY = (os.getenv("HELIUS_API_KEY") or "").strip()
MAX_CANDIDATES = int(os.getenv("GATE_WALLET_MAX_CANDIDATES", "4"))
MAX_BUYERS = int(os.getenv("GATE_WALLET_MAX_BUYERS", "8"))
TIMEOUT = 20


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def helius_rpc(method, params):
    if not HELIUS_API_KEY:
        return None, "NO_HELIUS_KEY"
    try:
        r = requests.post(
            f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
            json={"jsonrpc": "2.0", "id": "avci", "method": method, "params": params},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            return None, str(body["error"])[:240]
        return body.get("result"), None
    except Exception as exc:
        return None, f"{type(exc).__name__}:{str(exc)[:180]}"


def result_rows(result):
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("data", "transactions", "transfers", "items"):
            if isinstance(result.get(key), list):
                return result[key]
    return []


def wallet_oldest_time(wallet):
    result, err = helius_rpc(
        "getTransactionsForAddress",
        [wallet, {"transactionDetails": "full", "sortOrder": "asc", "limit": 1,
                  "filters": {"status": "succeeded"}}],
    )
    if err:
        return None, err
    rows = result_rows(result)
    if not rows:
        return None, "NO_HISTORY"
    row = rows[0]
    ts = row.get("blockTime") if isinstance(row, dict) else None
    try:
        return int(ts), None
    except (TypeError, ValueError):
        return None, "NO_BLOCKTIME"


def first_inbound_funder(wallet):
    result, err = helius_rpc(
        "getTransfersByAddress",
        [wallet, {"direction": "in", "sortOrder": "asc", "limit": 12, "solMode": "merged",
                  "filters": {"status": "succeeded"}}],
    )
    if err:
        return None, err
    rows = result_rows(result)
    for row in rows:
        if not isinstance(row, dict):
            continue
        sender = row.get("fromUserAccount")
        recipient = row.get("toUserAccount")
        typ = str(row.get("type") or "").lower()
        if sender and recipient == wallet and sender != wallet and typ not in ("mint", "burn"):
            return str(sender), None
    return None, "NO_INBOUND_FUNDER"


def recent_buyers(pool):
    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/trades",
            headers={"accept": "application/json;version=20230203"}, timeout=TIMEOUT,
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("data") or []
    except Exception as exc:
        return [], f"{type(exc).__name__}:{str(exc)[:180]}"
    buyers = []
    seen = set()
    for row in rows:
        a = (row or {}).get("attributes") or {}
        if str(a.get("kind") or "").lower() != "buy":
            continue
        wallet = str(a.get("tx_from_address") or "").strip()
        if wallet and wallet not in seen:
            seen.add(wallet)
            buyers.append(wallet)
        if len(buyers) >= MAX_BUYERS:
            break
    return buyers, None


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS gate_wallet_intelligence (
        batch_id TEXT NOT NULL,
        network_id TEXT NOT NULL,
        token_contract TEXT NOT NULL,
        pool TEXT,
        sampled_wallets INTEGER NOT NULL DEFAULT 0,
        aged_wallets INTEGER NOT NULL DEFAULT 0,
        funded_wallets INTEGER NOT NULL DEFAULT 0,
        wallet_age_median_days REAL,
        fresh_7d_ratio REAL,
        fresh_30d_ratio REAL,
        common_funder TEXT,
        common_funder_ratio REAL,
        sybil_proxy INTEGER,
        status TEXT NOT NULL,
        details_json TEXT,
        created_at_utc TEXT NOT NULL,
        PRIMARY KEY(batch_id, network_id, token_contract)
    )""")


def candidate_rows():
    if not os.path.exists(DB) or not os.path.exists(VALIDATION_DB):
        return []
    with sqlite3.connect(DB) as obs, sqlite3.connect(VALIDATION_DB) as val:
        obs.row_factory = sqlite3.Row
        val.row_factory = sqlite3.Row
        h = obs.execute("SELECT batch_id FROM gate_scan_health WHERE status='VALID' ORDER BY scan_ts DESC LIMIT 1").fetchone()
        if not h:
            return []
        batch = h["batch_id"]
        events = val.execute("""SELECT id,batch_id,network_id,token_contract,signal_iso
            FROM validation_events WHERE batch_id=? AND network_id='solana'
              AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')
            ORDER BY CASE WHEN group_type='CANDIDATE' THEN 0 ELSE 1 END,id LIMIT ?""",
            (batch, MAX_CANDIDATES)).fetchall()
        out = []
        for e in events:
            snap = obs.execute("""SELECT pool,raw_json FROM snapshots
                WHERE network_id='solana' AND token_contract=? AND zaman_utc>=?
                ORDER BY id ASC LIMIT 1""", (e["token_contract"], e["signal_iso"])).fetchone()
            if snap:
                out.append((batch, e["token_contract"], snap["pool"], snap["raw_json"]))
        return out


def main():
    if not os.path.exists(DB):
        print("Gate wallet graph: avci2.db yok")
        return
    rows = candidate_rows()
    with sqlite3.connect(DB, timeout=30) as con:
        ensure_table(con)
        if not rows:
            con.commit()
            print("Gate wallet graph: son batchte Solana aday yok")
            return
        for batch, contract, pool, raw in rows:
            status = "OK" if HELIUS_API_KEY else "NO_HELIUS_KEY"
            buyers, trade_err = recent_buyers(pool)
            if trade_err:
                status = "TRADE_SOURCE_ERROR"
            ages = []
            funders = []
            errors = []
            now = int(datetime.now(timezone.utc).timestamp())
            if HELIUS_API_KEY:
                for wallet in buyers:
                    ts, err = wallet_oldest_time(wallet)
                    if ts:
                        ages.append(max(0.0, (now-ts)/86400.0))
                    elif err:
                        errors.append(f"age:{err}")
                    funder, ferr = first_inbound_funder(wallet)
                    if funder:
                        funders.append(funder)
                    elif ferr:
                        errors.append(f"fund:{ferr}")
            common = Counter(funders).most_common(1)[0] if funders else (None, 0)
            common_ratio = common[1] / len(funders) if funders else None
            fresh7 = sum(x < 7 for x in ages) / len(ages) if ages else None
            fresh30 = sum(x < 30 for x in ages) / len(ages) if ages else None
            sybil = int(bool(len(funders) >= 4 and common_ratio is not None and common_ratio >= .50
                             and fresh30 is not None and fresh30 >= .50))
            details = {"buyers": buyers, "ages_days": ages, "funders": funders,
                       "errors": errors[:20], "sample_proxy": True,
                       "note": "Common funder/fresh-wallet pattern is a Sybil proxy, not proof."}
            con.execute("""INSERT OR REPLACE INTO gate_wallet_intelligence
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                batch, "solana", contract, pool, len(buyers), len(ages), len(funders),
                statistics.median(ages) if ages else None, fresh7, fresh30,
                common[0], common_ratio, sybil, status, json.dumps(details), now_iso()))
            print("Gate wallet graph", contract[:10], "buyers", len(buyers), "aged", len(ages),
                  "funded", len(funders), "sybil_proxy", sybil, "status", status)
        con.commit()


if __name__ == "__main__":
    main()
