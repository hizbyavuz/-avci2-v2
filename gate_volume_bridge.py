"""Separate own-history volume wake-up; same fail-closed security gate as V5."""

import sqlite3
import time

from gate_early_observer import contract_key
from gate_notify import security_decision

VERSION = "gate-volume-wakeup-v0.1-20260923"


def review(db_path, batch, observations, enrich, risk_shapes, now=None):
    now = int(now or time.time())
    by_contract = {}
    for row in observations:
        key = (row["network_id"], contract_key(row["network_id"],
               row["token_contract"]))
        if key not in by_contract or row.get("liquidity", 0) > by_contract[key].get("liquidity", 0):
            by_contract[key] = row
    with sqlite3.connect(db_path, timeout=30) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_volume_alert_audit (
            batch_id TEXT NOT NULL, network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL, decided_at INTEGER NOT NULL,
            version TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL, message TEXT,
            PRIMARY KEY(batch_id, network_id, token_contract))""")
        scan = con.execute("""SELECT scan_ts,status FROM gate_scan_health
            WHERE batch_id=?""", (batch,)).fetchone()
        if not scan or scan[1] != "VALID" or now - scan[0] > 15 * 60:
            return {"qualified": 0, "pending": 0, "withheld": 0}
        rows = con.execute("""SELECT network_id,token_contract,pool,price,liquidity,
                volume_5m,buys_5m,sells_5m,change_24h,own_volume_ratio
            FROM gate_early_observations WHERE batch_id=?
              AND observed_anomaly=1 AND own_volume_ratio>=3
              AND volume_5m>=2000 AND liquidity>=30000
              AND change_24h BETWEEN 0 AND 25
              AND buys_5m>=1.5*MAX(sells_5m,1)
            ORDER BY own_volume_ratio DESC LIMIT 8""", (batch,)).fetchall()
        qualified = []
        for network, contract, pool, price, liquidity, volume, buys, sells, day, ratio in rows:
            item = by_contract.get((network, contract))
            if not item or item.get("pool") != pool or item.get("price_usd", 0) <= 0:
                continue
            prior = con.execute("""SELECT price FROM gate_early_observations
                WHERE network_id=? AND token_contract=? AND scan_ts<=?
                  AND scan_ts>=? AND price>0 ORDER BY scan_ts DESC LIMIT 1""",
                (network, contract, scan[0] - 20*60, scan[0] - 90*60)).fetchone()
            if not prior or not 0 <= 100 * (price / prior[0] - 1) <= 15:
                continue
            if con.execute("""SELECT 1 FROM gate_volume_alert_audit
                WHERE network_id=? AND token_contract=? AND status IN ('PENDING','SENT')
                  AND decided_at>?""", (network, contract, now - 86400)).fetchone():
                continue
            qualified.append((network, contract, price, ratio, item))
        counts = {"qualified": len(qualified), "pending": 0, "withheld": 0}
        for network, contract, price, ratio, item in qualified[:2]:
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
                message = (f"🔎 GATE AVCI | AYRI HACİM UYANIŞI\n"
                    f"{item.get('name') or item.get('symbol') or '?'}\n"
                    f"Ağ ve tam kontrat: {network} {contract}\n"
                    f"5 dk hacim kendi geçmişine göre {ratio:.1f} kat.\n"
                    f"Sinyal fiyatı: ${price:.8g} (güncel fiyat değil).\n"
                    "DEX alıcı akışı ve güvenlik/satış kontrolleri doğrulandı. "
                    "V5 sinyali değildir; ayrı kağıt araştırma sinyalidir. "
                    "Bot alım emri vermez.")
                counts["pending"] += 1
            con.execute("""INSERT OR IGNORE INTO gate_volume_alert_audit
                VALUES (?,?,?,?,?,?,?,?)""",
                (batch, network, contract, now, VERSION, status, reason or "", message))
        con.commit()
        return counts
