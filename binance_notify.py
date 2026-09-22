#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3

import requests


DB_FILE = "binance_avci2.db"
TELEGRAM_LIMIT = 4096


STAGE_NAMES = {
    "TRIGGER": "TETİK",
    "REIGNITION": "YENİDEN CANLANMA",
    "CONTINUATION": "DEVAM",
    "WAKE_UP": "UYANIŞ",
    "OBSERVE": "GÖZLEM",
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
    return connection.execute(
        """
        SELECT *
        FROM scans
        ORDER BY scan_time_utc DESC
        LIMIT 1
        """
    ).fetchone()


def read_new_candidates(
    connection,
    scan_time,
):
    return connection.execute(
        """
        SELECT
            symbol,
            stage,
            score,
            validation_tier
        FROM signal_events
        WHERE event_class = 'CANDIDATE'
          AND signal_time_utc = ?
        ORDER BY
            score DESC,
            symbol ASC
        """,
        (scan_time,),
    ).fetchall()


def build_message(
    scan,
    candidates,
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

        score = candidate[
            "score"
        ]

        validation = candidate[
            "validation_tier"
        ]

        lines.extend([
            f"{index}. {candidate['symbol']}",
            f"Aşama: {stage_text}",
            f"Puan: {score}/8",
            f"Doğrulama: {validation}",
            "",
        ])

    lines.extend([
        "Açıklama:",
        "UYANIŞ = İlk olağan dışı hareket.",
        "DEVAM = Hacim ve ilgi sürüyor.",
        "YENİDEN CANLANMA = Hareket tekrar hızlanıyor.",
        "TETİK = Birden fazla şart aynı anda oluştu.",
        "",
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
        DB_FILE
    )

    connection.row_factory = (
        sqlite3.Row
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
            print(
                "Tarama sağlık kontrolünden "
                "geçemedi; bildirim gönderilmedi"
            )
            return

        candidates = read_new_candidates(
            connection,
            scan["scan_time_utc"],
        )

    finally:
        connection.close()

    if not candidates:
        print(
            "Bu taramada yeni temiz aday yok; "
            "Telegram bildirimi gönderilmedi"
        )
        return

    token, chat_id = (
        get_telegram_settings()
    )

    message = build_message(
        scan,
        candidates,
    )

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
