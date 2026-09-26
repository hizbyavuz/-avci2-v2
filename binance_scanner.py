#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone

import requests

from research_telemetry import (start as telemetry_start, record as telemetry_record,\n                                infer_source_event_time)

from binance_snapshot_store import (
    init_db,
    save_scan,
    save_scan_observation,
    save_feature,
    save_daily_mover,
    save_data_issue,
    save_raw_klines,
    save_raw_deriv,
    save_orderbook_snapshot,
    get_asset_metadata,
    save_asset_metadata,
    save_universe_member,
    update_scan_health,
    create_signal_event,
)

CONFIG_VERSION = "binance-avci2-v2.4-early-entry-observation"

DATA_MODE_SPOT = "SPOT_ONLY"
DATA_MODE_FULL = "SPOT_FUTURES_FULL"

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

KLINE_INTERVAL = "5m"
KLINE_LIMIT = 1000

# Seven full days of closed 5-minute bars.  This is deliberately frozen in the
# config hash so later research cannot silently change the comparison window.
BASELINE_BARS = 7 * 24 * 12

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
MAX_NEAR_MISS = 5
MAX_RANDOM_CONTROL = 5

COOLDOWN_HOURS = 24
ENTRY_DELAY_SECONDS = 120

FEE_BPS_PER_SIDE = 10.0
SLIPPAGE_BPS_PER_SIDE = 10.0

MAX_SPREAD_BPS = 30.0
MAX_BUY_IMPACT_1K_BPS = 35.0
MAX_BUY_IMPACT_5K_BPS = 100.0
MIN_VALID_UNIVERSE = 80
MIN_COIN_AGE_DAYS = 30
HISTORY_LOOKBACK_DAYS = 90
MAX_GAIN_FROM_90D_FLOOR_PCT = 50.0

BTC_UP_REGIME_PCT = 2.0
BTC_DOWN_REGIME_PCT = -2.0
MAX_CLOCK_SKEW_SECONDS = 10.0
MAX_KLINE_STALENESS_MINUTES = 12.0

WINNER_LEVELS = (
    20,
    30,
    40,
    50,
)

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
    "AMDB",
    "NVDAB",
    "INTCB",
    "QQQB",
    "SOXLB",
    "GOOGLB",
    "TSLAB",
}

