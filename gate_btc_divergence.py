#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Observational Gate/BTC decoupling research.

Records coins that rise or hold strength while BTC is falling. This module
never changes frozen candidate rules, never bypasses security, and never
places trades.
"""

import json
import math
import os
import sqlite3
import time
from collections import Counter
from urllib import parse, request

DB = os.getenv("GATE_SPOT_DB", "avci2.db")
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
VERSION = "gate-btc-divergence-v0.1-20260923"


def _finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def btc_returns(open_url=request.urlopen, now=None):
    """Return closed-candle BTCUSDT returns for 15m/1h/3h."""
    now = int(now or time.time())
    query = parse.urlencode({"symbol": "BTCUSDT", "interval": "5m", "limit": 50})
    with open_url(f"{BINANCE_KLINES}?{query}", timeout=15) as response:
        rows = json.load(response)
    if not isinstance(rows, list):
        raise ValueError("BTC kline response invalid")

    closed = []
    now_ms = now * 1000
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            continue
        close = _finite(row[4])
        close_time = int(row[6])
        if close and close > 0 and close_time < now_ms:
            closed.append(close)
    if len(closed) < 37:
        raise ValueError("BTC closed-candle history insufficient")

    latest = closed[-1]

    def ret(bars):
        base = closed[-1 - bars]
        return 100.0 * (latest / base - 1.0)

    return {
        "btc_15m_pct": ret(3),
        "btc_1h_pct": ret(12),
        "btc_3h_pct": ret(36),
    }


def _regime(ctx):
    one = ctx["btc_1h_pct"]
    fifteen = ctx["btc_15m_pct"]
    if one <= -0.5 or (one < 0 and fifteen <= -0.35):
        return "DOWN"
    if one >= 0.5 or (one > 0 and fifteen >= 0.35):
        return "UP"
    return "SIDEWAYS"


def ensure_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS gate_btc_context (
        batch_id TEXT PRIMARY KEY,
        scan_ts INTEGER NOT NULL,
        version TEXT NOT NULL,
        btc_15m_pct REAL NOT NULL,
        btc_1h_pct REAL NOT NULL,
        btc_3h_pct REAL NOT NULL,
        btc_regime TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS gate_btc_divergence (
        batch_id TEXT NOT NULL,
        pair TEXT NOT NULL,
        symbol TEXT NOT NULL,
        scan_ts INTEGER NOT NULL,
        price REAL NOT NULL,
        change_24h REAL NOT NULL,
        short_return_pct REAL NOT NULL,
        relative_to_btc_pct REAL NOT NULL,
        volume_24h REAL NOT NULL,
        volume_accel_pct REAL,
        spread_pct REAL,
        retention_proxy INTEGER NOT NULL,
        repeat_count_24h INTEGER NOT NULL,
        tags_json TEXT NOT NULL,
        PRIMARY KEY (batch_id, pair)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_gate_btc_div_pair
        ON gate_btc_divergence(pair, scan_ts)""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_gate_btc_div_time
        ON gate_btc_divergence(scan_ts)""")


