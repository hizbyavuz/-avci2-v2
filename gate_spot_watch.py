"""Separate, paper-only Gate Spot early-watch cohort. No trade or Telegram alert."""

import json
import math
import os
import sqlite3
from urllib import parse, request

DB = os.getenv("GATE_SPOT_DB", "avci2.db")
VERSION = "gate-spot-watch-v0.1-20260923"


def shortlist(con, now, batch, diagnostics=None):
    """Require two real observations and exchange-provided identity/quotes."""
    current = con.execute("""SELECT h.pair, h.symbol, h.last, h.volume_24h,
        h.change_24h, q.buy_start, q.bid, q.ask
        FROM gate_spot_history h JOIN gate_spot_quality q
          ON q.batch_id=h.batch_id AND q.pair=h.pair
        WHERE h.batch_id=?""", (batch,)).fetchall()
    has_watch_table = con.execute("""SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='gate_spot_watch'""").fetchone()
    counts = diagnostics if diagnostics is not None else {}
    counts["liquid_early"] = counts["aged"] = counts["tight"] = 0
    counts["history"] = counts["rising"] = counts["mapped"] = 0
    result = []
    for pair, symbol, price, volume, day_change, start, bid, ask in current:
        if volume < 300000 or not 3 <= day_change <= 25:
            continue
        counts["liquid_early"] += 1
        if start <= 0 or now - start < 30 * 86400:
            continue
        counts["aged"] += 1
        if ask <= 0 or 100 * (ask - bid) / ask > .4:
            continue
        counts["tight"] += 1
        # Exact Gate pair, from a previous run at least 18 minutes ago.
        prior = con.execute("""SELECT h.last, g.scan_ts FROM gate_spot_history h
            JOIN gate_spot_health g ON g.batch_id=h.batch_id
            WHERE h.pair=? AND g.status='VALID'
              AND g.scan_ts BETWEEN ? AND ?
            ORDER BY g.scan_ts DESC LIMIT 1""",
            (pair, now - 3600, now - 18 * 60)).fetchone()
        if prior is None or prior[0] <= 0:
            continue
        counts["history"] += 1
        rise = 100 * (price / prior[0] - 1)
        if not 1 <= rise <= 8:
            continue
        counts["rising"] += 1
        # Ticker alone never establishes a coin's identity.
        addresses = con.execute("""SELECT network_id, token_contract
            FROM gate_spot_contracts WHERE pair=?""", (pair,)).fetchall()
        if not addresses:
            continue
        counts["mapped"] += 1
        recent_watch = con.execute("""SELECT 1 FROM gate_spot_watch w
            JOIN gate_spot_health g ON g.batch_id=w.batch_id
            WHERE w.pair=? AND w.status='PAPER_WATCH'
              AND g.scan_ts BETWEEN ? AND ? LIMIT 1""",
            (pair, now - 24 * 3600, now)).fetchone() if has_watch_table else None
        if recent_watch:
            continue
        result.append({"pair": pair, "symbol": symbol, "price": price,
                       "volume_24h": volume, "change_24h": day_change,
                       "rise_pct": rise, "spread_pct": 100 * (ask - bid) / ask,
                       "network": addresses[0][0], "contract": addresses[0][1],
                       "age_days": (now - start) / 86400})
    return sorted(result, key=lambda x: (x["rise_pct"], x["volume_24h"]),
                  reverse=True)


def round_trip_loss(orderbook, usd=1000):
    """Simulate a market buy and immediate sell of the same token quantity."""
    try:
        remaining_usd, bought = usd, 0.0
        for price, size in orderbook["asks"]:
            price, size = float(price), float(size)
            if price <= 0 or size <= 0:
                continue
            spend = min(remaining_usd, price * size)
            bought += spend / price
            remaining_usd -= spend
            if remaining_usd < 1e-6:
                break
        if remaining_usd >= 1e-6:
            return None
        remaining, proceeds = bought, 0.0
        for price, size in orderbook["bids"]:
            price, size = float(price), float(size)
            if price <= 0 or size <= 0:
                continue
            sold = min(remaining, size)
            proceeds += sold * price
            remaining -= sold
            if remaining < 1e-9:
                break
        if remaining >= 1e-9:
            return None
        loss = 100 * (1 - proceeds / usd) + .5  # assumed total trading fees
        return loss if math.isfinite(loss) and 0 <= loss <= 100 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def fetch_book(pair):
    url = ("https://api.gateio.ws/api/v4/spot/order_book?" +
           parse.urlencode({"currency_pair": pair, "limit": 100}))
    with request.urlopen(url, timeout=8) as response:
        return json.load(response)


