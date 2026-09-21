#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from datetime import datetime, timezone, timedelta

import requests

from binance_snapshot_store import (
    init_db,
    get_pending_events,
    update_event_entry,
    close_event,
    save_outcome_label,
    save_data_issue,
)

SPOT_BASES = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
)

REQUEST_TIMEOUT = 20

INTERVAL = "5m"

TARGETS = (
    3.0,
    5.0,
    7.0,
    10.0,
    15.0,
)

STOP_PCT = -7.0

HORIZON_HOURS = 72

FEE_BPS_PER_SIDE = 10.0
SLIPPAGE_BPS_PER_SIDE = 10.0

session = requests.Session()

session.headers.update({
    "User-Agent": "binance-avci2-outcome-labeler/1.0"
})


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def api_get(
    path,
    params=None,
):
    last_error = None

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
                    f"{response.status_code} from {base}{path}",
                    response=response,
                )
                continue

            response.raise_for_status()

            return response.json()

        except requests.RequestException as error:
            last_error = error

    if last_error is not None:
        raise last_error

    raise RuntimeError(
        "All Binance spot endpoints failed"
    )


def fetch_klines(
    symbol,
    start_time_ms,
    end_time_ms,
    limit=1000,
):
    return api_get(
        "/api/v3/klines",
        {
            "symbol":
                symbol,

            "interval":
                INTERVAL,

            "startTime":
                start_time_ms,

            "endTime":
                end_time_ms,

            "limit":
                limit,
        },
    )


def ms_to_iso(
    timestamp_ms,
):
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).isoformat()


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


def apply_entry_cost(
    raw_price,
    fee_bps_per_side,
    slippage_bps_per_side,
):
    total_bps = (
        fee_bps_per_side
        + slippage_bps_per_side
    )

    return raw_price * (
        1.0
        + total_bps / 10000.0
    )


def apply_exit_cost(
    raw_price,
    fee_bps_per_side,
    slippage_bps_per_side,
):
    total_bps = (
        fee_bps_per_side
        + slippage_bps_per_side
    )

    return raw_price * (
        1.0
        - total_bps / 10000.0
    )


def get_event_path(
    event,
):
    entry_open_time_ms = int(
        event[
            "entry_open_time_ms"
        ]
    )

    horizon_end_ms = (
        entry_open_time_ms
        + HORIZON_HOURS
        * 60
        * 60
        * 1000
    )

    now_ms = int(
        datetime.now(
            timezone.utc
        ).timestamp()
        * 1000
    )

    request_end_ms = min(
        horizon_end_ms,
        now_ms,
    )

    rows = fetch_klines(
        event[
            "symbol"
        ],
        entry_open_time_ms,
        request_end_ms,
        1000,
    )

    return rows


