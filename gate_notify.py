"""Selective Gate/on-chain research alerts; never places an order."""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from gate_early_observer import early_context


OBS_DB = "avci2.db"
VALIDATION_DB = "avci_validation_v5.db"
SOL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
NETWORK_NAMES = {"solana": "Solana", "base": "Base", "bsc": "BSC",
                 "eth": "Ethereum", "arbitrum": "Arbitrum"}
RULE_NAMES = {
    "R1_WAKEUP_STRICT": "İlk olağan dışı hareket",
    "R2_REIGNITION_TRIGGER": "Hareketin yeniden hızlanması",
    "R3_PERSISTENCE_STRUCTURE": "İlginin ve fiyatın korunması",
}


def valid_contract(network, address):
    if network == "solana":
        return bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address or ""))
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", address or ""))


def security_decision(item):
    """Conservative alert gate, separate from the frozen V5 candidate rules."""
    network = item.get("network_id")
    if not valid_contract(network, item.get("token_contract")):
        return "Kontrat adresi doğrulanamadı"
    if item.get("risk_band") != "LOW_FLAGS":
        return "Risk işaretleri var veya güvenlik verisi eksik"
    if item.get("security_risk_reasons"):
        return "Güvenlik uyarıları var"
    if (item.get("climax") or {}).get("risk") or (item.get("trap_proxy") or {}).get("risk"):
        return "Hareket olgunlaşmış veya işlem örüntüsü şüpheli"

    holder = item.get("adjusted_holder") or {}
    lp = item.get("lp_protection") or {}
    if not holder.get("ok") or lp.get("status") == "DATA_MISSING":
        return "Holder veya likidite koruması doğrulanamadı"
    for field, maximum in (("top1_pct", 40), ("top5_pct", 70), ("top10_pct", 85)):
        if holder.get(field) is None or float(holder[field]) >= maximum:
            return "Düzeltilmiş holder yoğunluğu yüksek veya ölçülemedi"
    if float(lp.get("protected_pct") or 0) < 50:
        return "Likidite koruması yetersiz"

    if network == "solana":
        profile = item.get("solana_security") or {}
        if not profile.get("ok") or not profile.get("largest_accounts_ok"):
            return "Solana mint/holder verisi eksik"
        if profile.get("token_program") != SOL_TOKEN_PROGRAM:
            return "Token-2022 veya bilinmeyen program: uzantılar manuel inceleme gerektirir"
        if profile.get("mint_authority_active") is not False or (
                profile.get("freeze_authority_active") is not False):
            return "Mint veya dondurma yetkisi açık/bilinmiyor"
        for field, maximum in (("top1_pct", 40), ("top5_pct", 70), ("top10_pct", 85)):
            if profile.get(field) is None or float(profile[field]) >= maximum:
                return "Holder yoğunluğu yüksek veya ölçülemedi"
        quotes = (item.get("exit_1k") or {}, item.get("exit_5k") or {})
    else:
        sec = item.get("evm_security") or {}
        if not sec.get("ok") or not (item.get("goplus_raw") or {}).get("ok"):
            return "EVM sözleşme güvenliği doğrulanamadı"
        for field in ("is_honeypot", "hidden_owner", "can_take_back_ownership",
                      "owner_change_balance", "selfdestruct", "is_blacklisted"):
            if sec.get(field) is not False:
                return f"{field} durumu güvenle dışlanamadı"
        if sec.get("is_open_source") is not True:
            return "Açık kaynak sözleşme doğrulanamadı"
        if sec.get("is_proxy") is not False:
            return "Proxy yetkisi güvenle dışlanamadı"
        quotes = (item.get("evm_exit_1k") or {}, item.get("evm_exit_5k") or {})
    for quote, maximum in zip(quotes, (7, 12)):
        if not quote.get("ok") or quote.get("loss_pct") is None:
            return "Satış fiyatı teklifi alınamadı"
        if float(quote["loss_pct"]) > maximum:
            return "Tahmini satış kaybı yüksek"
    return None


def price(value):
    return f"{float(value):.10f}".rstrip("0").rstrip(".")


