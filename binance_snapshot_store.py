#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sqlite3
from datetime import datetime, timezone, timedelta

DB_FILE = "binance_avci2.db"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def open_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn, table):
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def _add_column(conn, table, definition):
    name = definition.split()[0]

    if name not in _columns(conn, table):
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {definition}"
        )


def _backfill_column(
    conn,
    table,
    new_column,
    old_column,
):
    columns = _columns(
        conn,
        table,
    )

    if (
        new_column in columns
        and old_column in columns
    ):
        conn.execute(
            f"""
            UPDATE {table}
            SET {new_column} = {old_column}
            WHERE {new_column} IS NULL
            """
        )


def init_db():
    conn = open_db()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time_utc TEXT,
            config_version TEXT,
            data_mode TEXT,
            universe_size INTEGER,
            btc_change_24h REAL,
            created_at_utc TEXT
        );

        CREATE TABLE IF NOT EXISTS features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time_utc TEXT,
            config_version TEXT,
            data_mode TEXT,
            symbol TEXT,
            stage TEXT,
            engine TEXT,
            score INTEGER,
            is_signal INTEGER DEFAULT 0,
            is_selected INTEGER DEFAULT 0,
            price REAL,
            signal_bar_open_ms INTEGER,
            signal_bar_close_ms INTEGER,
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
            raw_json TEXT,
            created_at_utc TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_movers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            symbol TEXT,
            change_24h REAL,
            quote_volume_24h REAL,
            rank_value INTEGER,
            config_version TEXT,
            created_at_utc TEXT
        );

        CREATE TABLE IF NOT EXISTS signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE,
            symbol TEXT,
            signal_time_utc TEXT,
            signal_bar_open_ms INTEGER,
            signal_bar_close_ms INTEGER,
            config_version TEXT,
            data_mode TEXT,
            stage TEXT,
            engine TEXT,
            score INTEGER,
            signal_price REAL,
            entry_open_time_ms INTEGER,
            entry_status TEXT DEFAULT 'PENDING',
            entry_time_utc TEXT,
            entry_price_raw REAL,
            entry_price_exec REAL,
            fee_bps_per_side REAL,
            slippage_bps_per_side REAL,
            outcome_status TEXT DEFAULT 'OPEN',
            closed_at_utc TEXT,
            cooldown_hours INTEGER,
            raw_json TEXT,
            created_at_utc TEXT
        );

        CREATE TABLE IF NOT EXISTS outcome_labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE,
            symbol TEXT,
            entry_time_utc TEXT,
            entry_price_exec REAL,
            stop_pct REAL,
            horizon_hours INTEGER,
            first_touch_json TEXT,
            reach_json TEXT,
            hit_time_json TEXT,
            mfe_pct REAL,
            mae_pct REAL,
            raw_mfe_pct REAL,
            executable_mfe_pct REAL,
            net_return_pct REAL,
            btc_return_pct REAL,
            universe_return_pct REAL,
            excess_vs_btc_pct REAL,
            excess_vs_universe_pct REAL,
            label_status TEXT,
            updated_at_utc TEXT
        );

        CREATE TABLE IF NOT EXISTS winner_anatomy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analyzed_at_utc TEXT,
            trade_date TEXT,
            symbol TEXT,
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
            created_at_utc TEXT
        );

        CREATE TABLE IF NOT EXISTS data_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_time_utc TEXT,
            issue_type TEXT,
            details TEXT,
            created_at_utc TEXT
        );
        """
    )

    for definition in (
        "scan_time_utc TEXT",
        "config_version TEXT",
        "data_mode TEXT",
        "universe_size INTEGER",
        "btc_change_24h REAL",
        "created_at_utc TEXT",
    ):
        _add_column(
            conn,
            "scans",
            definition,
        )

    _backfill_column(
        conn,
        "scans",
        "scan_time_utc",
        "ts_utc",
    )

    for definition in (
        "scan_time_utc TEXT",
        "config_version TEXT",
        "data_mode TEXT",
        "symbol TEXT",
        "stage TEXT",
        "engine TEXT",
        "score INTEGER",
        "is_signal INTEGER DEFAULT 0",
        "is_selected INTEGER DEFAULT 0",
        "price REAL",
        "signal_bar_open_ms INTEGER",
        "signal_bar_close_ms INTEGER",
        "change_15m REAL",
        "change_1h REAL",
        "change_3h REAL",
        "change_24h REAL",
        "btc_relative_24h REAL",
        "volume_z_15m REAL",
        "trade_z_15m REAL",
        "return_z_15m REAL",
        "volume_mult_15m REAL",
        "volume_mult_1h REAL",
        "retention_proxy REAL",
        "taker_buy_ratio_15m REAL",
        "oi_change_1h_pct REAL",
        "funding_rate REAL",
        "wakeup INTEGER",
        "persistence INTEGER",
        "retention INTEGER",
        "reignition INTEGER",
        "trigger INTEGER",
        "climax_risk INTEGER",
        "raw_json TEXT",
        "created_at_utc TEXT",
    ):
        _add_column(
            conn,
            "features",
            definition,
        )

    _backfill_column(
        conn,
        "features",
        "scan_time_utc",
        "ts_utc",
    )

    for definition in (
        "trade_date TEXT",
        "symbol TEXT",
        "change_24h REAL",
        "quote_volume_24h REAL",
        "rank_value INTEGER",
        "config_version TEXT",
        "created_at_utc TEXT",
    ):
        _add_column(
            conn,
            "daily_movers",
            definition,
        )

    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_scans_unique
        ON scans(
            scan_time_utc,
            config_version
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_features_unique
        ON features(
            scan_time_utc,
            config_version,
            symbol
        );

        CREATE INDEX IF NOT EXISTS idx_features_symbol_time
        ON features(
            symbol,
            scan_time_utc
        );

        CREATE INDEX IF NOT EXISTS idx_features_signal
        ON features(
            is_signal,
            scan_time_utc
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_movers_unique
        ON daily_movers(
            trade_date,
            symbol,
            config_version
        );

        CREATE INDEX IF NOT EXISTS idx_signal_events_symbol_time
        ON signal_events(
            symbol,
            signal_time_utc
        );

        CREATE INDEX IF NOT EXISTS idx_signal_events_status
        ON signal_events(
            outcome_status,
            entry_status
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_winner_anatomy_unique
        ON winner_anatomy(
            trade_date,
            symbol
        );
        """
    )

    conn.commit()
    conn.close()


