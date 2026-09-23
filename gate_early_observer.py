"""Append-only, observational Gate/on-chain history. V5 rules are unchanged."""

import sqlite3
import statistics
import time


OBSERVATION_VERSION = "gate-early-observation-v1"


def contract_key(network, contract):
    # Solana base58 addresses are case sensitive. EVM hex addresses are not.
    return contract if network == "solana" else contract.lower()


def record_scan(path, batch_id, pools, feed_errors=(), now_ts=None):
    """Record the eligible pool universe, including controls, before alerting."""
    now_ts = int(now_ts or time.time())
    best = {}
    for item in pools:
        network, contract = item.get("network_id"), item.get("token_contract")
        if not network or not contract:
            continue
        key = (network, contract_key(network, contract))
        if key not in best or float(item.get("liquidity") or 0) > float(
                best[key].get("liquidity") or 0):
            best[key] = item

    con = sqlite3.connect(path, timeout=30)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_scan_health (
            batch_id TEXT PRIMARY KEY, scan_ts INTEGER NOT NULL,
            observation_version TEXT NOT NULL, source_errors TEXT NOT NULL,
            observed_tokens INTEGER NOT NULL, status TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS gate_early_observations (
            batch_id TEXT NOT NULL, scan_ts INTEGER NOT NULL,
            network_id TEXT NOT NULL, token_contract TEXT NOT NULL,
            pool TEXT, price REAL, liquidity REAL, volume_5m REAL,
            volume_1h REAL, buys_5m REAL, sells_5m REAL,
            change_24h REAL, own_volume_ratio REAL,
            observed_anomaly INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (batch_id, network_id, token_contract))""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_gate_early_history
            ON gate_early_observations(network_id, token_contract, scan_ts)""")
        con.execute("""CREATE TABLE IF NOT EXISTS gate_buyer_observations (
            batch_id TEXT NOT NULL, scan_ts INTEGER NOT NULL,
            network_id TEXT NOT NULL, token_contract TEXT NOT NULL,
            pool TEXT, buyers_5m INTEGER, buyers_1h INTEGER,
            PRIMARY KEY(batch_id, network_id, token_contract))""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_gate_buyers_history
            ON gate_buyer_observations(network_id, token_contract, scan_ts)""")
        errors = tuple(feed_errors)
        # Zero eligible pools can be a valid scan; only source failures invalidate it.
        status = "INVALID" if errors else "VALID"
        con.execute("""INSERT OR REPLACE INTO gate_scan_health VALUES
            (?, ?, ?, ?, ?, ?)""",
            (batch_id, now_ts, OBSERVATION_VERSION,
             "; ".join(errors), len(best), status))

        for (network, contract), item in best.items():
            price = float(item.get("price_usd") or 0)
            volume = float(item.get("volume_5m") or 0)
            buys = float(item.get("buys_5m") or 0)
            sells = float(item.get("sells_5m") or 0)
            liquidity = float(item.get("liquidity") or 0)
            previous = con.execute("""SELECT scan_ts, volume_5m, liquidity
                FROM gate_early_observations
                WHERE network_id=? AND token_contract=? AND scan_ts<?
                AND scan_ts>=? ORDER BY scan_ts DESC LIMIT 36""",
                (network, contract, now_ts, now_ts - 6 * 3600)).fetchall()
            # Rolling 5m volume is compared with the token's own prior
            # observations. Insufficient history stays explicitly unknown.
            baseline = [row[1] for row in previous if row[1] and row[1] > 0]
            ratio = None
            anomaly = False
            if len(baseline) >= 3 and now_ts - previous[-1][0] >= 20 * 60:
                ratio = volume / statistics.median(baseline)
                recent_liquidity = previous[0][2] or 0
                anomaly = bool(price > 0 and liquidity >= recent_liquidity * .8
                               and ratio >= 2.5 and buys >= 1.2 * max(sells, 1)
                               and float(item.get("change_24h") or 0) < 40)
            con.execute("""INSERT OR REPLACE INTO gate_early_observations
                (batch_id, scan_ts, network_id, token_contract, pool, price,
                 liquidity, volume_5m, volume_1h, buys_5m, sells_5m,
                 change_24h, own_volume_ratio, observed_anomaly)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, now_ts, network, contract, item.get("pool"),
                 price, liquidity, volume, float(item.get("volume_1h") or 0),
                 buys, sells, float(item.get("change_24h") or 0),
                 ratio, int(anomaly)))
            con.execute("""INSERT OR REPLACE INTO gate_buyer_observations
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, now_ts, network, contract, item.get("pool"),
                 item.get("unique_buyers_5m"), item.get("unique_buyers_1h")))
        con.commit()
        return {"status": status, "observed": len(best), "errors": errors}
    finally:
        con.close()


def early_context(con, batch_id, network, contract, signal_price):
    """Return known first anomaly and pre-signal change, never post-signal PNL."""
    rows = con.execute("""SELECT scan_ts, price, own_volume_ratio,
        observed_anomaly FROM gate_early_observations
        WHERE network_id=? AND token_contract=? AND
        scan_ts >= (SELECT scan_ts - 72*3600 FROM gate_scan_health
                    WHERE batch_id=?)
        ORDER BY scan_ts""", (network, contract_key(network, contract), batch_id)).fetchall()
    first = next((row for row in rows if row[3] and row[1] > 0), None)
    current = rows[-1] if rows else None
    try:
        buyer_rows = con.execute("""SELECT scan_ts, buyers_5m, buyers_1h
            FROM gate_buyer_observations WHERE network_id=?
            AND token_contract=? AND scan_ts <=
                (SELECT scan_ts FROM gate_scan_health WHERE batch_id=?)
            AND scan_ts >=
                (SELECT scan_ts - 6*3600 FROM gate_scan_health WHERE batch_id=?)
            ORDER BY scan_ts DESC LIMIT 36""",
            (network, contract_key(network, contract), batch_id, batch_id)).fetchall()
    except sqlite3.OperationalError:
        buyer_rows = []
    current_buyers = buyer_rows[0][1] if buyer_rows else None
    prior = [(ts, count) for ts, count, _ in buyer_rows[1:]
             if count is not None and count > 0]
    buyer_ratio = None
    if (current_buyers is not None and len(prior) >= 3 and
            buyer_rows[0][0] - prior[-1][0] >= 1200):
        buyer_ratio = current_buyers / statistics.median(
            count for _, count in prior)
    return {"first_anomaly_ts": first[0] if first else None,
            "gain_before_signal_pct":
                100 * (signal_price / first[1] - 1) if first else None,
            "own_volume_ratio": current[2] if current else None,
            "unique_buyers_5m": current_buyers,
            "buyer_ratio": buyer_ratio}


def record_candidate_risk(path, batch_id, candidates):
    """Keep observed security samples; no inferred rug labels or V5 rule edits."""
    with sqlite3.connect(path, timeout=30) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_candidate_risk_history (
            batch_id TEXT NOT NULL, scan_ts INTEGER NOT NULL,
            network_id TEXT NOT NULL, token_contract TEXT NOT NULL,
            creator_address TEXT, lp_protected_pct REAL,
            creator_unlocked_lp_pct REAL, top10_adjusted_pct REAL,
            trades_sampled INTEGER, unique_buyers_sample INTEGER,
            roundtrip_wallets_sample INTEGER,
            PRIMARY KEY(batch_id, network_id, token_contract))""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_gate_candidate_risk_history
            ON gate_candidate_risk_history(network_id, token_contract, scan_ts)""")
        con.execute("""CREATE TABLE IF NOT EXISTS gate_optional_context (
            batch_id TEXT NOT NULL, network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL, creator_risk_status TEXT,
            sampled_wash_proxy INTEGER, social_status TEXT,
            x_mentions_15m INTEGER, x_mentions_prev_45m INTEGER,
            PRIMARY KEY(batch_id, network_id, token_contract))""")
        scan = con.execute("SELECT scan_ts FROM gate_scan_health WHERE batch_id=?",
                           (batch_id,)).fetchone()
        if not scan:
            raise ValueError("Risk history requires a recorded scan")
        written = 0
        for item in candidates:
            network = item.get("network_id")
            contract = item.get("token_contract")
            if not network or not contract:
                continue
            lp = item.get("lp_protection") or {}
            holder = item.get("adjusted_holder") or {}
            cluster = item.get("trade_cluster") or {}
            creator = item.get("creator_address")
            creator = creator if isinstance(creator, str) else None
            con.execute("""INSERT OR IGNORE INTO gate_candidate_risk_history
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, scan[0], network, contract_key(network, contract),
                 creator, lp.get("protected_pct"),
                 lp.get("creator_unlocked_pct"), holder.get("top10_pct"),
                 cluster.get("trades_seen"), cluster.get("unique_buyers_sample"),
                 cluster.get("roundtrip_wallets_sample")))
            social = item.get("social_signal") or {}
            reputation = item.get("creator_reputation") or {}
            con.execute("""INSERT OR IGNORE INTO gate_optional_context
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, network, contract_key(network, contract), reputation.get("status"),
                 int(cluster["wash_proxy"]) if cluster.get("wash_proxy") is not None
                 else None, social.get("status"), social.get("last_15m"),
                 social.get("previous_45m")))
            written += 1
        return written


