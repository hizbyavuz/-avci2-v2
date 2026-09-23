"""Separate, paper-only Gate Spot early-watch cohort. No trade or Telegram alert."""

import json
import math
import os
import sqlite3
from urllib import parse, request

DB = os.getenv("GATE_SPOT_DB", "avci2.db")
VERSION = "gate-spot-watch-v0.2-multipath-20260923"


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
    counts["momentum"] = counts["retention"] = counts["prebreakout"] = 0
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
        # Use real prior Gate observations. One narrow ~20m momentum condition
        # used to be the only entry path; keep it, but add retention and
        # pre-breakout paths without changing any downstream safety gate.
        history = con.execute("""SELECT h.last, h.volume_24h, g.scan_ts
            FROM gate_spot_history h
            JOIN gate_spot_health g ON g.batch_id=h.batch_id
            WHERE h.pair=? AND g.status='VALID'
              AND g.scan_ts BETWEEN ? AND ?
            ORDER BY g.scan_ts DESC LIMIT 4""",
            (pair, now - 75 * 60, now - 18 * 60)).fetchall()
        if not history or history[0][0] <= 0:
            continue
        counts["history"] += 1
        prior_price, prior_volume, prior_ts = history[0]
        rise = 100 * (price / prior_price - 1)
        volume_accel = (100 * (volume / prior_volume - 1)
                        if prior_volume and prior_volume > 0 else None)

        entry_path = None
        path_strength = 0.0

        # 1) MOMENTUM: the original path.
        if 1 <= rise <= 8:
            entry_path = "MOMENTUM"
            path_strength = rise
            counts["momentum"] += 1

        # 2) RETENTION: an impulse happened before the latest interval, then
        # price held at least 65% of it with no worse than a 0.6% pullback.
        if entry_path is None and len(history) >= 2:
            older_price = history[-1][0]
            if older_price and older_price > 0 and prior_price > older_price:
                impulse = 100 * (prior_price / older_price - 1)
                pullback = 100 * (price / prior_price - 1)
                retained = (price - older_price) / (prior_price - older_price)
                if 1 <= impulse <= 10 and pullback >= -0.6 and retained >= 0.65:
                    entry_path = "RETENTION"
                    path_strength = impulse * min(retained, 1.25)
                    counts["retention"] += 1

        # 3) PRE_BREAKOUT: price is only starting to move, but rolling Gate
        # volume has accelerated materially since the prior observation.
        # This is an observation path only; the exact-contract DEX/security
        # review remains mandatory before any Telegram alert.
        if (entry_path is None and 0.15 <= rise < 1
                and day_change <= 15 and volume_accel is not None
                and volume_accel >= 1.5):
            entry_path = "PRE_BREAKOUT"
            path_strength = rise + min(volume_accel, 20) / 10
            counts["prebreakout"] += 1

        if entry_path is None:
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
                       "age_days": (now - start) / 86400,
                       "entry_path": entry_path,
                       "path_strength": path_strength,
                       "volume_accel_pct": volume_accel})
    return sorted(result, key=lambda x: (x["path_strength"], x["volume_24h"]),
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


def orderbook_pressure(orderbook, levels=20):
    """Top-of-book notional imbalance; observational, never a safety verdict."""
    try:
        bids = sum(float(p) * float(q) for p, q in orderbook["bids"][:levels]
                   if float(p) > 0 and float(q) > 0)
        asks = sum(float(p) * float(q) for p, q in orderbook["asks"][:levels]
                   if float(p) > 0 and float(q) > 0)
        if asks <= 0 or bids <= 0:
            return None
        return bids / asks
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def orderbook_candidates(con, now, batch, existing_pairs, book_fetch):
    """Independent path: quiet price + strong bid depth on verified Gate pairs."""
    rows = con.execute("""SELECT h.pair,h.symbol,h.last,h.volume_24h,h.change_24h,
        q.buy_start,q.bid,q.ask FROM gate_spot_history h
        JOIN gate_spot_quality q ON q.batch_id=h.batch_id AND q.pair=h.pair
        WHERE h.batch_id=? ORDER BY h.volume_24h DESC LIMIT 120""",
        (batch,)).fetchall()
    out = []
    for pair, symbol, price, volume, day_change, start, bid, ask in rows:
        if pair in existing_pairs or volume < 500000 or not 0 <= day_change <= 15:
            continue
        if start <= 0 or now - start < 30 * 86400 or ask <= 0:
            continue
        if 100 * (ask - bid) / ask > .35:
            continue
        prior = con.execute("""SELECT h.last FROM gate_spot_history h
            JOIN gate_spot_health g ON g.batch_id=h.batch_id
            WHERE h.pair=? AND g.status='VALID' AND g.scan_ts BETWEEN ? AND ?
            ORDER BY g.scan_ts DESC LIMIT 1""",
            (pair, now - 3600, now - 18 * 60)).fetchone()
        if not prior or prior[0] <= 0:
            continue
        rise = 100 * (price / prior[0] - 1)
        if not -0.4 <= rise <= 1.0:
            continue
        addresses = con.execute("""SELECT network_id,token_contract
            FROM gate_spot_contracts WHERE pair=?""", (pair,)).fetchall()
        if not addresses:
            continue
        recent = con.execute("""SELECT 1 FROM gate_spot_watch w
            JOIN gate_spot_health g ON g.batch_id=w.batch_id
            WHERE w.pair=? AND w.status='PAPER_WATCH'
              AND g.scan_ts BETWEEN ? AND ? LIMIT 1""",
            (pair, now - 24*3600, now)).fetchone()
        if recent:
            continue
        try:
            book = book_fetch(pair)
        except (OSError, TimeoutError, ValueError):
            continue
        imbalance = orderbook_pressure(book)
        if imbalance is None or imbalance < 1.8:
            continue
        out.append({"pair":pair,"symbol":symbol,"price":price,
            "volume_24h":volume,"change_24h":day_change,"rise_pct":rise,
            "spread_pct":100*(ask-bid)/ask,"network":addresses[0][0],
            "contract":addresses[0][1],"age_days":(now-start)/86400,
            "entry_path":"ORDERBOOK_PRESSURE","path_strength":min(imbalance,4),
            "volume_accel_pct":None,"orderbook_imbalance":imbalance,
            "_book":book})
    return sorted(out, key=lambda x:(x["path_strength"],x["volume_24h"]),
                  reverse=True)


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
            entry_path TEXT,
            PRIMARY KEY (batch_id, pair))""")
        columns = {row[1] for row in con.execute("PRAGMA table_info(gate_spot_watch)")}
        if "entry_path" not in columns:
            con.execute("ALTER TABLE gate_spot_watch ADD COLUMN entry_path TEXT")
        health = con.execute("""SELECT batch_id, scan_ts, status
            FROM gate_spot_health ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not health or health[2] != "VALID":
            return "Gate Spot erken izleme: güncel veri yok; aday üretilmedi"
        batch, now, _ = health
        record_watch_paths(con, batch, now)
        counts = {}
        result = shortlist(con, now, batch, counts)
        existing = {item["pair"] for item in result}
        book_rows = orderbook_candidates(con, now, batch, existing, book_fetch)
        counts["orderbook"] = len(book_rows)
        result = sorted(result + book_rows,
            key=lambda x: (x["path_strength"], x["volume_24h"]), reverse=True)
        messages = []
        for item in result[:5]:
            try:
                loss = round_trip_loss(item.get("_book") or book_fetch(item["pair"]))
            except (OSError, TimeoutError, ValueError):
                loss = None
            status = "PAPER_WATCH" if loss is not None and loss <= 3 else "BOOK_UNVERIFIED"
            con.execute("""INSERT OR IGNORE INTO gate_spot_watch
                (batch_id, pair, version, price, change_24h, rise_pct,
                 round_trip_1k_pct, status, entry_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch, item["pair"], VERSION, item["price"],
                 item["change_24h"], item["rise_pct"], loss, status,
                 item["entry_path"]))
            if status == "PAPER_WATCH":
                messages.append(f"{item['pair']} ({item['entry_path']}; "
                    f"yakın dönem %{item['rise_pct']:+.1f}; "
                    f"24s +%{item['change_24h']:.1f}; "
                    f"$1k gidiş-dönüş ~%{loss:.1f}; {item['network']} "
                    f"{item['contract']})")
        return (f"Gate Spot ayrı kağıt izleme: {len(result)} ön eleme, "
                f"{len(messages)} derinlik doğrulandı. "
                f"Eleme adımları: hacim+24s değişim {counts['liquid_early']}, "
                f"30g yaş {counts['aged']}, dar makas {counts['tight']}, "
                f"önceki fiyat {counts['history']}, çoklu-yol eşleşme "
                f"{counts['rising']} (momentum {counts['momentum']}, "
                f"retention {counts['retention']}, pre-breakout "
                f"{counts['prebreakout']}, order-book {counts['orderbook']}), "
                f"resmi kontrat {counts['mapped']}. " +
                (" | ".join(messages) if messages else "Temiz izleme yok."))


if __name__ == "__main__":
    report = run()
    print(report)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("\n## Gate Spot erken kağıt izleme\n\n" + report + "\n")
