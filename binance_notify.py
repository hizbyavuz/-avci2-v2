#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


DB_FILE = "binance_avci2.db"
TELEGRAM_LIMIT = 4096

# Only show names whose symbol-to-asset mapping has been checked. A ticker
# alone is not enough to guess a project's name (several coins share tickers).
VERIFIED_COIN_NAMES = {
    "TIA": "Celestia",
    "ATOM": "Cosmos",
    "APT": "Aptos",
    "BCH": "Bitcoin Cash",
    "FLOKI": "Floki",
    "HBAR": "Hedera",
    "LTC": "Litecoin",
    "PENGU": "Pudgy Penguins",
    "QNT": "Quant",
    "VIRTUAL": "Virtuals Protocol",
}


def candidate_identity(symbol):
    """Show the exact Binance Spot market even when no name is verified."""
    if not symbol.endswith("USDT"):
        raise ValueError(f"Expected a USDT Spot pair: {symbol}")
    base = symbol[:-4]
    name = VERIFIED_COIN_NAMES.get(base)
    title = f"{name} ({base})" if name else base
    return (f"{title} | Spot {base}/USDT",
            f"https://www.binance.com/en/trade/{base}_USDT?type=spot")


STAGE_NAMES = {
    "TRIGGER": "TETİK",
    "REIGNITION": "YENİDEN CANLANMA",
    "CONTINUATION": "DEVAM",
    "WAKE_UP": "UYANIŞ",
    "OBSERVE": "GÖZLEM",
}


ENGINE_NAMES = {
    "SPOT_LED_DEMAND": "SPOT TALEBİ",
    "LEVERAGED_BREAKOUT": "KALDIRAÇLI KIRILIM",
    "SHORT_SQUEEZE": "SHORT SIKIŞMASI",
    "LIQUIDITY_VACUUM": "LİKİDİTE BOŞLUĞU",
    "MIXED": "KARMA",
}


REGIME_NAMES = {
    "UP": "YÜKSELİŞ",
    "DOWN": "DÜŞÜŞ",
    "SIDEWAYS": "YATAY",
}


HEALTH_NAMES = {
    "VALID_FULL": "GEÇERLİ - TAM VERİ",
    "VALID_SPOT_OBSERVATION": "GEÇERLİ - SADECE SPOT",
    "INVALID": "GEÇERSİZ",
}


VALIDATION_NAMES = {
    "PRIMARY": "ANA DOĞRULAMA",
    "OBSERVATIONAL": "GÖZLEMSEL",
    "LEGACY": "ESKİ KAYIT",
}


CLASS_NAMES = {
    "KAZANANA_BENZER":
        "KAZANANA BENZER",

    "KONTROLE_BENZER":
        "BAŞARISIZ KONTROLE BENZER",

    "KARMA":
        "KARIŞIK",

    "REFERANS_DISI":
        "TARİHSEL REFERANS DIŞI",

    "YETERSIZ_VERI":
        "YETERSİZ VERİ",
}


def find_chat_id(token):
    response = requests.get(
        (
            "https://api.telegram.org/"
            f"bot{token}/getUpdates"
        ),
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        description = data.get(
            "description",
            "Bilinmeyen Telegram hatası",
        )

        raise RuntimeError(
            f"Telegram hatası: {description}"
        )

    private_chat_ids = []

    for update in data.get(
        "result",
        [],
    ):
        message_data = (
            update.get("message")
            or update.get("edited_message")
            or {}
        )

        chat = message_data.get(
            "chat",
            {},
        )

        if (
            chat.get("type") == "private"
            and chat.get("id") is not None
        ):
            private_chat_ids.append(
                str(chat["id"])
            )

    if not private_chat_ids:
        raise RuntimeError(
            "Telegram Chat ID bulunamadı. "
            "Binance Avcı 2 botuna yeni bir "
            "mesaj gönderip tekrar çalıştır."
        )

    return private_chat_ids[-1]


def get_telegram_settings():
    token = os.environ.get(
        "TELEGRAM_BOT_TOKEN"
    )

    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN eksik"
        )

    token = token.strip()

    if ":" not in token:
        raise RuntimeError(
            "Telegram token hatalı. "
            "Tokenin sayılar, iki nokta ve "
            "devamındaki yazı dahil tamamı "
            "GitHub secret içinde olmalı."
        )

    chat_id = os.environ.get(
        "TELEGRAM_CHAT_ID"
    )

    if chat_id:
        chat_id = chat_id.strip()

    if not chat_id:
        chat_id = find_chat_id(
            token
        )

        print(
            "Telegram Chat ID "
            "otomatik bulundu"
        )

    return token, chat_id


