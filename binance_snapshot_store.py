import json
import sqlite3
import statistics
import time
from datetime import datetime, timezone, timedelta

DB_FILE = "binance_avci2.db"

DB_TIMEOUT_SECONDS = 60
DB_BUSY_TIMEOUT_MS = 60000
DB_RETRY_COUNT = 8
DB_RETRY_SLEEP = 0.25

FEATURE_RAW_EXTRA_KEYS = (
    "quote_volume_24h",
    "quote_volume_15m",
    "quote_volume_1h",
    "quote_volume_3h",
    "futures_change_24h",
    "futures_taker_ratio_1h",
    "wake_components",
    "trigger_components",
    "data_staleness_minutes",
    "impulse_start_ms",
    "impulse_low",
    "impulse_high",
    "impulse_return_pct",
    "impulse_volume_multiple",
    "near_miss_distance",
    "manipulation_risk",
    "catalyst_status",
    "coin_age_days",
)


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def open_db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=DB_TIMEOUT_SECONDS,
        isolation_level=None,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}"
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA synchronous=NORMAL"
    )

    conn.execute(
        "PRAGMA wal_autocheckpoint=1000"
    )

    return conn


def _is_locked_error(error):
    text = str(
        error
    ).lower()

    return (
        "database is locked" in text
        or
        "database table is locked" in text
    )


def _retry_write(callback):
    last_error = None

    for attempt in range(
        DB_RETRY_COUNT
    ):
        conn = None

        try:
            conn = open_db()

            result = callback(
                conn
            )

            return result

        except sqlite3.OperationalError as error:
            last_error = error

            if not _is_locked_error(
                error
            ):
                raise

            time.sleep(
                DB_RETRY_SLEEP
                * (
                    attempt
                    + 1
                )
            )

        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    if last_error is not None:
        raise last_error

    raise RuntimeError(
        "Database write failed"
    )


def _retry_read(callback):
    last_error = None

    for attempt in range(
        DB_RETRY_COUNT
    ):
        conn = None

        try:
            conn = open_db()

            return callback(
                conn
            )

        except sqlite3.OperationalError as error:
            last_error = error

            if not _is_locked_error(
                error
            ):
                raise

            time.sleep(
                DB_RETRY_SLEEP
                * (
                    attempt
                    + 1
                )
            )

        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    if last_error is not None:
        raise last_error

    raise RuntimeError(
        "Database read failed"
    )


def _columns(
    conn,
    table,
):
    return {
        row["name"]
        for row
        in conn.execute(
            f"PRAGMA table_info({table})"
        )
    }


def _add_column(
    conn,
    table,
    definition,
):
    name = (
        definition.split()[0]
    )

    if name not in _columns(
        conn,
        table,
    ):
        conn.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {definition}"
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
        and
        old_column in columns
    ):
        conn.execute(
            f"""
            UPDATE {table}
            SET {new_column} = {old_column}
            WHERE {new_column} IS NULL
            """
        )


def _drop_index(
    conn,
    index_name,
):
    conn.execute(
        f"DROP INDEX IF EXISTS {index_name}"
    )


def _dedupe_scans(
    conn,
):
    columns = _columns(
        conn,
        "scans",
    )

    required = {
        "id",
        "scan_time_utc",
        "config_version",
    }

    if not required.issubset(
        columns
    ):
        return

    conn.execute(
        """
        DELETE FROM scans
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM scans
            WHERE scan_time_utc IS NOT NULL
              AND config_version IS NOT NULL
            GROUP BY
                scan_time_utc,
                config_version
        )
        AND scan_time_utc IS NOT NULL
        AND config_version IS NOT NULL
        """
    )


def _dedupe_features(
    conn,
):
    columns = _columns(
        conn,
        "features",
    )

    required = {
        "id",
        "scan_time_utc",
        "config_version",
        "symbol",
    }

    if not required.issubset(
        columns
    ):
        return

    if "is_selected" in columns:
        conn.execute(
            """
            UPDATE features
            SET is_selected = (
                SELECT MAX(
                    COALESCE(
                        f2.is_selected,
                        0
                    )
                )
                FROM features f2
                WHERE
                    f2.scan_time_utc =
                        features.scan_time_utc
                    AND
                    f2.config_version =
                        features.config_version
                    AND
                    f2.symbol =
                        features.symbol
            )
            WHERE scan_time_utc IS NOT NULL
              AND config_version IS NOT NULL
              AND symbol IS NOT NULL
            """
        )

    if "is_signal" in columns:
        conn.execute(
            """
            UPDATE features
            SET is_signal = (
                SELECT MAX(
                    COALESCE(
                        f2.is_signal,
                        0
                    )
                )
                FROM features f2
                WHERE
                    f2.scan_time_utc =
                        features.scan_time_utc
                    AND
                    f2.config_version =
                        features.config_version
                    AND
                    f2.symbol =
                        features.symbol
            )
            WHERE scan_time_utc IS NOT NULL
              AND config_version IS NOT NULL
              AND symbol IS NOT NULL
            """
        )

    conn.execute(
        """
        DELETE FROM features
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM features
            WHERE scan_time_utc IS NOT NULL
              AND config_version IS NOT NULL
              AND symbol IS NOT NULL
            GROUP BY
                scan_time_utc,
                config_version,
                symbol
        )
        AND scan_time_utc IS NOT NULL
        AND config_version IS NOT NULL
        AND symbol IS NOT NULL
        """
    )