def candidate_risk_context(con, batch_id, network, contract):
    """Top-10 holder change from a prior sample at least ten minutes earlier."""
    try:
        current = con.execute("""SELECT scan_ts, top10_adjusted_pct,
            creator_address FROM gate_candidate_risk_history
            WHERE batch_id=? AND network_id=? AND token_contract=?""",
            (batch_id, network, contract_key(network, contract))).fetchone()
    except sqlite3.OperationalError:
        current = None
    if not current or current[1] is None:
        return {"top10_change_pp": None, "creator_tokens_observed": None}
    older = con.execute("""SELECT top10_adjusted_pct
        FROM gate_candidate_risk_history WHERE network_id=?
        AND token_contract=? AND scan_ts<=? AND scan_ts>=?
        AND top10_adjusted_pct IS NOT NULL ORDER BY scan_ts DESC LIMIT 1""",
        (network, contract_key(network, contract), current[0] - 600,
         current[0] - 6 * 3600)).fetchone()
    creator_count = None
    if current[2]:
        creator_count = con.execute("""SELECT COUNT(DISTINCT token_contract)
            FROM gate_candidate_risk_history
            WHERE network_id=? AND lower(creator_address)=?""",
            (network, current[2].lower())).fetchone()[0]
    return {"top10_change_pp": current[1] - older[0] if older else None,
            "creator_tokens_observed": creator_count}
