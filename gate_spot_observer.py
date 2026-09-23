"""Gate Spot market coverage; research data only, never a trading signal."""

import json
import math
import os
import re
import sqlite3
import time
from urllib import error, request
from datetime import datetime, timezone


BASE_URL = "https://api.gateio.ws/api/v4/spot"
DB_PATH = os.getenv("GATE_SPOT_DB", "avci2.db")
CHAIN_IDS = {
    "SOL": "solana", "SOLANA": "solana",
    "ETH": "eth", "ERC20": "eth",
    "BSC": "bsc", "BEP20": "bsc",
    "BASE": "base",
    "ARB": "arbitrum", "ARBITRUM": "arbitrum",
}
EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
SOL_ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}\Z")
LEVERAGED_SYMBOL = re.compile(r"(?:[235]L|[235]S)\Z", re.I)


def valid_address(network, address):
    pattern = SOL_ADDRESS if network == "solana" else EVM_ADDRESS
    return bool(pattern.fullmatch(address or ""))


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def is_leveraged_product(base, currency, ticker):
    """Gate's ETF ticker fields take precedence over generic pair.type."""
    if any(ticker.get(field) not in (None, "", 0, "0")
           for field in ("etf_leverage", "etf_net_value", "etf_pre_net_value")):
        return True
    name = str(currency.get("name") or "")
    return bool(LEVERAGED_SYMBOL.search(str(base or "")) or
                re.search(r"\b\d+x(?:long|short)\b", name, re.I))


def build_snapshot(currencies, pairs, tickers):
    """Join only Gate's own pair/currency/chain data; never join by ticker alone."""
    currencies = {item.get("currency"): item for item in currencies
                  if isinstance(item, dict) and item.get("currency")}
    tickers = {item.get("currency_pair"): item for item in tickers
               if isinstance(item, dict) and item.get("currency_pair")}
    market, contracts = [], []
    owners = {}
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        pair_id = pair.get("id")
        base = pair.get("base")
        currency = currencies.get(base)
        ticker = tickers.get(pair_id)
        if (pair.get("quote") != "USDT" or pair.get("trade_status") != "tradable"
                or pair.get("type") != "normal" or pair.get("st_tag") is True
                or not currency or currency.get("delisted") is not False
                or currency.get("trade_disabled") is not False or not ticker):
            continue
        if is_leveraged_product(base, currency, ticker):
            continue
        last = finite_number(ticker.get("last"))
        volume = finite_number(ticker.get("quote_volume"))
        change = finite_number(ticker.get("change_percentage"))
        if last is None or last <= 0 or volume is None or volume < 0 or change is None:
            continue
        market.append((pair_id, base, currency.get("name") or "",
                       last, volume, change))
        for chain in currency.get("chains") or []:
            if not isinstance(chain, dict):
                continue
            network = CHAIN_IDS.get(str(chain.get("name") or "").upper())
            address = str(chain.get("addr") or "").strip()
            if not network or not valid_address(network, address):
                continue
            key = network, address.lower()
            owners.setdefault(key, set()).add(pair_id)
            contracts.append((pair_id, network, address.lower()))
    # If the official feed maps one address to multiple pairs, keep it unknown.
    contracts = sorted(set(row for row in contracts
                           if len(owners[(row[1], row[2])]) == 1))
    return market, contracts


def build_market_quality(pairs, tickers):
    """Retain official listing dates and best quotes for separate Spot research."""
    ticker_by_pair = {row.get("currency_pair"): row for row in tickers
                      if isinstance(row, dict) and row.get("currency_pair")}
    quality = []
    for pair in pairs:
        if not isinstance(pair, dict) or pair.get("quote") != "USDT":
            continue
        ticker = ticker_by_pair.get(pair.get("id")) or {}
        bid = finite_number(ticker.get("highest_bid"))
        ask = finite_number(ticker.get("lowest_ask"))
        try:
            buy_start = int(pair.get("buy_start") or 0)
        except (ValueError, TypeError):
            buy_start = 0
        if bid is None or ask is None or bid <= 0 or ask < bid:
            continue
        quality.append((pair["id"], buy_start, bid, ask))
    return quality


def fetch_lists(open_url=request.urlopen):
    lists = []
    for path in ("currencies", "currency_pairs", "tickers"):
        with open_url(f"{BASE_URL}/{path}", timeout=30) as response:
            data = json.load(response)
        if not isinstance(data, list):
            raise ValueError(f"Gate Spot {path}: invalid response")
        lists.append(data)
    return lists