def save_scan(
    scan_time_utc,
    config_version,
    universe_size,
    btc_change_24h,
    data_mode="UNKNOWN",
):
    conn = open_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO scans (
            scan_time_utc,
            config_version,
            data_mode,
            universe_size,
            btc_change_24h,
            created_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            scan_time_utc,
            config_version,
            data_mode,
            universe_size,
            btc_change_24h,
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


def save_feature(
    feature,
    is_selected=0,
    is_signal=None,
):
    if is_signal is None:
        is_signal = int(
            feature.get("stage") != "OBSERVE"
            and not feature.get(
                "climax_risk",
                False,
            )
        )

    raw_json = json.dumps(
        feature,
        ensure_ascii=False,
        sort_keys=True,
    )

    conn = open_db()

    conn.execute(
        """
        INSERT INTO features (
            scan_time_utc,
            config_version,
            data_mode,
            symbol,
            stage,
            engine,
            score,
            is_signal,
            is_selected,
            price,
            signal_bar_open_ms,
            signal_bar_close_ms,
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
            raw_json,
            created_at_utc
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        ON CONFLICT(
            scan_time_utc,
            config_version,
            symbol
        )
        DO UPDATE SET
            data_mode=excluded.data_mode,
            stage=excluded.stage,
            engine=excluded.engine,
            score=excluded.score,
            is_signal=excluded.is_signal,
            is_selected=MAX(
                features.is_selected,
                excluded.is_selected
            ),
            price=excluded.price,
            signal_bar_open_ms=excluded.signal_bar_open_ms,
            signal_bar_close_ms=excluded.signal_bar_close_ms,
            change_15m=excluded.change_15m,
            change_1h=excluded.change_1h,
            change_3h=excluded.change_3h,
            change_24h=excluded.change_24h,
            btc_relative_24h=excluded.btc_relative_24h,
            volume_z_15m=excluded.volume_z_15m,
            trade_z_15m=excluded.trade_z_15m,
            return_z_15m=excluded.return_z_15m,
            volume_mult_15m=excluded.volume_mult_15m,
            volume_mult_1h=excluded.volume_mult_1h,
            retention_proxy=excluded.retention_proxy,
            taker_buy_ratio_15m=excluded.taker_buy_ratio_15m,
            oi_change_1h_pct=excluded.oi_change_1h_pct,
            funding_rate=excluded.funding_rate,
            wakeup=excluded.wakeup,
            persistence=excluded.persistence,
            retention=excluded.retention,
            reignition=excluded.reignition,
            trigger=excluded.trigger,
            climax_risk=excluded.climax_risk,
            raw_json=excluded.raw_json
        """,
        (
            feature["ts_utc"],
            feature["config_version"],
            feature.get(
                "data_mode",
                "UNKNOWN",
            ),
            feature["symbol"],
            feature["stage"],
            feature["engine"],
            int(
                feature["score"]
            ),
            int(
                bool(
                    is_signal
                )
            ),
            int(
                bool(
                    is_selected
                )
            ),
            feature.get(
                "price"
            ),
            feature.get(
                "signal_bar_open_ms"
            ),
            feature.get(
                "signal_bar_close_ms"
            ),
            feature.get(
                "change_15m"
            ),
            feature.get(
                "change_1h"
            ),
            feature.get(
                "change_3h"
            ),
            feature.get(
                "change_24h"
            ),
            feature.get(
                "btc_relative_24h"
            ),
            feature.get(
                "volume_z_15m"
            ),
            feature.get(
                "trade_z_15m"
            ),
            feature.get(
                "return_z_15m"
            ),
            feature.get(
                "volume_mult_15m"
            ),
            feature.get(
                "volume_mult_1h"
            ),
            feature.get(
                "retention_proxy"
            ),
            feature.get(
                "taker_buy_ratio_15m"
            ),
            feature.get(
                "oi_change_1h_pct"
            ),
            feature.get(
                "funding_rate"
            ),
            int(
                bool(
                    feature.get(
                        "wakeup"
                    )
                )
            ),
            int(
                bool(
                    feature.get(
                        "persistence"
                    )
                )
            ),
            int(
                bool(
                    feature.get(
                        "retention"
                    )
                )
            ),
            int(
                bool(
                    feature.get(
                        "reignition"
                    )
                )
            ),
            int(
                bool(
                    feature.get(
                        "trigger"
                    )
                )
            ),
            int(
                bool(
                    feature.get(
                        "climax_risk"
                    )
                )
            ),
            raw_json,
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


def save_daily_mover(
    trade_date,
    symbol,
    change_24h,
    quote_volume_24h,
    rank_value,
    config_version,
):
    conn = open_db()

    conn.execute(
        """
        INSERT OR REPLACE INTO daily_movers (
            trade_date,
            symbol,
            change_24h,
            quote_volume_24h,
            rank_value,
            config_version,
            created_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_date,
            symbol,
            change_24h,
            quote_volume_24h,
            rank_value,
            config_version,
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


def has_recent_event(
    symbol,
    signal_time_utc,
    cooldown_hours=24,
):
    cutoff = (
        datetime.fromisoformat(
            signal_time_utc
        )
        - timedelta(
            hours=cooldown_hours
        )
    ).isoformat()

    conn = open_db()

    row = conn.execute(
        """
        SELECT event_id
        FROM signal_events
        WHERE symbol = ?
          AND signal_time_utc >= ?
        ORDER BY signal_time_utc DESC
        LIMIT 1
        """,
        (
            symbol,
            cutoff,
        ),
    ).fetchone()

    conn.close()

    return row is not None


def create_signal_event(
    feature,
    cooldown_hours,
    fee_bps_per_side,
    slippage_bps_per_side,
):
    signal_time_utc = (
        feature[
            "ts_utc"
        ]
    )

    symbol = (
        feature[
            "symbol"
        ]
    )

    if has_recent_event(
        symbol,
        signal_time_utc,
        cooldown_hours,
    ):
        return None

    signal_bar_open_ms = int(
        feature[
            "signal_bar_open_ms"
        ]
    )

    signal_bar_close_ms = int(
        feature[
            "signal_bar_close_ms"
        ]
    )

    interval_ms = (
        5
        * 60
        * 1000
    )

    entry_open_time_ms = (
        signal_bar_open_ms
        + interval_ms
    )

    event_id = (
        f"{symbol}-"
        f"{signal_bar_close_ms}-"
        f"{feature['config_version']}"
    )

    raw_json = json.dumps(
        feature,
        ensure_ascii=False,
        sort_keys=True,
    )

    conn = open_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO signal_events (
            event_id,
            symbol,
            signal_time_utc,
            signal_bar_open_ms,
            signal_bar_close_ms,
            config_version,
            data_mode,
            stage,
            engine,
            score,
            signal_price,
            entry_open_time_ms,
            fee_bps_per_side,
            slippage_bps_per_side,
            cooldown_hours,
            raw_json,
            created_at_utc
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            event_id,
            symbol,
            signal_time_utc,
            signal_bar_open_ms,
            signal_bar_close_ms,
            feature[
                "config_version"
            ],
            feature.get(
                "data_mode",
                "UNKNOWN",
            ),
            feature[
                "stage"
            ],
            feature[
                "engine"
            ],
            int(
                feature[
                    "score"
                ]
            ),
            feature[
                "price"
            ],
            entry_open_time_ms,
            fee_bps_per_side,
            slippage_bps_per_side,
            cooldown_hours,
            raw_json,
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()

    return event_id


def get_pending_events():
    conn = open_db()

    rows = conn.execute(
        """
        SELECT *
        FROM signal_events
        WHERE outcome_status = 'OPEN'
        ORDER BY signal_time_utc
        """
    ).fetchall()

    conn.close()

    return [
        dict(
            row
        )
        for row
        in rows
    ]


def update_event_entry(
    event_id,
    entry_time_utc,
    entry_price_raw,
    entry_price_exec,
):
    conn = open_db()

    conn.execute(
        """
        UPDATE signal_events
        SET entry_status = 'READY',
            entry_time_utc = ?,
            entry_price_raw = ?,
            entry_price_exec = ?
        WHERE event_id = ?
        """,
        (
            entry_time_utc,
            entry_price_raw,
            entry_price_exec,
            event_id,
        ),
    )

    conn.commit()
    conn.close()


def close_event(
    event_id,
    closed_at_utc,
):
    conn = open_db()

    conn.execute(
        """
        UPDATE signal_events
        SET outcome_status = 'CLOSED',
            closed_at_utc = ?
        WHERE event_id = ?
        """,
        (
            closed_at_utc,
            event_id,
        ),
    )

    conn.commit()
    conn.close()


def save_outcome_label(
    label,
):
    conn = open_db()

    conn.execute(
        """
        INSERT INTO outcome_labels (
            event_id,
            symbol,
            entry_time_utc,
            entry_price_exec,
            stop_pct,
            horizon_hours,
            first_touch_json,
            reach_json,
            hit_time_json,
            mfe_pct,
            mae_pct,
            raw_mfe_pct,
            executable_mfe_pct,
            net_return_pct,
            btc_return_pct,
            universe_return_pct,
            excess_vs_btc_pct,
            excess_vs_universe_pct,
            label_status,
            updated_at_utc
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(event_id)
        DO UPDATE SET
            entry_time_utc=excluded.entry_time_utc,
            entry_price_exec=excluded.entry_price_exec,
            stop_pct=excluded.stop_pct,
            horizon_hours=excluded.horizon_hours,
            first_touch_json=excluded.first_touch_json,
            reach_json=excluded.reach_json,
            hit_time_json=excluded.hit_time_json,
            mfe_pct=excluded.mfe_pct,
            mae_pct=excluded.mae_pct,
            raw_mfe_pct=excluded.raw_mfe_pct,
            executable_mfe_pct=excluded.executable_mfe_pct,
            net_return_pct=excluded.net_return_pct,
            btc_return_pct=excluded.btc_return_pct,
            universe_return_pct=excluded.universe_return_pct,
            excess_vs_btc_pct=excluded.excess_vs_btc_pct,
            excess_vs_universe_pct=excluded.excess_vs_universe_pct,
            label_status=excluded.label_status,
            updated_at_utc=excluded.updated_at_utc
        """,
        (
            label[
                "event_id"
            ],
            label[
                "symbol"
            ],
            label.get(
                "entry_time_utc"
            ),
            label.get(
                "entry_price_exec"
            ),
            label.get(
                "stop_pct"
            ),
            label.get(
                "horizon_hours"
            ),
            json.dumps(
                label.get(
                    "first_touch",
                    {},
                ),
                sort_keys=True,
            ),
            json.dumps(
                label.get(
                    "reach",
                    {},
                ),
                sort_keys=True,
            ),
            json.dumps(
                label.get(
                    "hit_time",
                    {},
                ),
                sort_keys=True,
            ),
            label.get(
                "mfe_pct"
            ),
            label.get(
                "mae_pct"
            ),
            label.get(
                "raw_mfe_pct"
            ),
            label.get(
                "executable_mfe_pct"
            ),
            label.get(
                "net_return_pct"
            ),
            label.get(
                "btc_return_pct"
            ),
            label.get(
                "universe_return_pct"
            ),
            label.get(
                "excess_vs_btc_pct"
            ),
            label.get(
                "excess_vs_universe_pct"
            ),
            label.get(
                "label_status",
                "OPEN",
            ),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


def save_winner_anatomy(
    row,
):
    conn = open_db()

    conn.execute(
        """
        INSERT OR REPLACE INTO winner_anatomy (
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
            created_at_utc
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        """,
        (
            row[
                "analyzed_at_utc"
            ],
            row[
                "trade_date"
            ],
            row[
                "symbol"
            ],
            row.get(
                "winner_change_24h"
            ),
            row.get(
                "first_anomaly_time_utc"
            ),
            row.get(
                "first_anomaly_price"
            ),
            row.get(
                "winner_reference_price"
            ),
            row.get(
                "gain_before_first_anomaly"
            ),
            row.get(
                "hours_before_reference"
            ),
            row.get(
                "anomaly_volume_z"
            ),
            row.get(
                "anomaly_trade_z"
            ),
            row.get(
                "anomaly_return_z"
            ),
            row.get(
                "anomaly_volume_mult"
            ),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


def save_data_issue(
    issue_type,
    details,
    issue_time_utc=None,
):
    conn = open_db()

    conn.execute(
        """
        INSERT INTO data_issues (
            issue_time_utc,
            issue_type,
            details,
            created_at_utc
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            issue_time_utc
            or utc_now(),
            issue_type,
            details,
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()
