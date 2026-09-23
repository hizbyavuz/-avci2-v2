#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from telegram_readable import (
    binance_price, coingecko_logo, fmt_price, pct,
    record_initial, send_photo_or_text, due_followups, mark_followup,
)


DB_FILE = "binance_avci2.db"
TELEGRAM_LIMIT = 4096
# Telegram chat ids are persisted in the restored research DB; resolver is shared with Gate.

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
    "XPL": "Plasma",
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

STAGE_EXPLANATIONS = {
    "TRIGGER": "Birden fazla hareket şartı aynı anda görüldü.",
    "REIGNITION": "İlk hareketten sonra fiyat ve ilgi yeniden hızlandı.",
    "CONTINUATION": "İlk hareketten sonra ilgi sürüyor.",
    "WAKE_UP": "İlk olağan dışı hareket görüldü.",
    "OBSERVE": "İzleme aşamasında.",
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


def load_cached_chat_id(db_path=DB_FILE):
    """Return the last successfully used private Telegram chat id."""
    if not db_path or not os.path.exists(db_path):
        return ""
    try:
        with sqlite3.connect(db_path, timeout=10) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS runtime_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            row = con.execute(
                "SELECT value FROM runtime_settings WHERE key='telegram_chat_id'"
            ).fetchone()
            return str(row[0]).strip() if row and row[0] else ""
    except sqlite3.Error:
        return ""


def save_cached_chat_id(chat_id, db_path=DB_FILE):
    """Persist a working chat id in the scanner state artifact."""
    chat_id = str(chat_id or "").strip()
    if not chat_id or not db_path:
        return
    try:
        with sqlite3.connect(db_path, timeout=10) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS runtime_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            con.execute("""INSERT OR REPLACE INTO runtime_settings
                (key, value, updated_at) VALUES
                ('telegram_chat_id', ?, datetime('now'))""", (chat_id,))
    except sqlite3.Error as exc:
        print("::warning::Telegram Chat ID cache yazılamadı:", type(exc).__name__)


def resolve_chat_id(token, configured_chat_id="", db_path=DB_FILE,
                    label="Telegram"):
    """Resolve configured -> cached -> getUpdates and persist success."""
    chat_id = str(configured_chat_id or "").strip()
    if chat_id:
        save_cached_chat_id(chat_id, db_path)
        return chat_id

    cached = load_cached_chat_id(db_path)
    if cached:
        print(f"{label} Chat ID kalıcı cache'den bulundu")
        return cached

    chat_id = find_chat_id(token)
    save_cached_chat_id(chat_id, db_path)
    print(f"{label} Chat ID otomatik bulundu ve kalıcı kaydedildi")
    return chat_id


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

    chat_id = resolve_chat_id(
        token,
        os.environ.get("TELEGRAM_CHAT_ID") or "",
        DB_FILE,
        "Binance Telegram",
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
            e.symbol,
            e.stage,
            e.engine,
            e.score,
            e.signal_price,
            e.validation_tier,
            e.config_version,
            f.change_15m,
            f.change_1h,
            f.change_24h,
            f.volume_mult_15m,
            f.taker_buy_ratio_15m,
            f.retention_proxy,
            f.cross_sectional_rarity_pct
        FROM signal_events e
        LEFT JOIN features f
          ON f.scan_time_utc=e.signal_time_utc
         AND f.symbol=e.symbol
        WHERE e.event_class = 'CANDIDATE'
          AND e.signal_time_utc = ?
        ORDER BY
            e.score DESC,
            e.symbol ASC
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
    """Separate simulated price observations from actual account activity."""
    groups = {"profit": [], "loss": [], "watch": [], "entry": []}
    for alert in alerts:
        symbol = alert["symbol"]
        price = format_paper_price(alert["price"])
        reason = alert["reason"]
        if alert["alert_kind"] == "PAPER_ENTRY":
            key, detail = "entry", "Bot bu fiyatı kâğıt üzerinde giriş olarak işaretledi"
        elif "%10 yukarıda" in reason:
            key, detail = "profit", "Deneme girişinden en az %10 yukarıda"
        elif "%7 aşağıda" in reason:
            key, detail = "loss", "Deneme girişinden en az %7 aşağıda"
        elif "%1,5 altına" in reason:
            key, detail = "watch", "İşaretlendiği fiyattan %1,5 geriledi"
        else:
            key, detail = "watch", reason
        identity, _ = candidate_identity(symbol)
        groups[key].append(f"• {identity}: {price} USDT — {detail}")

    lines = ["🧪 BOTUN KÂĞIT ÜZERİNDEKİ İŞLEM TAKİBİ",
             "Aşağıdaki fiyatlar botun test kaydıdır; senin alış fiyatın değildir.",
             "Hesabından alım veya satım yapılmadı."]
    for key, title in (("profit", "Kâğıt üzerinde +%10 görülenler (kâr gerçekleşmedi)"),
                       ("loss", "Kâğıt üzerinde -%7 görülenler"),
                        ("entry", "Yeni kâğıt üzeri girişler"),
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

    local_time = datetime.fromisoformat(scan_time.replace("Z", "+00:00"))
    local_time = local_time.astimezone(ZoneInfo("Europe/Istanbul"))
    lines = [
        "🔎 BINANCE AVCI 2 | YENİ İZLEME ADAYI",
        f"{local_time:%d.%m.%Y %H:%M} (Türkiye) • {len(candidates)} coin",
        "Bot bu coinlerde olağan dışı hareket gördü. Bu bir alım önerisi değil.",
        f"Piyasa: BTC {regime_text.lower()} • Taranan: {universe_size} coin",
        "",
    ]

    if health == "VALID_SPOT_OBSERVATION":
        lines.extend([
            "⚠️ Yalnızca spot verisi var; vadeli piyasa desteği kontrol edilemedi.",
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
        signal_price = candidate.get("signal_price")
        lines.extend([
            f"{index}. {identity}",
            *( [f"Sinyal anındaki fiyat: {format_paper_price(signal_price)} USDT"
                " (şu anki fiyat değil)."] if signal_price is not None else [] ),
            f"Ne görüldü? {STAGE_EXPLANATIONS.get(stage, stage_text)}",
            f"Hareketin türü: {engine_text.lower()} • Sağlanan kural: {score}/8",
            "Bu sayı kazanma ihtimali değildir.",
            f"Veri düzeyi: {validation_text.lower()}",
            f"Binance Spot: {market_url}",
            "Bu coin için henüz sonuç yok; sinyalden sonraki hareket ölçülecek.",
            "",
        ])

        bridge = bridge_scores.get(
            candidate[
                "symbol"
            ]
        )

        if bridge:
            classification = CLASS_NAMES.get(
                bridge.get("classification"), "YETERSİZ VERİ")
            lines.append(f"Geçmiş örneklerle karşılaştırma: {classification.lower()}.")
            lines.append("Bu benzerlik kazanma olasılığı değildir.")
            lines.append("")

    lines.extend([
        "Bot yalnızca izler; hesabında işlem açmaz.",
        "Kâğıt üzerindeki test kaydı varsa aşağıda ayrıca gösterilir.",
    ])

    return "\n".join(lines)



def build_readable_candidate(scan, candidate, bridge=None):
    symbol=candidate["symbol"]
    base=symbol[:-4]
    identity, market_url=candidate_identity(symbol)
    now_price=binance_price(symbol)
    signal_price=candidate.get("signal_price")
    change=pct(now_price, signal_price)
    stage=candidate.get("stage")
    score=int(candidate.get("score") or 0)
    c15=candidate.get("change_15m")
    c1h=candidate.get("change_1h")
    c24=candidate.get("change_24h")
    vm=candidate.get("volume_mult_15m")
    buy_ratio=candidate.get("taker_buy_ratio_15m")
    retention=candidate.get("retention_proxy")

    stage_plain={
        "WAKE_UP":"Bot ilk sıra dışı hareketi yeni fark etti.",
        "CONTINUATION":"İlk hareketten sonra ilgi sönmedi; coin hareketi koruyor.",
        "REIGNITION":"İlk hareket yavaşladıktan sonra yeniden hızlanma başladı.",
        "TRIGGER":"Birden fazla olumlu işaret aynı anda güçlendi.",
        "OBSERVE":"Coin dikkat çekiyor ama henüz güçlü aday seviyesinde değil.",
    }.get(stage,"Coin normal davranışından ayrıştı.")

    lines=[
        "🔎 BINANCE AVCI 2 | YENİ ADAY",
        f"🪙 {identity}",
        f"💵 Şu an: {fmt_price(now_price)} USDT" if now_price is not None else "💵 Şu anki fiyat alınamadı",
        f"🎯 Sinyal geldiğinde: {fmt_price(signal_price)} USDT" if signal_price is not None else "🎯 Sinyal fiyatı yok",
    ]
    if change is not None:
        lines.append(f"📊 Sinyalden beri: %{change:+.2f}")
    moves=[]
    if c15 is not None: moves.append(f"15 dk %{float(c15):+.1f}")
    if c1h is not None: moves.append(f"1 sa %{float(c1h):+.1f}")
    if c24 is not None: moves.append(f"24 sa %{float(c24):+.1f}")
    if moves:
        lines.append("⏱ Hareket: " + " • ".join(moves))

    lines.extend([
        "",
        "👀 Neden geldi?",
        f"• {stage_plain}",
    ])
    if vm is not None:
        lines.append(f"• Son 15 dk hacmi kendi normalinin yaklaşık {float(vm):.1f} katı.")
    if buy_ratio is not None:
        br=float(buy_ratio)
        if br>=0.58:
            lines.append("• Alım tarafı satış tarafına göre daha baskın.")
        elif br<=0.42:
            lines.append("• Satış tarafı hâlâ güçlü; bu yüzden dikkatli izleniyor.")
        else:
            lines.append("• Alım-satım dengesi henüz net biçimde tek tarafa dönmemiş.")
    if retention is not None:
        rp=float(retention)
        if rp>=0.70:
            lines.append("• İlk yükselişin büyük kısmını geri vermedi.")
        elif rp>=0.50:
            lines.append("• İlk hareketin yaklaşık yarısını koruyor.")
        else:
            lines.append("• İlk hareketi koruma gücü zayıf.")

    regime=REGIME_NAMES.get(scan.get("btc_regime"),scan.get("btc_regime","?")).lower()
    if scan.get("btc_regime")=="DOWN":
        lines.append("• BTC düşerken bu coin görece direnç gösterdiği için ayrıca dikkat çekti.")
    else:
        lines.append(f"• Genel piyasa şu an BTC tarafında {regime}.")

    lines.extend([
        "",
        "🧭 Bu ne demek?",
        f"Botun aradığı 8 işaretten {score} tanesi aynı anda görüldü. Bu kazanma ihtimali değildir.",
    ])
    if c24 is not None:
        if float(c24)>=20:
            lines.append("Coin son 24 saatte zaten çok hareket etmiş; geç kalma riski yüksek.")
        elif float(c24)>=10:
            lines.append("Coin hareket etmiş durumda ama bot devam edip etmediğini ölçüyor.")
        else:
            lines.append("Coin henüz 24 saatlik ölçekte aşırı kaçmış görünmüyor.")

    if bridge:
        cls=bridge.get("classification")
        if cls=="KAZANANA_BENZER":
            lines.append("Geçmişte devam eden güçlü hareketlerle bazı ortak özellikleri var.")
        elif cls=="KONTROLE_BENZER":
            lines.append("Geçmişte sönümlenen örneklere de benzer tarafları var; temkinli izleniyor.")
        elif cls=="KARMA":
            lines.append("Geçmiş örneklerde hem devam eden hem sönen hareketlere benzeyen tarafları var.")
        else:
            lines.append("Geçmiş örnek karşılaştırması henüz net sonuç vermiyor.")

    lines.extend([
        "",
        "📌 Bot sadece izliyor; hesabında işlem açmıyor.",
        f"Binance Spot: {market_url}",
    ])
    logo=coingecko_logo(base, VERIFIED_COIN_NAMES.get(base))
    return "\n".join(lines), now_price, logo

def send_binance_followups(token, chat_id, connection):
    def getter(_key, symbol):
        return binance_price(symbol)
    rows=due_followups(connection,"binance_telegram_price_history",getter,min_pp=3.0)
    sent=0
    for row in rows[:5]:
        direction="yukarıda" if row["change"]>=0 else "aşağıda"
        last_dir="yükseldi" if (row["since_last"] or 0)>=0 else "düştü"
        text=(
            f"📊 BINANCE AVCI 2 | TAKİP\n"
            f"🪙 {row['symbol']}\n"
            f"💵 Şu an: {fmt_price(row['current'])} USDT\n"
            f"🎯 İlk sinyal: {fmt_price(row['signal_price'])} USDT\n"
            f"📈 İlk sinyalden beri: %{abs(row['change']):.2f} {direction}\n"
            f"🔄 Önceki bildirime göre: %{abs(row['since_last'] or 0):.2f} {last_dir}\n\n"
            "Bot hareketin devamını ölçüyor; hesabında işlem açmıyor."
        )
        if send_photo_or_text(token,chat_id,text,None):
            mark_followup(connection,"binance_telegram_price_history",
                          row["key"],row["current"],row["change"])
            sent+=1
    connection.commit()
    return sent

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

    # Follow-up tracking is independent from whether this scan produced
    # a fresh candidate. Existing signals must still be checked every run.
    with sqlite3.connect(DB_FILE, timeout=60) as hist:
        try:
            has_followups = bool(hist.execute(
                "SELECT 1 FROM binance_telegram_price_history LIMIT 1"
            ).fetchone())
        except sqlite3.OperationalError:
            has_followups = False

    if not candidates and not trade_alerts and not has_followups:
        print(
            "Bu taramada yeni temiz aday veya takip edilecek eski aday yok; "
            "Telegram bildirimi gönderilmedi"
        )
        return

    token, chat_id = (
        get_telegram_settings()
    )

    scan_time_local = datetime.fromisoformat(scan["scan_time_utc"])
    scan_time_local = scan_time_local.astimezone(
        ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M")
    with sqlite3.connect(DB_FILE, timeout=60) as hist:
        if candidates:
            for candidate in candidates[:5]:
                bridge = bridge_scores.get(candidate["symbol"])
                message, current_price, logo = build_readable_candidate(scan, candidate, bridge)
                print(message)
                send_photo_or_text(token, chat_id, message, logo)
                record_initial(
                    hist, "binance_telegram_price_history",
                    f"{candidate['symbol']}|{scan['scan_time_utc']}",
                    candidate["symbol"], candidate.get("signal_price"),
                    current_price, logo,
                )
            hist.commit()

        followups = send_binance_followups(token, chat_id, hist)
        if followups:
            print(f"Binance takip bildirimi: {followups}")
    if trade_alerts:
        paper_message = f"{scan_time_local} (Türkiye)\n" + format_paper_alerts(trade_alerts)
        print(paper_message)
        send_telegram(token, chat_id, paper_message)

    print(
        "Binance Avcı 2 Türkçe Telegram "
        "bildirimi gönderildi"
    )


if __name__ == "__main__":
    main()
