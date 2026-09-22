import json
import os
import re
import sqlite3
from datetime import datetime, timezone

import requests


DB_FILE = "binance_avci2.db"
LOG_FILE = "binance_winner_anatomy.log"
TELEGRAM_LIMIT = 4096


def read_log_summary():
    if not os.path.exists(LOG_FILE):
        return {}

    with open(
        LOG_FILE,
        "r",
        encoding="utf-8",
    ) as handle:
        text = handle.read()

    result = {}

    universe_match = re.search(
        r"Evren:\s*(\d+)\s*\|\s*"
        r"Winner olayi:\s*(\d+)",
        text,
    )

    if universe_match:
        result["universe"] = int(
            universe_match.group(1)
        )
        result["winner_events"] = int(
            universe_match.group(2)
        )

    selection_match = re.search(
        r"Detay winner:\s*(\d+)\s*"
        r"\|\s*CLOSED:\s*(\d+)\s*"
        r"\|\s*OPEN:\s*(\d+)\s*"
        r"\|\s*Eslesmis kontrol:\s*(\d+)",
        text,
    )

    if selection_match:
        result["detailed"] = int(
            selection_match.group(1)
        )
        result["closed"] = int(
            selection_match.group(2)
        )
        result["open"] = int(
            selection_match.group(3)
        )
        result["controls"] = int(
            selection_match.group(4)
        )

    saved_match = re.search(
        r"Kaydedilen olay:\s*(\d+)\s*"
        r"\|\s*Pre-event snapshot:\s*(\d+)",
        text,
    )

    if saved_match:
        result["saved_events"] = int(
            saved_match.group(1)
        )
        result["snapshots"] = int(
            saved_match.group(2)
        )

    levels_match = re.search(
        r"Seviye sayilari:\s*(\{[^\n]+\})",
        text,
    )

    if levels_match:
        try:
            result["levels"] = json.loads(
                levels_match.group(1)
            )
        except json.JSONDecodeError:
            result["levels"] = {}

    return result


def read_database_summary():
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError(
            f"Veritabani bulunamadi: {DB_FILE}"
        )

    connection = sqlite3.connect(
        DB_FILE
    )
    connection.row_factory = sqlite3.Row

    latest = connection.execute(
        """
        SELECT
            research_version,
            MAX(analyzed_at_utc) AS analyzed_at_utc
        FROM winner_events
        WHERE research_version IS NOT NULL
        GROUP BY research_version
        ORDER BY analyzed_at_utc DESC
        LIMIT 1
        """
    ).fetchone()

    if latest is None:
        connection.close()
        raise RuntimeError(
            "Winner Anatomy sonucu bulunamadi"
        )

    version = latest[
        "research_version"
    ]

    grouped_rows = connection.execute(
        """
        SELECT
            subject_class,
            event_status,
            COUNT(*) AS count_value,
            AVG(max_gain_pct) AS average_gain
        FROM winner_events
        WHERE research_version = ?
        GROUP BY subject_class, event_status
        """,
        (version,),
    ).fetchall()

    top_closed = connection.execute(
        """
        SELECT
            symbol,
            max_gain_pct,
            hours_anomaly_before_start
        FROM winner_events
        WHERE research_version = ?
          AND subject_class = 'WINNER'
          AND event_status = 'CLOSED'
        ORDER BY max_gain_pct DESC
        LIMIT 5
        """,
        (version,),
    ).fetchall()

    top_open = connection.execute(
        """
        SELECT
            symbol,
            max_gain_pct
        FROM winner_events
        WHERE research_version = ?
          AND subject_class = 'WINNER'
          AND event_status = 'OPEN'
        ORDER BY max_gain_pct DESC
        LIMIT 5
        """,
        (version,),
    ).fetchall()

    feature_count_row = connection.execute(
        """
        SELECT COUNT(*) AS count_value
        FROM pre_event_features
        WHERE research_version = ?
        """,
        (version,),
    ).fetchone()

    connection.close()

    grouped = {}

    for row in grouped_rows:
        grouped[
            (
                row["subject_class"],
                row["event_status"],
            )
        ] = {
            "count": int(
                row["count_value"]
                or 0
            ),
            "average_gain": float(
                row["average_gain"]
                or 0.0
            ),
        }

    return {
        "version":
            version,

        "analyzed_at_utc":
            latest["analyzed_at_utc"],

        "grouped":
            grouped,

        "top_closed": [
            dict(row)
            for row in top_closed
        ],

        "top_open": [
            dict(row)
            for row in top_open
        ],

        "feature_count": int(
            feature_count_row[
                "count_value"
            ]
            or 0
        ),
    }