def analyze(path=DB, open_url=request.urlopen, now=None):
    now = int(now or time.time())
    ctx = btc_returns(open_url=open_url, now=now)
    ctx["btc_regime"] = _regime(ctx)

    if not os.path.exists(path):
        return {"status": "NO_DB", "context": ctx, "rows": []}

    with sqlite3.connect(path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        ensure_schema(con)
        health = con.execute("""SELECT batch_id, scan_ts, status
            FROM gate_spot_health ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not health or health["status"] != "VALID":
            return {"status": "NO_VALID_GATE_SNAPSHOT", "context": ctx, "rows": []}
        batch = health["batch_id"]
        scan_ts = int(health["scan_ts"])

        con.execute("""INSERT OR REPLACE INTO gate_btc_context
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (batch, scan_ts, VERSION, ctx["btc_15m_pct"], ctx["btc_1h_pct"],
             ctx["btc_3h_pct"], ctx["btc_regime"]))

        current = con.execute("""SELECT h.pair,h.symbol,h.last,h.volume_24h,
            h.change_24h,q.bid,q.ask
            FROM gate_spot_history h
            LEFT JOIN gate_spot_quality q
              ON q.batch_id=h.batch_id AND q.pair=h.pair
            WHERE h.batch_id=?""", (batch,)).fetchall()

        rows = []
        for row in current:
            price = _finite(row["last"])
            volume = _finite(row["volume_24h"])
            day_change = _finite(row["change_24h"])
            bid = _finite(row["bid"])
            ask = _finite(row["ask"])
            if not price or price <= 0 or not volume or volume < 300000:
                continue
            if day_change is None:
                continue
            spread = (100 * (ask - bid) / ask
                      if bid and ask and ask > 0 and ask >= bid else None)
            if spread is None or spread > 0.6:
                continue

            history = con.execute("""SELECT h.last,h.volume_24h,g.scan_ts
                FROM gate_spot_history h
                JOIN gate_spot_health g ON g.batch_id=h.batch_id
                WHERE h.pair=? AND g.status='VALID'
                  AND g.scan_ts BETWEEN ? AND ?
                ORDER BY g.scan_ts DESC LIMIT 5""",
                (row["pair"], scan_ts - 90*60, scan_ts - 15*60)).fetchall()
            if not history:
                continue
            prior_price = _finite(history[0]["last"])
            prior_volume = _finite(history[0]["volume_24h"])
            if not prior_price or prior_price <= 0:
                continue

            short_ret = 100 * (price / prior_price - 1)
            relative = short_ret - ctx["btc_1h_pct"]
            vol_accel = (100 * (volume / prior_volume - 1)
                         if prior_volume and prior_volume > 0 else None)

            retention = 0
            if len(history) >= 2:
                older = _finite(history[-1]["last"])
                peak = prior_price
                if older and older > 0 and peak > older:
                    impulse = peak - older
                    retained = (price - older) / impulse if impulse > 0 else 0
                    if price >= older and retained >= 0.65:
                        retention = 1

            # This table is research-only: require actual decoupling when BTC
            # is negative, not merely a positive daily ticker.
            if ctx["btc_1h_pct"] >= 0 or short_ret <= 0 or relative < 1.0:
                continue

            repeat = con.execute("""SELECT COUNT(DISTINCT batch_id)
                FROM gate_btc_divergence
                WHERE pair=? AND scan_ts>=?""",
                (row["pair"], scan_ts - 86400)).fetchone()[0]

            tags = ["BTC_NEGATIVE", "ABSOLUTE_RISE", "RELATIVE_STRENGTH",
                    "TIGHT_SPREAD"]
            if vol_accel is not None and vol_accel >= 5:
                tags.append("VOLUME_ACCEL")
            if retention:
                tags.append("RETENTION")
            if repeat >= 2:
                tags.append("PERSISTENT_DECOUPLING")
            if day_change >= 10:
                tags.append("DAY_MOMENTUM")

            con.execute("""INSERT OR REPLACE INTO gate_btc_divergence
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch, row["pair"], row["symbol"], scan_ts, price, day_change,
                 short_ret, relative, volume, vol_accel, spread, retention,
                 repeat + 1, json.dumps(tags, ensure_ascii=False)))

            rows.append({
                "pair": row["pair"], "symbol": row["symbol"],
                "short_return_pct": short_ret,
                "relative_to_btc_pct": relative,
                "volume_accel_pct": vol_accel,
                "spread_pct": spread,
                "retention_proxy": bool(retention),
                "repeat_count_24h": repeat + 1,
                "change_24h": day_change,
                "tags": tags,
            })
        con.commit()

    rows.sort(key=lambda x: (
        "PERSISTENT_DECOUPLING" in x["tags"],
        "VOLUME_ACCEL" in x["tags"],
        x["relative_to_btc_pct"],
    ), reverse=True)
    return {"status": "OK", "context": ctx, "rows": rows}


def common_patterns(path=DB, now=None):
    now = int(now or time.time())
    if not os.path.exists(path):
        return {}
    with sqlite3.connect(path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("""SELECT symbol,tags_json,relative_to_btc_pct,
                volume_accel_pct,retention_proxy
                FROM gate_btc_divergence WHERE scan_ts>=?""",
                (now - 86400,)).fetchall()
        except sqlite3.OperationalError:
            return {}
    tags = Counter()
    symbols = Counter()
    relatives = []
    for row in rows:
        symbols[row["symbol"]] += 1
        relatives.append(float(row["relative_to_btc_pct"]))
        try:
            tags.update(json.loads(row["tags_json"] or "[]"))
        except (TypeError, json.JSONDecodeError):
            pass
    return {
        "events": len(rows),
        "tags": tags.most_common(),
        "symbols": symbols.most_common(8),
        "avg_relative_pct": sum(relatives) / len(relatives) if relatives else None,
    }


def format_report(result, path=DB):
    ctx = result["context"]
    lines = [
        "GATE BTC TERSİNE GÜÇ ARAŞTIRMASI",
        (f"BTC: 15dk {ctx['btc_15m_pct']:+.2f}% | "
         f"1s {ctx['btc_1h_pct']:+.2f}% | 3s {ctx['btc_3h_pct']:+.2f}% "
         f"| rejim {ctx['btc_regime']}"),
    ]
    if ctx["btc_1h_pct"] >= 0:
        lines.append("BTC 1 saatlik bazda düşmüyor; ayrışma olayı üretilmedi.")
    elif not result["rows"]:
        lines.append("BTC düşerken temiz likit pozitif ayrışma bulunmadı.")
    else:
        lines.append(f"BTC düşerken ayrışan likit parite: {len(result['rows'])}")
        for row in result["rows"][:8]:
            extra = []
            if row["volume_accel_pct"] is not None:
                extra.append(f"hacim {row['volume_accel_pct']:+.1f}%")
            if row["retention_proxy"]:
                extra.append("retention")
            if row["repeat_count_24h"] >= 3:
                extra.append(f"{row['repeat_count_24h']}x tekrar")
            lines.append(
                f"- {row['pair']}: kısa dönem {row['short_return_pct']:+.2f}% | "
                f"BTC'ye göre {row['relative_to_btc_pct']:+.2f} puan | "
                + (", ".join(extra) if extra else "yalnız fiyat ayrışması")
            )
    patterns = common_patterns(path)
    if patterns.get("events"):
        top_tags = ", ".join(f"{name}:{count}" for name, count in patterns["tags"][:5])
        top_symbols = ", ".join(f"{name}:{count}" for name, count in patterns["symbols"][:5])
        lines += [
            f"Son 24s ortak olay sayısı: {patterns['events']}",
            f"Tekrarlayan ortak izler: {top_tags or 'yok'}",
            f"En çok tekrar edenler: {top_symbols or 'yok'}",
        ]
    lines.append("Not: Bu katman gözlemseldir; güvenlik filtresini veya frozen V5 kurallarını değiştirmez.")
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        result = analyze()
        report = format_report(result)
    except Exception as exc:
        report = f"::warning::Gate BTC ayrışma katmanı çalışmadı: {type(exc).__name__}: {exc}"
    print(report)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n## BTC tersine güç araştırması\n\n")
            handle.write(report + "\n")
