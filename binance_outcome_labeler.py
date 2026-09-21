#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import datetime, timezone

import requests

from binance_snapshot_store import (
    init_db,
    get_pending_events,
    update_event_entry,
    close_event,
    save_outcome_label,
    save_data_issue,
    get_universe_return,
    save_raw_klines,
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
HORIZONS = (4, 24, 72)
PRIMARY_TARGET_PCT = 10.0

FEE_BPS_PER_SIDE = 10.0
SLIPPAGE_BPS_PER_SIDE = 10.0

session = requests.Session()

session.headers.update({
    "User-Agent": "binance-avci2-outcome-labeler/2.0"
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
    interval=INTERVAL,
):
    return api_get(
        "/api/v3/klines",
        {
            "symbol":
                symbol,

            "interval":
                interval,

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

    return fetch_klines(
        event[
            "symbol"
        ],
        entry_open_time_ms,
        request_end_ms,
        1000,
    )


def get_btc_path_for_events(
    events,
):
    if not events:
        return []

    start_time_ms = min(
        int(event["entry_open_time_ms"])
        for event in events
    )

    now_ms = int(
        datetime.now(
            timezone.utc
        ).timestamp()
        * 1000
    )

    end_time_ms = min(
        max(
            int(event["entry_open_time_ms"])
            + HORIZON_HOURS
            * 60
            * 60
            * 1000
            for event in events
        ),
        now_ms,
    )

    rows = []
    cursor_ms = start_time_ms
    interval_ms = 5 * 60 * 1000

    while cursor_ms <= end_time_ms:
        batch = fetch_klines(
            "BTCUSDT",
            cursor_ms,
            end_time_ms,
            1000,
        )

        if not batch:
            break

        rows.extend(
            batch
        )

        next_cursor_ms = (
            int(batch[-1][0])
            + interval_ms
        )

        if next_cursor_ms <= cursor_ms:
            break

        cursor_ms = next_cursor_ms

        if len(batch) < 1000:
            break

    return rows


def benchmark_return(
    btc_rows,
    entry_open_time_ms,
    last_open_time_ms,
):
    matching_rows = [
        row
        for row in btc_rows
        if entry_open_time_ms
        <= int(row[0])
        <= last_open_time_ms
    ]

    if not matching_rows:
        return None

    first_row = matching_rows[0]

    if int(first_row[0]) != entry_open_time_ms:
        return None

    return pct_change(
        float(first_row[1]),
        float(matching_rows[-1][4]),
    )


def resolve_same_candle(
    symbol,
    bar_open_time_ms,
    entry_price_exec,
    fee_bps,
    slippage_bps,
    target,
):
    try:
        rows = fetch_klines(
            symbol,
            bar_open_time_ms,
            bar_open_time_ms + 5 * 60 * 1000 - 1,
            limit=10,
            interval="1m",
        )

    except Exception:
        return "STOP"

    if not rows:
        return "STOP"

    save_raw_klines(symbol, rows, "outcome-v2", interval_value="1m")

    for row in rows:
        high_return = pct_change(
            entry_price_exec,
            apply_exit_cost(
                float(row[2]),
                fee_bps,
                slippage_bps,
            ),
        )

        low_return = pct_change(
            entry_price_exec,
            apply_exit_cost(
                float(row[3]),
                fee_bps,
                slippage_bps,
            ),
        )

        stop_hit = low_return <= STOP_PCT
        target_hit = high_return >= target

        if stop_hit:
            return "STOP"

        if target_hit:
            return "TARGET"

    return "STOP"


def rows_for_horizon(rows, entry_open_time_ms, horizon_hours):
    end_ms = entry_open_time_ms + horizon_hours * 60 * 60 * 1000
    return [row for row in rows if int(row[0]) < end_ms]


def path_metrics(rows, entry_price_raw, entry_price_exec, fee_bps, slippage_bps):
    if not rows:
        return {"raw_mfe_pct": None, "mfe_pct": None, "mae_pct": None,
                "close_return_pct": None}
    max_high = max(float(row[2]) for row in rows)
    min_low = min(float(row[3]) for row in rows)
    last_close = apply_exit_cost(float(rows[-1][4]), fee_bps, slippage_bps)
    return {
        "raw_mfe_pct": pct_change(entry_price_raw, max_high),
        "mfe_pct": pct_change(
            entry_price_exec,
            apply_exit_cost(max_high, fee_bps, slippage_bps),
        ),
        "mae_pct": pct_change(
            entry_price_exec,
            apply_exit_cost(min_low, fee_bps, slippage_bps),
        ),
        "close_return_pct": pct_change(entry_price_exec, last_close),
    }


def evaluate_barrier(event, rows, entry_price_exec, fee_bps,
                     slippage_bps, target, horizon_complete):
    for row in rows:
        open_time_ms = int(row[0])
        open_return = pct_change(
            entry_price_exec,
            apply_exit_cost(float(row[1]), fee_bps, slippage_bps),
        )
        high_return = pct_change(
            entry_price_exec,
            apply_exit_cost(float(row[2]), fee_bps, slippage_bps),
        )
        low_return = pct_change(
            entry_price_exec,
            apply_exit_cost(float(row[3]), fee_bps, slippage_bps),
        )
        target_hit = high_return >= target
        stop_hit = low_return <= STOP_PCT

        if not target_hit and not stop_hit:
            continue

        if target_hit and stop_hit:
            result = resolve_same_candle(
                event["symbol"], open_time_ms, entry_price_exec,
                fee_bps, slippage_bps, target,
            )
        elif target_hit:
            result = "TARGET"
        else:
            result = "STOP"

        realized = target
        if result == "STOP":
            realized = min(STOP_PCT, open_return) if open_return <= STOP_PCT else STOP_PCT

        return {
            "result": result,
            "touch_time_utc": ms_to_iso(open_time_ms),
            "net_return_pct": realized,
            "complete": True,
        }

    metrics = path_metrics(rows, float(rows[0][1]), entry_price_exec,
                           fee_bps, slippage_bps) if rows else {}
    return {
        "result": "TIMEOUT" if horizon_complete else "OPEN",
        "touch_time_utc": None,
        "net_return_pct": metrics.get("close_return_pct"),
        "complete": horizon_complete,
    }


def label_event(event, btc_rows=None):
    rows = get_event_path(event)
    if not rows:
        return None
    save_raw_klines(
        event["symbol"], rows, event["config_version"], interval_value="5m"
    )

    entry_open_time_ms = int(event["entry_open_time_ms"])
    if int(rows[0][0]) != entry_open_time_ms:
        return None

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows = [row for row in rows if int(row[6]) < now_ms]
    if not rows:
        return None

    entry_price_raw = float(rows[0][1])
    fee_bps = float(event.get("fee_bps_per_side") or FEE_BPS_PER_SIDE)
    slippage_bps = float(
        event.get("slippage_bps_per_side") or SLIPPAGE_BPS_PER_SIDE
    )
    entry_price_exec = apply_entry_cost(entry_price_raw, fee_bps, slippage_bps)

    if event.get("entry_status") != "READY":
        update_event_entry(
            event["event_id"], ms_to_iso(entry_open_time_ms),
            entry_price_raw, entry_price_exec,
        )

    barrier_results = {}
    horizon_metrics = {}
    for horizon in HORIZONS:
        cutoff_ms = entry_open_time_ms + horizon * 60 * 60 * 1000
        horizon_rows = rows_for_horizon(rows, entry_open_time_ms, horizon)
        horizon_complete = now_ms >= cutoff_ms
        metrics = path_metrics(
            horizon_rows, entry_price_raw, entry_price_exec,
            fee_bps, slippage_bps,
        )
        metrics["complete"] = horizon_complete
        metrics["last_time_utc"] = (
            ms_to_iso(int(horizon_rows[-1][0])) if horizon_rows else None
        )
        horizon_metrics[str(horizon)] = metrics
        barrier_results[str(horizon)] = {
            str(target): evaluate_barrier(
                event, horizon_rows, entry_price_exec, fee_bps,
                slippage_bps, target, horizon_complete,
            )
            for target in TARGETS
        }

    primary = barrier_results[str(HORIZON_HOURS)][str(PRIMARY_TARGET_PCT)]
    full_metrics = horizon_metrics[str(HORIZON_HOURS)]
    closed = now_ms >= (
        entry_open_time_ms + HORIZON_HOURS * 60 * 60 * 1000
    )
    net_return_pct = primary["net_return_pct"]

    reach = {
        str(target): any(
            result["result"] == "TARGET"
            for result in [barrier_results[str(HORIZON_HOURS)][str(target)]]
        )
        for target in TARGETS
    }
    first_touch = {
        str(target): barrier_results[str(HORIZON_HOURS)][str(target)]["result"]
        for target in TARGETS
    }
    hit_time = {
        str(target): barrier_results[str(HORIZON_HOURS)][str(target)]["touch_time_utc"]
        for target in TARGETS
    }

    last_open_ms = int(rows[-1][0])
    btc_return_pct = benchmark_return(
        btc_rows or [], entry_open_time_ms, last_open_ms,
    )
    universe_return_pct = get_universe_return(
        event["config_version"], event["signal_time_utc"],
        ms_to_iso(last_open_ms),
    )

    label = {
        "event_id": event["event_id"],
        "symbol": event["symbol"],
        "entry_time_utc": ms_to_iso(entry_open_time_ms),
        "entry_price_exec": entry_price_exec,
        "stop_pct": STOP_PCT,
        "horizon_hours": HORIZON_HOURS,
        "first_touch": first_touch,
        "reach": reach,
        "hit_time": hit_time,
        "mfe_pct": full_metrics["mfe_pct"],
        "mae_pct": full_metrics["mae_pct"],
        "raw_mfe_pct": full_metrics["raw_mfe_pct"],
        "executable_mfe_pct": full_metrics["mfe_pct"],
        "net_return_pct": net_return_pct,
        "btc_return_pct": btc_return_pct,
        "universe_return_pct": universe_return_pct,
        "excess_vs_btc_pct": (
            net_return_pct - btc_return_pct
            if net_return_pct is not None and btc_return_pct is not None else None
        ),
        "excess_vs_universe_pct": (
            net_return_pct - universe_return_pct
            if net_return_pct is not None and universe_return_pct is not None else None
        ),
        "primary_target_pct": PRIMARY_TARGET_PCT,
        "primary_exit_reason": primary["result"],
        "primary_exit_time_utc": primary["touch_time_utc"],
        "primary_exit_return_pct": primary["net_return_pct"],
        "barrier_results": barrier_results,
        "horizon_metrics": horizon_metrics,
        "label_status": "CLOSED" if closed else "OPEN",
    }
    return label, closed


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

    try:
        btc_rows = get_btc_path_for_events(
            events
        )

    except Exception as error:
        btc_rows = []

        save_data_issue(
            "BTC_BENCHMARK_FETCH_FAILED",
            str(error),
        )

    updated = 0
    closed_count = 0
    error_count = 0

    for event in events:
        try:
            result = (
                label_event(
                    event,
                    btc_rows,
                )
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
                f"| {label['label_status']} "
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
