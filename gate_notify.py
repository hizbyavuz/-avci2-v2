"""Selective Gate/on-chain research alerts; never places an order."""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from telegram_readable import (
    gecko_token, fmt_price, pct, record_initial, send_photo_or_text,
    due_followups, mark_followup,
)

from binance_notify import find_chat_id, resolve_chat_id
from gate_early_observer import candidate_risk_context, early_context
from gate_security_confidence import one_line as security_confidence_line


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


def observation_only_reason(reason):
    """Allow research-only alerts for missing evidence, never for hard risk."""
    raw = str(reason or "").strip()
    if not raw:
        return False
    # Keep the generic mixed verdict blocked. It contains no specific
    # missing-only cause and must never be promoted to a research alert.
    if raw in (
        "Risk işaretleri var veya güvenlik verisi eksik",
        "RISK ISARETLERI VAR VEYA GUVENLIK VERISI EKSIK",
    ):
        return False
    text = raw.upper()
    if "RİSK İŞARETLERİ VAR VEYA GÜVENLİK VERİSİ EKSİK:" in text:
        text = text.split(":", 1)[1].strip()
    elif "RISK ISARETLERI VAR VEYA GUVENLIK VERISI EKSIK:" in text:
        text = text.split(":", 1)[1].strip()
    elif text in (
        "RİSK İŞARETLERİ VAR VEYA GÜVENLİK VERİSİ EKSİK",
        "RISK ISARETLERI VAR VEYA GUVENLIK VERISI EKSIK",
    ):
        return False
    hard = (
        "LOSS_HIGH", "LP_LOW_PROTECTION", "BUNDLE_SNIPER", "HONEYPOT",
        "BLACKLIST", "MINT ", "FREEZE", "DONDURMA", "HOLDER YOĞUN",
        "HOLDER YOGUN", "WASH", "KARŞILIKLI İŞLEM", "KARSILIKLI ISLEM",
        "CREATOR", "KÖTÜ NİYET", "KOTU NIYET", "PROXY YETK",
        "SELFDESTRUCT", "OWNER_CHANGE", "TAKE_BACK", "CLIMAX",
        "ŞÜPHELİ", "SUPHELI",
    )
    if any(token in text for token in hard):
        return False
    missing = (
        "DATA_MISSING", "MISSING", "EKSİK", "EKSIK", "DOĞRULANAMADI",
        "DOGRULANAMADI", "ALINAMADI", "UNKNOWN", "BİLİNMİYOR", "BILINMIYOR",
        "TAMAMLANAMADI", "ÖLÇÜLEMEDİ", "OLCULEMEDI",
    )
    return any(token in text for token in missing)


def format_observation_alert(kind, item, network, contract, signal_price, reason, detail=""):
    """Readable research-only alert for strong movement with incomplete safety evidence."""
    name = item.get("name") or item.get("symbol") or "?"
    lines = [
        "🟡 GATE AVCI 2 | ERKEN İZ — GÜVENLİK BEKLİYOR",
        f"🪙 {name} • {NETWORK_NAMES.get(network, network)}",
        f"🎯 İz fiyatı: USD {price(signal_price)}",
    ]
    if detail:
        lines.append(f"👀 {detail}")
    lines.extend([
        "",
        "🛡 Neden temiz aday değil?",
        f"• {reason}",
        "",
        "Güvenlik güveni: ZAYIF (kanıt eksik).",
        "Bu coinde hareket izi var ama güvenlik doğrulaması tamamlanmadı.",
        "Temiz aday değildir; bot yalnızca araştırma için izliyor.",
        f"Tam kontrat: {contract}",
    ])
    return "\n".join(lines)


