#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import statistics
import time
from datetime import datetime, timezone

import requests

from binance_snapshot_store import (
    init_db,
    save_winner_anatomy,
    save_data_issue,
)

SPOT_BASE = "https://api.binance.com"

REQUEST_TIMEOUT = 20

INTERVAL = "5m"

BASELINE_BARS = 288
MIN_WINNER_CHANGE = 20.0
MAX_WINNERS = 10

ANOMALY_VOLUME_Z = 2.5
ANOMALY_TRADE_Z = 2.0
ANOMALY_RETURN_Z = 2.0

session = requests.Session()

session.headers.update({
    "User-Agent":
        "binance-avci2-winner-anatomy"
})


def api_get(
    path,
    params=None,
):
    response = session.get(
        SPOT_BASE + path,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def average(
    values,
):
    if not values:
        return 0.0

    return statistics.fmean(
        values
    )


def deviation(
    values,
):
    if len(
        values
    ) < 2:
        return 0.0

    return statistics.pstdev(
        values
    )


def zscore(
    value,
    baseline,
):
    std = deviation(
        baseline
    )

    if std <= 1e-12:
        return 0.0

    return (
        value
        - average(
            baseline
        )
    ) / std


def pct_change(
    start,
    end,
):
    if start in (
        None,
        0,
    ):
        return 0.0

    return (
        end / start
        - 1.0
    ) * 100.0


def fetch_24h():
    return api_get(
        "/api/v3/ticker/24hr"
    )


def fetch_klines(
    symbol,
    end_time=None,
    limit=1000,
):
    params = {
        "symbol":
            symbol,

        "interval":
            INTERVAL,

        "limit":
            limit,
    }

    if end_time is not None:
        params[
            "endTime"
        ] = end_time

    return api_get(
        "/api/v3/klines",
        params,
    )


def fetch_history(
    symbol,
):
    latest = fetch_klines(
        symbol,
        limit=1000,
    )

    if not latest:
        return []

    first_open_time = int(
        latest[0][0]
    )

    earlier = fetch_klines(
        symbol,
        end_time=(
            first_open_time
            - 1
        ),
        limit=1000,
    )

    combined = (
        earlier
        + latest
    )

    unique = {}

    for row in combined:
        open_time = int(
            row[0]
        )

        unique[
            open_time
        ] = row

    return [
        unique[
            timestamp
        ]
        for timestamp
        in sorted(
            unique
        )
    ]


def parse_rows(
    rows,
):
    parsed = []

    previous_close = None

    for row in rows:
        close = float(
            row[4]
        )

        quote_volume = float(
            row[7]
        )

        trades = float(
            row[8]
        )

        if previous_close:
            absolute_return = abs(
                pct_change(
                    previous_close,
                    close,
                )
            )

        else:
            absolute_return = 0.0

        parsed.append({
            "open_time":
                int(
                    row[0]
                ),

            "close":
                close,

            "quote_volume":
                quote_volume,

            "trades":
                trades,

            "abs_return":
                absolute_return,
        })

        previous_close = close

    return parsed


def detect_first_anomaly(
    rows,
):
    if len(
        rows
    ) < (
        BASELINE_BARS
        + 20
    ):
        return None

    for index in range(
        BASELINE_BARS,
        len(
            rows
        ),
    ):
        baseline = rows[
            index
            - BASELINE_BARS:
            index
        ]

        current = rows[
            index
        ]

        baseline_volumes = [
            item[
                "quote_volume"
            ]
            for item
            in baseline
        ]

        baseline_trades = [
            item[
                "trades"
            ]
            for item
            in baseline
        ]

        baseline_returns = [
            item[
                "abs_return"
            ]
            for item
            in baseline
        ]

        volume_z = zscore(
            current[
                "quote_volume"
            ],
            baseline_volumes,
        )

        trade_z = zscore(
            current[
                "trades"
            ],
            baseline_trades,
        )

        return_z = zscore(
            current[
                "abs_return"
            ],
            baseline_returns,
        )

        median_volume = (
            statistics.median(
                baseline_volumes
            )
            if baseline_volumes
            else 1.0
        )

        volume_multiple = (
            current[
                "quote_volume"
            ]
            / (
                median_volume
                or 1.0
            )
        )

        anomaly_components = [
            volume_z
            >= ANOMALY_VOLUME_Z,

            trade_z
            >= ANOMALY_TRADE_Z,

            return_z
            >= ANOMALY_RETURN_Z,
        ]

        if (
            sum(
                anomaly_components
            )
            >= 2
        ):
            return {
                "open_time":
                    current[
                        "open_time"
                    ],

                "close":
                    current[
                        "close"
                    ],

                "volume_z":
                    volume_z,

                "trade_z":
                    trade_z,

                "return_z":
                    return_z,

                "volume_multiple":
                    volume_multiple,
            }

    return None


def main():
    init_db()

    now = datetime.now(
        timezone.utc
    )

    try:
        ticker_data = (
            fetch_24h()
        )

    except Exception as error:
        save_data_issue(
            "WINNER_TICKER_FETCH_FAILED",
            str(
                error
            ),
        )

        raise

    winners = []

    for item in ticker_data:
        symbol = item.get(
            "symbol",
            "",
        )

        if not symbol.endswith(
            "USDT"
        ):
            continue

        change_24h = float(
            item.get(
                "priceChangePercent",
                0.0,
            )
        )

        quote_volume = float(
            item.get(
                "quoteVolume",
                0.0,
            )
        )

        if (
            change_24h
            >= MIN_WINNER_CHANGE
        ):
            winners.append(
                (
                    symbol,
                    change_24h,
                    quote_volume,
                )
            )

    winners.sort(
        key=lambda item:
            item[1],
        reverse=True,
    )

    winners = winners[
        :MAX_WINNERS
    ]

    print(
        "=" * 80
    )

    print(
        "BINANCE WINNER ANATOMY"
    )

    print(
        now.isoformat()
    )

    print(
        f"Winners found: "
        f"{len(winners)}"
    )

    print(
        "-" * 80
    )

    for (
        symbol,
        winner_change,
        quote_volume,
    ) in winners:

        try:
            raw_rows = fetch_history(
                symbol
            )

            parsed_rows = parse_rows(
                raw_rows
            )

            first_anomaly = (
                detect_first_anomaly(
                    parsed_rows
                )
            )

            reference_price = (
                parsed_rows[
                    -1
                ][
                    "close"
                ]
                if parsed_rows
                else None
            )

            if first_anomaly:
                anomaly_time = (
                    datetime.fromtimestamp(
                        first_anomaly[
                            "open_time"
                        ] / 1000,
                        tz=timezone.utc,
                    )
                )

                starting_price = (
                    parsed_rows[
                        0
                    ][
                        "close"
                    ]
                    if parsed_rows
                    else None
                )

                if (
                    starting_price
                    is not None
                ):
                    gain_before_anomaly = (
                        pct_change(
                            starting_price,
                            first_anomaly[
                                "close"
                            ],
                        )
                    )

                else:
                    gain_before_anomaly = (
                        None
                    )

                hours_before_reference = (
                    (
                        now
                        - anomaly_time
                    ).total_seconds()
                    / 3600.0
                )

                row = {
                    "analyzed_at_utc":
                        now.isoformat(),

                    "trade_date":
                        now.date().isoformat(),

                    "symbol":
                        symbol,

                    "winner_change_24h":
                        winner_change,

                    "first_anomaly_time_utc":
                        anomaly_time.isoformat(),

                    "first_anomaly_price":
                        first_anomaly[
                            "close"
                        ],

                    "winner_reference_price":
                        reference_price,

                    "gain_before_first_anomaly":
                        gain_before_anomaly,

                    "hours_before_reference":
                        hours_before_reference,

                    "anomaly_volume_z":
                        first_anomaly[
                            "volume_z"
                        ],

                    "anomaly_trade_z":
                        first_anomaly[
                            "trade_z"
                        ],

                    "anomaly_return_z":
                        first_anomaly[
                            "return_z"
                        ],

                    "anomaly_volume_mult":
                        first_anomaly[
                            "volume_multiple"
                        ],
                }

                save_winner_anatomy(
                    row
                )

                print(
                    f"{symbol}"
                )

                print(
                    f"   24H move: "
                    f"{winner_change:+.2f}%"
                )

                print(
                    f"   First anomaly: "
                    f"{anomaly_time.isoformat()}"
                )

                print(
                    f"   Volume Z: "
                    f"{first_anomaly['volume_z']:.2f}"
                )

                print(
                    f"   Trade Z: "
                    f"{first_anomaly['trade_z']:.2f}"
                )

                print(
                    f"   Return Z: "
                    f"{first_anomaly['return_z']:.2f}"
                )

                print(
                    f"   Volume multiple: "
                    f"{first_anomaly['volume_multiple']:.2f}x"
                )

            else:
                row = {
                    "analyzed_at_utc":
                        now.isoformat(),

                    "trade_date":
                        now.date().isoformat(),

                    "symbol":
                        symbol,

                    "winner_change_24h":
                        winner_change,

                    "first_anomaly_time_utc":
                        None,

                    "first_anomaly_price":
                        None,

                    "winner_reference_price":
                        reference_price,

                    "gain_before_first_anomaly":
                        None,

                    "hours_before_reference":
                        None,

                    "anomaly_volume_z":
                        None,

                    "anomaly_trade_z":
                        None,

                    "anomaly_return_z":
                        None,

                    "anomaly_volume_mult":
                        None,
                }

                save_winner_anatomy(
                    row
                )

                print(
                    f"{symbol}: "
                    f"no qualifying anomaly found"
                )

        except Exception as error:
            save_data_issue(
                "WINNER_ANALYSIS_FAILED",
                (
                    f"{symbol}: "
                    f"{error}"
                ),
            )

            print(
                f"{symbol}: "
                f"ERROR - "
                f"{error}"
            )

        print(
            "-" * 80
        )

        time.sleep(
            0.15
        )

    print(
        "=" * 80
    )


if __name__ == "__main__":
    main()
