"""Exact-contract Gate Spot vs DEX ignition observer.

Never matches by ticker alone. Frozen V5 is untouched.
"""

import sqlite3
import time

from gate_early_observer import contract_key
from gate_notify import security_decision

VERSION = "gate-cross-venue-v0.1-20260923"


def review(db_path, batch, observations, enrich, risk_shapes, now=None):
    now = int(now or time.time())
    by_contract = {}
    for row in observations:
        key = (row["network_id"], contract_key(row["network_id"],
               row["token_contract"]))
        if key not in by_contract or row.get("liquidity", 0) > by_contract[key].get("liquidity", 0):
            by_contract[key] = row

    with sqlite3.connect(db_path, timeout=30) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_cross_venue_alert_audit (
            batch_id TEXT NOT NULL, pair TEXT NOT NULL, network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL, decided_at INTEGER NOT NULL,
            version TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
            message TEXT, PRIMARY KEY(batch_id,pair,network_id,token_contract))""")
        scan = con.execute("""SELECT scan_ts,status FROM gate_scan_health
            WHERE batch_id=?""", (batch,)).fetchone()
        spot = con.execute("""SELECT batch_id,scan_ts,status FROM gate_spot_health
            ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if (not scan or scan[1] != "VALID" or now - scan[0] > 15*60
                or not spot or spot[2] != "VALID" or now - spot[1] > 20*60):
            return {"qualified":0,"pending":0,"withheld":0}

        rows = con.execute("""SELECT m.pair,m.last,m.change_24h,c.network_id,
                c.token_contract
            FROM gate_spot_market m JOIN gate_spot_contracts c ON c.pair=m.pair
            WHERE m.volume_24h>=300000 AND m.change_24h BETWEEN -2 AND 20
            ORDER BY m.volume_24h DESC LIMIT 180""").fetchall()

        candidates = []
        for pair, gate_price, day, network, contract in rows:
            key = (network, contract_key(network, contract))
            item = by_contract.get(key)
            if not item or gate_price <= 0 or item.get("price_usd", 0) <= 0:
                continue
            dex_price = item["price_usd"]
            lead = 100 * (dex_price / gate_price - 1)
            if not 0.4 <= lead <= 3.0:
                continue
            if (item.get("liquidity", 0) < 30000
                    or item.get("volume_24h", 0) < 30000
                    or item.get("buys_5m", 0) < 1.3 * max(item.get("sells_5m", 0), 1)
                    or item.get("change_24h", 0) > 25):
                continue
            obs = con.execute("""SELECT own_volume_ratio FROM gate_early_observations
                WHERE batch_id=? AND network_id=? AND token_contract=?""",
                (batch, network, key[1])).fetchone()
            vol_ratio = obs[0] if obs else None
            if vol_ratio is None or vol_ratio < 1.5:
                continue
            if con.execute("""SELECT 1 FROM gate_cross_venue_alert_audit
                WHERE network_id=? AND token_contract=?
                  AND status IN ('PENDING','SENT') AND decided_at>?""",
                (network, key[1], now - 86400)).fetchone():
                continue
            candidates.append((lead, pair, network, key[1], gate_price,
                               dex_price, vol_ratio, item))

        candidates.sort(reverse=True, key=lambda x:x[0])
        counts = {"qualified":len(candidates),"pending":0,"withheld":0}
        for lead, pair, network, contract, gate_price, dex_price, vol_ratio, item in candidates[:3]:
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
                message = (f"🔎 GATE AVCI | CROSS-VENUE IGNITION\n"
                    f"{pair}\nAğ ve tam kontrat: {network} {contract}\n"
                    f"DEX fiyatı Gate fiyatının %{lead:.1f} üzerinde; "
                    f"5 dk hacim kendi geçmişinin {vol_ratio:.1f} katı.\n"
                    f"Gate fiyatı: ${gate_price:.8g} • DEX: ${dex_price:.8g}\n"
                    "Eşleşme ticker ile değil resmi kontratla yapıldı. "
                    "Aynı güvenlik ve satış yapılabilirliği kapıları geçti. "
                    "V5 sinyali değildir; ayrı kağıt araştırma sinyalidir.")
                counts["pending"] += 1
            con.execute("""INSERT OR IGNORE INTO gate_cross_venue_alert_audit
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (batch,pair,network,contract,now,VERSION,status,reason or "",message))
        con.commit()
        return counts