def security_decision(item):
    """Conservative alert gate, separate from the frozen V5 candidate rules."""
    network = item.get("network_id")
    if not valid_contract(network, item.get("token_contract")):
        return "Kontrat adresi doğrulanamadı"
    if item.get("risk_band") != "LOW_FLAGS":
        flags = item.get("security_risk_reasons") or []
        return ("Risk işaretleri var veya güvenlik verisi eksik: "
                + ", ".join(str(flag) for flag in flags[:4])) if flags else (
                "Risk işaretleri var veya güvenlik verisi eksik")
    if item.get("security_risk_reasons"):
        return "Güvenlik uyarıları var"
    if (item.get("climax") or {}).get("risk") or (item.get("trap_proxy") or {}).get("risk"):
        return "Hareket olgunlaşmış veya işlem örüntüsü şüpheli"

    holder = item.get("adjusted_holder") or {}
    lp = item.get("lp_protection") or {}
    if not holder.get("ok") or lp.get("status") in (
            "DATA_MISSING", "MULTIPLE_POOLS_UNVERIFIED"):
        return "Holder veya likidite koruması doğrulanamadı"
    for field, maximum in (("top1_pct", 40), ("top5_pct", 70), ("top10_pct", 85)):
        if holder.get(field) is None or float(holder[field]) >= maximum:
            return "Düzeltilmiş holder yoğunluğu yüksek veya ölçülemedi"
    if float(lp.get("protected_pct") or 0) < 50:
        return "Likidite koruması yetersiz"
    if "EXPIRES_SOON" in (lp.get("lock_expiry_statuses") or []):
        return "LP kilidi 24 saat içinde açılabilir"
    if (lp.get("creator_unlocked_pct") is not None and
            float(lp["creator_unlocked_pct"]) >= 10):
        return "Deployer cüzdanında kilitsiz LP payı yüksek"
    if (item.get("creator_reputation") or {}).get("status") == "FLAGGED":
        return "GoPlus creator adresinde kötü niyet kaydı buldu"
    if (item.get("trade_cluster") or {}).get("wash_proxy") is True:
        return "Aynı blokta benzer tutarlı karşılıklı işlemler görüldü"

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