def _dedupe_daily_movers(
    conn,
):
    columns = _columns(
        conn,
        "daily_movers",
    )

    required = {
        "id",
        "trade_date",
        "symbol",
        "config_version",
    }

    if not required.issubset(
        columns
    ):
        return

    conn.execute(
        """
        DELETE FROM daily_movers
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM daily_movers
            WHERE trade_date IS NOT NULL
              AND symbol IS NOT NULL
              AND config_version IS NOT NULL
            GROUP BY
                trade_date,
                symbol,
                config_version
        )
        AND trade_date IS NOT NULL
        AND symbol IS NOT NULL
        AND config_version IS NOT NULL
        """
    )


def _dedupe_winner_anatomy(
    conn,
):
    columns = _columns(
        conn,
        "winner_anatomy",
    )

    required = {
        "id",
        "trade_date",
        "symbol",
    }

    if not required.issubset(
        columns
    ):
        return

    conn.execute(
        """
        DELETE FROM winner_anatomy
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM winner_anatomy
            WHERE trade_date IS NOT NULL
              AND symbol IS NOT NULL
            GROUP BY
                trade_date,
                symbol
        )
        AND trade_date IS NOT NULL
        AND symbol IS NOT NULL
        """
    )


def init_db():
    def operation(
        conn,
    ):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time_utc TEXT,
                config_version TEXT,
                data_mode TEXT,
                universe_size INTEGER,
                btc_change_24h REAL,
                btc_regime TEXT,
                health_status TEXT,
                config_hash TEXT,
                git_sha TEXT,
                clock_skew_seconds REAL,
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
                selection_class TEXT DEFAULT 'NONE',
                validation_tier TEXT DEFAULT 'OBSERVATIONAL',
                btc_regime TEXT,
                spread_bps REAL,
                volume_rarity_pct REAL,
                trade_rarity_pct REAL,
                return_rarity_pct REAL,
                cross_sectional_rarity_pct REAL,
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
                first_anomaly_time_utc TEXT,
                first_anomaly_price REAL,
                gain_before_signal_pct REAL,
                minutes_from_first_anomaly REAL,
                entry_open_time_ms INTEGER,
                entry_delay_seconds INTEGER DEFAULT 120,
                entry_status TEXT DEFAULT 'PENDING',
                entry_time_utc TEXT,
                entry_price_raw REAL,
                entry_price_exec REAL,
                fee_bps_per_side REAL,
                slippage_bps_per_side REAL,
                outcome_status TEXT DEFAULT 'OPEN',
                closed_at_utc TEXT,
                cooldown_hours INTEGER,
                event_class TEXT DEFAULT 'LEGACY_SIGNAL',
                validation_tier TEXT DEFAULT 'LEGACY',
                btc_regime TEXT,
                near_miss_distance REAL,
                manipulation_risk INTEGER DEFAULT 0,
                spread_bps REAL,
                buy_impact_1k_bps REAL,
                sell_impact_1k_bps REAL,
                buy_impact_5k_bps REAL,
                sell_impact_5k_bps REAL,
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
                primary_target_pct REAL,
                primary_exit_reason TEXT,
                primary_exit_time_utc TEXT,
                primary_exit_return_pct REAL,
                barrier_results_json TEXT,
                horizon_metrics_json TEXT,
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

            CREATE TABLE IF NOT EXISTS winner_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                research_version TEXT,
                subject_class TEXT,
                symbol TEXT,
                matched_winner_event_id TEXT,
                event_start_time_utc TEXT,
                threshold_time_utc TEXT,
                peak_time_utc TEXT,
                horizon_end_time_utc TEXT,
                event_status TEXT,
                start_price REAL,
                peak_price REAL,
                max_gain_pct REAL,
                max_drawdown_pct REAL,
                highest_level_reached REAL,
                levels_reached_json TEXT,
                first_anomaly_time_utc TEXT,
                first_anomaly_price REAL,
                gain_before_first_anomaly_pct REAL,
                hours_anomaly_before_start REAL,
                pre_volume_24h REAL,
                pre_volatility_24h REAL,
                analyzed_at_utc TEXT,
                raw_json TEXT,
                created_at_utc TEXT
            );

            CREATE TABLE IF NOT EXISTS pre_event_features (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                research_version TEXT,
                subject_class TEXT,
                symbol TEXT,
                offset_hours INTEGER,
                feature_time_utc TEXT,
                price REAL,
                return_15m_pct REAL,
                return_1h_pct REAL,
                return_3h_pct REAL,
                return_6h_pct REAL,
                return_12h_pct REAL,
                return_24h_pct REAL,
                btc_return_24h_pct REAL,
                excess_vs_btc_24h_pct REAL,
                quote_volume_15m REAL,
                quote_volume_1h REAL,
                quote_volume_24h REAL,
                volume_z_15m REAL,
                trade_z_15m REAL,
                return_z_15m REAL,
                volume_mult_15m REAL,
                volume_mult_1h REAL,
                taker_buy_ratio_15m REAL,
                realized_volatility_24h REAL,
                range_compression_24h REAL,
                raw_json TEXT,
                created_at_utc TEXT,
                UNIQUE(event_id, symbol, offset_hours, research_version)
            );

            CREATE TABLE IF NOT EXISTS data_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_time_utc TEXT,
                issue_type TEXT,
                details TEXT,
                created_at_utc TEXT
            );

            CREATE TABLE IF NOT EXISTS raw_klines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                interval_value TEXT,
                open_time_ms INTEGER,
                close_time_ms INTEGER,
                open_price REAL,
                high_price REAL,
                low_price REAL,
                close_price REAL,
                quote_volume REAL,
                trade_count INTEGER,
                taker_buy_quote REAL,
                config_version TEXT,
                created_at_utc TEXT,
                UNIQUE(symbol, interval_value, open_time_ms)
            );

            CREATE TABLE IF NOT EXISTS raw_derivs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time_utc TEXT,
                symbol TEXT,
                oi_change_1h_pct REAL,
                funding_rate REAL,
                futures_taker_ratio_1h REAL,
                data_mode TEXT,
                config_version TEXT,
                created_at_utc TEXT,
                UNIQUE(scan_time_utc, symbol, config_version)
            );

            CREATE TABLE IF NOT EXISTS orderbook_snap (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time_utc TEXT,
                symbol TEXT,
                event_class TEXT,
                best_bid REAL,
                best_ask REAL,
                spread_bps REAL,
                buy_impact_1k_bps REAL,
                sell_impact_1k_bps REAL,
                buy_impact_5k_bps REAL,
                sell_impact_5k_bps REAL,
                visible_bid_capacity_usd REAL,
                visible_ask_capacity_usd REAL,
                raw_json TEXT,
                config_version TEXT,
                created_at_utc TEXT,
                UNIQUE(scan_time_utc, symbol, event_class, config_version)
            );

            CREATE TABLE IF NOT EXISTS asset_metadata (
                symbol TEXT PRIMARY KEY,
                base_asset TEXT,
                listing_time_ms INTEGER,
                first_seen_utc TEXT,
                last_seen_utc TEXT,
                last_status TEXT,
                quote_asset TEXT
            );

            CREATE TABLE IF NOT EXISTS universe_history (
                scan_time_utc TEXT,
                symbol TEXT,
                status TEXT,
                exclusion_reason TEXT,
                config_version TEXT,
                UNIQUE(scan_time_utc, symbol, config_version)
            );

            CREATE TABLE IF NOT EXISTS manual_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                action TEXT NOT NULL,
                decision_time_utc TEXT,
                execution_time_utc TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL,
                fee_quote REAL DEFAULT 0,
                notes TEXT,
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
            "btc_regime TEXT",
            "health_status TEXT",
            "config_hash TEXT",
            "git_sha TEXT",
            "clock_skew_seconds REAL",
            "btc_flash_15m_pct REAL",
            "btc_flash_crash INTEGER DEFAULT 0",
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
            "selection_class TEXT DEFAULT 'NONE'",
            "validation_tier TEXT DEFAULT 'OBSERVATIONAL'",
            "btc_regime TEXT",
            "spread_bps REAL",
            "volume_rarity_pct REAL",
            "trade_rarity_pct REAL",
            "return_rarity_pct REAL",
            "cross_sectional_rarity_pct REAL",
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

        feature_columns = _columns(
            conn,
            "features",
        )

        if {
            "is_selected",
            "selected",
        }.issubset(
            feature_columns
        ):
            conn.execute(
                """
                UPDATE features
                SET is_selected = MAX(
                    COALESCE(
                        is_selected,
                        0
                    ),
                    COALESCE(
                        selected,
                        0
                    )
                )
                """
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

        for definition in (
            "event_class TEXT DEFAULT 'LEGACY_SIGNAL'",
            "entry_delay_seconds INTEGER DEFAULT 120",
            "first_anomaly_time_utc TEXT",
            "first_anomaly_price REAL",
            "gain_before_signal_pct REAL",
            "minutes_from_first_anomaly REAL",
            "validation_tier TEXT DEFAULT 'LEGACY'",
            "spread_bps REAL",
            "buy_impact_1k_bps REAL",
            "sell_impact_1k_bps REAL",
            "buy_impact_5k_bps REAL",
            "sell_impact_5k_bps REAL",
            "btc_regime TEXT",
            "near_miss_distance REAL",
            "manipulation_risk INTEGER DEFAULT 0",
        ):
            _add_column(
                conn,
                "signal_events",
                definition,
            )

        conn.execute(
            """
            UPDATE signal_events
            SET event_class = 'LEGACY_SIGNAL'
            WHERE event_class IS NULL
               OR event_class = ''
            """
        )

        for definition in (
            "primary_target_pct REAL",
            "primary_exit_reason TEXT",
            "primary_exit_time_utc TEXT",
            "primary_exit_return_pct REAL",
            "barrier_results_json TEXT",
            "horizon_metrics_json TEXT",
        ):
            _add_column(
                conn,
                "outcome_labels",
                definition,
            )

        for definition in (
            "visible_bid_capacity_usd REAL",
            "visible_ask_capacity_usd REAL",
            "raw_json TEXT",
        ):
            _add_column(conn, "orderbook_snap", definition)

        _drop_index(
            conn,
            "idx_scans_unique",
        )

        _drop_index(
            conn,
            "idx_features_unique",
        )

        _drop_index(
            conn,
            "idx_daily_movers_unique",
        )

        _drop_index(
            conn,
            "idx_winner_anatomy_unique",
        )

        _dedupe_scans(
            conn
        )

        _dedupe_features(
            conn
        )

        _dedupe_daily_movers(
            conn
        )

        _dedupe_winner_anatomy(
            conn
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

            CREATE INDEX IF NOT EXISTS idx_signal_events_class
            ON signal_events(
                config_version,
                event_class,
                signal_time_utc
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_winner_anatomy_unique
            ON winner_anatomy(
                trade_date,
                symbol
            );

            CREATE INDEX IF NOT EXISTS idx_winner_events_symbol_time
            ON winner_events(symbol, event_start_time_utc);

            CREATE INDEX IF NOT EXISTS idx_winner_events_class_status
            ON winner_events(subject_class, event_status, max_gain_pct);

            CREATE INDEX IF NOT EXISTS idx_pre_event_features_event
            ON pre_event_features(event_id, subject_class, offset_hours);

            CREATE INDEX IF NOT EXISTS idx_raw_klines_symbol_time
            ON raw_klines(symbol, interval_value, open_time_ms);

            CREATE INDEX IF NOT EXISTS idx_raw_derivs_symbol_time
            ON raw_derivs(symbol, scan_time_utc);

            CREATE INDEX IF NOT EXISTS idx_orderbook_symbol_time
            ON orderbook_snap(symbol, scan_time_utc);

            CREATE INDEX IF NOT EXISTS idx_universe_history_symbol_time
            ON universe_history(symbol, scan_time_utc);
            """
        )

    _retry_write(
        operation
    )


def get_asset_metadata(symbol):
    def operation(conn):
        row = conn.execute(
            "SELECT * FROM asset_metadata WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else None
    return _retry_read(operation)


def save_asset_metadata(symbol, base_asset, quote_asset, status,
                        listing_time_ms=None):
    def operation(conn):
        conn.execute(
            """
            INSERT INTO asset_metadata (
                symbol, base_asset, listing_time_ms, first_seen_utc,
                last_seen_utc, last_status, quote_asset
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                base_asset=excluded.base_asset,
                listing_time_ms=COALESCE(asset_metadata.listing_time_ms,
                                         excluded.listing_time_ms),
                last_seen_utc=excluded.last_seen_utc,
                last_status=excluded.last_status,
                quote_asset=excluded.quote_asset
            """,
            (symbol, base_asset, listing_time_ms, utc_now(), utc_now(),
             status, quote_asset),
        )
    _retry_write(operation)


def save_universe_member(scan_time_utc, symbol, status, exclusion_reason,
                         config_version):
    def operation(conn):
        conn.execute(
            """
            INSERT OR REPLACE INTO universe_history (
                scan_time_utc, symbol, status, exclusion_reason, config_version
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (scan_time_utc, symbol, status, exclusion_reason, config_version),
        )
    _retry_write(operation)


def save_manual_trade(event_id, action, execution_time_utc, price,
                      quantity=None, fee_quote=0.0, notes=None,
                      decision_time_utc=None):
    action = str(action).upper()
    if action not in {"ENTRY", "EXIT", "SKIP"}:
        raise ValueError("action ENTRY, EXIT veya SKIP olmali")
    if action != "SKIP" and float(price) <= 0:
        raise ValueError("price pozitif olmali")

    def operation(conn):
        conn.execute(
            """
            INSERT INTO manual_trades (
                event_id, action, decision_time_utc, execution_time_utc,
                price, quantity, fee_quote, notes, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, action, decision_time_utc, execution_time_utc,
             float(price), quantity, fee_quote, notes, utc_now()),
        )
    _retry_write(operation)


def save_scan(
    scan_time_utc,
    config_version,
    universe_size,
    btc_change_24h,
    data_mode="UNKNOWN",
    btc_regime=None,
    health_status=None,
    config_hash=None,
    git_sha=None,
    clock_skew_seconds=None,
):
    def operation(
        conn,
    ):
        conn.execute(
            """
            INSERT OR IGNORE INTO scans (
                scan_time_utc,
                config_version,
                data_mode,
                universe_size,
                btc_change_24h,
                btc_regime,
                health_status,
                config_hash,
                git_sha,
                clock_skew_seconds,
                created_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_time_utc,
                config_version,
                data_mode,
                universe_size,
                btc_change_24h,
                btc_regime,
                health_status,
                config_hash,
                git_sha,
                clock_skew_seconds,
                utc_now(),
            ),
        )

    _retry_write(
        operation
    )


def update_scan_health(scan_time_utc, config_version, health_status):
    def operation(conn):
        conn.execute(
            "UPDATE scans SET health_status=? WHERE scan_time_utc=? AND config_version=?",
            (health_status, scan_time_utc, config_version),
        )
    _retry_write(operation)


def save_scan_observation(scan_time_utc, config_version, btc_flash_15m_pct,
                          btc_flash_crash):
    def operation(conn):
        conn.execute("""UPDATE scans SET btc_flash_15m_pct=?, btc_flash_crash=?
                        WHERE scan_time_utc=? AND config_version=?""",
                     (btc_flash_15m_pct, int(btc_flash_crash), scan_time_utc,
                      config_version))
    _retry_write(operation)


def mark_event_data_failure(event_id, reason):
    def operation(conn):
        conn.execute("""UPDATE signal_events SET outcome_status='DATA_FAILURE',
                        closed_at_utc=? WHERE event_id=? AND outcome_status='OPEN'""",
                     (utc_now(), event_id))
    _retry_write(operation)
    save_data_issue("EVENT_DATA_FAILURE", f"{event_id}: {reason}")


def save_feature(
    feature,
    is_selected=0,
    is_signal=None,
    selection_class="NONE",
):
    if is_signal is None:
        is_signal = int(
            feature.get(
                "stage"
            )
            != "OBSERVE"
            and
            not feature.get(
                "climax_risk",
                False,
            )
        )

    raw_json = json.dumps(
        {
            key: feature.get(key)
            for key in FEATURE_RAW_EXTRA_KEYS
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    def operation(
        conn,
    ):
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
                selection_class,
                validation_tier,
                btc_regime,
                spread_bps,
                volume_rarity_pct,
                trade_rarity_pct,
                return_rarity_pct,
                cross_sectional_rarity_pct,
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
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?
            )
            ON CONFLICT(
                scan_time_utc,
                config_version,
                symbol
            )
            DO UPDATE SET
                data_mode =
                    excluded.data_mode,

                stage =
                    excluded.stage,

                engine =
                    excluded.engine,

                score =
                    excluded.score,

                is_signal =
                    MAX(
                        features.is_signal,
                        excluded.is_signal
                    ),

                is_selected =
                    MAX(
                        features.is_selected,
                        excluded.is_selected
                    ),

                selection_class =
                    CASE
                        WHEN excluded.selection_class
                            != 'NONE'
                        THEN excluded.selection_class
                        ELSE features.selection_class
                    END,

                validation_tier =
                    excluded.validation_tier,

                btc_regime =
                    excluded.btc_regime,

                spread_bps =
                    excluded.spread_bps,

                volume_rarity_pct =
                    excluded.volume_rarity_pct,

                trade_rarity_pct =
                    excluded.trade_rarity_pct,

                return_rarity_pct =
                    excluded.return_rarity_pct,

                cross_sectional_rarity_pct =
                    excluded.cross_sectional_rarity_pct,

                price =
                    excluded.price,

                signal_bar_open_ms =
                    excluded.signal_bar_open_ms,

                signal_bar_close_ms =
                    excluded.signal_bar_close_ms,

                change_15m =
                    excluded.change_15m,

                change_1h =
                    excluded.change_1h,

                change_3h =
                    excluded.change_3h,

                change_24h =
                    excluded.change_24h,

                btc_relative_24h =
                    excluded.btc_relative_24h,

                volume_z_15m =
                    excluded.volume_z_15m,

                trade_z_15m =
                    excluded.trade_z_15m,

                return_z_15m =
                    excluded.return_z_15m,

                volume_mult_15m =
                    excluded.volume_mult_15m,

                volume_mult_1h =
                    excluded.volume_mult_1h,

                retention_proxy =
                    excluded.retention_proxy,

                taker_buy_ratio_15m =
                    excluded.taker_buy_ratio_15m,

                oi_change_1h_pct =
                    excluded.oi_change_1h_pct,

                funding_rate =
                    excluded.funding_rate,

                wakeup =
                    excluded.wakeup,

                persistence =
                    excluded.persistence,

                retention =
                    excluded.retention,

                reignition =
                    excluded.reignition,

                trigger =
                    excluded.trigger,

                climax_risk =
                    excluded.climax_risk,

                raw_json =
                    excluded.raw_json
            """,
            (
                feature[
                    "ts_utc"
                ],
                feature[
                    "config_version"
                ],
                feature.get(
                    "data_mode",
                    "UNKNOWN",
                ),
                feature[
                    "symbol"
                ],
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
                selection_class,
                feature.get(
                    "validation_tier",
                    "OBSERVATIONAL",
                ),
                feature.get(
                    "btc_regime"
                ),
                feature.get(
                    "spread_bps"
                ),
                feature.get(
                    "volume_rarity_pct"
                ),
                feature.get(
                    "trade_rarity_pct"
                ),
                feature.get(
                    "return_rarity_pct"
                ),
                feature.get(
                    "cross_sectional_rarity_pct"
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

    _retry_write(
        operation
    )


def save_raw_klines(
    symbol,
    rows,
    config_version,
    interval_value="5m",
):
    if not rows:
        return

    def operation(conn):
        conn.executemany(
            """
            INSERT OR IGNORE INTO raw_klines (
                symbol, interval_value, open_time_ms,
                close_time_ms, open_price, high_price,
                low_price, close_price, quote_volume,
                trade_count, taker_buy_quote,
                config_version, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    symbol,
                    interval_value,
                    int(row[0]),
                    int(row[6]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[7]),
                    int(row[8]),
                    float(row[10]),
                    config_version,
                    utc_now(),
                )
                for row in rows
            ],
        )

    _retry_write(operation)


def save_raw_deriv(feature):
    def operation(conn):
        conn.execute(
            """
            INSERT OR REPLACE INTO raw_derivs (
                scan_time_utc, symbol, oi_change_1h_pct,
                funding_rate, futures_taker_ratio_1h,
                data_mode, config_version, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feature["ts_utc"],
                feature["symbol"],
                feature.get("oi_change_1h_pct"),
                feature.get("funding_rate"),
                feature.get("futures_taker_ratio_1h"),
                feature.get("data_mode", "UNKNOWN"),
                feature["config_version"],
                utc_now(),
            ),
        )

    _retry_write(operation)


def save_orderbook_snapshot(
    feature,
    event_class,
):
    liquidity = feature.get("liquidity") or {}

    def operation(conn):
        conn.execute(
            """
            INSERT OR REPLACE INTO orderbook_snap (
                scan_time_utc, symbol, event_class,
                best_bid, best_ask, spread_bps,
                buy_impact_1k_bps, sell_impact_1k_bps,
                buy_impact_5k_bps, sell_impact_5k_bps,
                visible_bid_capacity_usd,
                visible_ask_capacity_usd,
                raw_json,
                config_version, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feature["ts_utc"],
                feature["symbol"],
                event_class,
                liquidity.get("best_bid"),
                liquidity.get("best_ask"),
                liquidity.get("spread_bps"),
                liquidity.get("buy_impact_1k_bps"),
                liquidity.get("sell_impact_1k_bps"),
                liquidity.get("buy_impact_5k_bps"),
                liquidity.get("sell_impact_5k_bps"),
                liquidity.get("visible_bid_capacity_usd"),
                liquidity.get("visible_ask_capacity_usd"),
                json.dumps(
                    liquidity.get("depth_raw", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                feature["config_version"],
                utc_now(),
            ),
        )

    _retry_write(operation)


def save_daily_mover(
    trade_date,
    symbol,
    change_24h,
    quote_volume_24h,
    rank_value,
    config_version,
):
    def operation(
        conn,
    ):
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

    _retry_write(
        operation
    )


def has_recent_event(
    symbol,
    signal_time_utc,
    cooldown_hours=24,
    event_class="CANDIDATE",
):
    cutoff = (
        datetime.fromisoformat(
            signal_time_utc
        )
        - timedelta(
            hours=cooldown_hours
        )
    ).isoformat()

    def operation(
        conn,
    ):
        row = conn.execute(
            """
            SELECT event_id
            FROM signal_events
            WHERE symbol = ?
              AND signal_time_utc >= ?
              AND event_class = ?
            ORDER BY signal_time_utc DESC
            LIMIT 1
            """,
            (
                symbol,
                cutoff,
                event_class,
            ),
        ).fetchone()

        return (
            row is not None
        )

    return _retry_read(
        operation
    )


def create_signal_event(
    feature,
    cooldown_hours,
    fee_bps_per_side,
    slippage_bps_per_side,
    event_class="CANDIDATE",
    entry_delay_seconds=120,
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
        event_class,
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

    signal_time_ms = int(
        datetime.fromisoformat(
            signal_time_utc
        ).timestamp()
        * 1000
    )

    delayed_entry_ms = (
        signal_time_ms
        + int(entry_delay_seconds)
        * 1000
    )

    entry_open_time_ms = (
        (
            delayed_entry_ms
            + interval_ms
            - 1
        )
        // interval_ms
        * interval_ms
    )

    event_id = (
        f"{symbol}-"
        f"{signal_bar_close_ms}-"
        f"{feature['config_version']}-"
        f"{event_class}"
    )

    raw_payload = dict(
        feature
    )

    raw_payload[
        "event_class"
    ] = event_class

    raw_json = json.dumps(
        raw_payload,
        ensure_ascii=False,
        sort_keys=True,
    )

    def operation(
        conn,
    ):
        first_anomaly = conn.execute(
            """
            SELECT scan_time_utc, price
            FROM features
            WHERE symbol = ?
              AND config_version = ?
              AND is_signal = 1
              AND scan_time_utc <= ?
            ORDER BY scan_time_utc ASC
            LIMIT 1
            """,
            (
                symbol,
                feature["config_version"],
                signal_time_utc,
            ),
        ).fetchone()

        first_anomaly_time_utc = None
        first_anomaly_price = None
        gain_before_signal_pct = None
        minutes_from_first_anomaly = None

        if first_anomaly is not None:
            first_anomaly_time_utc = (
                first_anomaly["scan_time_utc"]
            )
            first_anomaly_price = (
                first_anomaly["price"]
            )

            if first_anomaly_price not in (None, 0):
                gain_before_signal_pct = (
                    feature["price"]
                    / first_anomaly_price
                    - 1.0
                ) * 100.0

            minutes_from_first_anomaly = (
                datetime.fromisoformat(signal_time_utc)
                - datetime.fromisoformat(
                    first_anomaly_time_utc
                )
            ).total_seconds() / 60.0

        cursor = conn.execute(
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
                first_anomaly_time_utc,
                first_anomaly_price,
                gain_before_signal_pct,
                minutes_from_first_anomaly,
                entry_open_time_ms,
                entry_delay_seconds,
                fee_bps_per_side,
                slippage_bps_per_side,
                cooldown_hours,
                event_class,
                validation_tier,
                btc_regime,
                near_miss_distance,
                manipulation_risk,
                spread_bps,
                buy_impact_1k_bps,
                sell_impact_1k_bps,
                buy_impact_5k_bps,
                sell_impact_5k_bps,
                raw_json,
                created_at_utc
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?
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
                first_anomaly_time_utc,
                first_anomaly_price,
                gain_before_signal_pct,
                minutes_from_first_anomaly,
                entry_open_time_ms,
                entry_delay_seconds,
                fee_bps_per_side,
                slippage_bps_per_side,
                cooldown_hours,
                event_class,
                feature.get(
                    "validation_tier",
                    "OBSERVATIONAL",
                ),
                feature.get("btc_regime"),
                feature.get("near_miss_distance"),
                int(bool(feature.get("manipulation_risk", False))),
                (feature.get("liquidity") or {}).get(
                    "spread_bps"
                ),
                (feature.get("liquidity") or {}).get(
                    "buy_impact_1k_bps"
                ),
                (feature.get("liquidity") or {}).get(
                    "sell_impact_1k_bps"
                ),
                (feature.get("liquidity") or {}).get(
                    "buy_impact_5k_bps"
                ),
                (feature.get("liquidity") or {}).get(
                    "sell_impact_5k_bps"
                ),
                raw_json,
                utc_now(),
            ),
        )

        return (
            cursor.rowcount > 0
        )

    inserted = _retry_write(
        operation
    )

    if inserted:
        return event_id

    return None


def get_pending_events():
    def operation(
        conn,
    ):
        rows = conn.execute(
            """
            SELECT *
            FROM signal_events
            WHERE outcome_status = 'OPEN'
            ORDER BY signal_time_utc
            """
        ).fetchall()

        return [
            dict(
                row
            )
            for row
            in rows
        ]

    return _retry_read(
        operation
    )


def get_universe_return(
    config_version,
    signal_time_utc,
    end_time_utc,
):
    def operation(conn):
        end_scan = conn.execute(
            """
            SELECT MAX(scan_time_utc) AS scan_time
            FROM scans
            WHERE config_version = ?
              AND scan_time_utc <= ?
            """,
            (
                config_version,
                end_time_utc,
            ),
        ).fetchone()["scan_time"]

        if end_scan is None:
            return None

        rows = conn.execute(
            """
            SELECT start.price AS start_price,
                   finish.price AS end_price
            FROM features start
            JOIN features finish
              ON finish.symbol = start.symbol
             AND finish.config_version = start.config_version
            WHERE start.config_version = ?
              AND start.scan_time_utc = ?
              AND finish.scan_time_utc = ?
              AND start.price > 0
              AND finish.price > 0
            """,
            (
                config_version,
                signal_time_utc,
                end_scan,
            ),
        ).fetchall()

        returns = [
            (row["end_price"] / row["start_price"] - 1.0)
            * 100.0
            for row in rows
        ]

        if not returns:
            return None

        return statistics.median(returns)

    return _retry_read(operation)


def update_event_entry(
    event_id,
    entry_time_utc,
    entry_price_raw,
    entry_price_exec,
):
    def operation(
        conn,
    ):
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

    _retry_write(
        operation
    )


def close_event(
    event_id,
    closed_at_utc,
):
    def operation(
        conn,
    ):
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

    _retry_write(
        operation
    )


def save_outcome_label(
    label,
):
    def operation(
        conn,
    ):
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
                primary_target_pct,
                primary_exit_reason,
                primary_exit_time_utc,
                primary_exit_return_pct,
                barrier_results_json,
                horizon_metrics_json,
                label_status,
                updated_at_utc
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(event_id)
            DO UPDATE SET
                entry_time_utc =
                    excluded.entry_time_utc,

                entry_price_exec =
                    excluded.entry_price_exec,

                stop_pct =
                    excluded.stop_pct,

                horizon_hours =
                    excluded.horizon_hours,

                first_touch_json =
                    excluded.first_touch_json,

                reach_json =
                    excluded.reach_json,

                hit_time_json =
                    excluded.hit_time_json,

                mfe_pct =
                    excluded.mfe_pct,

                mae_pct =
                    excluded.mae_pct,

                raw_mfe_pct =
                    excluded.raw_mfe_pct,

                executable_mfe_pct =
                    excluded.executable_mfe_pct,

                net_return_pct =
                    excluded.net_return_pct,

                btc_return_pct =
                    excluded.btc_return_pct,

                universe_return_pct =
                    excluded.universe_return_pct,

                excess_vs_btc_pct =
                    excluded.excess_vs_btc_pct,

                excess_vs_universe_pct =
                    excluded.excess_vs_universe_pct,

                primary_target_pct =
                    excluded.primary_target_pct,

                primary_exit_reason =
                    excluded.primary_exit_reason,

                primary_exit_time_utc =
                    excluded.primary_exit_time_utc,

                primary_exit_return_pct =
                    excluded.primary_exit_return_pct,

                barrier_results_json =
                    excluded.barrier_results_json,

                horizon_metrics_json =
                    excluded.horizon_metrics_json,

                label_status =
                    excluded.label_status,

                updated_at_utc =
                    excluded.updated_at_utc
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
                    "primary_target_pct"
                ),
                label.get("primary_exit_reason"),
                label.get("primary_exit_time_utc"),
                label.get("primary_exit_return_pct"),
                json.dumps(
                    label.get(
                        "barrier_results",
                        {},
                    ),
                    sort_keys=True,
                ),
                json.dumps(
                    label.get(
                        "horizon_metrics",
                        {},
                    ),
                    sort_keys=True,
                ),
                label.get(
                    "label_status",
                    "OPEN",
                ),
                utc_now(),
            ),
        )

    _retry_write(
        operation
    )


def save_winner_anatomy(
    row,
):
    def operation(
        conn,
    ):
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

    _retry_write(
        operation
    )


def save_winner_event(row):
    def operation(conn):
        conn.execute(
            """
            INSERT INTO winner_events (
                event_id, research_version, subject_class, symbol,
                matched_winner_event_id, event_start_time_utc,
                threshold_time_utc, peak_time_utc, horizon_end_time_utc,
                event_status, start_price, peak_price, max_gain_pct,
                max_drawdown_pct, highest_level_reached,
                levels_reached_json, first_anomaly_time_utc,
                first_anomaly_price, gain_before_first_anomaly_pct,
                hours_anomaly_before_start, pre_volume_24h,
                pre_volatility_24h, analyzed_at_utc, raw_json,
                created_at_utc
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            ON CONFLICT(event_id) DO UPDATE SET
                event_status=excluded.event_status,
                peak_time_utc=excluded.peak_time_utc,
                horizon_end_time_utc=excluded.horizon_end_time_utc,
                peak_price=excluded.peak_price,
                max_gain_pct=excluded.max_gain_pct,
                max_drawdown_pct=excluded.max_drawdown_pct,
                highest_level_reached=excluded.highest_level_reached,
                levels_reached_json=excluded.levels_reached_json,
                first_anomaly_time_utc=excluded.first_anomaly_time_utc,
                first_anomaly_price=excluded.first_anomaly_price,
                gain_before_first_anomaly_pct=excluded.gain_before_first_anomaly_pct,
                hours_anomaly_before_start=excluded.hours_anomaly_before_start,
                pre_volume_24h=excluded.pre_volume_24h,
                pre_volatility_24h=excluded.pre_volatility_24h,
                analyzed_at_utc=excluded.analyzed_at_utc,
                raw_json=excluded.raw_json
            """,
            (
                row["event_id"], row["research_version"],
                row["subject_class"], row["symbol"],
                row.get("matched_winner_event_id"),
                row["event_start_time_utc"], row.get("threshold_time_utc"),
                row.get("peak_time_utc"), row.get("horizon_end_time_utc"),
                row.get("event_status", "OPEN"), row.get("start_price"),
                row.get("peak_price"), row.get("max_gain_pct"),
                row.get("max_drawdown_pct"),
                row.get("highest_level_reached"),
                json.dumps(row.get("levels_reached", {}), sort_keys=True),
                row.get("first_anomaly_time_utc"),
                row.get("first_anomaly_price"),
                row.get("gain_before_first_anomaly_pct"),
                row.get("hours_anomaly_before_start"),
                row.get("pre_volume_24h"), row.get("pre_volatility_24h"),
                row.get("analyzed_at_utc", utc_now()),
                json.dumps(row.get("raw", {}), sort_keys=True), utc_now(),
            ),
        )
    _retry_write(operation)


def save_pre_event_feature(row):
    def operation(conn):
        conn.execute(
            """
            INSERT INTO pre_event_features (
                event_id, research_version, subject_class, symbol,
                offset_hours, feature_time_utc, price,
                return_15m_pct, return_1h_pct, return_3h_pct,
                return_6h_pct, return_12h_pct, return_24h_pct,
                btc_return_24h_pct, excess_vs_btc_24h_pct,
                quote_volume_15m, quote_volume_1h, quote_volume_24h,
                volume_z_15m, trade_z_15m, return_z_15m,
                volume_mult_15m, volume_mult_1h,
                taker_buy_ratio_15m, realized_volatility_24h,
                range_compression_24h, raw_json, created_at_utc
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(event_id, symbol, offset_hours, research_version)
            DO UPDATE SET
                feature_time_utc=excluded.feature_time_utc,
                price=excluded.price,
                return_15m_pct=excluded.return_15m_pct,
                return_1h_pct=excluded.return_1h_pct,
                return_3h_pct=excluded.return_3h_pct,
                return_6h_pct=excluded.return_6h_pct,
                return_12h_pct=excluded.return_12h_pct,
                return_24h_pct=excluded.return_24h_pct,
                btc_return_24h_pct=excluded.btc_return_24h_pct,
                excess_vs_btc_24h_pct=excluded.excess_vs_btc_24h_pct,
                quote_volume_15m=excluded.quote_volume_15m,
                quote_volume_1h=excluded.quote_volume_1h,
                quote_volume_24h=excluded.quote_volume_24h,
                volume_z_15m=excluded.volume_z_15m,
                trade_z_15m=excluded.trade_z_15m,
                return_z_15m=excluded.return_z_15m,
                volume_mult_15m=excluded.volume_mult_15m,
                volume_mult_1h=excluded.volume_mult_1h,
                taker_buy_ratio_15m=excluded.taker_buy_ratio_15m,
                realized_volatility_24h=excluded.realized_volatility_24h,
                range_compression_24h=excluded.range_compression_24h,
                raw_json=excluded.raw_json
            """,
            (
                row["event_id"], row["research_version"],
                row["subject_class"], row["symbol"], row["offset_hours"],
                row["feature_time_utc"], row.get("price"),
                row.get("return_15m_pct"), row.get("return_1h_pct"),
                row.get("return_3h_pct"), row.get("return_6h_pct"),
                row.get("return_12h_pct"), row.get("return_24h_pct"),
                row.get("btc_return_24h_pct"),
                row.get("excess_vs_btc_24h_pct"),
                row.get("quote_volume_15m"), row.get("quote_volume_1h"),
                row.get("quote_volume_24h"), row.get("volume_z_15m"),
                row.get("trade_z_15m"), row.get("return_z_15m"),
                row.get("volume_mult_15m"), row.get("volume_mult_1h"),
                row.get("taker_buy_ratio_15m"),
                row.get("realized_volatility_24h"),
                row.get("range_compression_24h"),
                json.dumps(row.get("raw", {}), sort_keys=True), utc_now(),
            ),
        )
    _retry_write(operation)


def save_data_issue(
    issue_type,
    details,
    issue_time_utc=None,
):
    def operation(
        conn,
    ):
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

    _retry_write(
        operation
    )