def save_snapshot(path, batch, market, contracts, error="", quality=()):
    with sqlite3.connect(path, timeout=30) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_spot_health (
            batch_id TEXT PRIMARY KEY, scan_ts INTEGER NOT NULL,
            status TEXT NOT NULL, pair_count INTEGER NOT NULL,
            contract_count INTEGER NOT NULL, error TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS gate_spot_market (
            pair TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,
            last REAL NOT NULL, volume_24h REAL NOT NULL,
            change_24h REAL NOT NULL, batch_id TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS gate_spot_contracts (
            pair TEXT NOT NULL, network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL,
            PRIMARY KEY (pair, network_id, token_contract))""")
        con.execute("""CREATE TABLE IF NOT EXISTS gate_spot_history (
            batch_id TEXT NOT NULL, pair TEXT NOT NULL, symbol TEXT NOT NULL,
            last REAL NOT NULL, volume_24h REAL NOT NULL,
            change_24h REAL NOT NULL,
            PRIMARY KEY (batch_id, pair))""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_gate_spot_history_pair
            ON gate_spot_history(pair, batch_id)""")
        con.execute("""CREATE TABLE IF NOT EXISTS gate_spot_quality (
            batch_id TEXT NOT NULL, pair TEXT NOT NULL,
            buy_start INTEGER NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL,
            PRIMARY KEY (batch_id, pair))""")
        con.execute("DELETE FROM gate_spot_market")
        con.execute("DELETE FROM gate_spot_contracts")
        con.executemany("INSERT INTO gate_spot_market VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(*row, batch) for row in market])
        con.executemany("INSERT INTO gate_spot_contracts VALUES (?, ?, ?)", contracts)
        if not error:
            con.executemany("""INSERT OR IGNORE INTO gate_spot_history
                VALUES (?, ?, ?, ?, ?, ?)""",
                [(batch, pair, symbol, last, volume, change)
                 for pair, symbol, _name, last, volume, change in market
                 if volume >= 30000])
            market_pairs = {row[0] for row in market if row[4] >= 30000}
            con.executemany("""INSERT OR IGNORE INTO gate_spot_quality
                VALUES (?, ?, ?, ?, ?)""",
                [(batch, pair, start, bid, ask)
                 for pair, start, bid, ask in quality if pair in market_pairs])
        con.execute("INSERT OR REPLACE INTO gate_spot_health VALUES (?, ?, ?, ?, ?, ?)",
                    (batch, int(time.time()), "ERROR" if error else "VALID",
                     len(market), len(contracts), error[:300]))


def coverage_report(path, batch, min_volume=30000):
    """Measure missed Gate movers by verified contract, without making signals."""
    with sqlite3.connect(path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        has_onchain = con.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='gate_scan_health'""").fetchone()
        health = con.execute("""SELECT batch_id, status, scan_ts
            FROM gate_scan_health ORDER BY scan_ts DESC LIMIT 1""").fetchone() if has_onchain else None
        mapped = con.execute("""SELECT pair, network_id, token_contract
            FROM gate_spot_contracts""").fetchall()
        by_pair = {}
        for row in mapped:
            by_pair.setdefault(row["pair"], set()).add(
                (row["network_id"], row["token_contract"].lower()))
        seen = {(row[0], row[1]) for row in con.execute("""
            SELECT network_id, lower(token_contract)
            FROM gate_early_observations WHERE batch_id=?""",
            (health["batch_id"],))} if health and health["status"] == "VALID" \
                and time.time() - health["scan_ts"] <= 3600 else None
        rows = con.execute("""SELECT pair, change_24h FROM gate_spot_history
            WHERE batch_id=? AND volume_24h>=? AND change_24h>=10""",
            (batch, min_volume)).fetchall()
        if not rows:
            return "Gate kapsama: +%10 hareketli, yeterli hacimli parite yok"
        mapped_count = sum(bool(by_pair.get(row["pair"])) for row in rows)
        seen_count = sum(bool(by_pair.get(row["pair"], set()) & seen)
                         for row in rows) if seen is not None else None
        large = sum(row["change_24h"] >= 20 for row in rows)
        return (f"Gate kapsama (24s artış, hacim >= ${min_volume:,}): "
                f"+%10 {len(rows)} parite, +%20 {large}; "
                f"resmi kontratı eşleşen {mapped_count}, "
                f"on-chain gözlemde görülen "
                f"{seen_count if seen_count is not None else 'karşılaştırma yok'}. "
                "24s artış erken sinyal veya güvenli alım anlamına gelmez.")


def main():
    batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    try:
        currencies, pairs, tickers = fetch_lists()
        market, contracts = build_snapshot(currencies, pairs, tickers)
        quality = build_market_quality(pairs, tickers)
        save_snapshot(DB_PATH, batch, market, contracts, quality=quality)
        print(f"Gate Spot gözlem: {len(market)} USDT paritesi, "
              f"{len(contracts)} ağ/kontrat eşleşmesi; alım bildirimi üretilmez.")
        report = coverage_report(DB_PATH, batch)
        print(report)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write("\n## Gate Spot piyasa kapsaması\n\n" + report + "\n")
    except (error.URLError, TimeoutError, ValueError) as exc:
        save_snapshot(DB_PATH, batch, [], [], type(exc).__name__)
        print(f"::warning::Gate Spot verisi alınamadı: {type(exc).__name__}")


if __name__ == "__main__":
    main()
