"""Retrospective Binance winner research; this module emits no buy signals."""

import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone

import requests

from binance_snapshot_store import (
    init_db,
    save_data_issue,
    save_pre_event_feature,
    save_winner_event,
)

RESEARCH_VERSION = "binance-winner-anatomy-v2.0.1"

SPOT_BASES = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
)

REQUEST_TIMEOUT = 25
REQUEST_SLEEP_SECONDS = 0.08

QUOTE_ASSET = "USDT"
MIN_QUOTE_VOLUME_24H = 3_000_000.0

EVENT_LOOKBACK_DAYS = 7
HOURLY_HISTORY_DAYS = 15
EVENT_HORIZON_HOURS = 72
EVENT_TROUGH_LOOKBACK_HOURS = 24
EVENT_COOLDOWN_HOURS = 12

CONTROL_MAX_GAIN_PCT = 10.0
MAX_DETAILED_EVENTS_PER_RUN = 30

WINNER_LEVELS = (
    15.0,
    20.0,
    30.0,
    40.0,
    50.0,
    60.0,
)

PRE_EVENT_OFFSETS_HOURS = (
    72,
    48,
    24,
    12,
    6,
    3,
    1,
)

BASELINE_5M_BARS = 7 * 24 * 12
PRE_EVENT_HOURS = 72

DETAIL_HISTORY_BEFORE_HOURS = (
    7 * 24
    + PRE_EVENT_HOURS
    + 4
)

ANOMALY_VOLUME_Z = 2.5
ANOMALY_TRADE_Z = 2.0
ANOMALY_RETURN_Z = 2.0

EXCLUDED_BASES = {
    "BTC",
    "USDC",
    "FDUSD",
    "USDP",
    "TUSD",
    "DAI",
    "USDE",
    "USD1",
    "USDS",
    "XUSD",
    "BFUSD",
    "PYUSD",
    "AEUR",
    "EURI",
    "RLUSD",
    "USTC",
    "LUSD",
    "FRAX",
    "SUSD",
    "GUSD",
    "USDJ",
    "EUR",
    "TRY",
    "WBTC",
    "WBETH",
    "WETH",
    "BTCB",
    "BTCUP",
    "BTCDOWN",
    "ETHUP",
    "ETHDOWN",
    "BNBUP",
    "BNBDOWN",
    "ADAUP",
    "ADADOWN",
    "XRPUP",
    "XRPDOWN",
    "DOTUP",
    "DOTDOWN",
    "LINKUP",
    "LINKDOWN",
    "TRXUP",
    "TRXDOWN",
}

session = requests.Session()

session.headers.update({
    "User-Agent": RESEARCH_VERSION,
})


def utc_now():
    return datetime.now(timezone.utc)


def iso_from_ms(value):
    if value is None:
        return None

    return datetime.fromtimestamp(
        value / 1000.0,
        tz=timezone.utc,
    ).isoformat()


def pct_change(start, end):
    if start in (None, 0):
        return 0.0

    if end is None:
        return 0.0

    return (
        float(end) / float(start)
        - 1.0
    ) * 100.0


def mean(values):
    if not values:
        return 0.0

    return statistics.fmean(values)


def pstdev(values):
    if len(values) < 2:
        return 0.0

    return statistics.pstdev(values)


def zscore(value, baseline):
    std = pstdev(baseline)

    if std <= 1e-12:
        return 0.0

    return (
        value - mean(baseline)
    ) / std


def api_get(path, params=None):
    last_error = None

    for attempt in range(4):
        for base in SPOT_BASES:
            try:
                response = session.get(
                    base + path,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )

                if response.status_code in (
                    403,
                    418,
                    429,
                    451,
                ):
                    last_error = requests.HTTPError(
                        f"{response.status_code} "
                        f"from {base}{path}",
                        response=response,
                    )
                    continue

                response.raise_for_status()

                return response.json()

            except requests.RequestException as error:
                last_error = error

        if attempt < 3:
            time.sleep(
                1.5 * (attempt + 1)
            )

    raise last_error or RuntimeError(
        "All Binance spot endpoints failed"
    )