def format_alert(event, item, context, risk_context=None):
    local=datetime.fromtimestamp(event["signal_ts"], timezone.utc).astimezone(ZoneInfo("Europe/Istanbul"))
    network,contract=event["network_id"],event["token_contract"]
    rules=[RULE_NAMES.get(rule,rule) for rule in event["rulesets"].split(",") if rule]
    lp=item.get("lp_protection") or {}
    holder=item.get("adjusted_holder") or {}
    q1=(item.get("exit_1k") if network=="solana" else item.get("evm_exit_1k")) or {}
    q5=(item.get("exit_5k") if network=="solana" else item.get("evm_exit_5k")) or {}
    buys=int(item.get("buys_5m") or 0); sells=int(item.get("sells_5m") or 0)
    c24=float(item.get("change_24h") or 0)

    lines=[
        "🔎 GATE AVCI 2 | YENİ ADAY",
        f"🪙 {item.get('name') or '?'} ({item.get('symbol') or '?'}) • {NETWORK_NAMES.get(network,network)}",
        f"🕒 {local:%d.%m.%Y %H:%M}",
        f"🎯 Sinyal geldiğinde: ${price(event['signal_price'])}",
        f"⏱ Son 24 saat: %{c24:+.1f}",
        "",
        "👀 Neden geldi?",
    ]
    for rule in rules:
        lines.append(f"• {rule}.")
    if context.get("own_volume_ratio") is not None:
        lines.append(f"• Hacim kendi normalinin yaklaşık {context['own_volume_ratio']:.1f} katına çıktı.")
    if buys or sells:
        if buys > sells*1.2:
            lines.append(f"• Son 5 dk alımlar satışlardan belirgin fazla: {buys} alış / {sells} satış.")
        else:
            lines.append(f"• Son 5 dk işlem dengesi: {buys} alış / {sells} satış.")
    if context.get("first_anomaly_ts") is not None:
        delta=max(0,(event["signal_ts"]-context["first_anomaly_ts"])//60)
        lines.append(f"• Bot ilk sıra dışı hareketi sinyalden {delta} dk önce fark etmiş.")
        if context.get("gain_before_signal_pct") is not None:
            lines.append(f"• Sinyal gelmeden önce fiyat zaten %{context['gain_before_signal_pct']:+.1f} hareket etmişti.")

    lines.extend(["","🛡 Güvenlik"])
    lines.append(f"• {security_confidence_line(item)}")
    lines.append(
        f"• Likiditenin korunan kısmı yaklaşık %{float(lp.get('protected_pct') or 0):.0f}."
    )
    lines.append(
        f"• En büyük 10 cüzdan toplamda yaklaşık %{float(holder.get('top10_pct') or 0):.0f} tutuyor."
    )
    if q1.get("loss_pct") is not None and q5.get("loss_pct") is not None:
        lines.append(
            f"• Satış testi: $1.000 işlemde ~%{float(q1['loss_pct']):.1f}, "
            f"$5.000 işlemde ~%{float(q5['loss_pct']):.1f} fiyat kaybı."
        )

    lines.extend(["","🧭 Bu ne demek?"])
    if c24>=20:
        lines.append("Coin son 24 saatte çok hareket etmiş; geç kalma riski yüksek.")
    elif c24>=10:
        lines.append("Coin hareket etmiş durumda; bot devam edip etmediğini izliyor.")
    else:
        lines.append("Coin henüz 24 saatlik ölçekte aşırı kaçmış görünmüyor.")
    lines.extend([
        "Güvenlik kontrollerinden geçmiş olması risksiz olduğu anlamına gelmez.",
        "Bu coin temiz araştırma adayıdır; kesin alım sinyali değildir.",
        f"Tam kontrat: {contract}",
        "📌 Bot sadece izliyor; hesabında işlem açmıyor.",
    ])
    return "\n".join(lines)

def gate_send_payload(token, chat, message, network, contract, signal_price=None,
                      session=requests):
    live=gecko_token(network, contract, session=session)
    current=live.get("price")
    # Always refresh volatile price lines at the actual send moment.
    kept=[line for line in message.splitlines()
          if not line.startswith("Şu anki fiyat:")
          and not line.startswith("Sinyalden beri:")]
    if current is not None:
        kept.insert(3, f"Şu anki fiyat: ${fmt_price(current)}")
    else:
        kept.insert(3, "Şu anki fiyat alınamadı")
    change=pct(current, signal_price)
    if change is not None:
        kept.insert(5, f"Sinyalden beri: %{abs(change):.2f} " +
                    ("yukarıda" if change>=0 else "aşağıda"))
    message="\n".join(kept)
    ok=send_photo_or_text(token, chat, message, None, session=session)
    return ok, current, live.get("logo")

def send_gate_aux(token, chat, message, network, contract, session=requests):
    live=gecko_token(network, contract, session=session)
    current=live.get("price")
    if current is not None and "Şu anki fiyat:" not in message:
        message += f"\nŞu anki fiyat: ${fmt_price(current)}"
    return send_photo_or_text(token, chat, message, None, session=session)


def send_gate_followups(token, chat, con, session=requests):
    def getter(_key, symbol):
        try:
            network, contract=symbol.split("|",1)
        except ValueError:
            return None
        return gecko_token(network, contract, session=session).get("price")
    rows=due_followups(con,"gate_telegram_price_history",getter,min_pp=3.0)
    sent=0
    for row in rows[:5]:
        direction="yukarıda" if row["change"]>=0 else "aşağıda"
        last_dir="yükseldi" if (row["since_last"] or 0)>=0 else "düştü"
        text=(f"📊 GATE AVCI 2 | TAKİP\n{row['symbol']}\n"
              f"Şu an: ${fmt_price(row['current'])}\n"
              f"Sinyalden beri: %{abs(row['change']):.2f} {direction}\n"
              f"Önceki bildirime göre: %{abs(row['since_last'] or 0):.2f} {last_dir}\n"
              "Bot sonucu izlemeye devam ediyor; bu bir işlem talimatı değil.")
        if send_photo_or_text(token,chat,text,None,session=session):
            mark_followup(con,"gate_telegram_price_history",row["key"],
                          row["current"],row["change"])
            sent+=1
    con.commit()
    return sent


def candidate_snapshot(db, event):
    # Match the immutable validation id, not the ticker (tickers are reused).
    contract = event["token_contract"]
    rows = db.execute("""SELECT raw_json FROM snapshots WHERE network_id=?
        AND token_contract=? COLLATE NOCASE AND zaman_utc>=?
        ORDER BY id ASC LIMIT 30""",
        (event["network_id"], contract,
         event["signal_iso"])).fetchall()
    if event["network_id"] == "solana":
        rows = db.execute("""SELECT raw_json FROM snapshots WHERE network_id=?
            AND token_contract=? AND zaman_utc>=?
            ORDER BY id ASC LIMIT 30""",
            (event["network_id"], contract, event["signal_iso"])).fetchall()
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
        events = val.execute("""SELECT id, batch_id, group_type, network_id, token_contract,
            signal_ts, signal_iso, signal_price, rulesets FROM validation_events
            WHERE batch_id=? AND group_type IN ('CANDIDATE', 'EXPANDED_CANDIDATE')
                AND rulesets!=''
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
                risk_context = candidate_risk_context(
                    obs, batch, e["network_id"], e["token_contract"])
                message = format_alert(event, item, context, risk_context)
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
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print("::warning::Gate Telegram bildirimleri KAPALI: "
              "TELEGRAM_BOT_TOKEN eksik. Onaylı adaylar kayıtlı bekler.")
        return 0
    try:
        chat = resolve_chat_id(token, chat, observation_path, "Gate Telegram")
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"::warning::Gate Telegram sohbeti bulunamadı "
              f"({type(exc).__name__}); onaylı adaylar kayıtlı bekler. "
              "Botta bir kez /start veya mesaj gelince ID kalıcı saklanacak.")
        return 0
    with sqlite3.connect(observation_path) as con:
        rows = con.execute("""SELECT validation_id, message FROM gate_alert_audit
            WHERE status='PENDING' ORDER BY validation_id LIMIT 5""").fetchall()
        sent = 0
        with sqlite3.connect(f"file:{validation_path}?mode=ro", uri=True) as val:
            val.row_factory=sqlite3.Row
            for event_id, message in rows:
                try:
                    ev=val.execute("""SELECT network_id,token_contract,signal_price
                        FROM validation_events WHERE id=?""",(event_id,)).fetchone()
                    if not ev:
                        # Backward-compatible pending rows from older state/test fixtures:
                        # send the stored safe text, but do not invent price/logo history.
                        ok=send_photo_or_text(token,chat,message,None,session=session)
                        if not ok:
                            raise RuntimeError("Telegram API gönderimi onaylamadı")
                        con.execute("""UPDATE gate_alert_audit SET status='SENT',
                            decided_at_utc=? WHERE validation_id=?""",
                            (datetime.now(timezone.utc).isoformat(), event_id))
                        con.commit()
                        sent += 1
                        continue
                    ok,current,logo=gate_send_payload(
                        token,chat,message,ev["network_id"],ev["token_contract"],
                        ev["signal_price"],session=session)
                    if not ok:
                        raise RuntimeError("Telegram API gönderimi onaylamadı")
                    con.execute("""UPDATE gate_alert_audit SET status='SENT',
                        decided_at_utc=? WHERE validation_id=?""",
                        (datetime.now(timezone.utc).isoformat(), event_id))
                    record_initial(con,"gate_telegram_price_history",event_id,
                        f"{ev['network_id']}|{ev['token_contract']}",
                        ev["signal_price"],current,logo)
                    con.commit()
                    sent += 1
                except Exception as exc:
                    print(f"Gate Telegram gönderilemedi, kayıt beklemede: {type(exc).__name__}")
                    break
        # The separate Gate Spot stream has its own audit and never enters V5.
        if con.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
            AND name='gate_spot_bridge_audit'""").fetchone():
            bridge = con.execute("""SELECT a.watch_batch, a.network_id,
                a.token_contract, a.message, h.scan_ts
                FROM gate_spot_bridge_audit a JOIN gate_spot_health h
                  ON h.batch_id=a.watch_batch
                WHERE a.status='PENDING' ORDER BY a.decided_at LIMIT 3""").fetchall()
            for batch, network, contract, message, signal_ts in bridge:
                if datetime.now(timezone.utc).timestamp() - signal_ts > 20 * 60:
                    con.execute("""UPDATE gate_spot_bridge_audit
                        SET status='EXPIRED', reason='Sinyal 20 dakikayı geçti'
                        WHERE watch_batch=? AND network_id=? AND token_contract=?""",
                        (batch, network, contract))
                    con.commit()
                    continue
                try:
                    if not send_gate_aux(token,chat,message,network,contract,session=session):
                        raise RuntimeError("Telegram API gönderimi onaylamadı")
                    con.execute("""UPDATE gate_spot_bridge_audit
                        SET status='SENT', decided_at=strftime('%s','now')
                        WHERE watch_batch=? AND network_id=? AND token_contract=?""",
                        (batch, network, contract))
                    con.commit()
                    sent += 1
                except Exception as exc:
                    print("Gate Spot Telegram gönderilemedi, kayıt beklemede:",
                          type(exc).__name__)
                    break
        if con.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
            AND name='gate_volume_alert_audit'""").fetchone():
            volume = con.execute("""SELECT a.batch_id,a.network_id,
                a.token_contract,a.message,h.scan_ts
                FROM gate_volume_alert_audit a JOIN gate_scan_health h
                  ON h.batch_id=a.batch_id
                WHERE a.status='PENDING' ORDER BY a.decided_at LIMIT 2""").fetchall()
            for batch, network, contract, message, signal_ts in volume:
                if datetime.now(timezone.utc).timestamp() - signal_ts > 20*60:
                    con.execute("""UPDATE gate_volume_alert_audit
                        SET status='EXPIRED',reason='Sinyal 20 dakikayı geçti'
                        WHERE batch_id=? AND network_id=? AND token_contract=?""",
                        (batch, network, contract))
                    con.commit()
                    continue
                try:
                    if not send_gate_aux(token,chat,message,network,contract,session=session):
                        raise RuntimeError("Telegram API gönderimi onaylamadı")
                    con.execute("""UPDATE gate_volume_alert_audit
                        SET status='SENT',decided_at=strftime('%s','now')
                        WHERE batch_id=? AND network_id=? AND token_contract=?""",
                        (batch, network, contract))
                    con.commit()
                    sent += 1
                except Exception as exc:
                    print("Hacim uyanışı Telegram gönderilemedi:", type(exc).__name__)
                    break

        # Buyer acceleration and liquidity expansion share the same safety gate.
        if con.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
            AND name='gate_activity_alert_audit'""").fetchone():
            rows = con.execute("""SELECT a.batch_id,a.network_id,a.token_contract,
                a.engine,a.message,h.scan_ts FROM gate_activity_alert_audit a
                JOIN gate_scan_health h ON h.batch_id=a.batch_id
                WHERE a.status='PENDING' ORDER BY a.decided_at LIMIT 4""").fetchall()
            for batch, network, contract, engine, message, signal_ts in rows:
                if datetime.now(timezone.utc).timestamp() - signal_ts > 20*60:
                    con.execute("""UPDATE gate_activity_alert_audit
                        SET status='EXPIRED',reason='Sinyal 20 dakikayı geçti'
                        WHERE batch_id=? AND network_id=? AND token_contract=?
                          AND engine=?""", (batch,network,contract,engine))
                    con.commit()
                    continue
                try:
                    if not send_gate_aux(token,chat,message,network,contract,session=session):
                        raise RuntimeError("Telegram API gönderimi onaylamadı")
                    con.execute("""UPDATE gate_activity_alert_audit
                        SET status='SENT',decided_at=strftime('%s','now')
                        WHERE batch_id=? AND network_id=? AND token_contract=?
                          AND engine=?""", (batch,network,contract,engine))
                    con.commit()
                    sent += 1
                except Exception as exc:
                    print("Erken aktivite Telegram gönderilemedi:", type(exc).__name__)
                    break

        if con.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
            AND name='gate_cross_venue_alert_audit'""").fetchone():
            rows = con.execute("""SELECT a.batch_id,a.pair,a.network_id,
                a.token_contract,a.message,h.scan_ts FROM gate_cross_venue_alert_audit a
                JOIN gate_scan_health h ON h.batch_id=a.batch_id
                WHERE a.status='PENDING' ORDER BY a.decided_at LIMIT 3""").fetchall()
            for batch,pair,network,contract,message,signal_ts in rows:
                if datetime.now(timezone.utc).timestamp()-signal_ts > 20*60:
                    con.execute("""UPDATE gate_cross_venue_alert_audit
                        SET status='EXPIRED',reason='Sinyal 20 dakikayı geçti'
                        WHERE batch_id=? AND pair=? AND network_id=? AND token_contract=?""",
                        (batch,pair,network,contract))
                    con.commit()
                    continue
                try:
                    if not send_gate_aux(token,chat,message,network,contract,session=session):
                        raise RuntimeError("Telegram API gönderimi onaylamadı")
                    con.execute("""UPDATE gate_cross_venue_alert_audit
                        SET status='SENT',decided_at=strftime('%s','now')
                        WHERE batch_id=? AND pair=? AND network_id=? AND token_contract=?""",
                        (batch,pair,network,contract))
                    con.commit()
                    sent += 1
                except Exception as exc:
                    print("Cross-venue Telegram gönderilemedi:", type(exc).__name__)
                    break
        followups=send_gate_followups(token,chat,con,session=session)
        if followups:
            print(f"Gate takip bildirimi: {followups}")
        return sent + followups


if __name__ == "__main__":
    print(f"Gate Telegram gönderilen aday: {send_pending()}")
