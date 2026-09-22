"""Append-only cross-venue research log. Never alters signal selection."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


BINANCE_DB = Path("binance_avci2.db")
GATE_DB = Path(".gate-state/avci_outcomes.db")
MAPPING_FILE = Path("verified_token_mapping.json")


def normalized_utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Signal timestamp has no timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def read_mapping(path):
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    result = {}
    for item in entries:
        if item.get("verified") is not True:
            continue
        key = (item["network_id"].lower(), item["token_contract"].lower())
        symbol = item["binance_symbol"].upper()
        if key in result and result[key] != symbol:
            raise ValueError(f"Conflicting verified token identity: {key}")
        result[key] = symbol
    return result


def main():
    mapping = read_mapping(MAPPING_FILE)
    conn = sqlite3.connect(BINANCE_DB)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS shared_signal_events (
            source_event_id TEXT PRIMARY KEY, source TEXT NOT NULL,
            source_time_utc TEXT NOT NULL, signal_time_utc TEXT NOT NULL,
            binance_symbol TEXT, network_id TEXT, token_contract TEXT,
            config_version TEXT NOT NULL)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_shared_symbol_time
            ON shared_signal_events(binance_symbol, signal_time_utc)""")
        for row in conn.execute("""SELECT event_id, signal_time_utc, symbol,
            config_version FROM signal_events WHERE event_class='CANDIDATE'""").fetchall():
            conn.execute("""INSERT OR IGNORE INTO shared_signal_events VALUES
                (?, 'BINANCE', ?, ?, ?, NULL, NULL, ?)""",
                (f"binance:{row[0]}", normalized_utc(row[1]),
                 normalized_utc(row[1]), row[2], row[3]))
        if GATE_DB.exists():
            with sqlite3.connect(f"file:{GATE_DB}?mode=ro", uri=True) as gate:
                for row in gate.execute("""SELECT id, network_id, token_contract,
                        config_version, signal_iso FROM signals"""):
                    network, contract = row[1].lower(), row[2].lower()
                    ts = normalized_utc(row[4])
                    conn.execute("""INSERT OR IGNORE INTO shared_signal_events VALUES
                        (?, 'GATE_ONCHAIN', ?, ?, ?, ?, ?, ?)""",
                        (f"gate:{row[0]}", ts, ts, mapping.get((network, contract)),
                         network, contract, row[3]))
        matches = conn.execute("""SELECT g.binance_symbol, g.signal_time_utc,
               b.signal_time_utc FROM shared_signal_events g
               JOIN shared_signal_events b ON g.binance_symbol=b.binance_symbol
               WHERE g.source='GATE_ONCHAIN' AND b.source='BINANCE'
                 AND g.binance_symbol IS NOT NULL
                 AND abs(strftime('%s', g.signal_time_utc) -
                         strftime('%s', b.signal_time_utc)) <= 86400
               ORDER BY g.signal_time_utc DESC LIMIT 30""").fetchall()
        conn.commit()
        print(f"Ortak geçmiş: {len(matches)} doğrulanmış 24 saatlik eşleşme; "
              f"{len(mapping)} doğrulanmış kontrat eşlemesi.")
        for symbol, gate_time, binance_time in matches[:5]:
            print(f"  {symbol}: zincir {gate_time} / Binance {binance_time}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
