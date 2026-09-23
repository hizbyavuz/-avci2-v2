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
            if observation_only_reason(reason):
                rows.append((source, network, contract, reason))
    return rows


def already_sent(con, batch, source, network, contract):
    con.execute("""CREATE TABLE IF NOT EXISTS gate_missing_evidence_alert_audit(
        batch_id TEXT NOT NULL,source TEXT NOT NULL,network_id TEXT NOT NULL,
        token_contract TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,
        decided_at INTEGER NOT NULL,PRIMARY KEY(batch_id,source,network_id,token_contract))""")
    return con.execute("""SELECT 1 FROM gate_missing_evidence_alert_audit
        WHERE batch_id=? AND source=? AND network_id=? AND token_contract=?""",
        (batch, source, network, contract)).fetchone() is not None


def build_message(source, network, contract, reason):
    source_text = {
        "VOLUME": "Hacim kendi geçmişine göre olağan dışı hızlandı.",
        "ACTIVITY": "Alıcı veya likidite aktivitesi olağan dışı hızlandı.",
        "CROSS_VENUE": "Gate ve DEX tarafında aynı anda olağan dışı hareket görüldü.",
    }.get(source, "Erken hareket izi görüldü.")
    return "\n".join([
        "🟡 GATE AVCI 2 | ERKEN İZ — GÜVENLİK BEKLİYOR",
        f"👀 {source_text}",
        f"Ağ: {network}",
        "",
        "🛡 Neden temiz aday değil?",
        f"• {reason}",
        "",
        "Bu bir temiz aday değildir. Güvenlik verisi tamamlanana kadar sadece izlenir.",
        f"Tam kontrat: {contract}",
    ])


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
        for source, network, contract, reason in candidates:
            identity = (network, contract)
            if identity in seen_contracts or already_sent(con, batch, source, network, contract):
                continue
            seen_contracts.add(identity)
            message = build_message(source, network, contract, reason)
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