def format_alert(event, item, context):
    local = datetime.fromtimestamp(event["signal_ts"], timezone.utc)
    local = local.astimezone(ZoneInfo("Europe/Istanbul"))
    network, contract = event["network_id"], event["token_contract"]
    rules = [RULE_NAMES.get(rule, rule)
             for rule in event["rulesets"].split(",") if rule]
    lines = [
        "🔎 GATE AVCI 2 | YENİ ON-CHAIN ADAY",
        f"{local:%d.%m.%Y %H:%M} (Türkiye)",
        f"{item.get('name') or '?'} ({item.get('symbol') or '?'}) • {NETWORK_NAMES.get(network, network)}",
        f"Tam kontrat: {contract}",
        f"Neden izleniyor? {', '.join(rules)}.",
        f"Sinyal fiyatı: ${price(event['signal_price'])} (şu anki fiyat değil)",
        f"Son 24 saat hareketi: %{float(item.get('change_24h') or 0):+.1f}",
        f"Likidite: ${float(item.get('liquidity') or 0):,.0f} • Son 1 saat hacim: ${float(item.get('volume_1h') or 0):,.0f}",
        f"Son 5 dk işlem: {int(item.get('buys_5m') or 0)} alış / {int(item.get('sells_5m') or 0)} satış",
    ]
    if context["own_volume_ratio"] is not None:
        lines.append(f"Hacim kendi yakın geçmişine göre: {context['own_volume_ratio']:.1f} kat")
    if context["first_anomaly_ts"] is not None:
        delta = max(0, (event["signal_ts"] - context["first_anomaly_ts"]) // 60)
        lines.append(f"İlk kaydedilen anomali: sinyalden {delta} dk önce")
        lines.append(f"Sinyalden önceki fiyat değişimi: %{context['gain_before_signal_pct']:+.1f} "
                     "(botun başarısına dahil değil)")
    else:
        lines.append("İlk anomali: yeterli önceki gözlem yok")
    lines.extend([
        f"Tahmini satış kaybı ($1.000 / $5.000): %{float((item.get('exit_1k') if network == 'solana' else item.get('evm_exit_1k'))['loss_pct']):.1f} / "
        f"%{float((item.get('exit_5k') if network == 'solana' else item.get('evm_exit_5k'))['loss_pct']):.1f}",
        "Kontrat sayfası: https://www.geckoterminal.com/"
        f"{network}/tokens/{contract}",
        "Bu on-chain araştırma sinyalidir; Gate borsasında listelendiği anlamına gelmez.",
        "Cüzdan kümeleri ve olası yapay işlemler kesin olarak doğrulanmış değildir.",
        "Bot hesabından alım/satım yapmaz. Sonuç, sinyalden sonra ayrıca ölçülür.",
    ])
    return "\n".join(lines)


def candidate_snapshot(db, event):
    # Match the immutable validation id, not the ticker (tickers are reused).
    rows = db.execute("""SELECT raw_json FROM snapshots WHERE network_id=?
        AND lower(token_contract)=? AND zaman_utc>=?
        ORDER BY id ASC LIMIT 30""",
        (event["network_id"], event["token_contract"].lower(),
         event["signal_iso"])).fetchall()
    for row in rows:
        try:
            item = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        if item.get("validation_event_id") == event["id"]:
            return item
    return None


def pending_alerts(observation_path=OBS_DB, validation_path=VALIDATION_DB):
    """Persist decisions so a retry cannot silently relabel old candidates."""
    obs = sqlite3.connect(observation_path, timeout=30)
    obs.row_factory = sqlite3.Row
    val = sqlite3.connect(f"file:{validation_path}?mode=ro", uri=True)
    val.row_factory = sqlite3.Row
    try:
        obs.execute("""CREATE TABLE IF NOT EXISTS gate_alert_audit (
            validation_id INTEGER PRIMARY KEY, decided_at_utc TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL, message TEXT)""")
        latest = obs.execute("""SELECT batch_id, status, source_errors
            FROM gate_scan_health ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not latest:
            return []
        batch = latest["batch_id"]
        events = val.execute("""SELECT id, batch_id, network_id, token_contract,
            signal_ts, signal_iso, signal_price, rulesets FROM validation_events
            WHERE batch_id=? AND group_type='CANDIDATE' AND rulesets!=''
            ORDER BY id LIMIT 50""", (batch,)).fetchall()
        alerts = []
        for e in events:
            event = dict(e)
            previous = obs.execute("""SELECT status FROM gate_alert_audit
                WHERE validation_id=?""", (event["id"],)).fetchone()
            if previous:
                continue
            item = candidate_snapshot(obs, event)
            reason = ("Kaynak verisi eksik" if latest["status"] != "VALID"
                      else "Adayın güvenlik görüntüsü yok" if item is None
                      else security_decision(item))
            now = datetime.now(timezone.utc).isoformat()
            if reason:
                obs.execute("""INSERT OR IGNORE INTO gate_alert_audit
                    VALUES (?, ?, 'WITHHELD', ?, NULL)""", (event["id"], now, reason))
            else:
                context = early_context(obs, batch, e["network_id"],
                                        e["token_contract"], e["signal_price"])
                message = format_alert(event, item, context)
                obs.execute("""INSERT OR IGNORE INTO gate_alert_audit
                    VALUES (?, ?, 'PENDING', '', ?)""", (event["id"], now, message))
                alerts.append((event["id"], message))
        obs.commit()
        return alerts
    finally:
        val.close()
        obs.close()


def send_pending(observation_path=OBS_DB, validation_path=VALIDATION_DB,
                 session=requests):
    pending_alerts(observation_path, validation_path)
    with sqlite3.connect(observation_path) as con:
        summary = con.execute("""SELECT status, reason, COUNT(*)
            FROM gate_alert_audit GROUP BY status, reason ORDER BY status, reason""").fetchall()
        print("Gate bildirim denetimi:", summary[-15:])
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        missing = ", ".join(name for name, value in
                            (("TELEGRAM_BOT_TOKEN", token),
                             ("TELEGRAM_CHAT_ID", chat)) if not value)
        print(f"::warning::Gate Telegram bildirimleri KAPALI: {missing} "
              "GitHub Secrets içinde eksik. Onaylı adaylar kayıtlı bekler; "
              "hiçbir mesaj gönderilmedi.")
        return 0
    with sqlite3.connect(observation_path) as con:
        rows = con.execute("""SELECT validation_id, message FROM gate_alert_audit
            WHERE status='PENDING' ORDER BY validation_id LIMIT 5""").fetchall()
        sent = 0
        for event_id, message in rows:
            try:
                r = session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat, "text": message[:4096]},
                                 timeout=20)
                r.raise_for_status()
                if not r.json().get("ok"):
                    raise RuntimeError("Telegram API gönderimi onaylamadı")
                con.execute("""UPDATE gate_alert_audit SET status='SENT',
                    decided_at_utc=? WHERE validation_id=?""",
                    (datetime.now(timezone.utc).isoformat(), event_id))
                con.commit()
                sent += 1
            except Exception as exc:
                print(f"Gate Telegram gönderilemedi, kayıt beklemede: {type(exc).__name__}")
                break
        return sent


if __name__ == "__main__":
    print(f"Gate Telegram gönderilen aday: {send_pending()}")