def last_closed_end_ms(
    interval_ms,
    now_ms=None,
):
    now_ms = (
        now_ms
        or int(
            utc_now().timestamp()
            * 1000
        )
    )

    return (
        now_ms // interval_ms
    ) * interval_ms - 1


def fetch_klines_range(
    symbol,
    interval,
    start_ms,
    end_ms,
):
    """
    Mumlari ileri yonlu ve sinirli sayfalama ile indirir.
    API imleci ilerlemezse sonsuz donguye girmek yerine hata verir.
    """

    interval_ms_by_name = {
        "5m": 5 * 60 * 1000,
        "1h": 60 * 60 * 1000,
    }

    interval_ms = (
        interval_ms_by_name.get(
            interval
        )
    )

    if interval_ms is None:
        raise ValueError(
            f"Desteklenmeyen zaman araligi: {interval}"
        )

    start_ms = max(
        0,
        int(start_ms),
    )

    end_ms = int(end_ms)

    if end_ms < start_ms:
        return []

    expected_bars = (
        (end_ms - start_ms)
        // interval_ms
        + 1
    )

    max_pages = max(
        1,
        math.ceil(
            expected_bars / 1000
        ) + 3,
    )

    cursor = start_ms
    collected = {}

    for _page in range(max_pages):
        batch = api_get(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
        )

        if not batch:
            break

        for row in batch:
            open_ms = int(row[0])
            close_ms = int(row[6])

            if (
                start_ms <= open_ms
                and close_ms <= end_ms
            ):
                collected[open_ms] = row

        newest_open_ms = int(
            batch[-1][0]
        )

        next_cursor = (
            newest_open_ms
            + interval_ms
        )

        if next_cursor <= cursor:
            raise RuntimeError(
                "Mum indirme noktasi ilerlemedi: "
                f"{symbol} {interval} "
                f"cursor={cursor} "
                f"newest={newest_open_ms}"
            )

        cursor = next_cursor

        if (
            cursor > end_ms
            or len(batch) < 1000
        ):
            break

        time.sleep(
            REQUEST_SLEEP_SECONDS
        )

    else:
        raise RuntimeError(
            "Mum indirme sayfa siniri asildi: "
            f"{symbol} {interval} "
            f"pages={max_pages}"
        )

    return [
        collected[key]
        for key in sorted(collected)
    ]


