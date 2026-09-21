#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_FILE = os.getenv("BINANCE_AVCI_DB", "binance_avci2.db")


def open_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = open_db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        config_version TEXT NOT NULL,
        universe_size INTEGER,
        btc_change_24h REAL
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS features (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        config_version TEXT NOT NULL,
        symbol TEXT NOT NULL,
        stage TEXT,
        engine TEXT,
        score INTEGER,
        is_selected INTEGER DEFAULT 0,
        price REAL,
        change_15m REAL,
        change_1h REAL,
        change_3h REAL,
        change_24h REAL,
        btc_relative_24h REAL,
        volume_z_15m REAL,
        trade_z_15m REAL,
        return_z_15m REAL,
        volume_mult_15m REAL,
        volume_mult_1h REAL,
        retention_proxy REAL,
        taker_buy_ratio_15m REAL,
        oi_change_1h_pct REAL,
        funding_rate REAL,
        wakeup INTEGER,
        persistence INTEGER,
        retention INTEGER,
        reignition INTEGER,
        trigger INTEGER,
        climax_risk INTEGER,
        raw_json TEXT
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_features_symbol_time
    ON features(symbol, ts_utc)
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS daily_movers (
        trade_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        change_24h REAL,
        quote_volume_24h REAL,
        rank_no INTEGER,
        config_version TEXT,
        PRIMARY KEY (trade_date, symbol)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS winner_anatomy (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analyzed_at_utc TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        winner_change_24h REAL,
        first_anomaly_time_utc TEXT,
        first_anomaly_price REAL,
        winner_reference_price REAL,
        gain_before_first_anomaly REAL,
        hours_before_reference REAL,
        anomaly_volume_z REAL,
        anomaly_trade_z REAL,
        anomaly_return_z REAL,
        anomaly_volume_mult REAL,
        raw_json TEXT,
        UNIQUE(trade_date, symbol)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS data_issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        issue_type TEXT NOT NULL,
        details TEXT
    )
    """)

    conn.commit()
    conn.close()


def save_scan(ts_utc, config_version, universe_size, btc_change_24h):
    conn = open_db()

    conn.execute(
        """
        INSERT INTO scans(
            ts_utc,
            config_version,
            universe_size,
            btc_change_24h
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            ts_utc,
            config_version,
            universe_size,
            btc_change_24h,
        ),
    )

    conn.commit()
    conn.close()


def save_feature(feature, is_selected=0):
    conn = open_db()

    conn.execute("""
    INSERT INTO features(
        ts_utc,
        config_version,
        symbol,
        stage,
        engine,
        score,
        is_selected,
        price,
        change_15m,
        change_1h,
        change_3h,
        change_24h,
        btc_relative_24h,
        volume_z_15m,
        trade_z_15m,
        return_z_15m,
        volume_mult_15m,
        volume_mult_1h,
        retention_proxy,
        taker_buy_ratio_15m,
        oi_change_1h_pct,
        funding_rate,
        wakeup,
        persistence,
        retention,
        reignition,
        trigger,
        climax_risk,
        raw_json
    )
    VALUES (
        ?,?,?,?,?,?,?,
        ?,?,?,?,?,?,
        ?,?,?,?,?,?,
        ?,?,
        ?,?,?,?,?,?,
        ?,?
    )
    """, (
        feature.get("ts_utc"),
        feature.get("config_version"),
        feature.get("symbol"),
        feature.get("stage"),
        feature.get("engine"),
        feature.get("score"),
        is_selected,
        feature.get("price"),
        feature.get("change_15m"),
        feature.get("change_1h"),
        feature.get("change_3h"),
        feature.get("change_24h"),
        feature.get("btc_relative_24h"),
        feature.get("volume_z_15m"),
        feature.get("trade_z_15m"),
        feature.get("return_z_15m"),
        feature.get("volume_mult_15m"),
        feature.get("volume_mult_1h"),
        feature.get("retention_proxy"),
        feature.get("taker_buy_ratio_15m"),
        feature.get("oi_change_1h_pct"),
        feature.get("funding_rate"),
        int(bool(feature.get("wakeup"))),
        int(bool(feature.get("persistence"))),
        int(bool(feature.get("retention"))),
        int(bool(feature.get("reignition"))),
        int(bool(feature.get("trigger"))),
        int(bool(feature.get("climax_risk"))),
        json.dumps(feature, ensure_ascii=False),
    ))

    conn.commit()
    conn.close()


def save_daily_mover(
    trade_date,
    symbol,
    change_24h,
    quote_volume_24h,
    rank_no,
    config_version,
):
    conn = open_db()

    conn.execute("""
    INSERT OR REPLACE INTO daily_movers(
        trade_date,
        symbol,
        change_24h,
        quote_volume_24h,
        rank_no,
        config_version
    )
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        trade_date,
        symbol,
        change_24h,
        quote_volume_24h,
        rank_no,
        config_version,
    ))

    conn.commit()
    conn.close()


def save_winner_anatomy(row):
    conn = open_db()

    conn.execute("""
    INSERT OR REPLACE INTO winner_anatomy(
        analyzed_at_utc,
        trade_date,
        symbol,
        winner_change_24h,
        first_anomaly_time_utc,
        first_anomaly_price,
        winner_reference_price,
        gain_before_first_anomaly,
        hours_before_reference,
        anomaly_volume_z,
        anomaly_trade_z,
        anomaly_return_z,
        anomaly_volume_mult,
        raw_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row.get("analyzed_at_utc"),
        row.get("trade_date"),
        row.get("symbol"),
        row.get("winner_change_24h"),
        row.get("first_anomaly_time_utc"),
        row.get("first_anomaly_price"),
        row.get("winner_reference_price"),
        row.get("gain_before_first_anomaly"),
        row.get("hours_before_reference"),
        row.get("anomaly_volume_z"),
        row.get("anomaly_trade_z"),
        row.get("anomaly_return_z"),
        row.get("anomaly_volume_mult"),
        json.dumps(row, ensure_ascii=False),
    ))

    conn.commit()
    conn.close()


def save_data_issue(issue_type, details, ts_utc=None):
    if ts_utc is None:
        ts_utc = datetime.now(timezone.utc).isoformat()

    conn = open_db()

    conn.execute(
        """
        INSERT INTO data_issues(
            ts_utc,
            issue_type,
            details
        )
        VALUES (?, ?, ?)
        """,
        (
            ts_utc,
            issue_type,
            details,
        ),
    )

    conn.commit()
    conn.close()
