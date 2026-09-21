#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import statistics
import time
from datetime import datetime, timezone

import requests

from binance_snapshot_store import (
    init_db,
    save_scan,
    save_feature,
    save_daily_mover,
    save_data_issue,
)

CONFIG_VERSION = "binance-avci2-v1.3"

SPOT_BASES = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
)
FUTURES_BASE = "https://fapi.binance.com"

REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.10

QUOTE_ASSET = "USDT"
MIN_SPOT_VOLUME_24H = 3_000_000.0
MIN_FUTURES_VOLUME_24H = 5_000_000.0

EXCLUDED_BASES = {
    "USDC", "FDUSD", "USDP", "TUSD", "DAI", "EUR", "TRY",
}
EXCLUDED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")

KLINE_INTERVAL = "5m"
KLINE_LIMIT = 300
BASELINE_BARS = 144
BARS_15M = 3
BARS_1H = 12
BARS_3H = 36

WAKE_VOLUME_Z = 2.5
WAKE_RETURN_Z = 2.0
WAKE_TRADE_Z = 2.0

RETENTION_MIN = 0.50
PERSISTENCE_VOLUME_MULT = 1.75
REIGNITION_VOLUME_MULT = 1.80
TRIGGER_MIN_COMPONENTS = 3

CLIMAX_CHANGE_24H = 28.0
CLIMAX_CHANGE_1H = 12.0
CLIMAX_FUNDING_ABS = 0.0015
CLIMAX_OI_1H = 25.0

MAX_SELECTED = 5
WINNER_LEVELS = (20, 30, 40, 50)

FUTURES_AVAILABLE = True

session = requests.Session()
session.headers.update({"User-Agent": "binance-avci2-v1.3"})


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def spot_api_get(path, params=None):
    last_error = None

    for base in SPOT_BASES:
        try:
            response = session.get(
                base + path,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code in (403, 418, 429, 451):
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

    raise RuntimeError("All Binance spot endpoints failed")


def futures_api_get(path, params=None):
    global FUTURES_AVAILABLE

    if not FUTURES_AVAILABLE:
        return None

    try:
        response = session.get(
            FUTURES_BASE + path,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code in (403, 418, 429, 451):
            FUTURES_AVAILABLE = False
            print(
                f"Futures unavailable ({response.status_code}). "
                "Switching to SPOT-ONLY MODE."
            )
            return None

        response.raise_for_status()
        return response.json()

    except requests.RequestException as error:
        FUTURES_AVAILABLE = False
        print(f"Futures request failed: {error}")
        print("Switching to SPOT-ONLY MODE.")
        return None


def average(values):
    return statistics.fmean(values) if values else 0.0


def deviation(values):
    return statistics.pstdev(values) if len(values) >= 2 else 0.0


def zscore(value, baseline):
    if not baseline:
        return 0.0

    std = deviation(baseline)
    if std <= 1e-12:
        return 0.0

    return (value - average(baseline)) / std


def pct_change(start, end):
    if start in (None, 0) or end is None:
        return 0.0

    return (end / start - 1.0) * 100.0


def recent_sum(values, count):
    return sum(values[-count:]) if values else 0.0


def grouped_sums(values, group_size):
    result = []

    for start in range(0, len(values) - group_size + 1, group_size):
        result.append(sum(values[start:start + group_size]))

    return result


def fetch_spot_exchange_info():
    return spot_api_get("/api/v3/exchangeInfo")


def fetch_spot_24h():
    return spot_api_get("/api/v3/ticker/24hr")


def fetch_futures_24h():
    data = futures_api_get("/fapi/v1/ticker/24hr")
    return data if isinstance(data, list) else []


def fetch_klines(symbol):
    return spot_api_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": KLINE_INTERVAL,
            "limit": KLINE_LIMIT,
        },
    )


def fetch_funding(symbol):
    data = futures_api_get(
        "/fapi/v1/premiumIndex",
        {"symbol": symbol},
    )

    if not isinstance(data, dict):
        return None

    try:
        return float(data.get("lastFundingRate", 0.0))
    except (TypeError, ValueError):
        return None


def fetch_open_interest_history(symbol):
    data = futures_api_get(
        "/futures/data/openInterestHist",
        {
            "symbol": symbol,
            "period": "5m",
            "limit": 13,
        },
    )

    return data if isinstance(data, list) else []


