"""Deterministic paper entry and exit observations; sends no orders."""

import json
import sqlite3

from binance_snapshot_store import DB_FILE, utc_now


def entry_reason(feature):
    bars = feature.get("recent_closed_klines") or []
    if len(bars) < 13 or feature.get("stage") not in ("TRIGGER", "REIGNITION"):
        return None
    if (feature.get("btc_flash_crash") or feature.get("climax_risk")
            or feature.get("manipulation_risk")
            or feature.get("history_gain_90d_pct") is None
            or feature["history_gain_90d_pct"] >= 50):
        return None
    if (float(feature.get("volume_mult_1h") or 0) < 1.75
            or float(feature.get("taker_buy_ratio_15m") or 0) < .55
            or float(feature.get("change_15m") or 0) <= 0):
        return None
    previous_high = max(float(bar[2]) for bar in bars[-13:-1])
    last_close = float(bars[-1][4])
    previous_close = float(bars[-2][4])
    if previous_high <= last_close <= previous_high * 1.015:
        return "Son 1 saatlik tepe yeni aşıldı; hacim ve alıcı akışı güçlü"
    if (previous_high * .99 <= last_close <= previous_high
            and float(bars[-1][3]) <= previous_high
            and last_close >= previous_close):
        return "Önceki tepeye geri çekilip tutundu; alıcı akışı sürüyor"
    return None


def exit_reason(feature, event):
    price = float(feature.get("price") or 0)
    entry = float(event.get("entry_price_exec") or 0)
    signal = float(event.get("signal_price") or 0)
    if not price or event.get("entry_status") != "READY" or not entry:
        return None
    if price <= entry * .93:
        return "Kağıt üzerindeki girişten %7 aşağıda (zarar sınırı)"
    if price >= entry * 1.10:
        return "Kağıt üzerindeki girişten %10 yukarıda (kâr gözlemi)"
    if feature.get("btc_flash_crash"):
        return "BTC son 15 dakikada sert düştü"
    if signal and price < signal * .985:
        return "Sinyal fiyatının %1,5 altına indi"
    if (float(feature.get("change_15m") or 0) <= -2
            and float(feature.get("taker_buy_ratio_15m") or 0) < .45):
        return "Fiyat düşerken alıcı payı zayıfladı"
    return None


def main():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS trade_alerts (
            id INTEGER PRIMARY KEY, event_id TEXT NOT NULL,
            scan_time_utc TEXT NOT NULL, symbol TEXT NOT NULL,
            alert_kind TEXT NOT NULL, price REAL NOT NULL, reason TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            UNIQUE(event_id, alert_kind))""")
        scan = conn.execute("SELECT * FROM scans ORDER BY scan_time_utc DESC LIMIT 1").fetchone()
        if not scan or scan["health_status"] == "INVALID":
            return
        now = scan["scan_time_utc"]
        features = {row["symbol"]: {**dict(row),
                    "btc_flash_crash": bool(scan["btc_flash_crash"])}
                    for row in conn.execute("""SELECT symbol, price, change_15m,
                        taker_buy_ratio_15m FROM features
                        WHERE scan_time_utc=? AND config_version=?""",
                                            (now, scan["config_version"]))
                    }
        events = conn.execute("""SELECT * FROM signal_events
            WHERE event_class='CANDIDATE' AND outcome_status='OPEN'""").fetchall()
        count = 0
        for row in events:
            event = dict(row)
            feature = features.get(event["symbol"])
            if not feature:
                continue
            if event["signal_time_utc"] == now:
                # Full selected feature is in the event, including daily history.
                full = json.loads(event["raw_json"])
                reason = entry_reason(full)
                kind = "PAPER_ENTRY" if reason else None
            else:
                reason = exit_reason(feature, event)
                kind = "PAPER_EXIT_WARNING" if reason else None
            if kind:
                cursor = conn.execute("""INSERT OR IGNORE INTO trade_alerts
                    (event_id, scan_time_utc, symbol, alert_kind, price, reason, created_at_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (event["event_id"], now, event["symbol"], kind,
                     float(feature.get("price") or 0), reason, utc_now()))
                count += cursor.rowcount
        conn.commit()
        print(f"Yeni kağıt üstü alım/satış işareti: {count}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
