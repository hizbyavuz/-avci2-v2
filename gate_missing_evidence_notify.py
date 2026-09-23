"""Research-only Telegram alerts for Gate signals blocked only by missing safety evidence.

Hard-risk findings remain silent. Frozen V5 selection is untouched.
"""
import os
import sqlite3
import time

import requests

from binance_notify import resolve_chat_id
from gate_notify import observation_only_reason, send_gate_aux

DB = "avci2.db"


def latest_batch(con):
    row = con.execute("""SELECT batch_id,scan_ts,status FROM gate_scan_health
        ORDER BY scan_ts DESC LIMIT 1""").fetchone()
    if not row or row[2] != "VALID" or int(time.time()) - int(row[1]) > 20 * 60:
        return None
    return row[0]


def collect_soft_blocks(con, batch):
    rows = []
    specs = (
        ("VOLUME", "gate_volume_alert_audit",
         "SELECT network_id,token_contract,reason,message FROM gate_volume_alert_audit WHERE batch_id=? AND status='WITHHELD'"),
        ("ACTIVITY", "gate_activity_alert_audit",
         "SELECT network_id,token_contract,reason,message FROM gate_activity_alert_audit WHERE batch_id=? AND status='WITHHELD'"),
        ("CROSS_VENUE", "gate_cross_venue_alert_audit",
         "SELECT network_id,token_contract,reason,message FROM gate_cross_venue_alert_audit WHERE batch_id=? AND status='WITHHELD'"),
    )
    for source, table, sql in specs:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        for network, contract, reason, _ in con.execute(sql, (batch,)).fetchall():
            if not observation_only_reason(reason):
                continue
            obs = con.execute("""SELECT price,change_24h,own_volume_ratio,
                buys_5m,sells_5m,liquidity,volume_5m
                FROM gate_early_observations
                WHERE batch_id=? AND network_id=? AND token_contract=?
                ORDER BY scan_ts DESC LIMIT 1""",
                (batch, network, contract)).fetchone()
            data = {
                "price": obs[0] if obs else None,
                "change_24h": obs[1] if obs else None,
                "volume_ratio": obs[2] if obs else None,
                "buys_5m": obs[3] if obs else None,
                "sells_5m": obs[4] if obs else None,
                "liquidity": obs[5] if obs else None,
                "volume_5m": obs[6] if obs else None,
            }
            rows.append((source, network, contract, reason, data))
    return rows

def already_sent(con, batch, source, network, contract):
    con.execute("""CREATE TABLE IF NOT EXISTS gate_missing_evidence_alert_audit(
        batch_id TEXT NOT NULL,source TEXT NOT NULL,network_id TEXT NOT NULL,
        token_contract TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,
        decided_at INTEGER NOT NULL,PRIMARY KEY(batch_id,source,network_id,token_contract))""")
    return con.execute("""SELECT 1 FROM gate_missing_evidence_alert_audit
        WHERE batch_id=? AND source=? AND network_id=? AND token_contract=?""",
        (batch, source, network, contract)).fetchone() is not None


def build_message(source, network, contract, reason, data=None):
    data = data or {}
    source_text = {
        "VOLUME": "Hacim kendi geçmişine göre olağan dışı hızlandı.",
        "ACTIVITY": "Alıcı veya likidite aktivitesi olağan dışı hızlandı.",
        "CROSS_VENUE": "Gate ve DEX tarafında aynı anda olağan dışı hareket görüldü.",
    }.get(source, "Erken hareket izi görüldü.")
    short_contract = contract[:8] + "…" + contract[-6:]
    lines = [
        "🔎 GATE AVCI 2 | YENİ İZLEME ADAYI",
        f"🪙 {network.upper()} • {short_contract}",
    ]
    if data.get("price") is not None:
        lines.append(f"🎯 Sinyal fiyatı: ${float(data['price']):.10g}")
    if data.get("change_24h") is not None:
        lines.append(f"⏱ Hareket: 24 sa %{float(data['change_24h']):+.1f}")
    lines.extend(["", "👀 Neden geldi?", f"• {source_text}"])
    if data.get("volume_ratio") is not None:
        lines.append(f"• 5 dk hacmi kendi normalinin yaklaşık {float(data['volume_ratio']):.1f} katı.")
    buys, sells = data.get("buys_5m"), data.get("sells_5m")
    if buys is not None and sells is not None:
        lines.append(f"• Son 5 dk: {int(buys)} alış / {int(sells)} satış.")
    if data.get("liquidity") is not None:
        lines.append(f"• DEX likiditesi yaklaşık ${float(data['liquidity']):,.0f}.")
    lines.extend([
        "",
        "🛡 Güvenlik",
        "• Güvenlik güveni: ZAYIF (kanıt henüz eksik).",
        f"• Eksik/doğrulanmamış taraf: {reason}",
        "• Hard veto tespit edilmedi; edilseydi bu iz gönderilmezdi.",
        "",
        "🧭 Bu ne demek?",
        "Hareket botun dikkatini çekti ama güvenlik kanıtı temiz aday seviyesine ulaşmadı.",
        "Bu yüzden ALIM ADAYI değil; güvenlik tamamlanana kadar izleme adayıdır.",
        "",
        f"Tam kontrat: {contract}",
        "📌 Bot sadece izliyor; hesabında işlem açmıyor.",
    ])
    return "\n".join(lines)

def main(db_path=DB, session=requests):
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print("Gate eksik-veri bildirimi kapalı: Telegram token yok")
        return 0
    with sqlite3.connect(db_path, timeout=30) as con:
        batch = latest_batch(con)
        if not batch:
            print("Gate eksik-veri bildirimi: güncel geçerli tarama yok")
            return 0
        candidates = collect_soft_blocks(con, batch)
        if not candidates:
            print("Gate eksik-veri bildirimi: uygun erken iz yok")
            return 0
        chat = resolve_chat_id(token, configured, db_path, "Gate Telegram")
        sent = 0
        seen_contracts = set()
        for source, network, contract, reason, data in candidates:
            identity = (network, contract)
            if identity in seen_contracts or already_sent(con, batch, source, network, contract):
                continue
            seen_contracts.add(identity)
            message = build_message(source, network, contract, reason, data)
            if send_gate_aux(token, chat, message, network, contract, session=session):
                con.execute("""INSERT OR REPLACE INTO gate_missing_evidence_alert_audit
                    VALUES(?,?,?,?,'SENT',?,strftime('%s','now'))""",
                    (batch, source, network, contract, reason))
                con.commit()
                sent += 1
            if sent >= 3:
                break
        print(f"Gate güvenlik-bekleyen erken iz bildirimi: {sent}")
        return sent


if __name__ == "__main__":
    main()