def fetch_taker_ratio(symbol):
    data = futures_api_get(
        "/futures/data/takerlongshortRatio",
        {
            "symbol": symbol,
            "period": "5m",
            "limit": 12,
        },
    )

    return data if isinstance(data, list) else []


def build_universe():
    global FUTURES_AVAILABLE

    exchange_info = fetch_spot_exchange_info()
    spot_list = fetch_spot_24h()
    futures_list = fetch_futures_24h()

    spot_24h = {
        item["symbol"]: item
        for item in spot_list
        if "symbol" in item
    }

    futures_24h = {
        item["symbol"]: item
        for item in futures_list
        if "symbol" in item
    }

    futures_mode = bool(futures_24h) and FUTURES_AVAILABLE

    if not futures_mode:
        FUTURES_AVAILABLE = False

    symbols = []

    for item in exchange_info.get("symbols", []):
        symbol = item.get("symbol")
        base = item.get("baseAsset")
        quote = item.get("quoteAsset")

        if not symbol or not base:
            continue

        if quote != QUOTE_ASSET:
            continue

        if item.get("status") != "TRADING":
            continue

        if base in EXCLUDED_BASES:
            continue

        if any(base.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
            continue

        spot_row = spot_24h.get(symbol)
        if not spot_row:
            continue

        spot_volume = float(
            spot_row.get("quoteVolume", 0.0) or 0.0
        )

        if spot_volume < MIN_SPOT_VOLUME_24H:
            continue

        if futures_mode:
            futures_row = futures_24h.get(symbol)

            if not futures_row:
                continue

            futures_volume = float(
                futures_row.get("quoteVolume", 0.0) or 0.0
            )

            if futures_volume < MIN_FUTURES_VOLUME_24H:
                continue

        symbols.append(symbol)

    return sorted(symbols), spot_24h, futures_24h, futures_mode


def parse_klines(rows):
    opens = []
    highs = []
    lows = []
    closes = []
    quote_volumes = []
    trades = []
    taker_buy_quote = []

    for row in rows:
        opens.append(float(row[1]))
        highs.append(float(row[2]))
        lows.append(float(row[3]))
        closes.append(float(row[4]))
        quote_volumes.append(float(row[7]))
        trades.append(float(row[8]))
        taker_buy_quote.append(float(row[10]))

    returns = []

    for index in range(1, len(closes)):
        returns.append(
            pct_change(
                closes[index - 1],
                closes[index],
            )
        )

    return {
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "quote_volumes": quote_volumes,
        "trades": trades,
        "taker_buy_quote": taker_buy_quote,
        "returns": returns,
    }


def calculate_features(
    symbol,
    spot_data,
    futures_data,
    btc_change_24h,
):
    rows = fetch_klines(symbol)
    parsed = parse_klines(rows)

    closes = parsed["closes"]
    highs = parsed["highs"]
    lows = parsed["lows"]
    quote_volumes = parsed["quote_volumes"]
    trades = parsed["trades"]
    taker_buy_quote = parsed["taker_buy_quote"]
    returns = parsed["returns"]

    required_bars = BASELINE_BARS + BARS_3H + 5

    if len(closes) < required_bars:
        raise ValueError("insufficient_klines")

    current_price = closes[-1]

    change_15m = pct_change(closes[-4], current_price)
    change_1h = pct_change(closes[-13], current_price)
    change_3h = pct_change(closes[-37], current_price)
    change_24h = float(
        spot_data.get("priceChangePercent", 0.0) or 0.0
    )

    futures_change_24h = None

    if futures_data:
        try:
            futures_change_24h = float(
                futures_data.get("priceChangePercent", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            futures_change_24h = None

    baseline_volume = quote_volumes[
        -(BASELINE_BARS + BARS_3H):-BARS_3H
    ]

    baseline_trades = trades[
        -(BASELINE_BARS + BARS_3H):-BARS_3H
    ]

    baseline_returns = [
        abs(value)
        for value in returns[
            -(BASELINE_BARS + BARS_3H):-BARS_3H
        ]
    ]

    volume_15m = recent_sum(
        quote_volumes,
        BARS_15M,
    )

    volume_1h = recent_sum(
        quote_volumes,
        BARS_1H,
    )

    volume_3h = recent_sum(
        quote_volumes,
        BARS_3H,
    )

    trade_15m = recent_sum(
        trades,
        BARS_15M,
    )

    baseline_volume_15m = grouped_sums(
        baseline_volume,
        BARS_15M,
    )

    baseline_volume_1h = grouped_sums(
        baseline_volume,
        BARS_1H,
    )

    baseline_trade_15m = grouped_sums(
        baseline_trades,
        BARS_15M,
    )

    baseline_return_15m = grouped_sums(
        baseline_returns,
        BARS_15M,
    )

    volume_z = zscore(
        volume_15m,
        baseline_volume_15m,
    )

    trade_z = zscore(
        trade_15m,
        baseline_trade_15m,
    )

    return_z = zscore(
        abs(change_15m),
        baseline_return_15m,
    )

    baseline_volume_15m_median = (
        statistics.median(baseline_volume_15m)
        if baseline_volume_15m
        else 1.0
    )

    baseline_volume_1h_median = (
        statistics.median(baseline_volume_1h)
        if baseline_volume_1h
        else 1.0
    )

    volume_mult_15m = (
        volume_15m
        / (baseline_volume_15m_median or 1.0)
    )

    volume_mult_1h = (
        volume_1h
        / (baseline_volume_1h_median or 1.0)
    )

    taker_buy_15m = recent_sum(
        taker_buy_quote,
        BARS_15M,
    )

    taker_buy_ratio_15m = (
        taker_buy_15m / volume_15m
        if volume_15m > 0
        else 0.0
    )

    low_3h = min(
        lows[-BARS_3H:]
    )

    high_3h = max(
        highs[-BARS_3H:]
    )

    impulse_range = (
        high_3h
        - low_3h
    )

    retention_proxy = (
        (current_price - low_3h)
        / impulse_range
        if impulse_range > 0
        else 0.0
    )

    oi_change_1h = None
    funding_rate = None
    futures_taker_ratio = None

    if FUTURES_AVAILABLE:
        oi_history = (
            fetch_open_interest_history(
                symbol
            )
        )

        if len(oi_history) >= 2:
            try:
                first_oi = float(
                    oi_history[0].get(
                        "sumOpenInterestValue",
                        0.0,
                    )
                    or 0.0
                )

                last_oi = float(
                    oi_history[-1].get(
                        "sumOpenInterestValue",
                        0.0,
                    )
                    or 0.0
                )

                oi_change_1h = pct_change(
                    first_oi,
                    last_oi,
                )

            except (TypeError, ValueError):
                oi_change_1h = None

        funding_rate = (
            fetch_funding(
                symbol
            )
        )

        taker_history = (
            fetch_taker_ratio(
                symbol
            )
        )

        taker_values = []

        for item in taker_history:
            try:
                taker_values.append(
                    float(
                        item.get(
                            "buySellRatio",
                            0.0,
                        )
                        or 0.0
                    )
                )

            except (TypeError, ValueError):
                pass

        if taker_values:
            futures_taker_ratio = (
                average(
                    taker_values
                )
            )

    btc_relative_24h = (
        change_24h
        - btc_change_24h
    )

    wake_components = {
        "volume_z":
            volume_z
            >= WAKE_VOLUME_Z,

        "return_z":
            return_z
            >= WAKE_RETURN_Z,

        "trade_z":
            trade_z
            >= WAKE_TRADE_Z,
    }

    wakeup = (
        sum(
            wake_components.values()
        )
        >= 2
    )

    persistence = (
        volume_mult_1h
        >= PERSISTENCE_VOLUME_MULT
        and
        change_1h > -3.0
    )

    retention = (
        retention_proxy
        >= RETENTION_MIN
    )

    previous_15m_volume = (
        sum(
            quote_volumes[-6:-3]
        )
    )

    reignition_ratio = (
        volume_15m
        / previous_15m_volume
        if previous_15m_volume > 0
        else 0.0
    )

    reignition = (
        reignition_ratio
        >= REIGNITION_VOLUME_MULT
        and
        change_15m > 0
    )

    trigger_components = {
        "price_positive":
            change_15m > 0.8,

        "spot_taker_buy":
            taker_buy_ratio_15m
            >= 0.55,

        "retention":
            retention,

        "oi_support":
            (
                oi_change_1h
                is not None
                and
                oi_change_1h > 2.0
            ),

        "btc_relative":
            btc_relative_24h > 2.0,

        "reignition":
            reignition,
    }

    trigger = (
        sum(
            trigger_components.values()
        )
        >= TRIGGER_MIN_COMPONENTS
    )

    climax_risk = (
        change_24h
        >= CLIMAX_CHANGE_24H

        or
        change_1h
        >= CLIMAX_CHANGE_1H

        or
        (
            funding_rate
            is not None
            and
            abs(funding_rate)
            >= CLIMAX_FUNDING_ABS
        )

        or
        (
            oi_change_1h
            is not None
            and
            oi_change_1h
            >= CLIMAX_OI_1H
        )
    )

    engine = "MIXED"

    if (
        FUTURES_AVAILABLE
        and
        change_15m > 0
        and
        oi_change_1h is not None
    ):
        if (
            oi_change_1h > 2.0
            and
            futures_taker_ratio is not None
            and
            futures_taker_ratio > 1.05
        ):
            engine = (
                "LEVERAGED_BREAKOUT"
            )

        elif (
            oi_change_1h < -2.0
            and
            change_15m > 1.5
        ):
            engine = (
                "SHORT_SQUEEZE"
            )

    if (
        taker_buy_ratio_15m
        >= 0.60
    ):
        engine = (
            "SPOT_LED_DEMAND"
        )

    score = 0

    score += (
        1 if wakeup else 0
    )

    score += (
        1 if persistence else 0
    )

    score += (
        1 if retention else 0
    )

    score += (
        1 if reignition else 0
    )

    score += (
        1 if trigger else 0
    )

    score += (
        1
        if taker_buy_ratio_15m
        >= 0.55
        else 0
    )

    score += (
        1
        if (
            oi_change_1h
            is not None
            and
            oi_change_1h > 0
        )
        else 0
    )

    score += (
        1
        if btc_relative_24h > 0
        else 0
    )

    if climax_risk:
        score = max(
            0,
            score - 2,
        )

    if (
        trigger
        and
        retention
        and
        (
            wakeup
            or persistence
            or reignition
        )
    ):
        stage = "TRIGGER"

    elif reignition:
        stage = "REIGNITION"

    elif (
        persistence
        and retention
    ):
        stage = "CONTINUATION"

    elif wakeup:
        stage = "WAKE_UP"

    else:
        stage = "OBSERVE"

    return {
        "ts_utc":
            utc_now(),

        "config_version":
            CONFIG_VERSION,

        "symbol":
            symbol,

        "stage":
            stage,

        "engine":
            engine,

        "score":
            score,

        "price":
            current_price,

        "change_15m":
            change_15m,

        "change_1h":
            change_1h,

        "change_3h":
            change_3h,

        "change_24h":
            change_24h,

        "btc_relative_24h":
            btc_relative_24h,

        "volume_z_15m":
            volume_z,

        "trade_z_15m":
            trade_z,

        "return_z_15m":
            return_z,

        "volume_mult_15m":
            volume_mult_15m,

        "volume_mult_1h":
            volume_mult_1h,

        "retention_proxy":
            retention_proxy,

        "taker_buy_ratio_15m":
            taker_buy_ratio_15m,

        "oi_change_1h_pct":
            oi_change_1h,

        "funding_rate":
            funding_rate,

        "wakeup":
            wakeup,

        "persistence":
            persistence,

        "retention":
            retention,

        "reignition":
            reignition,

        "trigger":
            trigger,

        "climax_risk":
            climax_risk,

        "futures_change_24h":
            futures_change_24h,

        "futures_taker_ratio_1h":
            futures_taker_ratio,

        "quote_volume_15m":
            volume_15m,

        "quote_volume_1h":
            volume_1h,

        "quote_volume_3h":
            volume_3h,

        "wake_components":
            wake_components,

        "trigger_components":
            trigger_components,
    }


def ranking_key(feature):
    return (
        int(feature["trigger"]),
        int(feature["reignition"]),
        int(feature["persistence"]),
        int(feature["retention"]),
        feature["score"],
        feature["volume_z_15m"],
        feature["btc_relative_24h"],
    )


def run_scan():
    init_db()

    scan_time = utc_now()

    print(
        "=" * 80
    )

    print(
        "BINANCE AVCI 2 V1.3"
    )

    print(
        scan_time
    )

    print(
        f"Config: "
        f"{CONFIG_VERSION}"
    )

    try:
        (
            universe,
            spot_24h,
            futures_24h,
            futures_mode,
        ) = build_universe()

    except Exception as error:
        save_data_issue(
            "UNIVERSE_FETCH_FAILED",
            str(error),
            scan_time,
        )

        raise

    if futures_mode:
        print(
            "Data mode: SPOT + FUTURES"
        )

    else:
        print(
            "Data mode: SPOT-ONLY FALLBACK"
        )

    btc_change_24h = float(
        spot_24h.get(
            "BTCUSDT",
            {},
        ).get(
            "priceChangePercent",
            0.0,
        )
        or 0.0
    )

    save_scan(
        scan_time,
        CONFIG_VERSION,
        len(universe),
        btc_change_24h,
    )

    daily_movers = []

    for symbol in universe:
        change = float(
            spot_24h[
                symbol
            ].get(
                "priceChangePercent",
                0.0,
            )
            or 0.0
        )

        if (
            change
            >= min(
                WINNER_LEVELS
            )
        ):
            daily_movers.append(
                (
                    symbol,
                    change,
                    float(
                        spot_24h[
                            symbol
                        ].get(
                            "quoteVolume",
                            0.0,
                        )
                        or 0.0
                    ),
                )
            )

    daily_movers.sort(
        key=lambda item:
            item[1],
        reverse=True,
    )

    for rank, item in enumerate(
        daily_movers[:30],
        start=1,
    ):
        symbol, change, volume = item

        save_daily_mover(
            scan_time[:10],
            symbol,
            change,
            volume,
            rank,
            CONFIG_VERSION,
        )

    signals = []

    total = len(
        universe
    )

    for index, symbol in enumerate(
        universe,
        start=1,
    ):
        try:
            futures_data = (
                futures_24h.get(
                    symbol
                )
                if futures_mode
                else None
            )

            feature = (
                calculate_features(
                    symbol,
                    spot_24h[
                        symbol
                    ],
                    futures_data,
                    btc_change_24h,
                )
            )

            save_feature(
                feature,
                is_selected=0,
            )

            if (
                feature[
                    "stage"
                ]
                != "OBSERVE"

                and
                not feature[
                    "climax_risk"
                ]
            ):
                signals.append(
                    feature
                )

        except Exception as error:
            save_data_issue(
                "SYMBOL_SCAN_FAILED",
                f"{symbol}: {error}",
                scan_time,
            )

        print(
            f"[{index}/{total}] "
            f"{symbol}"
        )

        time.sleep(
            SCAN_SLEEP_SECONDS
        )

    signals.sort(
        key=ranking_key,
        reverse=True,
    )

    selected = (
        signals[
            :MAX_SELECTED
        ]
    )

    print(
        "-" * 80
    )

    print(
        f"Universe: "
        f"{len(universe)}"
    )

    print(
        f"Signals: "
        f"{len(signals)}"
    )

    print(
        f"Selected: "
        f"{len(selected)}"
    )

    print(
        "-" * 80
    )

    if not selected:
        print(
            "NO CANDIDATES"
        )

        print(
            "=" * 80
        )

        return

    for rank, feature in enumerate(
        selected,
        start=1,
    ):
        save_feature(
            feature,
            is_selected=1,
        )

        print(
            f"{rank}. "
            f"{feature['symbol']} "
            f"| {feature['stage']} "
            f"| score "
            f"{feature['score']}/8"
        )

        print(
            f"   24H: "
            f"{feature['change_24h']:+.2f}% "
            f"| 1H: "
            f"{feature['change_1h']:+.2f}% "
            f"| 15M: "
            f"{feature['change_15m']:+.2f}%"
        )

        print(
            f"   Volume Z: "
            f"{feature['volume_z_15m']:.2f} "
            f"| Volume 1H: "
            f"{feature['volume_mult_1h']:.2f}x"
        )

        print(
            f"   Retention: "
            f"{feature['retention_proxy']:.2f} "
            f"| BTC Relative: "
            f"{feature['btc_relative_24h']:+.2f}%"
        )

        print(
            f"   Wake-up: "
            f"{feature['wakeup']} "
            f"| Persistence: "
            f"{feature['persistence']} "
            f"| Re-ignition: "
            f"{feature['reignition']} "
            f"| Trigger: "
            f"{feature['trigger']}"
        )

        oi_value = (
            feature[
                "oi_change_1h_pct"
            ]
        )

        funding_value = (
            feature[
                "funding_rate"
            ]
        )

        oi_text = (
            "N/A"
            if oi_value is None
            else f"{oi_value:+.2f}%"
        )

        funding_text = (
            "N/A"
            if funding_value is None
            else f"{funding_value:.6f}"
        )

        print(
            f"   OI 1H: "
            f"{oi_text} "
            f"| Funding: "
            f"{funding_text} "
            f"| Engine: "
            f"{feature['engine']}"
        )

        print(
            "-" * 80
        )

    print(
        "=" * 80
    )


if __name__ == "__main__":
    run_scan()
