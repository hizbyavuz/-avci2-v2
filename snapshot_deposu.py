import sqlite3
import json
from datetime import datetime, timezone

DB_FILE = "avci2.db"


def baglanti_ac():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zaman_utc TEXT NOT NULL,
            config_version TEXT,
            network TEXT,
            network_id TEXT,
            token_contract TEXT,
            token_name TEXT,
            symbol TEXT,
            pool TEXT,
            stage TEXT,
            score INTEGER,
            age_minutes REAL,
            liquidity REAL,
            volume_24h REAL,
            volume_6h REAL,
            volume_1h REAL,
            volume_5m REAL,
            change_24h REAL,
            change_6h REAL,
            change_1h REAL,
            change_5m REAL,
            buys_24h REAL,
            sells_24h REAL,
            buys_1h REAL,
            sells_1h REAL,
            buys_5m REAL,
            sells_5m REAL,
            volume_liquidity_ratio REAL,
            acceleration_1h REAL,
            acceleration_5m REAL,
            raw_json TEXT
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_snapshots_contract_time
        ON snapshots (
            network_id,
            token_contract,
            zaman_utc
        )
    """)

    conn.commit()
    return conn


def snapshot_kaydet(candidate, config_version):
    conn = baglanti_ac()

    zaman_utc = datetime.now(
        timezone.utc
    ).isoformat()

    conn.execute("""
        INSERT INTO snapshots (
            zaman_utc,
            config_version,
            network,
            network_id,
            token_contract,
            token_name,
            symbol,
            pool,
            stage,
            score,
            age_minutes,
            liquidity,
            volume_24h,
            volume_6h,
            volume_1h,
            volume_5m,
            change_24h,
            change_6h,
            change_1h,
            change_5m,
            buys_24h,
            sells_24h,
            buys_1h,
            sells_1h,
            buys_5m,
            sells_5m,
            volume_liquidity_ratio,
            acceleration_1h,
            acceleration_5m,
            raw_json
        )
        VALUES (
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?
        )
    """, (
        zaman_utc,
        config_version,
        candidate.get("network"),
        candidate.get("network_id"),
        candidate.get("token_contract"),
        candidate.get("name"),
        candidate.get("symbol"),
        candidate.get("pool"),
        candidate.get("stage"),
        candidate.get("score"),
        candidate.get("age_minutes"),
        candidate.get("liquidity"),
        candidate.get("volume_24h"),
        candidate.get("volume_6h"),
        candidate.get("volume_1h"),
        candidate.get("volume_5m"),
        candidate.get("change_24h"),
        candidate.get("change_6h"),
        candidate.get("change_1h"),
        candidate.get("change_5m"),
        candidate.get("buys_24h"),
        candidate.get("sells_24h"),
        candidate.get("buys_1h"),
        candidate.get("sells_1h"),
        candidate.get("buys_5m"),
        candidate.get("sells_5m"),
        candidate.get("volume_liquidity_ratio"),
        candidate.get("acceleration_1h"),
        candidate.get("acceleration_5m"),
        json.dumps(candidate, ensure_ascii=False)
    ))

    conn.commit()
    conn.close()


def son_snapshot(network_id, token_contract):
    conn = baglanti_ac()

    row = conn.execute("""
        SELECT raw_json
        FROM snapshots
        WHERE network_id = ?
          AND token_contract = ?
        ORDER BY zaman_utc DESC
        LIMIT 1
    """, (
        network_id,
        token_contract
    )).fetchone()

    conn.close()

    if not row:
        return None

    return json.loads(row[0])


def snapshot_sayisi():
    conn = baglanti_ac()

    count = conn.execute("""
        SELECT COUNT(*)
        FROM snapshots
    """).fetchone()[0]

    conn.close()
    return count
