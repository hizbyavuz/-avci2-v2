#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sqlite3
import statistics

from binance_notify import resolve_chat_id, send_telegram

DB = os.getenv("BINANCE_DB", "binance_avci2.db")


def median(values):
    vals = [float(v) for v in values if v is not None]
    return statistics.median(vals) if vals else None


def pct(wins, total):
    return 100.0 * wins / total if total else None


def fmt(value, digits=1):
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def load_rows(conn):
    rows = conn.execute(
        """
        SELECT
            e.event_id,
            e.event_class,
            e.symbol,
            e.signal_time_utc,
            f.retention_proxy,
            f.change_1h,
            f.change_3h,
            f.taker_buy_ratio_15m,
            f.cross_sectional_rarity_pct,
            f.volume_z_15m,
            f.trade_z_15m,
            f.return_z_15m,
            f.spread_bps,
            fo.book_imbalance,
            fo.imbalance_change,
            fo.tape_json,
            json_extract(o.horizon_metrics_json, '$."24".complete') AS h24_complete,
            json_extract(
                o.barrier_results_json,
                '$."24"."10.0".result'
            ) AS result_10_24
        FROM signal_events e
        JOIN outcome_labels o
          ON o.event_id = e.event_id
        LEFT JOIN features f
          ON f.symbol = e.symbol
         AND f.scan_time_utc = e.signal_time_utc
         AND f.config_version = e.config_version
        LEFT JOIN flow_observations fo
          ON fo.event_id = e.event_id
        WHERE e.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
          AND json_extract(o.horizon_metrics_json, '$."24".complete') = 1
        ORDER BY e.signal_time_utc
        """
    ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        item["win10"] = int(item.get("result_10_24") == "TARGET")
        tape = {}
        try:
            tape = json.loads(item.get("tape_json") or "{}")
        except (TypeError, ValueError):
            tape = {}
        item["large_buy_share"] = tape.get("large_buy_share")
        item["large_buy_streak"] = tape.get("large_buy_streak")
        result.append(item)
    return result


def feature_summary(rows, key):
    winners = [r.get(key) for r in rows if r["win10"]]
    losers = [r.get(key) for r in rows if not r["win10"]]
    return median(winners), median(losers), sum(v is not None for v in winners), sum(v is not None for v in losers)


def combo_stats(rows, name, predicate):
    eligible = [r for r in rows if predicate(r)]
    wins = sum(r["win10"] for r in eligible)
    total = len(eligible)
    return {
        "name": name,
        "wins": wins,
        "total": total,
        "rate": pct(wins, total),
    }


def gt(value, threshold):
    return value is not None and float(value) > threshold


def ge(value, threshold):
    return value is not None and float(value) >= threshold


def main():
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_cfg = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not os.path.exists(DB):
        return

    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = load_rows(conn)

    total = len(rows)
    wins = sum(r["win10"] for r in rows)
    baseline = pct(wins, total)

    class_lines = []
    for cls in ("CANDIDATE", "NEAR_MISS", "RANDOM_CONTROL"):
        group = [r for r in rows if r["event_class"] == cls]
        group_wins = sum(r["win10"] for r in group)
        class_lines.append(
            f"• {cls}: {group_wins}/{len(group)} = %{fmt(pct(group_wins, len(group)))}"
        )

    features = [
        ("Retention", "retention_proxy"),
        ("1s momentum", "change_1h"),
        ("3s momentum", "change_3h"),
        ("Taker alım", "taker_buy_ratio_15m"),
        ("Rarity", "cross_sectional_rarity_pct"),
        ("Hacim z", "volume_z_15m"),
        ("Trade z", "trade_z_15m"),
        ("Return z", "return_z_15m"),
        ("Book Δ", "imbalance_change"),
        ("Büyük alım payı", "large_buy_share"),
    ]

    feature_lines = []
    for label, key in features:
        wmed, lmed, wn, ln = feature_summary(rows, key)
        if wn + ln < 20 or wmed is None or lmed is None:
            continue
        feature_lines.append(
            f"• {label}: kazanan {fmt(wmed,2)} | diğer {fmt(lmed,2)} | n={wn+ln}"
        )

    combos = [
        combo_stats(
            rows,
            "Retention≥0.40 + 1s>%0.5",
            lambda r: ge(r.get("retention_proxy"), 0.40) and gt(r.get("change_1h"), 0.5),
        ),
        combo_stats(
            rows,
            "Retention>0 + 1s>%0.5",
            lambda r: gt(r.get("retention_proxy"), 0.0) and gt(r.get("change_1h"), 0.5),
        ),
        combo_stats(
            rows,
            "Retention>0 + taker>0.57",
            lambda r: gt(r.get("retention_proxy"), 0.0) and gt(r.get("taker_buy_ratio_15m"), 0.57),
        ),
        combo_stats(
            rows,
            "Taker>0.57 + rarity>65",
            lambda r: gt(r.get("taker_buy_ratio_15m"), 0.57) and gt(r.get("cross_sectional_rarity_pct"), 65),
        ),
        combo_stats(
            rows,
            "Retention>0 + rarity>65",
            lambda r: gt(r.get("retention_proxy"), 0.0) and gt(r.get("cross_sectional_rarity_pct"), 65),
        ),
    ]

    for combo in combos:
        combo["lift"] = (
            combo["rate"] / baseline
            if combo["rate"] is not None and baseline and combo["total"] >= 10
            else None
        )
    combos = [c for c in combos if c["total"] >= 10 and c["rate"] is not None]
    combos.sort(key=lambda c: (c["lift"] or 0, c["total"]), reverse=True)

    combo_lines = []
    for c in combos[:4]:
        combo_lines.append(
            f"• {c['name']}: {c['wins']}/{c['total']} = %{fmt(c['rate'])}"
            f" | {fmt(c['lift'],2)}x taban"
        )

    text = (
        "🧬 WINNER ANATOMY | 24s araştırma\n"
        f"• Kapanmış event: {total}\n"
        f"• +10 yapan: {wins}/{total} = %{fmt(baseline)}\n"
        "\n📊 Sınıflar\n"
        + "\n".join(class_lines)
        + "\n\n🔬 Sinyal öncesi farklar\n"
        + ("\n".join(feature_lines[:6]) if feature_lines else "• Yeterli ortak özellik verisi yok")
        + "\n\n🧪 En güçlü sabit kombinasyonlar\n"
        + ("\n".join(combo_lines) if combo_lines else "• Henüz en az 10 örnekli kombinasyon yok")
        + "\n\n⚠️ Gözlemsel katman; V5 skor/eşiklerini değiştirmez."
    )

    chat = resolve_chat_id(token, chat_cfg, DB, "Winner Anatomy")
    send_telegram(token, chat, text)


if __name__ == "__main__":
    main()