def send_telegram(
    token,
    chat_id,
    message,
):
    response = requests.post(
        (
            "https://api.telegram.org/"
            f"bot{token}/sendMessage"
        ),
        json={
            "chat_id": chat_id,
            "text": message[
                :TELEGRAM_LIMIT
            ],
            "disable_web_page_preview": True,
        },
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        description = data.get(
            "description",
            "Bilinmeyen Telegram hatası",
        )

        raise RuntimeError(
            "Telegram mesajı gönderilemedi: "
            f"{description}"
        )


def read_latest_scan(connection):
    row = connection.execute(
        """
        SELECT *
        FROM scans
        ORDER BY scan_time_utc DESC
        LIMIT 1
        """
    ).fetchone()

    return (
        dict(row)
        if row is not None
        else None
    )


def read_new_candidates(
    connection,
    scan_time,
):
    rows = connection.execute(
        """
        SELECT
            symbol,
            stage,
            engine,
            score,
            validation_tier,
            config_version
        FROM signal_events
        WHERE event_class = 'CANDIDATE'
          AND signal_time_utc = ?
        ORDER BY
            score DESC,
            symbol ASC
        """,
        (scan_time,),
    ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def read_trade_alerts(connection, scan_time):
    try:
        rows = connection.execute("""SELECT symbol, alert_kind, price, reason
            FROM trade_alerts WHERE scan_time_utc=? ORDER BY id""",
                                  (scan_time,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def format_paper_price(price):
    return f"{float(price):.10f}".rstrip("0").rstrip(".")


def format_paper_alerts(alerts):
    """Make paper observations readable without suggesting real orders."""
    groups = {"profit": [], "loss": [], "watch": [], "entry": []}
    for alert in alerts:
        symbol = alert["symbol"]
        price = format_paper_price(alert["price"])
        reason = alert["reason"]
        if alert["alert_kind"] == "PAPER_ENTRY":
            key, detail = "entry", "Deneme alımı için şartlar oluştu"
        elif "%10 yukarıda" in reason:
            key, detail = "profit", "Deneme girişinden en az %10 yukarıda"
        elif "%7 aşağıda" in reason:
            key, detail = "loss", "Deneme girişinden en az %7 aşağıda"
        elif "%1,5 altına" in reason:
            key, detail = "watch", "İşaretlendiği fiyattan %1,5 geriledi"
        else:
            key, detail = "watch", reason
        groups[key].append(f"• {symbol}: {price} USDT — {detail}")

    lines = ["📋 AVCI - DENEME İŞLEMLERİNİN DURUMU",
             "Bunlar geçmiş sinyallerin kağıt üzerindeki takibidir.",
             "Hesabından alım veya satım yapılmadı.",
             "Bugünün adaylarının 24 saatlik toplu raporu ayrı gelir."]
    for key, title in (("profit", "Kâr seviyesine gelenler"),
                       ("loss", "Zarar sınırına gelenler"),
                       ("entry", "Yeni deneme alımları"),
                       ("watch", "İzleme uyarıları")):
        items = groups[key]
        if not items:
            continue
        lines.extend(["", f"{title} ({len(items)}):", *items[:5]])
        if len(items) > 5:
            lines.append(f"• Ayrıca {len(items) - 5} kayıt daha var.")
    return "\n".join(lines)


def read_bridge_scores(
    connection,
    scan_time,
    config_version,
):
    try:
        rows = connection.execute(
            """
            SELECT *
            FROM winner_bridge_scores
            WHERE scan_time_utc = ?
              AND config_version = ?
            ORDER BY
                created_at_utc DESC,
                id DESC
            """,
            (
                scan_time,
                config_version,
            ),
        ).fetchall()

    except sqlite3.OperationalError as error:
        if (
            "no such table"
            in str(error).lower()
        ):
            return {}

        raise

    results = {}

    for row in rows:
        item = dict(row)

        symbol = item[
            "symbol"
        ]

        if symbol not in results:
            results[
                symbol
            ] = item

    return results


def parse_feature_labels(
    raw_text,
    positive,
):
    if not raw_text:
        return []

    try:
        rows = json.loads(
            raw_text
        )
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        return []

    labels = []

    for row in rows:
        edge = row.get(
            "winner_edge"
        )

        label = row.get(
            "label"
        )

        if (
            edge is None
            or not label
        ):
            continue

        edge = float(
            edge
        )

        if positive and edge > 0:
            labels.append(
                label
            )

        if (
            not positive
            and edge < 0
        ):
            labels.append(
                label
            )

        if len(labels) >= 2:
            break

    return labels


def add_bridge_lines(
    lines,
    bridge,
):
    if not bridge:
        lines.extend([
            "Tarihsel karşılaştırma: VERİ YOK",
            "",
        ])
        return

    classification = bridge.get(
        "classification",
        "YETERSIZ_VERI",
    )

    classification_text = (
        CLASS_NAMES.get(
            classification,
            classification,
        )
    )

    similarity = bridge.get(
        "winner_similarity_pct"
    )

    reference_fit = bridge.get(
        "reference_fit_pct"
    )

    offset = bridge.get(
        "best_offset_hours"
    )

    winner_count = int(
        bridge.get(
            "winner_sample_count"
        )
        or 0
    )

    control_count = int(
        bridge.get(
            "control_sample_count"
        )
        or 0
    )

    feature_count = int(
        bridge.get(
            "compared_feature_count"
        )
        or 0
    )

    lines.append(
        "Winner Anatomy karşılaştırması:"
    )

    lines.append(
        f"Sınıf: {classification_text}"
    )

    if similarity is not None:
        lines.append(
            "Geçmiş kazanan benzerliği: "
            f"%{float(similarity):.1f}"
        )

    if reference_fit is not None:
        lines.append(
            "Tarihsel profile uyum: "
            f"%{float(reference_fit):.1f}"
        )

    if offset is not None:
        lines.append(
            "En yakın geçmiş pencere: "
            f"hareketten {int(offset)} saat önce"
        )

    lines.append(
        "Karşılaştırılan örnek: "
        f"{winner_count} kazanan / "
        f"{control_count} kontrol"
    )

    lines.append(
        "Karşılaştırılan özellik: "
        f"{feature_count}"
    )

    strong_labels = parse_feature_labels(
        bridge.get(
            "strong_features_json"
        ),
        positive=True,
    )

    weak_labels = parse_feature_labels(
        bridge.get(
            "weak_features_json"
        ),
        positive=False,
    )

    if strong_labels:
        lines.append(
            "Kazanana benzeyen taraf: "
            + ", ".join(
                strong_labels
            )
        )

    if weak_labels:
        lines.append(
            "Kontrole benzeyen taraf: "
            + ", ".join(
                weak_labels
            )
        )

    lines.append("")


def build_message(
    scan,
    candidates,
    bridge_scores,
):
    scan_time = scan[
        "scan_time_utc"
    ]

    health = scan[
        "health_status"
    ]

    regime = scan[
        "btc_regime"
    ]

    universe_size = scan[
        "universe_size"
    ]

    health_text = HEALTH_NAMES.get(
        health,
        health,
    )

    regime_text = REGIME_NAMES.get(
        regime,
        regime,
    )

    lines = [
        "🚨 BINANCE AVCI 2 - YENİ ADAY",
        "",
        f"Tarama zamanı: {scan_time}",
        f"Sürüm: {scan['config_version']} | ayar: {(scan['config_hash'] or '-')[:12]} | kod: {(scan['git_sha'] or '-')[:12]}",
        f"Veri sağlığı: {health_text}",
        f"BTC rejimi: {regime_text}",
        f"Tarama evreni: {universe_size} coin",
        f"Yeni temiz aday: {len(candidates)}",
        "",
    ]

    if health == "VALID_SPOT_OBSERVATION":
        lines.extend([
            "⚠️ Bu taramada futures verisi yok.",
            "OI ve fonlama doğrulaması yapılamadı.",
            "",
        ])

    if scan.get("btc_flash_crash"):
        lines.extend(["⚠️ BTC son 15 dakikada sert düştü (gözlem etiketi).",
                      "Bu taramadaki sinyaller ayrıca değerlendirilmeli.", ""])
    if len(candidates) > 1:
        lines.extend([f"⚠️ Aynı taramada {len(candidates)} aday: sonuçlar bağımsız sayılmayacak.", ""])

    for index, candidate in enumerate(
        candidates,
        start=1,
    ):
        stage = candidate[
            "stage"
        ]

        stage_text = STAGE_NAMES.get(
            stage,
            stage,
        )

        engine = candidate.get(
            "engine"
        )

        engine_text = ENGINE_NAMES.get(
            engine,
            engine or "BİLİNMİYOR",
        )

        score = candidate[
            "score"
        ]

        validation = candidate[
            "validation_tier"
        ]

        validation_text = (
            VALIDATION_NAMES.get(
                validation,
                validation,
            )
        )

        identity, market_url = candidate_identity(candidate["symbol"])
        lines.extend([
            f"{index}. {identity}",
            f"Binance Spot: {market_url}",
            f"Aşama: {stage_text}",
            f"Hareket tipi: {engine_text}",
            f"Puan: {score}/8",
            f"Doğrulama: {validation_text}",
        ])

        bridge = bridge_scores.get(
            candidate[
                "symbol"
            ]
        )

        add_bridge_lines(
            lines,
            bridge,
        )

    lines.extend([
        "Açıklama:",
        "UYANIŞ = İlk olağan dışı hareket.",
        "DEVAM = Hacim ve ilgi sürüyor.",
        "YENİDEN CANLANMA = Hareket tekrar hızlanıyor.",
        "TETİK = Birden fazla şart aynı anda oluştu.",
        "",
        "Geçmiş kazanan benzerliği başarı ihtimali değildir.",
        "Bu bildirim otomatik alım emri değildir.",
        "İlk aşama paper-trade ve araştırma amaçlıdır.",
    ])

    return "\n".join(lines)


def main():
    if not os.path.exists(
        DB_FILE
    ):
        raise FileNotFoundError(
            f"Veritabanı bulunamadı: {DB_FILE}"
        )

    connection = sqlite3.connect(
        DB_FILE,
        timeout=60,
    )

    connection.row_factory = (
        sqlite3.Row
    )

    connection.execute(
        "PRAGMA busy_timeout=60000"
    )

    try:
        scan = read_latest_scan(
            connection
        )

        if scan is None:
            print(
                "Kayıtlı tarama bulunamadı; "
                "bildirim gönderilmedi"
            )
            return

        if scan[
            "health_status"
        ] == "INVALID":
            token, chat_id = get_telegram_settings()
            send_telegram(token, chat_id,
                          "⚠️ BINANCE AVCI 2 TARAMA HATASI\n"
                          f"UTC: {scan['scan_time_utc']}\n"
                          f"Sürüm: {scan['config_version']}\n"
                          f"Ayar: {(scan['config_hash'] or '-')[:12]}\n"
                          f"Kod: {(scan['git_sha'] or '-')[:12]}\n"
                          "Veri sağlığı geçersiz; aday kararı üretilmedi.")
            print("Geçersiz tarama Telegram uyarısı gönderildi")
            return

        candidates = read_new_candidates(
            connection,
            scan[
                "scan_time_utc"
            ],
        )

        trade_alerts = read_trade_alerts(connection, scan["scan_time_utc"])

        bridge_scores = read_bridge_scores(
            connection,
            scan[
                "scan_time_utc"
            ],
            scan[
                "config_version"
            ],
        )

    finally:
        connection.close()

    if not candidates and not trade_alerts:
        print(
            "Bu taramada yeni temiz aday yok; "
            "Telegram bildirimi gönderilmedi"
        )
        return

    token, chat_id = (
        get_telegram_settings()
    )

    scan_time_local = datetime.fromisoformat(scan["scan_time_utc"])
    scan_time_local = scan_time_local.astimezone(
        ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M")
    message = (build_message(scan, candidates, bridge_scores)
               if candidates else f"Tarama: {scan_time_local} (Türkiye)\n")
    if trade_alerts:
        message += "\n" + format_paper_alerts(trade_alerts)

    print(message)

    send_telegram(
        token,
        chat_id,
        message,
    )

    print(
        "Binance Avcı 2 Türkçe Telegram "
        "bildirimi gönderildi"
    )


if __name__ == "__main__":
    main()