def record_watch_paths(con, batch, now):
    """Track sampled post-signal prices; never call them executable returns."""
    con.execute("""CREATE TABLE IF NOT EXISTS gate_spot_watch_path (
        watch_batch TEXT NOT NULL, pair TEXT NOT NULL,
        observation_batch TEXT NOT NULL, elapsed_minutes REAL NOT NULL,
        sampled_return_pct REAL NOT NULL,
        PRIMARY KEY (watch_batch, pair, observation_batch))""")
    con.execute("""INSERT OR IGNORE INTO gate_spot_watch_path
        SELECT w.batch_id, w.pair, ?, (? - start.scan_ts) / 60.0,
               100.0 * (h.last / w.price - 1.0)
        FROM gate_spot_watch w
        JOIN gate_spot_health start ON start.batch_id=w.batch_id
        JOIN gate_spot_history h ON h.pair=w.pair AND h.batch_id=?
        WHERE w.status='PAPER_WATCH' AND w.price>0
          AND ? > start.scan_ts AND ? <= start.scan_ts + 72*3600""",
        (batch, now, batch, now, now))


def run(path=DB, book_fetch=fetch_book):
    with sqlite3.connect(path, timeout=30) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_spot_watch (
            batch_id TEXT NOT NULL, pair TEXT NOT NULL,
            version TEXT NOT NULL, price REAL NOT NULL,
            change_24h REAL NOT NULL, rise_pct REAL NOT NULL,
            round_trip_1k_pct REAL, status TEXT NOT NULL,
            PRIMARY KEY (batch_id, pair))""")
        health = con.execute("""SELECT batch_id, scan_ts, status
            FROM gate_spot_health ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not health or health[2] != "VALID":
            return "Gate Spot erken izleme: güncel veri yok; aday üretilmedi"
        batch, now, _ = health
        record_watch_paths(con, batch, now)
        counts = {}
        result = shortlist(con, now, batch, counts)
        messages = []
        for item in result[:3]:
            try:
                loss = round_trip_loss(book_fetch(item["pair"]))
            except (OSError, TimeoutError, ValueError):
                loss = None
            status = "PAPER_WATCH" if loss is not None and loss <= 3 else "BOOK_UNVERIFIED"
            con.execute("""INSERT OR IGNORE INTO gate_spot_watch
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch, item["pair"], VERSION, item["price"],
                 item["change_24h"], item["rise_pct"], loss, status))
            if status == "PAPER_WATCH":
                messages.append(f"{item['pair']} (+%{item['rise_pct']:.1f} "
                    f"~20dk; 24s +%{item['change_24h']:.1f}; "
                    f"$1k gidiş-dönüş ~%{loss:.1f}; {item['network']} "
                    f"{item['contract']})")
        return (f"Gate Spot ayrı kağıt izleme: {len(result)} ön eleme, "
                f"{len(messages)} derinlik doğrulandı. "
                f"Eleme adımları: hacim+24s değişim {counts['liquid_early']}, "
                f"30g yaş {counts['aged']}, dar makas {counts['tight']}, "
                f"önceki fiyat {counts['history']}, 20dk yükseliş "
                f"{counts['rising']}, resmi kontrat {counts['mapped']}. " +
                (" | ".join(messages) if messages else "Temiz izleme yok."))


if __name__ == "__main__":
    report = run()
    print(report)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("\n## Gate Spot erken kağıt izleme\n\n" + report + "\n")