def format_coin_rows(
    rows,
    include_lead_time=False,
):
    if not rows:
        return "- Yok"

    lines = []

    for row in rows:
        gain = float(
            row["max_gain_pct"]
            or 0.0
        )

        line = (
            f"- {row['symbol']}: "
            f"%{gain:+.2f}"
        )

        lead_time = row.get(
            "hours_anomaly_before_start"
        )

        if (
            include_lead_time
            and lead_time is not None
        ):
            line += (
                " | ilk anomali "
                f"{float(lead_time):.1f} "
                "saat once"
            )

        lines.append(line)

    return "\n".join(lines)


def build_message(
    log_summary,
    database_summary,
):
    grouped = database_summary[
        "grouped"
    ]

    closed_winners = grouped.get(
        ("WINNER", "CLOSED"),
        {
            "count": 0,
            "average_gain": 0.0,
        },
    )

    open_winners = grouped.get(
        ("WINNER", "OPEN"),
        {
            "count": 0,
            "average_gain": 0.0,
        },
    )

    controls = grouped.get(
        (
            "NEAR_MATCH_CONTROL",
            "CLOSED",
        ),
        {
            "count": 0,
            "average_gain": 0.0,
        },
    )

    expected_snapshots = (
        closed_winners["count"]
        + open_winners["count"]
        + controls["count"]
    ) * 7

    actual_snapshots = (
        database_summary[
            "feature_count"
        ]
    )

    missing_snapshots = max(
        0,
        expected_snapshots
        - actual_snapshots,
    )

    levels = (
        log_summary.get("levels")
        or {}
    )

    level_text = " | ".join(
        (
            f"+%{level}: "
            f"{levels.get(str(level), 0)}"
        )
        for level in (
            15,
            20,
            30,
            40,
            50,
            60,
        )
    )

    now_text = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    message = (
        "BINANCE WINNER ANATOMY "
        "- GUNLUK SONUC\n\n"

        f"Rapor zamani: {now_text}\n"

        f"Surum: "
        f"{database_summary['version']}\n"

        "Tarama evreni: "
        f"{log_summary.get('universe', '-')} "
        "coin\n"

        "Bulunan +%15 olayi: "
        f"{log_summary.get('winner_events', '-')}"
        "\n\n"

        "INCELEME GRUBU\n"

        "- Kapanmis kazanan: "
        f"{closed_winners['count']}\n"

        "- Acik/guncel olay: "
        f"{open_winners['count']}\n"

        "- Eslesmis basarisiz kontrol: "
        f"{controls['count']}\n"

        "- Hareket oncesi goruntu: "
        f"{actual_snapshots}/"
        f"{expected_snapshots}\n"

        "- Eksik goruntu: "
        f"{missing_snapshots}\n\n"

        "SEVIYELER\n"
        f"{level_text}\n\n"

        "ORTALAMA 72 SAATLIK "
        "MAKSIMUM HAREKET\n"

        "- Kapanmis kazananlar: "
        f"%{closed_winners['average_gain']:+.2f}"
        "\n"

        "- Eslesmis kontroller: "
        f"%{controls['average_gain']:+.2f}"
        "\n\n"

        "EN GUCLU KAPANMIS "
        "KAZANANLAR\n"

        f"{format_coin_rows(
            database_summary['top_closed'],
            True,
        )}\n\n"

        "GUNCEL OPEN OLAYLAR\n"

        f"{format_coin_rows(
            database_summary['top_open'],
        )}\n\n"

        "Not: OPEN olaylar 72 saat "
        "tamamlanmadan resmi basari "
        "sayilmaz. Bu rapor alim sinyali "
        "degil, geriye donuk arastirma "
        "sonucudur."
    )

    return message[
        :TELEGRAM_LIMIT
    ]


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
            "Bilinmeyen Telegram hatasi",
        )

        raise RuntimeError(
            f"Telegram hatasi: {description}"
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
            "Telegram Chat ID bulunamadi. "
            "Kendi olusturdugun bota /start "
            "ve ardindan merhaba yaz. "
            "Sonra workflow'u tekrar calistir."
        )

    return private_chat_ids[-1]


def send_telegram(message):
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
            "Telegram token hatali. "
            "Tokenin sayilar, iki nokta ve "
            "devamindaki yazi dahil tamami "
            "GitHub secret icinde olmali."
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

    response = requests.post(
        (
            "https://api.telegram.org/"
            f"bot{token}/sendMessage"
        ),
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        description = data.get(
            "description",
            "Bilinmeyen Telegram hatasi",
        )

        raise RuntimeError(
            f"Telegram mesaji gonderilemedi: "
            f"{description}"
        )


def main():
    log_summary = read_log_summary()

    database_summary = (
        read_database_summary()
    )

    message = build_message(
        log_summary,
        database_summary,
    )

    print(message)

    send_telegram(message)

    print(
        "Telegram Winner Anatomy "
        "bildirimi gonderildi"
    )


if __name__ == "__main__":
    main()