LEVERAGED_BASES = {
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

# Binance also lists tokenized equities and ETFs under ordinary USDT pairs.
# Match the underlying ticker plus the issuer suffix, rather than excluding
# every asset ending in B or X (which would remove legitimate crypto coins).
TOKENIZED_SECURITY_TICKERS = {
    "AAPL", "AMD", "AMZN", "ARM", "COIN", "GOOGL", "HOOD",
    "INTC", "META", "MSFT", "MSTR", "NFLX", "NVDA", "PLTR",
    "QQQ", "SOXL", "SPY", "TSLA", "TSM",
}
TOKENIZED_SECURITY_BASES = {
    ticker + suffix
    for ticker in TOKENIZED_SECURITY_TICKERS
    for suffix in ("B", "X")
} | set("""
    AAOIB AAPLB ALABB AMATB AMDB AMZNB ARMB ASMLB ASTSB AVGOB AXTIB
    BABAB BEB BMNRB CBRSB COHRB COINB CRCLB CRDOB CRWVB DELLB DJTB
    DRAMB EWYB FLNCB GLWB GMEB GOOGLB GSB HOODB IBMB INTCB INTWB
    IRENB KORUB LITEB METAB MRVLB MSFTB MSTRB MUB MUUB MVLLB NBISB
    NFLXB NOKB NVDAB ORCLB PLTRB PYPLB QCOMB QNTB QQQB RKLBB SKHYB
    SMCIB SMHB SNDKB SNXXB SOXLB SOXSB SPCXB SPYB TQQQB TSLAB TSMB
    USARB WDCB
""".split())

FUTURES_AVAILABLE = True

session = requests.Session()

session.headers.update({
    "User-Agent": "binance-avci2-v2.1-final"
})


CONFIG_SNAPSHOT = {
    "config_version": CONFIG_VERSION,
    "min_spot_volume_24h": MIN_SPOT_VOLUME_24H,
    "min_futures_volume_24h": MIN_FUTURES_VOLUME_24H,
    "baseline_bars": BASELINE_BARS,
    "wake_volume_z": WAKE_VOLUME_Z,
    "wake_return_z": WAKE_RETURN_Z,
    "wake_trade_z": WAKE_TRADE_Z,
    "retention_min": RETENTION_MIN,
    "persistence_volume_mult": PERSISTENCE_VOLUME_MULT,
    "reignition_volume_mult": REIGNITION_VOLUME_MULT,
    "trigger_min_components": TRIGGER_MIN_COMPONENTS,
    "climax_change_24h": CLIMAX_CHANGE_24H,
    "climax_change_1h": CLIMAX_CHANGE_1H,
    "climax_funding_abs": CLIMAX_FUNDING_ABS,
    "climax_oi_1h": CLIMAX_OI_1H,
    "max_selected": MAX_SELECTED,
    "max_near_miss": MAX_NEAR_MISS,
    "max_random_control": MAX_RANDOM_CONTROL,
    "cooldown_hours": COOLDOWN_HOURS,
    "entry_delay_seconds": ENTRY_DELAY_SECONDS,
    "fee_bps_per_side": FEE_BPS_PER_SIDE,
    "slippage_bps_per_side": SLIPPAGE_BPS_PER_SIDE,
    "max_spread_bps": MAX_SPREAD_BPS,
    "max_buy_impact_1k_bps": MAX_BUY_IMPACT_1K_BPS,
    "max_buy_impact_5k_bps": MAX_BUY_IMPACT_5K_BPS,
    "min_valid_universe": MIN_VALID_UNIVERSE,
    "winner_levels": list(WINNER_LEVELS),
    "quote_asset": QUOTE_ASSET,
    "kline_interval": KLINE_INTERVAL,
    "kline_limit": KLINE_LIMIT,
    "max_clock_skew_seconds": MAX_CLOCK_SKEW_SECONDS,
    "max_kline_staleness_minutes": MAX_KLINE_STALENESS_MINUTES,
    "min_coin_age_days": MIN_COIN_AGE_DAYS,
    "history_lookback_days": HISTORY_LOOKBACK_DAYS,
    "max_gain_from_90d_floor_pct": MAX_GAIN_FROM_90D_FLOOR_PCT,
    "tokenized_security_bases": sorted(TOKENIZED_SECURITY_BASES),
    "btc_up_regime_pct": BTC_UP_REGIME_PCT,
    "btc_down_regime_pct": BTC_DOWN_REGIME_PCT,
}

CONFIG_HASH = hashlib.sha256(
    json.dumps(
        CONFIG_SNAPSHOT,
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()


def is_excluded_base(base):
    return (
        base in EXCLUDED_BASES
        or base in LEVERAGED_BASES
        or base in TOKENIZED_SECURITY_BASES
    )


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def spot_api_get(
    path,
    params=None,
):
    last_error = None

    for base in SPOT_BASES:
        try:
            _tp,_tu = telemetry_start()
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
            data=response.json()
            telemetry_record("BINANCE","BINANCE_SPOT",path,_tp,_tu,
                             status="OK",http_status=response.status_code,
                             source_event_time_utc=infer_source_event_time(
                                 "BINANCE_SPOT",path,data),
                             context={"base":base})
            return data

        except requests.RequestException as error:
            try:
                telemetry_record("BINANCE","BINANCE_SPOT",path,_tp,_tu,
                                 status="ERROR",error=error,
                                 context={"base":base})
            except Exception:
                pass
            last_error = error

    if last_error is not None:
        raise last_error

    raise RuntimeError(
        "All Binance spot endpoints failed"
    )


def futures_api_get(
    path,
    params=None,
):
    global FUTURES_AVAILABLE

    if not FUTURES_AVAILABLE:
        return None

    try:
        _tp,_tu = telemetry_start()
        response = session.get(
            FUTURES_BASE + path,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code in (
            403,
            418,
            429,
            451,
        ):
            FUTURES_AVAILABLE = False

            print(
                f"Futures verisi kullanilamiyor "
                f"({response.status_code}). "
                "SADECE SPOT moda geciliyor."
            )

            return None

        response.raise_for_status()
        data=response.json()
        telemetry_record("BINANCE","BINANCE_FUTURES",path,_tp,_tu,
                         status="OK",http_status=response.status_code,
                         source_event_time_utc=infer_source_event_time(
                             "BINANCE_FUTURES",path,data))
        return data

    except requests.RequestException as error:
        try:
            telemetry_record("BINANCE","BINANCE_FUTURES",path,_tp,_tu,
                             status="ERROR",error=error)
        except Exception:
            pass
        FUTURES_AVAILABLE = False

        print(
            f"Futures veri hatasi: "
            f"{error}"
        )

        print(
            "SADECE SPOT moda geciliyor."
        )

        return None


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
    if len(values) < 2:
        return 0.0

    return statistics.pstdev(
        values
    )


def zscore(
    value,
    baseline,
):
    if not baseline:
        return 0.0

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

    if end is None:
        return 0.0

    return (
        end / start
        - 1.0
    ) * 100.0


def recent_sum(
    values,
    count,
):
    if not values:
        return 0.0

    return sum(
        values[
            -count:
        ]
    )


def grouped_sums(
    values,
    group_size,
):
    result = []

    for start in range(
        0,
        len(values)
        - group_size
        + 1,
        group_size,
    ):
        result.append(
            sum(
                values[
                    start:
                    start + group_size
                ]
            )
        )

    return result


def impulse_retention(parsed, baseline_volume_15m_median):
    """Measure retention from the first qualifying positive impulse, not range position."""
    closes = parsed["closes"]
    lows = parsed["lows"]
    highs = parsed["highs"]
    volumes = parsed["quote_volumes"]
    open_times = parsed["open_times"]
    start = max(2, len(closes) - BARS_3H)

    for index in range(start, len(closes)):
        impulse_return = pct_change(closes[index - 2], closes[index])
        impulse_volume = sum(volumes[index - 2:index + 1])
        volume_multiple = impulse_volume / (baseline_volume_15m_median or 1.0)
        if impulse_return < 1.0 or volume_multiple < REIGNITION_VOLUME_MULT:
            continue
        impulse_low = min(lows[index - 2:index + 1])
        impulse_high = max(highs[index - 2:])
        impulse_size = impulse_high - impulse_low
        retention = (
            (closes[-1] - impulse_low) / impulse_size
            if impulse_size > 0 else 0.0
        )
        return {
            "retention": max(0.0, min(1.5, retention)),
            "impulse_start_ms": open_times[index - 2],
            "impulse_low": impulse_low,
            "impulse_high": impulse_high,
            "impulse_return_pct": impulse_return,
            "impulse_volume_multiple": volume_multiple,
        }

    return {
        "retention": 0.0,
        "impulse_start_ms": None,
        "impulse_low": None,
        "impulse_high": None,
        "impulse_return_pct": None,
        "impulse_volume_multiple": None,
    }


def normalized_deficit(value, threshold):
    return max(0.0, (threshold - float(value or 0.0)) / abs(threshold))


def qualification_distance(feature):
    wake_deficits = sorted((
        normalized_deficit(feature.get("volume_z_15m"), WAKE_VOLUME_Z),
        normalized_deficit(feature.get("trade_z_15m"), WAKE_TRADE_Z),
        normalized_deficit(feature.get("return_z_15m"), WAKE_RETURN_Z),
    ))
    wake_distance = sum(wake_deficits[:2])
    continuation_distance = (
        normalized_deficit(feature.get("volume_mult_1h"), PERSISTENCE_VOLUME_MULT)
        + normalized_deficit(feature.get("retention_proxy"), RETENTION_MIN)
    )
    reignition_distance = normalized_deficit(
        feature.get("reignition_ratio"), REIGNITION_VOLUME_MULT
    ) + (0.0 if float(feature.get("change_15m") or 0.0) > 0 else 1.0)
    trigger_count = sum((feature.get("trigger_components") or {}).values())
    trigger_distance = normalized_deficit(trigger_count, TRIGGER_MIN_COMPONENTS)
    return min(wake_distance, continuation_distance, reignition_distance,
               trigger_distance)


def fetch_spot_exchange_info():
    return spot_api_get(
        "/api/v3/exchangeInfo"
    )


def fetch_spot_24h():
    return spot_api_get(
        "/api/v3/ticker/24hr"
    )


def fetch_listing_time_ms(symbol):
    rows = spot_api_get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": "1d", "startTime": 0, "limit": 1},
    )
    return int(rows[0][0]) if rows else None


def recent_history_gain(symbol, price, scan_time):
    """Gain from the lowest *daily close* in the last 90 completed days."""
    scan_ms = int(datetime.fromisoformat(scan_time).timestamp() * 1000)
    # Exclude today's unfinished daily candle; the current signal close is price.
    end_ms = (scan_ms // 86400000) * 86400000 - 1
    rows = spot_api_get("/api/v3/klines", {
        "symbol": symbol, "interval": "1d", "endTime": end_ms,
        "limit": HISTORY_LOOKBACK_DAYS,
    })
    if not isinstance(rows, list) or len(rows) < MIN_COIN_AGE_DAYS - 1:
        raise ValueError("Not enough closed daily history")
    if int(rows[-1][6]) < end_ms - 86400000:
        raise ValueError("Stale daily history")
    closes = [float(row[4]) for row in rows if int(row[6]) <= end_ms]
    if len(closes) < MIN_COIN_AGE_DAYS - 1 or min(closes) <= 0:
        raise ValueError("Invalid closed daily history")
    floor = min(closes)
    return {"history_floor_90d": floor,
            "history_gain_90d_pct": pct_change(floor, price),
            "history_gain_30d_pct": pct_change(min(closes[-30:]), price),
            "history_days_available": len(closes)}


def fetch_book_tickers():
    data = spot_api_get(
        "/api/v3/ticker/bookTicker"
    )

    return {
        row["symbol"]: row
        for row in data
        if isinstance(row, dict)
        and row.get("symbol")
    }


def fetch_depth(symbol):
    return spot_api_get(
        "/api/v3/depth",
        {
            "symbol": symbol,
            "limit": 100,
        },
    )


def fetch_server_time_ms():
    return int(spot_api_get("/api/v3/time")["serverTime"])


def book_spread_bps(book):
    if not book:
        return None

    try:
        bid = float(book.get("bidPrice") or 0.0)
        ask = float(book.get("askPrice") or 0.0)
    except (TypeError, ValueError):
        return None

    mid = (bid + ask) / 2.0

    if bid <= 0 or ask <= 0 or mid <= 0:
        return None

    return (ask - bid) / mid * 10000.0


def quote_buy_impact_bps(asks, quote_amount):
    remaining = float(quote_amount)
    base_bought = 0.0
    quote_spent = 0.0

    if not asks:
        return None

    best_ask = float(asks[0][0])

    for price_text, qty_text in asks:
        price = float(price_text)
        quantity = float(qty_text)
        available_quote = price * quantity
        spent = min(remaining, available_quote)
        base_bought += spent / price
        quote_spent += spent
        remaining -= spent

        if remaining <= 1e-9:
            break

    if remaining > 1e-6 or base_bought <= 0:
        return None

    average_price = quote_spent / base_bought
    return (average_price / best_ask - 1.0) * 10000.0


def quote_sell_impact_bps(bids, quote_amount):
    if not bids:
        return None

    best_bid = float(bids[0][0])
    base_to_sell = float(quote_amount) / best_bid
    remaining_base = base_to_sell
    quote_received = 0.0

    for price_text, qty_text in bids:
        price = float(price_text)
        quantity = float(qty_text)
        sold = min(remaining_base, quantity)
        quote_received += sold * price
        remaining_base -= sold

        if remaining_base <= 1e-12:
            break

    if remaining_base > 1e-9 or base_to_sell <= 0:
        return None

    average_price = quote_received / base_to_sell
    return (1.0 - average_price / best_bid) * 10000.0


def visible_capacity_usd(levels):
    total = 0.0
    for price_text, qty_text in levels or []:
        total += float(price_text) * float(qty_text)
    return total


def measure_liquidity(symbol, book_ticker):
    depth = fetch_depth(symbol)
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []

    best_bid = float(bids[0][0]) if bids else None
    best_ask = float(asks[0][0]) if asks else None

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": book_spread_bps(book_ticker),
        "buy_impact_1k_bps": quote_buy_impact_bps(
            asks,
            1000.0,
        ),
        "sell_impact_1k_bps": quote_sell_impact_bps(
            bids,
            1000.0,
        ),
        "buy_impact_5k_bps": quote_buy_impact_bps(
            asks,
            5000.0,
        ),
        "sell_impact_5k_bps": quote_sell_impact_bps(
            bids,
            5000.0,
        ),
        "visible_bid_capacity_usd": visible_capacity_usd(bids),
        "visible_ask_capacity_usd": visible_capacity_usd(asks),
        "depth_raw": {
            "lastUpdateId": depth.get("lastUpdateId"),
            "bids": bids,
            "asks": asks,
        },
    }


def fetch_futures_24h():
    data = futures_api_get(
        "/fapi/v1/ticker/24hr"
    )

    if isinstance(
        data,
        list,
    ):
        return data

    return []


def fetch_klines(symbol, end_time_ms=None, required_bars=None):
    required = required_bars or (BASELINE_BARS + BARS_3H + 5)
    cursor = end_time_ms
    collected = []

    while len(collected) < required:
        params = {
            "symbol": symbol,
            "interval": KLINE_INTERVAL,
            "limit": min(KLINE_LIMIT, required - len(collected)),
        }
        if cursor is not None:
            params["endTime"] = int(cursor)

        batch = spot_api_get("/api/v3/klines", params)
        if not batch:
            break
        collected = list(batch) + collected
        oldest_open_ms = int(batch[0][0])
        next_cursor = oldest_open_ms - 1
        if cursor is not None and next_cursor >= cursor:
            break
        cursor = next_cursor
        if len(batch) < params["limit"]:
            break

    unique = {int(row[0]): row for row in collected}
    return [unique[key] for key in sorted(unique)][-required:]


def validate_kline_rows(rows, scan_time_ms):
    if not rows:
        raise ValueError("Mum verisi bos")
    opens = [int(row[0]) for row in rows]
    if len(opens) != len(set(opens)):
        raise ValueError("Tekrarlanan mum zamani")
    expected_ms = 5 * 60 * 1000
    if any(b - a != expected_ms for a, b in zip(opens, opens[1:])):
        raise ValueError("Mum zaman serisinde bosluk var")
    for row in rows:
        open_price, high, low, close = map(float, row[1:5])
        if min(open_price, high, low, close) <= 0:
            raise ValueError("Gecersiz sifir/negatif fiyat")
        if high < max(open_price, close) or low > min(open_price, close):
            raise ValueError("Gecersiz OHLC sirasi")
        if float(row[7]) < 0 or float(row[8]) < 0:
            raise ValueError("Negatif hacim/islem sayisi")
    staleness_minutes = (scan_time_ms - int(rows[-1][6])) / 60000.0
    if staleness_minutes > MAX_KLINE_STALENESS_MINUTES:
        raise ValueError(f"Bayat mum verisi: {staleness_minutes:.1f} dakika")
    return staleness_minutes


def fetch_funding(
    symbol,
):
    data = futures_api_get(
        "/fapi/v1/premiumIndex",
        {
            "symbol":
                symbol,
        },
    )

    if not isinstance(
        data,
        dict,
    ):
        return None

    try:
        return float(
            data.get(
                "lastFundingRate",
                0.0,
            )
            or 0.0
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def fetch_open_interest_history(
    symbol,
):
    data = futures_api_get(
        "/futures/data/openInterestHist",
        {
            "symbol":
                symbol,

            "period":
                "5m",

            "limit":
                13,
        },
    )

    if isinstance(
        data,
        list,
    ):
        return data

    return []


def fetch_taker_ratio(
    symbol,
):
    data = futures_api_get(
        "/futures/data/takerlongshortRatio",
        {
            "symbol":
                symbol,

            "period":
                "5m",

            "limit":
                12,
        },
    )

    if isinstance(
        data,
        list,
    ):
        return data

    return []


def build_universe(scan_time):
    global FUTURES_AVAILABLE

    exchange_info = (
        fetch_spot_exchange_info()
    )

    spot_list = (
        fetch_spot_24h()
    )

    book_tickers = (
        fetch_book_tickers()
    )

    futures_list = (
        fetch_futures_24h()
    )

    spot_24h = {
        item[
            "symbol"
        ]: item
        for item
        in spot_list
        if "symbol"
        in item
    }

    futures_24h = {
        item[
            "symbol"
        ]: item
        for item
        in futures_list
        if "symbol"
        in item
    }

    futures_mode = (
        bool(
            futures_24h
        )
        and
        FUTURES_AVAILABLE
    )

    if not futures_mode:
        FUTURES_AVAILABLE = False

    symbols = []
    scan_time_ms = int(datetime.fromisoformat(scan_time).timestamp() * 1000)

    for item in exchange_info.get(
        "symbols",
        [],
    ):
        symbol = item.get(
            "symbol"
        )

        base = item.get(
            "baseAsset"
        )

        quote = item.get(
            "quoteAsset"
        )

        if not symbol:
            continue

        if not base:
            continue

        if quote != QUOTE_ASSET:
            continue

        save_asset_metadata(symbol, base, quote, item.get("status", "UNKNOWN"))

        if item.get(
            "status"
        ) != "TRADING":
            save_universe_member(scan_time, symbol, "EXCLUDED", "NOT_TRADING",
                                 CONFIG_VERSION)
            continue

        if is_excluded_base(base):
            save_universe_member(scan_time, symbol, "EXCLUDED", "ASSET_CLASS",
                                 CONFIG_VERSION)
            continue

        spot_row = (
            spot_24h.get(
                symbol
            )
        )

        if not spot_row:
            save_universe_member(scan_time, symbol, "EXCLUDED", "NO_24H_DATA",
                                 CONFIG_VERSION)
            continue

        spot_volume = float(
            spot_row.get(
                "quoteVolume",
                0.0,
            )
            or 0.0
        )

        if (
            spot_volume
            < MIN_SPOT_VOLUME_24H
        ):
            save_universe_member(scan_time, symbol, "EXCLUDED", "LOW_VOLUME",
                                 CONFIG_VERSION)
            continue

        spread_bps = book_spread_bps(
            book_tickers.get(symbol)
        )

        if (
            spread_bps is None
            or spread_bps > MAX_SPREAD_BPS
        ):
            save_universe_member(scan_time, symbol, "EXCLUDED", "WIDE_SPREAD",
                                 CONFIG_VERSION)
            continue

        metadata = get_asset_metadata(symbol) or {}
        listing_time_ms = metadata.get("listing_time_ms")
        if listing_time_ms is None:
            try:
                listing_time_ms = fetch_listing_time_ms(symbol)
                if listing_time_ms is None:
                    raise ValueError("No first trading candle")
                save_asset_metadata(symbol, base, quote, item.get("status", "UNKNOWN"),
                                    listing_time_ms)
            except Exception:
                save_universe_member(scan_time, symbol, "EXCLUDED",
                                     "AGE_UNKNOWN", CONFIG_VERSION)
                continue
        age_days = (scan_time_ms - int(listing_time_ms)) / 86400000.0
        if age_days < MIN_COIN_AGE_DAYS:
            save_universe_member(scan_time, symbol, "EXCLUDED", "TOO_NEW",
                                 CONFIG_VERSION)
            continue

        if futures_mode:
            futures_row = (
                futures_24h.get(
                    symbol
                )
            )

            if not futures_row:
                save_universe_member(scan_time, symbol, "EXCLUDED",
                                     "NO_FUTURES_PAIR", CONFIG_VERSION)
                continue

            futures_volume = float(
                futures_row.get(
                    "quoteVolume",
                    0.0,
                )
                or 0.0
            )

            if (
                futures_volume
                < MIN_FUTURES_VOLUME_24H
            ):
                save_universe_member(scan_time, symbol, "EXCLUDED",
                                     "LOW_FUTURES_VOLUME", CONFIG_VERSION)
                continue

        symbols.append(
            symbol
        )
        save_universe_member(scan_time, symbol, "INCLUDED", None, CONFIG_VERSION)

    return (
        sorted(
            symbols
        ),
        spot_24h,
        futures_24h,
        book_tickers,
        futures_mode,
    )


def parse_klines(
    rows,
):
    opens = []
    highs = []
    lows = []
    closes = []
    quote_volumes = []
    trades = []
    taker_buy_quote = []
    open_times = []
    close_times = []

    for row in rows:
        open_times.append(
            int(
                row[0]
            )
        )

        opens.append(
            float(
                row[1]
            )
        )

        highs.append(
            float(
                row[2]
            )
        )

        lows.append(
            float(
                row[3]
            )
        )

        closes.append(
            float(
                row[4]
            )
        )

        quote_volumes.append(
            float(
                row[7]
            )
        )

        trades.append(
            float(
                row[8]
            )
        )

        taker_buy_quote.append(
            float(
                row[10]
            )
        )

        close_times.append(
            int(
                row[6]
            )
        )

    returns = []

    for index in range(
        1,
        len(
            closes
        ),
    ):
        returns.append(
            pct_change(
                closes[
                    index - 1
                ],
                closes[
                    index
                ],
            )
        )

    return {
        "open_times":
            open_times,

        "close_times":
            close_times,

        "opens":
            opens,

        "highs":
            highs,

        "lows":
            lows,

        "closes":
            closes,

        "quote_volumes":
            quote_volumes,

        "trades":
            trades,

        "taker_buy_quote":
            taker_buy_quote,

        "returns":
            returns,
    }


def calculate_features(
    symbol,
    spot_data,
    futures_data,
    book_ticker,
    btc_change_24h,
    data_mode,
    scan_time,
):
    scan_time_ms = int(
        datetime.fromisoformat(
            scan_time
        ).timestamp()
        * 1000
    )

    interval_ms = 5 * 60 * 1000

    # Binance, scan aninda halen acik olan 5 dakikalik mumu da
    # dondurebilir. Yalnizca tamamen kapanmis son mumun bitis zamanini
    # kullanmak, acik mum silindikten sonra gerekli mum sayisinin bir
    # eksik kalmasini onler.
    last_closed_end_ms = (
        scan_time_ms // interval_ms
    ) * interval_ms - 1

    rows = [
        row
        for row in fetch_klines(
            symbol,
            last_closed_end_ms,
        )
        if int(row[6]) <= last_closed_end_ms
    ]

    staleness_minutes = validate_kline_rows(rows, scan_time_ms)

    parsed = parse_klines(
        rows
    )
    metadata = get_asset_metadata(symbol) or {}
    listing_time_ms = metadata.get("listing_time_ms")
    coin_age_days = (
        (scan_time_ms - int(listing_time_ms)) / 86400000.0
        if listing_time_ms is not None else None
    )

    open_times = parsed[
        "open_times"
    ]

    close_times = parsed[
        "close_times"
    ]

    closes = parsed[
        "closes"
    ]

    highs = parsed[
        "highs"
    ]

    lows = parsed[
        "lows"
    ]

    quote_volumes = parsed[
        "quote_volumes"
    ]

    trades = parsed[
        "trades"
    ]

    taker_buy_quote = parsed[
        "taker_buy_quote"
    ]

    returns = parsed[
        "returns"
    ]

    required_bars = (
        BASELINE_BARS
        + BARS_3H
        + 5
    )

    if (
        len(
            closes
        )
        < required_bars
    ):
        raise ValueError(
            "Yetersiz mum verisi"
        )

    signal_bar_open_ms = (
        open_times[
            -1
        ]
    )

    signal_bar_close_ms = (
        close_times[
            -1
        ]
    )

    current_price = (
        closes[
            -1
        ]
    )

    change_15m = pct_change(
        closes[
            -4
        ],
        current_price,
    )

    change_1h = pct_change(
        closes[
            -13
        ],
        current_price,
    )

    change_3h = pct_change(
        closes[
            -37
        ],
        current_price,
    )

    change_24h = float(
        spot_data.get(
            "priceChangePercent",
            0.0,
        )
        or 0.0
    )

    futures_change_24h = None

    if futures_data:
        try:
            futures_change_24h = float(
                futures_data.get(
                    "priceChangePercent",
                    0.0,
                )
                or 0.0
            )

        except (
            TypeError,
            ValueError,
        ):
            futures_change_24h = None

    baseline_volume = (
        quote_volumes[
            -(
                BASELINE_BARS
                + BARS_3H
            ):
            -BARS_3H
        ]
    )

    baseline_trades = (
        trades[
            -(
                BASELINE_BARS
                + BARS_3H
            ):
            -BARS_3H
        ]
    )

    baseline_returns = [
        abs(
            value
        )
        for value
        in returns[
            -(
                BASELINE_BARS
                + BARS_3H
            ):
            -BARS_3H
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

    baseline_volume_15m = (
        grouped_sums(
            baseline_volume,
            BARS_15M,
        )
    )

    baseline_volume_1h = (
        grouped_sums(
            baseline_volume,
            BARS_1H,
        )
    )

    baseline_trade_15m = (
        grouped_sums(
            baseline_trades,
            BARS_15M,
        )
    )

    baseline_return_15m = (
        grouped_sums(
            baseline_returns,
            BARS_15M,
        )
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
        abs(
            change_15m
        ),
        baseline_return_15m,
    )

    baseline_volume_15m_median = (
        statistics.median(
            baseline_volume_15m
        )
        if baseline_volume_15m
        else 1.0
    )

    baseline_volume_1h_median = (
        statistics.median(
            baseline_volume_1h
        )
        if baseline_volume_1h
        else 1.0
    )

    volume_mult_15m = (
        volume_15m
        / (
            baseline_volume_15m_median
            or 1.0
        )
    )

    volume_mult_1h = (
        volume_1h
        / (
            baseline_volume_1h_median
            or 1.0
        )
    )

    taker_buy_15m = recent_sum(
        taker_buy_quote,
        BARS_15M,
    )

    taker_buy_ratio_15m = (
        taker_buy_15m
        / volume_15m
        if volume_15m > 0
        else 0.0
    )

    impulse = impulse_retention(parsed, baseline_volume_15m_median)
    retention_proxy = impulse["retention"]

    oi_change_1h = None
    funding_rate = None
    futures_taker_ratio = None

    if FUTURES_AVAILABLE:
        oi_history = (
            fetch_open_interest_history(
                symbol
            )
        )

        if len(
            oi_history
        ) >= 2:
            try:
                first_oi = float(
                    oi_history[
                        0
                    ].get(
                        "sumOpenInterestValue",
                        0.0,
                    )
                    or 0.0
                )

                last_oi = float(
                    oi_history[
                        -1
                    ].get(
                        "sumOpenInterestValue",
                        0.0,
                    )
                    or 0.0
                )

                oi_change_1h = (
                    pct_change(
                        first_oi,
                        last_oi,
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
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

            except (
                TypeError,
                ValueError,
            ):
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
            quote_volumes[
                -6:
                -3
            ]
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
            abs(
                funding_rate
            )
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
        oi_change_1h
        is not None
    ):
        if (
            oi_change_1h > 2.0
            and
            futures_taker_ratio
            is not None
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

    if taker_buy_ratio_15m >= 0.75 and volume_z >= 3.0:
        engine = "WHALE_ASSISTED_PROXY"

    manipulation_risk = (
        (trade_z >= 6.0 and return_z < 0.5)
        or (taker_buy_ratio_15m >= 0.95 and abs(change_15m) < 0.25)
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
            or
            persistence
            or
            reignition
        )
    ):
        stage = "TRIGGER"

    elif reignition:
        stage = "REIGNITION"

    elif (
        persistence
        and
        retention
    ):
        stage = "CONTINUATION"

    elif wakeup:
        stage = "WAKE_UP"

    else:
        stage = "OBSERVE"

    feature = {
        "ts_utc":
            scan_time,

        "config_version":
            CONFIG_VERSION,

        "data_mode":
            data_mode,

        "symbol":
            symbol,

        "coin_age_days": coin_age_days,

        "recent_closed_klines":
            rows[-13:],

        "recent_returns_1h": [
            pct_change(closes[i - 1], closes[i])
            for i in range(len(closes) - 12, len(closes))
        ],

        "recent_returns_1h": [
            pct_change(closes[i - 1], closes[i])
            for i in range(len(closes) - 12, len(closes))
        ],

        "raw_klines": rows,

        "stage":
            stage,

        "engine":
            engine,

        "score":
            score,

        "price":
            current_price,

        "signal_bar_open_ms":
            signal_bar_open_ms,

        "signal_bar_close_ms":
            signal_bar_close_ms,

        "change_15m":
            change_15m,

        "change_1h":
            change_1h,

        "change_3h":
            change_3h,

        "change_24h":
            change_24h,

        "quote_volume_24h":
            float(
                spot_data.get(
                    "quoteVolume",
                    0.0,
                )
                or 0.0
            ),

        "spread_bps":
            book_spread_bps(
                book_ticker
            ),

        "data_staleness_minutes": staleness_minutes,

        "impulse_start_ms": impulse["impulse_start_ms"],
        "impulse_low": impulse["impulse_low"],
        "impulse_high": impulse["impulse_high"],
        "impulse_return_pct": impulse["impulse_return_pct"],
        "impulse_volume_multiple": impulse["impulse_volume_multiple"],

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

        "manipulation_risk": manipulation_risk,
        "catalyst_status": "UNAVAILABLE_NO_NEWS_FEED",

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

        "reignition_ratio": reignition_ratio,

        "wake_components":
            wake_components,

        "trigger_components":
            trigger_components,
    }

    feature["near_miss_distance"] = qualification_distance(feature)
    return feature


def ranking_key(
    feature,
):
    return (
        int(
            feature[
                "trigger"
            ]
        ),
        int(
            feature[
                "wakeup"
            ]
        ),
        int(
            feature[
                "persistence"
            ]
        ),
        int(
            feature[
                "reignition"
            ]
        ),
        int(
            feature[
                "retention"
            ]
        ),
        feature[
            "score"
        ],
        feature[
            "volume_z_15m"
        ],
        feature.get(
            "cross_sectional_rarity_pct",
            0.0,
        ),
        feature[
            "btc_relative_24h"
        ],
    )


def btc_regime(change_24h):
    if change_24h >= BTC_UP_REGIME_PCT:
        return "UP"

    if change_24h <= BTC_DOWN_REGIME_PCT:
        return "DOWN"

    return "SIDEWAYS"


def add_percentile_rank(
    features,
    source_key,
    target_key,
):
    values = sorted(
        float(feature.get(source_key) or 0.0)
        for feature in features
    )

    total = len(values)

    if total == 0:
        return

    for feature in features:
        value = float(
            feature.get(source_key)
            or 0.0
        )
        count_at_or_below = sum(
            candidate <= value
            for candidate in values
        )
        feature[target_key] = (
            count_at_or_below
            / total
            * 100.0
        )


def enrich_cross_sectional_features(features):
    add_percentile_rank(
        features,
        "volume_z_15m",
        "volume_rarity_pct",
    )
    add_percentile_rank(
        features,
        "trade_z_15m",
        "trade_rarity_pct",
    )
    add_percentile_rank(
        features,
        "return_z_15m",
        "return_rarity_pct",
    )

    for feature in features:
        feature[
            "cross_sectional_rarity_pct"
        ] = statistics.fmean(
            (
                feature["volume_rarity_pct"],
                feature["trade_rarity_pct"],
                feature["return_rarity_pct"],
            )
        )


def liquidity_is_valid(liquidity):
    required = (
        liquidity.get("spread_bps"),
        liquidity.get("buy_impact_1k_bps"),
        liquidity.get("buy_impact_5k_bps"),
    )

    if any(value is None for value in required):
        return False

    return (
        liquidity["spread_bps"] <= MAX_SPREAD_BPS
        and liquidity["buy_impact_1k_bps"]
        <= MAX_BUY_IMPACT_1K_BPS
        and liquidity["buy_impact_5k_bps"]
        <= MAX_BUY_IMPACT_5K_BPS
    )


def select_tradable_signal_groups(
    features,
    book_tickers,
):
    signal_pool = sorted(
        [
            feature for feature in features
            if feature["stage"] != "OBSERVE"
            and not feature["climax_risk"]
            and not feature.get("manipulation_risk", False)
        ],
        key=ranking_key,
        reverse=True,
    )
    qualified = []

    for feature in signal_pool:
        try:
            liquidity = measure_liquidity(
                feature["symbol"],
                book_tickers.get(
                    feature["symbol"]
                ),
            )
        except Exception as error:
            feature["liquidity_error"] = str(error)
            continue

        feature["liquidity"] = liquidity

        try:
            history = recent_history_gain(feature["symbol"], feature["price"],
                                          feature["ts_utc"])
            feature.update(history)
        except Exception as error:
            feature["history_exclusion_reason"] = "HISTORY_UNVERIFIED"
            save_data_issue("HISTORY_UNVERIFIED",
                            f"{feature['symbol']}: {error}", feature["ts_utc"])
            save_feature(feature, is_signal=False,
                         selection_class="HISTORY_UNVERIFIED")
            continue
        if history["history_gain_90d_pct"] >= MAX_GAIN_FROM_90D_FLOOR_PCT:
            feature["history_exclusion_reason"] = "ALREADY_UP_50_PCT_90D"
            save_feature(feature, is_signal=False,
                         selection_class="ALREADY_RISEN")
            continue

        if (
            float(liquidity.get("buy_impact_1k_bps") or 0.0) >= 20.0
            and float(feature.get("change_15m") or 0.0) > 0
        ):
            feature["engine"] = "LIQUIDITY_VACUUM"

        if liquidity_is_valid(liquidity):
            qualified.append(feature)

        if len(qualified) >= MAX_SELECTED:
            break

    selected = qualified[:MAX_SELECTED]
    selected_symbols = {feature["symbol"] for feature in selected}
    near_pool = sorted(
        [
            feature for feature in features
            if feature["symbol"] not in selected_symbols
            and not feature.get("history_exclusion_reason")
            and not feature["climax_risk"]
            and not feature.get("manipulation_risk", False)
        ],
        key=lambda feature: (
            float(feature.get("near_miss_distance") or 0.0),
            tuple(-float(value) if isinstance(value, (int, float)) else value
                  for value in ranking_key(feature)),
        ),
    )
    near_miss = []
    for feature in near_pool:
        try:
            liquidity = feature.get("liquidity") or measure_liquidity(
                feature["symbol"], book_tickers.get(feature["symbol"]),
            )
        except Exception:
            continue
        feature["liquidity"] = liquidity
        if liquidity_is_valid(liquidity):
            near_miss.append(feature)
        if len(near_miss) >= MAX_NEAR_MISS:
            break

    return selected, near_miss


def deterministic_control_key(
    scan_time,
    symbol,
):
    return hashlib.sha256(
        f"{scan_time}|{symbol}".encode(
            "utf-8"
        )
    ).hexdigest()


def select_matched_random_controls(
    features,
    selected,
    near_miss,
    scan_time,
    book_tickers,
):
    excluded_symbols = {
        feature["symbol"]
        for feature in selected + near_miss
    }

    pool = [
        feature
        for feature in features
        if feature["symbol"]
        not in excluded_symbols
        and feature["stage"] == "OBSERVE"
        and not feature["climax_risk"]
        and not feature.get("manipulation_risk", False)
    ]

    controls = []

    for candidate in selected:
        if not pool:
            break

        target_volume = max(
            float(
                candidate.get(
                    "quote_volume_24h",
                    0.0,
                )
                or 0.0
            ),
            1.0,
        )

        ranked_pool = sorted(
            pool,
            key=lambda feature: (
                abs(
                    math.log10(
                        max(
                            float(
                                feature.get(
                                    "quote_volume_24h",
                                    0.0,
                                )
                                or 0.0
                            ),
                            1.0,
                        )
                    )
                    - math.log10(
                        target_volume
                    )
                ),
                abs(
                    float(feature.get("coin_age_days") or 0.0)
                    - float(candidate.get("coin_age_days") or 0.0)
                ),
                abs(
                    float(feature.get("spread_bps") or 0.0)
                    - float(candidate.get("spread_bps") or 0.0)
                ),
                deterministic_control_key(
                    scan_time,
                    feature["symbol"],
                ),
            ),
        )

        chosen = None

        for option in ranked_pool:
            try:
                liquidity = measure_liquidity(
                    option["symbol"],
                    book_tickers.get(
                        option["symbol"]
                    ),
                )
            except Exception:
                pool.remove(option)
                continue

            option["liquidity"] = liquidity
            pool.remove(option)

            if liquidity_is_valid(liquidity):
                chosen = option
                break

        if chosen is not None:
            controls.append(chosen)

        if len(controls) >= MAX_RANDOM_CONTROL:
            break

    return controls


def return_correlation(first, second):
    """Pearson correlation of the same 12 closed five-minute returns."""
    if len(first) != 12 or len(second) != 12:
        return None
    mean_a, mean_b = statistics.fmean(first), statistics.fmean(second)
    numerator = sum((a - mean_a) * (b - mean_b) for a, b in zip(first, second))
    variance_a = sum((a - mean_a) ** 2 for a in first)
    variance_b = sum((b - mean_b) ** 2 for b in second)
    return numerator / math.sqrt(variance_a * variance_b) if variance_a * variance_b else None


def run_scan():
    init_db()

    scan_time = utc_now()
    local_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    clock_skew_seconds = None
    try:
        clock_skew_seconds = abs(fetch_server_time_ms() - local_time_ms) / 1000.0
    except Exception as error:
        save_data_issue("CLOCK_SYNC_CHECK_FAILED", str(error), scan_time)

    print(
        "=" * 80
    )

    print(
        "BINANCE AVCI 2 V2.1 FINAL"
    )

    print(
        f"UTC: {scan_time}"
    )

    print(
        f"Yapilandirma: "
        f"{CONFIG_VERSION}"
    )

    print(
        "=" * 80
    )

    try:
        (
            universe,
            spot_24h,
            futures_24h,
            book_tickers,
            futures_mode,
        ) = build_universe(scan_time)

    except Exception as error:
        save_data_issue(
            "UNIVERSE_FETCH_FAILED",
            str(
                error
            ),
            scan_time,
        )

        raise

    data_mode = (
        DATA_MODE_FULL
        if futures_mode
        else DATA_MODE_SPOT
    )

    if futures_mode:
        print(
            "Veri modu: "
            "SPOT + FUTURES"
        )

    else:
        print(
            "Veri modu: "
            "SADECE SPOT"
        )

        print(
            "OI ve fonlama verisi "
            "bu taramada kullanilamiyor."
        )

        save_data_issue(
            "FUTURES_DATA_UNAVAILABLE",
            (
                "Tarama SPOT_ONLY modunda; "
                "OI ve funding alanlari bos."
            ),
            scan_time,
        )

    btc_row = spot_24h.get("BTCUSDT")
    btc_available = bool(
        btc_row
        and btc_row.get("priceChangePercent") is not None
    )
    btc_change_24h = float(
        btc_row.get("priceChangePercent", 0.0) or 0.0
    ) if btc_available else 0.0
    regime = btc_regime(btc_change_24h)

    # Observation only: a sharp market-wide drop does not alter frozen signals.
    btc_flash_15m_pct = None
    try:
        btc_rows = [row for row in fetch_klines("BTCUSDT", required_bars=4)
                    if int(row[6]) <= (scan_time_ms := int(datetime.fromisoformat(scan_time).timestamp() * 1000))]
        if len(btc_rows) >= 4:
            btc_flash_15m_pct = pct_change(float(btc_rows[-4][4]), float(btc_rows[-1][4]))
    except Exception as error:
        save_data_issue("BTC_FLASH_OBSERVATION_FAILED", str(error), scan_time)
    btc_flash_crash = btc_flash_15m_pct is not None and btc_flash_15m_pct <= -3.0

    health_status = "VALID_FULL"
    if (
        len(universe) < MIN_VALID_UNIVERSE
        or not btc_available
        or clock_skew_seconds is None
        or clock_skew_seconds > MAX_CLOCK_SKEW_SECONDS
    ):
        health_status = "INVALID"
        save_data_issue(
            "SCAN_HEALTH_INVALID",
            f"universe={len(universe)}, btc_available={btc_available}, "
            f"clock_skew_seconds={clock_skew_seconds}",
            scan_time,
        )
    elif not futures_mode:
        health_status = "VALID_SPOT_OBSERVATION"

    validation_tier = (
        "PRIMARY"
        if health_status == "VALID_FULL"
        else "OBSERVATIONAL"
    )

    print(
        f"BTC 24s hareketi: "
        f"{btc_change_24h:+.2f}%"
    )

    print(
        f"Toplam taranacak coin: "
        f"{len(universe)}"
    )

    print(f"Saat farki: {clock_skew_seconds if clock_skew_seconds is not None else '-'} sn")

    print(
        "=" * 80
    )

    save_scan(
        scan_time,
        CONFIG_VERSION,
        len(
            universe
        ),
        btc_change_24h,
        data_mode,
        regime,
        health_status,
        CONFIG_HASH,
        os.environ.get("GITHUB_SHA"),
        clock_skew_seconds,
    )
    save_scan_observation(scan_time, CONFIG_VERSION, btc_flash_15m_pct, btc_flash_crash)

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
        daily_movers[
            :30
        ],
        start=1,
    ):
        symbol = item[
            0
        ]

        change = item[
            1
        ]

        volume = item[
            2
        ]

        save_daily_mover(
            scan_time[
                :10
            ],
            symbol,
            change,
            volume,
            rank,
            CONFIG_VERSION,
        )

    features = []
    signals = []

    stage_counts = {
        "WAKE_UP":
            0,

        "CONTINUATION":
            0,

        "REIGNITION":
            0,

        "TRIGGER":
            0,

        "OBSERVE":
            0,
    }

    climax_count = 0
    event_counts = {
        "CANDIDATE": 0,
        "NEAR_MISS": 0,
        "RANDOM_CONTROL": 0,
    }

    cooldown_skip_counts = {
        "CANDIDATE": 0,
        "NEAR_MISS": 0,
        "RANDOM_CONTROL": 0,
    }

    total = len(
        universe
    )
    symbol_error_count = 0

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
                    book_tickers.get(symbol),
                    btc_change_24h,
                    data_mode,
                    scan_time,
                )
            )

            features.append(
                feature
            )

            save_raw_klines(
                symbol,
                feature.pop("raw_klines", []),
                CONFIG_VERSION,
            )

            save_raw_deriv(feature)

            stage_counts[
                feature[
                    "stage"
                ]
            ] = (
                stage_counts.get(
                    feature[
                        "stage"
                    ],
                    0,
                )
                + 1
            )

            if feature[
                "climax_risk"
            ]:
                climax_count += 1

        except Exception as error:
            symbol_error_count += 1
            print(
                f"HATA | {symbol} | "
                f"{type(error).__name__}: {error}"
            )
            save_data_issue(
                "SYMBOL_SCAN_FAILED",
                (
                    f"{symbol}: "
                    f"{error}"
                ),
                scan_time,
            )

        print(
            f"[{index}/{total}] "
            f"{symbol}"
        )

        time.sleep(
            SCAN_SLEEP_SECONDS
        )

    enrich_cross_sectional_features(features)

    if total and symbol_error_count / total > 0.20:
        health_status = "INVALID"
        validation_tier = "OBSERVATIONAL"
        update_scan_health(scan_time, CONFIG_VERSION, health_status)
        save_data_issue(
            "SCAN_ERROR_RATE_INVALID",
            f"errors={symbol_error_count}, universe={total}",
            scan_time,
        )

    for feature in features:
        feature["btc_regime"] = regime
        feature["btc_flash_15m_pct"] = btc_flash_15m_pct
        feature["btc_flash_crash"] = btc_flash_crash
        feature["validation_tier"] = validation_tier
        is_signal = (
            feature["stage"] != "OBSERVE"
            and not feature["climax_risk"]
            and not feature.get("manipulation_risk", False)
        )
        save_feature(
            feature,
            is_selected=0,
            is_signal=is_signal,
            selection_class=("RAW_SIGNAL" if is_signal else "NONE"),
        )
        if is_signal:
            signals.append(feature)

    signals.sort(
        key=ranking_key,
        reverse=True,
    )

    if health_status == "INVALID":
        selected, near_miss, random_controls = [], [], []
    else:
        selected, near_miss = select_tradable_signal_groups(
            features,
            book_tickers,
        )
        random_controls = select_matched_random_controls(
            features,
            selected,
            near_miss,
            scan_time,
            book_tickers,
        )

    # Same-scan candidates share one market event until demonstrated otherwise.
    # This conservative cohort ID is for reporting, never signal selection.
    for feature in selected:
        feature["cohort_id"] = scan_time
        feature["cohort_size"] = len(selected)
        peers = [return_correlation(feature.get("recent_returns_1h", []),
                                    other.get("recent_returns_1h", []))
                 for other in selected if other is not feature]
        valid_peers = [value for value in peers if value is not None]
        feature["same_scan_corr_max"] = max(valid_peers) if valid_peers else None
        feature["same_scan_corr_high"] = any(value >= 0.8 for value in valid_peers)

    for feature in selected:
        save_feature(
            feature,
            is_selected=1,
            is_signal=True,
            selection_class="CANDIDATE",
        )

    for feature in near_miss:
        save_feature(
            feature,
            is_selected=0,
            is_signal=(feature["stage"] != "OBSERVE"),
            selection_class="NEAR_MISS",
        )

    for feature in random_controls:
        save_feature(
            feature,
            is_selected=0,
            is_signal=False,
            selection_class="RANDOM_CONTROL",
        )

    for event_class, group in (
        ("CANDIDATE", selected),
        ("NEAR_MISS", near_miss),
        ("RANDOM_CONTROL", random_controls),
    ):
        for feature in group:
            save_orderbook_snapshot(feature, event_class)
            liquidity = feature.get("liquidity") or {}
            observed_slippage = max(
                float(liquidity.get("buy_impact_1k_bps") or 0.0),
                float(liquidity.get("sell_impact_1k_bps") or 0.0),
            )
            event_id = create_signal_event(
                feature,
                cooldown_hours=COOLDOWN_HOURS,
                fee_bps_per_side=(
                    FEE_BPS_PER_SIDE
                ),
                slippage_bps_per_side=(
                    max(SLIPPAGE_BPS_PER_SIDE, observed_slippage)
                ),
                event_class=event_class,
                entry_delay_seconds=(
                    ENTRY_DELAY_SECONDS
                ),
            )

            if event_id is None:
                cooldown_skip_counts[
                    event_class
                ] += 1

            else:
                event_counts[
                    event_class
                ] += 1

    print(
        "=" * 80
    )

    print(
        "TARAMA OZETI"
    )

    print(f"Saglik: {health_status} | Dogrulama: {validation_tier}")
    print(f"BTC rejimi: {regime}")

    print(
        f"Tarama evreni: "
        f"{len(universe)}"
    )

    print(
        f"Kaydedilen tum coinler: "
        f"{len(features)}"
    )

    print(
        f"Sinyal veren coin: "
        f"{len(signals)}"
    )

    print(
        f"Secilen aday: "
        f"{len(selected)}"
    )

    print(
        "Yeni event "
        f"| aday: {event_counts['CANDIDATE']} "
        f"| near-miss: {event_counts['NEAR_MISS']} "
        "| random: "
        f"{event_counts['RANDOM_CONTROL']}"
    )

    print(
        "Cooldown nedeniyle atlanan "
        f"| aday: {cooldown_skip_counts['CANDIDATE']} "
        "| near-miss: "
        f"{cooldown_skip_counts['NEAR_MISS']} "
        "| random: "
        f"{cooldown_skip_counts['RANDOM_CONTROL']}"
    )

    print(
        f"Climax nedeniyle elenen: "
        f"{climax_count}"
    )

    print(
        "-" * 80
    )

    print(
        f"Uyanis: "
        f"{stage_counts['WAKE_UP']}"
    )

    print(
        f"Devam: "
        f"{stage_counts['CONTINUATION']}"
    )

    print(
        f"Yeniden canlanma: "
        f"{stage_counts['REIGNITION']}"
    )

    print(
        f"Tetik: "
        f"{stage_counts['TRIGGER']}"
    )

    print(
        f"Gozlem: "
        f"{stage_counts['OBSERVE']}"
    )

    print(
        "=" * 80
    )

    if health_status == "INVALID":
        print(
            "TARAMA GECERSIZ - ADAY KARARI URETILMEDI"
        )

        print(
            "Bu tarama 0 aday olarak degil, DATA_FAILURE olarak kaydedilmelidir."
        )

        print(
            "=" * 80
        )

        return

    if not selected:
        print(
            "TEMIZ ADAY YOK"
        )

        print(
            "0 aday da dogru bir sonuctur."
        )

        print(
            "=" * 80
        )

        return

    stage_names = {
        "TRIGGER":
            "TETIK",

        "REIGNITION":
            "YENIDEN CANLANMA",

        "CONTINUATION":
            "DEVAM",

        "WAKE_UP":
            "UYANIS",

        "OBSERVE":
            "GOZLEM",
    }

    engine_names = {
        "SPOT_LED_DEMAND":
            "SPOT TALEBI",

        "LEVERAGED_BREAKOUT":
            "KALDIRACLI KIRILIM",

        "SHORT_SQUEEZE":
            "SHORT SIKISMASI",

        "MIXED":
            "KARMA",
    }

    print(
        "TEMIZ AVCI ADAYLARI"
    )

    print(
        "=" * 80
    )

    for rank, feature in enumerate(
        selected,
        start=1,
    ):
        stage_tr = stage_names.get(
            feature[
                "stage"
            ],
            feature[
                "stage"
            ],
        )

        engine_tr = engine_names.get(
            feature[
                "engine"
            ],
            feature[
                "engine"
            ],
        )

        retention_percent = (
            feature[
                "retention_proxy"
            ]
            * 100.0
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

        if oi_value is None:
            oi_text = (
                "VERI YOK"
            )

        else:
            oi_text = (
                f"{oi_value:+.2f}%"
            )

        if funding_value is None:
            funding_text = (
                "VERI YOK"
            )

        else:
            funding_text = (
                f"{funding_value:.6f}"
            )

        print(
            f"{rank}. "
            f"{feature['symbol']} "
            f"| {stage_tr} "
            f"| Puan: "
            f"{feature['score']}/8"
        )

        print(
            f"   Veri modu: "
            f"{feature['data_mode']}"
        )

        print(
            f"   Fiyat -> "
            f"24s: "
            f"{feature['change_24h']:+.2f}% "
            f"| 1s: "
            f"{feature['change_1h']:+.2f}% "
            f"| 15dk: "
            f"{feature['change_15m']:+.2f}%"
        )

        print(
            f"   Hacim anomalisi: "
            f"{feature['volume_z_15m']:.2f} "
            f"| 1s hacim gucu: "
            f"{feature['volume_mult_1h']:.2f}x"
        )

        print(
            f"   Hareketi koruma: "
            f"%{retention_percent:.0f} "
            f"| BTC'ye gore guc: "
            f"{feature['btc_relative_24h']:+.2f}%"
        )

        print(
            f"   Uyanis: "
            f"{'EVET' if feature['wakeup'] else 'HAYIR'} "
            f"| Ilgi devam ediyor: "
            f"{'EVET' if feature['persistence'] else 'HAYIR'}"
        )

        print(
            f"   Yeniden canlanma: "
            f"{'EVET' if feature['reignition'] else 'HAYIR'} "
            f"| Tetik: "
            f"{'EVET' if feature['trigger'] else 'HAYIR'}"
        )

        print(
            f"   Acik pozisyon 1s: "
            f"{oi_text} "
            f"| Fonlama: "
            f"{funding_text}"
        )

        print(
            f"   Hareket tipi: "
            f"{engine_tr}"
        )

        print(
            "-" * 80
        )

    print(
        "=" * 80
    )

    print(
        "AVCI 2 BINANCE TARAMASI TAMAMLANDI"
    )

    print(
        "=" * 80
    )


if __name__ == "__main__":
    run_scan()