def parse_rows(rows):
    result = []
    previous_close = None

    for row in rows:
        close = float(row[4])

        result.append({
            "open_time": int(row[0]),
            "close_time": int(row[6]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": close,
            "quote_volume": float(row[7]),
            "trades": float(row[8]),
            "taker_buy_quote": float(row[10]),
            "abs_return": (
                abs(
                    pct_change(
                        previous_close,
                        close,
                    )
                )
                if previous_close
                else 0.0
            ),
        })

        previous_close = close

    return result


def build_universe():
    info = api_get(
        "/api/v3/exchangeInfo"
    )

    ticker = api_get(
        "/api/v3/ticker/24hr"
    )

    ticker_map = {
        row.get("symbol"): row
        for row in ticker
        if row.get("symbol")
    }

    symbols = []

    for item in info.get(
        "symbols",
        [],
    ):
        symbol = item.get("symbol")
        base = item.get("baseAsset")

        if not symbol or not base:
            continue

        if (
            item.get("quoteAsset")
            != QUOTE_ASSET
        ):
            continue

        if (
            item.get("status")
            != "TRADING"
        ):
            continue

        if base in EXCLUDED_BASES:
            continue

        quote_volume = float(
            (
                ticker_map.get(symbol)
                or {}
            ).get(
                "quoteVolume"
            )
            or 0.0
        )

        if (
            quote_volume
            < MIN_QUOTE_VOLUME_24H
        ):
            continue

        symbols.append(symbol)

    return sorted(symbols)


def pre_hourly_metrics(
    rows,
    index,
):
    prior = rows[
        max(0, index - 24):
        index
    ]

    returns = [
        pct_change(
            first["close"],
            second["close"],
        )
        for first, second
        in zip(
            prior,
            prior[1:],
        )
    ]

    return {
        "volume_24h": sum(
            row["quote_volume"]
            for row in prior
        ),
        "volatility_24h": pstdev(
            returns
        ),
    }


def event_identifier(
    symbol,
    start_ms,
    subject_class="WINNER",
    matched=None,
):
    raw = (
        f"{RESEARCH_VERSION}|"
        f"{subject_class}|"
        f"{symbol}|"
        f"{start_ms}|"
        f"{matched or ''}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]


def detect_winner_events(
    symbol,
    rows,
    now_ms,
):
    """
    Once dipten sonra gelen tepe hareketlerini bulur.
    Tepe dipten onceyse kazanan hareket sayilmaz.
    """

    events = []
    seen_starts = set()

    index = EVENT_TROUGH_LOOKBACK_HOURS
    total = len(rows)

    minimum_start_ms = (
        now_ms
        - EVENT_LOOKBACK_DAYS
        * 86400000
    )

    previous_index = -1

    while index < total:
        if index <= previous_index:
            raise RuntimeError(
                "Olay tarama noktasi ilerlemedi: "
                f"{symbol} index={index} "
                f"previous={previous_index}"
            )

        previous_index = index

        trough_start = max(
            0,
            index
            - EVENT_TROUGH_LOOKBACK_HOURS,
        )

        trough_index = min(
            range(
                trough_start,
                index + 1,
            ),
            key=lambda candidate_index:
                rows[candidate_index]["low"],
        )

        trough_price = rows[
            trough_index
        ]["low"]

        threshold_index = None
        cursor = trough_index

        search_end = min(
            total,
            trough_index
            + EVENT_HORIZON_HOURS
            + 1,
        )

        while cursor < search_end:
            if (
                rows[cursor]["low"]
                < trough_price
            ):
                trough_index = cursor
                trough_price = rows[
                    cursor
                ]["low"]

                search_end = min(
                    total,
                    trough_index
                    + EVENT_HORIZON_HOURS
                    + 1,
                )

            gain = pct_change(
                trough_price,
                rows[cursor]["high"],
            )

            if (
                gain
                >= WINNER_LEVELS[0]
            ):
                threshold_index = cursor
                break

            cursor += 1

        if threshold_index is None:
            index += 1
            continue

        event_start_ms = rows[
            trough_index
        ]["open_time"]

        if (
            event_start_ms
            < minimum_start_ms
        ):
            index = max(
                index + 1,
                threshold_index + 1,
            )
            continue

        if (
            event_start_ms
            in seen_starts
        ):
            index += 1
            continue

        horizon_stop = (
            trough_index
            + EVENT_HORIZON_HOURS
            + 1
        )

        available_stop = min(
            total,
            horizon_stop,
        )

        path = rows[
            trough_index:
            available_stop
        ]

        if not path:
            index += 1
            continue

        peak_offset = max(
            range(len(path)),
            key=lambda path_index:
                path[path_index]["high"],
        )

        peak_index = (
            trough_index
            + peak_offset
        )

        peak_price = rows[
            peak_index
        ]["high"]

        max_gain = pct_change(
            trough_price,
            peak_price,
        )

        levels = {}

        for level in WINNER_LEVELS:
            hit = next(
                (
                    row
                    for row in path
                    if pct_change(
                        trough_price,
                        row["high"],
                    ) >= level
                ),
                None,
            )

            if hit:
                levels[
                    str(int(level))
                ] = iso_from_ms(
                    hit["open_time"]
                )

        metrics = pre_hourly_metrics(
            rows,
            trough_index,
        )

        event = {
            "event_id": event_identifier(
                symbol,
                event_start_ms,
            ),
            "research_version":
                RESEARCH_VERSION,
            "subject_class":
                "WINNER",
            "symbol":
                symbol,
            "matched_winner_event_id":
                None,
            "event_start_ms":
                event_start_ms,
            "event_start_time_utc":
                iso_from_ms(
                    event_start_ms
                ),
            "threshold_time_utc":
                iso_from_ms(
                    rows[
                        threshold_index
                    ]["open_time"]
                ),
            "peak_time_utc":
                iso_from_ms(
                    rows[
                        peak_index
                    ]["open_time"]
                ),
            "horizon_end_time_utc":
                iso_from_ms(
                    rows[
                        min(
                            total - 1,
                            horizon_stop - 1,
                        )
                    ]["close_time"]
                ),
            "event_status": (
                "CLOSED"
                if horizon_stop <= total
                else "OPEN"
            ),
            "start_price":
                trough_price,
            "peak_price":
                peak_price,
            "max_gain_pct":
                max_gain,
            "max_drawdown_pct":
                min(
                    pct_change(
                        trough_price,
                        row["low"],
                    )
                    for row in path
                ),
            "highest_level_reached":
                max(
                    (
                        level
                        for level
                        in WINNER_LEVELS
                        if max_gain >= level
                    ),
                    default=None,
                ),
            "levels_reached":
                levels,
            "pre_volume_24h":
                metrics["volume_24h"],
            "pre_volatility_24h":
                metrics["volatility_24h"],
            "analyzed_at_utc":
                utc_now().isoformat(),
            "raw": {
                "detection_interval":
                    "1h",
                "horizon_hours":
                    EVENT_HORIZON_HOURS,
            },
        }

        events.append(event)
        seen_starts.add(
            event_start_ms
        )

        index = max(
            index + 1,
            horizon_stop,
        )

    return events


def nearest_index(
    rows,
    timestamp_ms,
):
    found = [
        index
        for index, row
        in enumerate(rows)
        if (
            row["open_time"]
            <= timestamp_ms
        )
    ]

    if not found:
        return None

    return found[-1]


def control_metrics_at(
    rows,
    timestamp_ms,
):
    index = nearest_index(
        rows,
        timestamp_ms,
    )

    if index is None:
        return None

    if index < 24:
        return None

    if (
        index
        + EVENT_HORIZON_HOURS
        >= len(rows)
    ):
        return None

    start_price = rows[
        index
    ]["close"]

    path = rows[
        index:
        index
        + EVENT_HORIZON_HOURS
        + 1
    ]

    peak_offset = max(
        range(len(path)),
        key=lambda path_index:
            path[path_index]["high"],
    )

    gain = pct_change(
        start_price,
        path[
            peak_offset
        ]["high"],
    )

    if gain >= CONTROL_MAX_GAIN_PCT:
        return None

    pre = pre_hourly_metrics(
        rows,
        index,
    )

    return {
        "start_price":
            start_price,
        "peak_price":
            path[
                peak_offset
            ]["high"],
        "peak_time_utc":
            iso_from_ms(
                path[
                    peak_offset
                ]["open_time"]
            ),
        "max_gain_pct":
            gain,
        "max_drawdown_pct":
            min(
                pct_change(
                    start_price,
                    row["low"],
                )
                for row in path
            ),
        "pre_volume_24h":
            pre["volume_24h"],
        "pre_volatility_24h":
            pre["volatility_24h"],
    }


def choose_matched_control(
    winner,
    hourly_by_symbol,
    used,
):
    target_volume = max(
        float(
            winner.get(
                "pre_volume_24h"
            )
            or 0.0
        ),
        1.0,
    )

    target_volatility = float(
        winner.get(
            "pre_volatility_24h"
        )
        or 0.0
    )

    candidates = []

    for symbol, rows in (
        hourly_by_symbol.items()
    ):
        if (
            symbol
            == winner["symbol"]
        ):
            continue

        used_key = (
            winner["event_id"],
            symbol,
        )

        if used_key in used:
            continue

        metrics = control_metrics_at(
            rows,
            winner["event_start_ms"],
        )

        if not metrics:
            continue

        volume = max(
            float(
                metrics[
                    "pre_volume_24h"
                ]
                or 0.0
            ),
            1.0,
        )

        distance = (
            abs(
                math.log10(volume)
                - math.log10(
                    target_volume
                )
            )
            + abs(
                float(
                    metrics[
                        "pre_volatility_24h"
                    ]
                    or 0.0
                )
                - target_volatility
            )
        )

        candidates.append(
            (
                distance,
                symbol,
                metrics,
            )
        )

    if not candidates:
        return None

    _, symbol, metrics = min(
        candidates,
        key=lambda item: (
            item[0],
            item[1],
        ),
    )

    used.add(
        (
            winner["event_id"],
            symbol,
        )
    )

    return {
        "event_id":
            event_identifier(
                symbol,
                winner[
                    "event_start_ms"
                ],
                "NEAR_MATCH_CONTROL",
                winner["event_id"],
            ),
        "research_version":
            RESEARCH_VERSION,
        "subject_class":
            "NEAR_MATCH_CONTROL",
        "symbol":
            symbol,
        "matched_winner_event_id":
            winner["event_id"],
        "event_start_ms":
            winner["event_start_ms"],
        "event_start_time_utc":
            winner[
                "event_start_time_utc"
            ],
        "threshold_time_utc":
            None,
        "peak_time_utc":
            metrics["peak_time_utc"],
        "horizon_end_time_utc":
            iso_from_ms(
                winner[
                    "event_start_ms"
                ]
                + EVENT_HORIZON_HOURS
                * 3600000
            ),
        "event_status":
            "CLOSED",
        "start_price":
            metrics["start_price"],
        "peak_price":
            metrics["peak_price"],
        "max_gain_pct":
            metrics["max_gain_pct"],
        "max_drawdown_pct":
            metrics[
                "max_drawdown_pct"
            ],
        "highest_level_reached":
            None,
        "levels_reached":
            {},
        "pre_volume_24h":
            metrics[
                "pre_volume_24h"
            ],
        "pre_volatility_24h":
            metrics[
                "pre_volatility_24h"
            ],
        "analyzed_at_utc":
            utc_now().isoformat(),
        "raw": {
            "matched_to":
                winner["event_id"],
            "matching":
                "volume+volatility",
        },
    }


def grouped_sums(
    values,
    size,
):
    return [
        sum(
            values[
                index:
                index + size
            ]
        )
        for index in range(
            0,
            len(values) - size + 1,
            size,
        )
    ]


def feature_snapshot(
    rows,
    target_ms,
    btc_rows=None,
):
    index = nearest_index(
        rows,
        target_ms,
    )

    if index is None:
        return None

    if (
        index
        < BASELINE_5M_BARS + 36
    ):
        return None

    baseline = rows[
        index
        - 36
        - BASELINE_5M_BARS:
        index - 36
    ]

    current = rows[
        :index + 1
    ]

    closes = [
        row["close"]
        for row in current
    ]

    volumes = [
        row["quote_volume"]
        for row in current
    ]

    trades = [
        row["trades"]
        for row in current
    ]

    taker = [
        row["taker_buy_quote"]
        for row in current
    ]

    base_volumes = [
        row["quote_volume"]
        for row in baseline
    ]

    base_trades = [
        row["trades"]
        for row in baseline
    ]

    base_returns = [
        row["abs_return"]
        for row in baseline
    ]

    volume_15m = sum(
        volumes[-3:]
    )

    volume_1h = sum(
        volumes[-12:]
    )

    volume_24h = sum(
        volumes[-288:]
    )

    trades_15m = sum(
        trades[-3:]
    )

    baseline_volume_15m = (
        grouped_sums(
            base_volumes,
            3,
        )
    )

    baseline_volume_1h = (
        grouped_sums(
            base_volumes,
            12,
        )
    )

    baseline_trades_15m = (
        grouped_sums(
            base_trades,
            3,
        )
    )

    baseline_returns_15m = (
        grouped_sums(
            base_returns,
            3,
        )
    )

    recent = current[-288:]

    if len(closes) >= 289:
        returns_24h = [
            pct_change(
                first,
                second,
            )
            for first, second
            in zip(
                closes[-289:-1],
                closes[-288:],
            )
        ]
    else:
        returns_24h = []

    def period_return(bars):
        if len(closes) <= bars:
            return None

        return pct_change(
            closes[-bars - 1],
            closes[-1],
        )

    btc_return = None

    if btc_rows:
        btc_index = nearest_index(
            btc_rows,
            target_ms,
        )

        if (
            btc_index is not None
            and btc_index >= 288
        ):
            btc_return = pct_change(
                btc_rows[
                    btc_index - 288
                ]["close"],
                btc_rows[
                    btc_index
                ]["close"],
            )

    return_24h = period_return(
        288
    )

    volume_median_15m = (
        statistics.median(
            baseline_volume_15m
        )
        if baseline_volume_15m
        else 1.0
    )

    volume_median_1h = (
        statistics.median(
            baseline_volume_1h
        )
        if baseline_volume_1h
        else 1.0
    )

    return {
        "feature_time_utc":
            iso_from_ms(
                rows[index][
                    "close_time"
                ]
            ),
        "price":
            closes[-1],
        "return_15m_pct":
            period_return(3),
        "return_1h_pct":
            period_return(12),
        "return_3h_pct":
            period_return(36),
        "return_6h_pct":
            period_return(72),
        "return_12h_pct":
            period_return(144),
        "return_24h_pct":
            return_24h,
        "btc_return_24h_pct":
            btc_return,
        "excess_vs_btc_24h_pct": (
            return_24h
            - btc_return
            if (
                return_24h
                is not None
                and btc_return
                is not None
            )
            else None
        ),
        "quote_volume_15m":
            volume_15m,
        "quote_volume_1h":
            volume_1h,
        "quote_volume_24h":
            volume_24h,
        "volume_z_15m":
            zscore(
                volume_15m,
                baseline_volume_15m,
            ),
        "trade_z_15m":
            zscore(
                trades_15m,
                baseline_trades_15m,
            ),
        "return_z_15m":
            zscore(
                abs(
                    period_return(3)
                    or 0.0
                ),
                baseline_returns_15m,
            ),
        "volume_mult_15m": (
            volume_15m
            / (
                volume_median_15m
                or 1.0
            )
        ),
        "volume_mult_1h": (
            volume_1h
            / (
                volume_median_1h
                or 1.0
            )
        ),
        "taker_buy_ratio_15m": (
            sum(
                taker[-3:]
            ) / volume_15m
            if volume_15m > 0
            else 0.0
        ),
        "realized_volatility_24h":
            pstdev(
                returns_24h
            ),
        "range_compression_24h":
            pct_change(
                min(
                    row["low"]
                    for row in recent
                ),
                max(
                    row["high"]
                    for row in recent
                ),
            ),
        "raw": {
            "source_interval":
                "5m",
            "baseline_days":
                7,
        },
    }


def first_pre_event_anomaly(
    rows,
    event_start_ms,
):
    start_index = nearest_index(
        rows,
        event_start_ms
        - PRE_EVENT_HOURS
        * 3600000,
    )

    end_index = nearest_index(
        rows,
        event_start_ms - 1,
    )

    if (
        start_index is None
        or end_index is None
    ):
        return None

    first_price = rows[
        start_index
    ]["close"]

    scan_start = max(
        start_index,
        BASELINE_5M_BARS + 36,
    )

    for index in range(
        scan_start,
        end_index + 1,
    ):
        snapshot = feature_snapshot(
            rows,
            rows[index][
                "close_time"
            ],
        )

        if not snapshot:
            continue

        components = (
            snapshot[
                "volume_z_15m"
            ] >= ANOMALY_VOLUME_Z,
            snapshot[
                "trade_z_15m"
            ] >= ANOMALY_TRADE_Z,
            snapshot[
                "return_z_15m"
            ] >= ANOMALY_RETURN_Z,
        )

        if sum(components) >= 2:
            return {
                "time_utc":
                    snapshot[
                        "feature_time_utc"
                    ],
                "price":
                    snapshot["price"],
                "gain_before_pct":
                    pct_change(
                        first_price,
                        snapshot["price"],
                    ),
                "hours_before_start": (
                    event_start_ms
                    - rows[index][
                        "close_time"
                    ]
                ) / 3600000.0,
            }

    return None


def enrich_and_save_event(
    event,
    btc_rows,
    now_ms,
):
    detail_start = (
        event["event_start_ms"]
        - DETAIL_HISTORY_BEFORE_HOURS
        * 3600000
    )

    detail_end = min(
        last_closed_end_ms(
            300000,
            now_ms,
        ),
        event["event_start_ms"]
        + EVENT_HORIZON_HOURS
        * 3600000,
    )

    rows = parse_rows(
        fetch_klines_range(
            event["symbol"],
            "5m",
            detail_start,
            detail_end,
        )
    )

    if not rows:
        raise ValueError(
            "5m detail history is empty"
        )

    anomaly = (
        first_pre_event_anomaly(
            rows,
            event["event_start_ms"],
        )
    )

    event.update({
        "first_anomaly_time_utc": (
            anomaly["time_utc"]
            if anomaly
            else None
        ),
        "first_anomaly_price": (
            anomaly["price"]
            if anomaly
            else None
        ),
        "gain_before_first_anomaly_pct": (
            anomaly["gain_before_pct"]
            if anomaly
            else None
        ),
        "hours_anomaly_before_start": (
            anomaly[
                "hours_before_start"
            ]
            if anomaly
            else None
        ),
    })

    save_winner_event(event)

    saved = 0

    for offset in (
        PRE_EVENT_OFFSETS_HOURS
    ):
        snapshot = feature_snapshot(
            rows,
            event["event_start_ms"]
            - offset * 3600000,
            btc_rows,
        )

        if not snapshot:
            continue

        snapshot.update({
            "event_id":
                event["event_id"],
            "research_version":
                RESEARCH_VERSION,
            "subject_class":
                event["subject_class"],
            "symbol":
                event["symbol"],
            "offset_hours":
                offset,
        })

        save_pre_event_feature(
            snapshot
        )

        saved += 1

    return saved


def main():
    init_db()

    now = utc_now()

    now_ms = int(
        now.timestamp()
        * 1000
    )

    hour_end = last_closed_end_ms(
        3600000,
        now_ms,
    )

    hour_start = (
        hour_end
        - HOURLY_HISTORY_DAYS
        * 86400000
    )

    print("=" * 80)
    print(
        "BINANCE WINNER ANATOMY V2 "
        "- RETROSPECTIVE RESEARCH"
    )
    print(
        f"UTC: {now.isoformat()} "
        f"| Research: "
        f"{RESEARCH_VERSION}"
    )
    print("=" * 80)

    try:
        symbols = build_universe()

    except Exception as error:
        save_data_issue(
            "WINNER_UNIVERSE_FAILED",
            str(error),
            now.isoformat(),
        )
        raise

    hourly_by_symbol = {}
    winner_events = []

    for index, symbol in enumerate(
        symbols,
        start=1,
    ):
        try:
            hourly = parse_rows(
                fetch_klines_range(
                    symbol,
                    "1h",
                    hour_start,
                    hour_end,
                )
            )

            hourly_by_symbol[
                symbol
            ] = hourly

            events = (
                detect_winner_events(
                    symbol,
                    hourly,
                    now_ms,
                )
            )

            winner_events.extend(
                events
            )

            print(
                f"[{index}/{len(symbols)}] "
                f"{symbol} "
                f"| events={len(events)}"
            )

        except Exception as error:
            save_data_issue(
                "WINNER_HOURLY_SCAN_FAILED",
                (
                    f"{symbol}: "
                    f"{type(error).__name__}: "
                    f"{error}"
                ),
                now.isoformat(),
            )

            print(
                f"HATA | {symbol} | "
                f"{type(error).__name__}: "
                f"{error}"
            )

        time.sleep(
            REQUEST_SLEEP_SECONDS
        )

    winner_events.sort(
        key=lambda event: (
            event["event_start_ms"],
            event["max_gain_pct"],
        ),
        reverse=True,
    )

    selected = winner_events[
        :MAX_DETAILED_EVENTS_PER_RUN
    ]

    used = set()
    controls = []

    for winner in [
        event
        for event in selected
        if (
            event["event_status"]
            == "CLOSED"
        )
    ]:
        control = (
            choose_matched_control(
                winner,
                hourly_by_symbol,
                used,
            )
        )

        if control:
            controls.append(
                control
            )

    if selected:
        all_detailed_events = (
            selected + controls
        )

        earliest = min(
            event["event_start_ms"]
            for event
            in all_detailed_events
        )

        btc_rows = parse_rows(
            fetch_klines_range(
                "BTCUSDT",
                "5m",
                earliest
                - DETAIL_HISTORY_BEFORE_HOURS
                * 3600000,
                last_closed_end_ms(
                    300000,
                    now_ms,
                ),
            )
        )

    else:
        btc_rows = []

    saved_events = 0
    saved_features = 0

    for event in (
        selected + controls
    ):
        try:
            saved_features += (
                enrich_and_save_event(
                    event,
                    btc_rows,
                    now_ms,
                )
            )

            saved_events += 1

            print(
                "SAVE | "
                f"{event['subject_class']} "
                f"| {event['symbol']} "
                "| max="
                f"{event['max_gain_pct']:+.2f}% "
                f"| {event['event_status']}"
            )

        except Exception as error:
            save_data_issue(
                "WINNER_DETAIL_FAILED",
                (
                    f"{event['symbol']}: "
                    f"{type(error).__name__}: "
                    f"{error}"
                ),
                now.isoformat(),
            )

            print(
                f"HATA | "
                f"{event['symbol']} | "
                f"{type(error).__name__}: "
                f"{error}"
            )

    counts = {
        int(level): sum(
            event["max_gain_pct"]
            >= level
            for event
            in winner_events
        )
        for level in WINNER_LEVELS
    }

    print("=" * 80)
    print(
        "WINNER ANATOMY OZETI"
    )
    print(
        f"Evren: {len(symbols)} "
        f"| Winner olayi: "
        f"{len(winner_events)}"
    )
    print(
        f"Detay winner: "
        f"{len(selected)} "
        "| Eslesmis kontrol: "
        f"{len(controls)}"
    )
    print(
        f"Kaydedilen olay: "
        f"{saved_events} "
        "| Pre-event snapshot: "
        f"{saved_features}"
    )
    print(
        "Seviye sayilari: "
        + json.dumps(
            counts,
            sort_keys=True,
        )
    )
    print(
        "OPEN olaylar resmi "
        "karsilastirmaya girmez; "
        "72 saat dolunca kapanir."
    )
    print("=" * 80)


if __name__ == "__main__":
    main()
