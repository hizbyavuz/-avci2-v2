"""Gate Spot market coverage; research data only, never a trading signal."""

import json
import math
import re
import sqlite3
import time
from urllib import error, request
from datetime import datetime, timezone


BASE_URL = "https://api.gateio.ws/api/v4/spot"
CHAIN_IDS = {
    "SOL": "solana", "SOLANA": "solana",
    "ETH": "eth", "ERC20": "eth",
    "BSC": "bsc", "BEP20": "bsc",
    "BASE": "base",
    "ARB": "arbitrum", "ARBITRUM": "arbitrum",
}
EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
SOL_ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}\Z")


def valid_address(network, address):
    pattern = SOL_ADDRESS if network == "solana" else EVM_ADDRESS
    return bool(pattern.fullmatch(address or ""))


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


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


def fetch_lists(open_url=request.urlopen):
    lists = []
    for path in ("currencies", "currency_pairs", "tickers"):
        with open_url(f"{BASE_URL}/{path}", timeout=30) as response:
            data = json.load(response)
        if not isinstance(data, list):
            raise ValueError(f"Gate Spot {path}: invalid response")
        lists.append(data)
    return lists


def save_snapshot(path, batch, market, contracts, error=""):
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
        con.execute("DELETE FROM gate_spot_market")
        con.execute("DELETE FROM gate_spot_contracts")
        con.executemany("INSERT INTO gate_spot_market VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(*row, batch) for row in market])
        con.executemany("INSERT INTO gate_spot_contracts VALUES (?, ?, ?)", contracts)
        con.execute("INSERT OR REPLACE INTO gate_spot_health VALUES (?, ?, ?, ?, ?, ?)",
                    (batch, int(time.time()), "ERROR" if error else "VALID",
                     len(market), len(contracts), error[:300]))


def main():
    batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    try:
        market, contracts = build_snapshot(*fetch_lists())
        save_snapshot("avci2.db", batch, market, contracts)
        print(f"Gate Spot gözlem: {len(market)} USDT paritesi, "
              f"{len(contracts)} ağ/kontrat eşleşmesi; alım bildirimi üretilmez.")
    except (error.URLError, TimeoutError, ValueError) as exc:
        save_snapshot("avci2.db", batch, [], [], type(exc).__name__)
        print(f"::warning::Gate Spot verisi alınamadı: {type(exc).__name__}")


if __name__ == "__main__":
    main()
