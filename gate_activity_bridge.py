"""Separate early-activity engines: buyer acceleration and liquidity expansion.

These paths do not alter frozen V5 selection. They only widen observation,
then reuse the same fail-closed security gate before any alert.
"""

import sqlite3
import statistics
import time

from gate_early_observer import contract_key
from gate_notify import security_decision

VERSION = "gate-activity-bridge-v0.1-20260923"


def _price_change(con, network, contract, now_ts, current_price):
    prior = con.execute("""SELECT price FROM gate_early_observations
        WHERE network_id=? AND token_contract=? AND scan_ts<=?
          AND scan_ts>=? AND price>0 ORDER BY scan_ts DESC LIMIT 1""",
        (network, contract, now_ts - 20*60, now_ts - 90*60)).fetchone()
    if not prior or prior[0] <= 0:
        return None
    return 100 * (current_price / prior[0] - 1)


def _buyer_ratio(con, network, contract, now_ts, current_buyers):
    rows = con.execute("""SELECT buyers_5m FROM gate_buyer_observations
        WHERE network_id=? AND token_contract=? AND scan_ts<?
          AND scan_ts>=? AND buyers_5m>0
        ORDER BY scan_ts DESC LIMIT 24""",
        (network, contract, now_ts, now_ts - 6*3600)).fetchall()
    vals = [row[0] for row in rows if row[0] is not None and row[0] > 0]
    if current_buyers is None or len(vals) < 3:
        return None
    return current_buyers / statistics.median(vals)


def _liquidity_ratio(con, network, contract, now_ts, current_liquidity):
    prior = con.execute("""SELECT liquidity FROM gate_early_observations
        WHERE network_id=? AND token_contract=? AND scan_ts<=?
          AND scan_ts>=? AND liquidity>0
        ORDER BY scan_ts DESC LIMIT 1""",
        (network, contract, now_ts - 20*60, now_ts - 120*60)).fetchone()
    if not prior or prior[0] <= 0:
        return None
    return current_liquidity / prior[0]


def review(db_path, batch, observations, enrich, risk_shapes, now=None):
    now = int(now or time.time())
    by_contract = {}
    for row in observations:
        key = (row["network_id"], contract_key(row["network_id"],
               row["token_contract"]))
        if key not in by_contract or row.get("liquidity", 0) > by_contract[key].get("liquidity", 0):
            by_contract[key] = row

    with sqlite3.connect(db_path, timeout=30) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_activity_alert_audit (
            batch_id TEXT NOT NULL, network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL, engine TEXT NOT NULL,
            decided_at INTEGER NOT NULL, version TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL, message TEXT,
            PRIMARY KEY(batch_id, network_id, token_contract, engine))""")
        scan = con.execute("""SELECT scan_ts,status FROM gate_scan_health
            WHERE batch_id=?""", (batch,)).fetchone()
        if not scan or scan[1] != "VALID" or now - scan[0] > 15*60:
            return {"qualified":0,"pending":0,"withheld":0}

        candidates = []
        rows = con.execute("""SELECT o.network_id,o.token_contract,o.pool,o.price,
                o.liquidity,o.volume_5m,o.buys_5m,o.sells_5m,o.change_24h,
                o.own_volume_ratio,b.buyers_5m
            FROM gate_early_observations o
            LEFT JOIN gate_buyer_observations b
              ON b.batch_id=o.batch_id AND b.network_id=o.network_id
             AND b.token_contract=o.token_contract
            WHERE o.batch_id=? AND o.price>0 AND o.liquidity>=30000
              AND o.change_24h BETWEEN -2 AND 20""", (batch,)).fetchall()

        for network, contract, pool, price, liquidity, volume5, buys, sells, day, vol_ratio, buyers5 in rows:
            item = by_contract.get((network, contract))
            if not item or item.get("pool") != pool:
                continue
            price_change = _price_change(con, network, contract, scan[0], price)
            if price_change is None or not -1 <= price_change <= 5:
                continue
            buyer_ratio = _buyer_ratio(con, network, contract, scan[0], buyers5)
            liquidity_ratio = _liquidity_ratio(con, network, contract, scan[0], liquidity)
            engines = []
            if (buyer_ratio is not None and buyer_ratio >= 2.0
                    and (buyers5 or 0) >= 8
                    and (vol_ratio or 0) >= 1.5
                    and buys >= 1.2 * max(sells, 1)
                    and price_change <= 3):
                engines.append(("BUYER_ACCELERATION", buyer_ratio))
            if (liquidity_ratio is not None and liquidity_ratio >= 1.25
                    and (vol_ratio or 0) >= 1.5
                    and buys >= 1.15 * max(sells, 1)
                    and price_change <= 5):
                engines.append(("LIQUIDITY_EXPANSION", liquidity_ratio))
            for engine, strength in engines:
                if con.execute("""SELECT 1 FROM gate_activity_alert_audit
                    WHERE network_id=? AND token_contract=? AND engine=?
                      AND status IN ('PENDING','SENT') AND decided_at>?""",
                    (network, contract, engine, now - 86400)).fetchone():
                    continue
                candidates.append((strength, engine, network, contract, price,
                                   price_change, buyer_ratio, liquidity_ratio, item))

        candidates.sort(key=lambda x: x[0], reverse=True)
        counts = {"qualified":len(candidates),"pending":0,"withheld":0}
        for _, engine, network, contract, price, pchg, bratio, lratio, item in candidates[:4]:
            item = dict(item)
            item["volume_liquidity_ratio"] = item["volume_24h"] / max(item["liquidity"], 1)
            item["buy_sell_ratio_24h"] = item["buys_24h"] / max(item["sells_24h"], 1)
            item["tx_count_5m"] = item["buys_5m"] + item["sells_5m"]
            climax, trap = risk_shapes
            item["climax"], item["trap_proxy"] = climax(item), trap(item)
            try:
                enrich(item)
                reason = security_decision(item)
            except (ValueError, TypeError, KeyError, OverflowError):
                reason = "Güvenlik doğrulaması tamamlanamadı"
            if reason:
                status, message = "WITHHELD", None
                counts["withheld"] += 1
            else:
                status = "PENDING"
                detail = (f"Farklı alıcılar kendi geçmişinin {bratio:.1f} katına çıktı."
                          if engine == "BUYER_ACCELERATION" else
                          f"Likidite önceki ölçüme göre {lratio:.2f} katına çıktı.")
                message = (f"🔎 GATE AVCI | {engine}\n"
                    f"{item.get('name') or item.get('symbol') or '?'}\n"
                    f"Ağ ve tam kontrat: {network} {contract}\n"
                    f"{detail}\n"
                    f"Yakın dönem fiyat hareketi: %{pchg:+.1f}\n"
                    f"Sinyal fiyatı: ${price:.8g} (güncel fiyat değil).\n"
                    "Aynı güvenlik, holder, LP ve satış yapılabilirliği kapıları geçti. "
                    "V5 sinyali değildir; ayrı kağıt araştırma sinyalidir.")
                counts["pending"] += 1
            con.execute("""INSERT OR IGNORE INTO gate_activity_alert_audit
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (batch, network, contract, engine, now, VERSION, status,
                 reason or "", message))
        con.commit()
        return counts