def label_event(
    event,
):
    rows = get_event_path(
        event
    )

    if not rows:
        return None

    entry_row = rows[
        0
    ]

    entry_open_time_ms = int(
        entry_row[
            0
        ]
    )

    entry_price_raw = float(
        entry_row[
            1
        ]
    )

    fee_bps = float(
        event.get(
            "fee_bps_per_side",
            FEE_BPS_PER_SIDE,
        )
        or FEE_BPS_PER_SIDE
    )

    slippage_bps = float(
        event.get(
            "slippage_bps_per_side",
            SLIPPAGE_BPS_PER_SIDE,
        )
        or SLIPPAGE_BPS_PER_SIDE
    )

    entry_price_exec = (
        apply_entry_cost(
            entry_price_raw,
            fee_bps,
            slippage_bps,
        )
    )

    if event.get(
        "entry_status"
    ) != "READY":
        update_event_entry(
            event[
                "event_id"
            ],
            ms_to_iso(
                entry_open_time_ms
            ),
            entry_price_raw,
            entry_price_exec,
        )

    reach = {
        str(
            target
        ): False
        for target in TARGETS
    }

    hit_time = {
        str(
            target
        ): None
        for target in TARGETS
    }

    first_touch = {
        str(
            target
        ): None
        for target in TARGETS
    }

    mfe_pct = None
    mae_pct = None

    raw_mfe_pct = None
    executable_mfe_pct = None

    max_high = None
    min_low = None

    stop_hit_time = None

    for row in rows:
        open_time_ms = int(
            row[
                0
            ]
        )

        high_raw = float(
            row[
                2
            ]
        )

        low_raw = float(
            row[
                3
            ]
        )

        if (
            max_high is None
            or high_raw > max_high
        ):
            max_high = high_raw

        if (
            min_low is None
            or low_raw < min_low
        ):
            min_low = low_raw

        high_exec = apply_exit_cost(
            high_raw,
            fee_bps,
            slippage_bps,
        )

        low_exec = apply_exit_cost(
            low_raw,
            fee_bps,
            slippage_bps,
        )

        high_return = pct_change(
            entry_price_exec,
            high_exec,
        )

        low_return = pct_change(
            entry_price_exec,
            low_exec,
        )

        if (
            stop_hit_time is None
            and low_return
            <= STOP_PCT
        ):
            stop_hit_time = (
                open_time_ms
            )

        for target in TARGETS:
            key = str(
                target
            )

            if (
                not reach[
                    key
                ]
                and high_return
                >= target
            ):
                reach[
                    key
                ] = True

                hit_time[
                    key
                ] = (
                    open_time_ms
                )

    if max_high is not None:
        raw_mfe_pct = pct_change(
            entry_price_raw,
            max_high,
        )

        executable_mfe_pct = pct_change(
            entry_price_exec,
            apply_exit_cost(
                max_high,
                fee_bps,
                slippage_bps,
            ),
        )

        mfe_pct = (
            executable_mfe_pct
        )

    if min_low is not None:
        mae_pct = pct_change(
            entry_price_exec,
            apply_exit_cost(
                min_low,
                fee_bps,
                slippage_bps,
            ),
        )

    for target in TARGETS:
        key = str(
            target
        )

        target_hit = (
            hit_time[
                key
            ]
        )

        if target_hit is None:
            if (
                stop_hit_time
                is not None
            ):
                first_touch[
                    key
                ] = "STOP"

            continue

        if stop_hit_time is None:
            first_touch[
                key
            ] = "TARGET"

        elif target_hit < stop_hit_time:
            first_touch[
                key
            ] = "TARGET"

        elif target_hit > stop_hit_time:
            first_touch[
                key
            ] = "STOP"

        else:
            first_touch[
                key
            ] = "STOP"

    horizon_end_ms = (
        int(
            event[
                "entry_open_time_ms"
            )
        )
        + HORIZON_HOURS
        * 60
        * 60
        * 1000
    )

    now_ms = int(
        datetime.now(
            timezone.utc
        ).timestamp()
        * 1000
    )

    closed = (
        now_ms
        >= horizon_end_ms
    )

    last_close_raw = float(
        rows[
            -1
        ][
            4
        ]
    )

    last_close_exec = (
        apply_exit_cost(
            last_close_raw,
            fee_bps,
            slippage_bps,
        )
    )

    net_return_pct = pct_change(
        entry_price_exec,
        last_close_exec,
    )

    label_status = (
        "CLOSED"
        if closed
        else "OPEN"
    )

    label = {
        "event_id":
            event[
                "event_id"
            ],

        "symbol":
            event[
                "symbol"
            ],

        "entry_time_utc":
            ms_to_iso(
                entry_open_time_ms
            ),

        "entry_price_exec":
            entry_price_exec,

        "stop_pct":
            STOP_PCT,

        "horizon_hours":
            HORIZON_HOURS,

        "first_touch":
            first_touch,

        "reach":
            reach,

        "hit_time":
            {
                key: (
                    ms_to_iso(
                        value
                    )
                    if value
                    is not None
                    else None
                )
                for key, value
                in hit_time.items()
            },

        "mfe_pct":
            mfe_pct,

        "mae_pct":
            mae_pct,

        "raw_mfe_pct":
            raw_mfe_pct,

        "executable_mfe_pct":
            executable_mfe_pct,

        "net_return_pct":
            net_return_pct,

        "btc_return_pct":
            None,

        "universe_return_pct":
            None,

        "excess_vs_btc_pct":
            None,

        "excess_vs_universe_pct":
            None,

        "label_status":
            label_status,
    }

    return (
        label,
        closed,
    )


def main():
    init_db()

    events = (
        get_pending_events()
    )

    print(
        "=" * 80
    )

    print(
        "BINANCE AVCI 2 OUTCOME LABELER"
    )

    print(
        f"UTC: {utc_now()}"
    )

    print(
        f"Acik event: "
        f"{len(events)}"
    )

    print(
        "=" * 80
    )

    if not events:
        print(
            "Etiketlenecek event yok."
        )

        print(
            "=" * 80
        )

        return

    updated = 0
    closed_count = 0
    error_count = 0

    for event in events:
        try:
            result = label_event(
                event
            )

            if result is None:
                print(
                    f"{event['symbol']}: "
                    "Henuz entry mumu yok."
                )

                continue

            label, closed = (
                result
            )

            save_outcome_label(
                label
            )

            updated += 1

            if closed:
                close_event(
                    event[
                        "event_id"
                    ],
                    utc_now(),
                )

                closed_count += 1

            print(
                f"{event['symbol']} "
                f"| "
                f"{label['label_status']} "
                f"| Net: "
                f"{label['net_return_pct']:+.2f}% "
                f"| MFE: "
                f"{label['mfe_pct']:+.2f}% "
                f"| MAE: "
                f"{label['mae_pct']:+.2f}%"
            )

        except Exception as error:
            error_count += 1

            save_data_issue(
                "OUTCOME_LABEL_FAILED",
                (
                    f"{event.get('symbol')}: "
                    f"{error}"
                ),
            )

            print(
                f"{event.get('symbol')}: "
                f"HATA - {error}"
            )

    print(
        "=" * 80
    )

    print(
        f"Guncellenen event: "
        f"{updated}"
    )

    print(
        f"Kapanan event: "
        f"{closed_count}"
    )

    print(
        f"Hata: "
        f"{error_count}"
    )

    print(
        "=" * 80
    )


if __name__ == "__main__":
    main()
