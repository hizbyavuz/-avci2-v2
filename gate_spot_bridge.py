"""Independent Gate Spot momentum -> exact-contract on-chain safety review.

This stream does not alter frozen V5 candidates, rules, or validation counts.
"""

import json
import sqlite3
import time

from gate_notify import security_decision
from gate_spot_observer import identity

VERSION = "gate-spot-bridge-v0.1-20260923"


def recent_watches(db_path, now=None):
    now = int(now or time.time())
    with sqlite3.connect(db_path) as con:
        health_table = con.execute("SELECT 1 FROM sqlite_master WHERE name='gate_spot_health'").fetchone()
        if not health_table:
            return []
        latest = con.execute("""SELECT scan_ts, status FROM gate_spot_health
            ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not latest or latest[1] != "VALID" or now - latest[0] > 20 * 60:
            return []
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='gate_spot_watch'").fetchone()
        if not exists:
            return []
        return con.execute("""SELECT w.batch_id, w.pair, w.price, w.rise_pct,
            w.round_trip_1k_pct, g.scan_ts, c.network_id, c.token_contract
            FROM gate_spot_watch w JOIN gate_spot_health g ON g.batch_id=w.batch_id
            JOIN gate_spot_contracts c ON c.pair=w.pair
            WHERE w.status='PAPER_WATCH' AND g.status='VALID'
              AND g.scan_ts BETWEEN ? AND ?
            ORDER BY g.scan_ts DESC, w.rise_pct DESC LIMIT 6""",
            (now - 20 * 60, now)).fetchall()


def exact_pool(rows, network, contract, gate_price):
    """Only a DEX pool whose BASE token is Gate's official contract qualifies."""
    contract = identity(network, contract)
    possible = [r for r in rows if r.get("network_id") == network
                and identity(network, str(r.get("token_contract") or "")) == contract
                and r.get("price_usd", 0) > 0
                and r.get("liquidity", 0) >= 15000
                and r.get("volume_24h", 0) >= 30000]
    possible.sort(key=lambda r: (r["liquidity"], r["volume_24h"]), reverse=True)
    if not possible:
        return None, "Resmi kontratla eşleşen yeterli DEX havuzu yok"
    row = possible[0]
    if abs(row["price_usd"] / gate_price - 1) > .05:
        return None, "Gate/DEX fiyatları %5'ten fazla ayrışıyor"
    if (row.get("buys_5m", 0) + row.get("sells_5m", 0) < 10
            or row.get("buys_5m", 0) <= row.get("sells_5m", 0)
            or row.get("change_5m", 0) < -3
            or row.get("change_24h", 0) > 40):
        return None, "DEX akışı veya geç hareket doğrulaması yetersiz"
    return row, None


def review(db_path, api_get, rows_from_payload, enrich, risk_shapes,
           sleep=lambda _: None, now=None):
    """Fail closed: missing feeds/identity/security never produce an alert."""
    now = int(now or time.time())
    watches = recent_watches(db_path, now)
    with sqlite3.connect(db_path, timeout=30) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_spot_bridge_audit (
            watch_batch TEXT NOT NULL, network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL, pair TEXT NOT NULL,
            decided_at INTEGER NOT NULL, version TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL,
            signal_price REAL NOT NULL, gate_rise_pct REAL NOT NULL,
            message TEXT, PRIMARY KEY(watch_batch, network_id, token_contract))""")
        counts = {"seen": len(watches), "pending": 0, "withheld": 0}
        for batch, pair, gate_price, rise, loss, scan_ts, network, contract in watches[:3]:
            if con.execute("""SELECT 1 FROM gate_spot_bridge_audit
                WHERE watch_batch=? AND network_id=? AND token_contract=?""",
                (batch, network, contract)).fetchone():
                continue
            # Repeat alerts on the same contract are suppressed for 24 hours.
            if con.execute("""SELECT 1 FROM gate_spot_bridge_audit
                WHERE network_id=? AND token_contract=? AND status IN ('PENDING','SENT')
                  AND decided_at>?""", (network, contract, now - 86400)).fetchone():
                continue
            reason = ""
            item = None
            if now - scan_ts > 20 * 60 or gate_price <= 0 or loss is None or loss > 3:
                reason = "Gate fiyat/defter ölçümü eski veya doğrulanmadı"
            else:
                payload = api_get(f"/networks/{network}/tokens/{contract}/pools"
                                  "?include=base_token,quote_token")
                if (not isinstance(payload, dict) or payload.get("_avci_data_error")
                        or not isinstance(payload.get("data"), list)):
                    reason = "DEX veri kaynağı eksik"
                else:
                    rows = rows_from_payload(payload, network, network,
                                             "GATE_SPOT_BRIDGE", observation=True,
                                             allow_old_pool=True)
                    item, reason = exact_pool(rows, network, contract, gate_price)
            if item is not None:
                item["volume_liquidity_ratio"] = item["volume_24h"] / max(item["liquidity"], 1)
                item["buy_sell_ratio_24h"] = item["buys_24h"] / max(item["sells_24h"], 1)
                item["tx_count_5m"] = item["buys_5m"] + item["sells_5m"]
                climax, trap = risk_shapes
                item["climax"] = climax(item)
                item["trap_proxy"] = trap(item)
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
                message = (f"🔎 GATE SPOT | AYRI ERKEN GÖZLEM\n{pair}\n"
                    f"Ağ ve resmi kontrat: {network} {contract}\n"
                    f"Gate ~20 dk: +%{rise:.1f} • Sinyal fiyatı: ${gate_price:.8g}\n"
                    f"Gate $1.000 gidiş-dönüş: ~%{loss:.1f}\n"
                    f"DEX fiyat/akış, holder, LP, sözleşme ve satış teklifleri doğrulandı.\n"
                    "V5 sinyali değildir; ayrı kağıt araştırma sinyalidir. "
                    "Canlı fiyatı ve işlemi ayrıca doğrula; bot emir vermez.")
                counts["pending"] += 1
            con.execute("""INSERT OR IGNORE INTO gate_spot_bridge_audit
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch, network, contract, pair, now, VERSION, status,
                 reason or "", gate_price, rise, message))
            sleep(2)
        con.commit()
    return counts
