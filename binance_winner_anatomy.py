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

RESEARCH_VERSION = "binance-winner-anatomy-v2.0"
SPOT_BASES = ("https://data-api.binance.vision", "https://api.binance.com")
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
WINNER_LEVELS = (15.0, 20.0, 30.0, 40.0, 50.0, 60.0)
PRE_EVENT_OFFSETS_HOURS = (72, 48, 24, 12, 6, 3, 1)
BASELINE_5M_BARS = 7 * 24 * 12
PRE_EVENT_HOURS = 72
# Seven-day baseline plus the 72-hour rewind and the three-hour exclusion
# gap used by feature_snapshot (with one extra hour of safety).
DETAIL_HISTORY_BEFORE_HOURS = 7 * 24 + PRE_EVENT_HOURS + 4
ANOMALY_VOLUME_Z = 2.5
ANOMALY_TRADE_Z = 2.0
ANOMALY_RETURN_Z = 2.0

EXCLUDED_BASES = {
    "BTC", "USDC", "FDUSD", "USDP", "TUSD", "DAI", "USDE", "USD1",
    "USDS", "XUSD", "BFUSD", "PYUSD", "AEUR", "EURI", "RLUSD", "USTC",
    "LUSD", "FRAX", "SUSD", "GUSD", "USDJ", "EUR", "TRY", "WBTC",
    "WBETH", "WETH", "BTCB", "BTCUP", "BTCDOWN", "ETHUP", "ETHDOWN",
    "BNBUP", "BNBDOWN", "ADAUP", "ADADOWN", "XRPUP", "XRPDOWN",
    "DOTUP", "DOTDOWN", "LINKUP", "LINKDOWN", "TRXUP", "TRXDOWN",
}

session = requests.Session()
session.headers.update({"User-Agent": RESEARCH_VERSION})


def utc_now():
    return datetime.now(timezone.utc)


def iso_from_ms(value):
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat() if value is not None else None


def pct_change(start, end):
    return (float(end) / float(start) - 1.0) * 100.0 if start not in (None, 0) and end is not None else 0.0


def mean(values):
    return statistics.fmean(values) if values else 0.0


def pstdev(values):
    return statistics.pstdev(values) if len(values) >= 2 else 0.0


def zscore(value, baseline):
    std = pstdev(baseline)
    return (value - mean(baseline)) / std if std > 1e-12 else 0.0


def api_get(path, params=None):
    last_error = None
    for attempt in range(4):
        for base in SPOT_BASES:
            try:
                response = session.get(base + path, params=params, timeout=REQUEST_TIMEOUT)
                if response.status_code in (403, 418, 429, 451):
                    last_error = requests.HTTPError(
                        f"{response.status_code} from {base}{path}", response=response
                    )
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as error:
                last_error = error
        if attempt < 3:
            time.sleep(1.5 * (attempt + 1))
    raise last_error or RuntimeError("All Binance spot endpoints failed")


def last_closed_end_ms(interval_ms, now_ms=None):
    now_ms = now_ms or int(utc_now().timestamp() * 1000)
    return (now_ms // interval_ms) * interval_ms - 1


def fetch_klines_range(symbol, interval, start_ms, end_ms):
    cursor, collected = int(end_ms), {}
    while cursor >= start_ms:
        batch = api_get("/api/v3/klines", {
            "symbol": symbol, "interval": interval, "endTime": cursor, "limit": 1000,
        })
        if not batch:
            break
        for row in batch:
            open_ms, close_ms = int(row[0]), int(row[6])
            if open_ms >= start_ms and close_ms <= end_ms:
                collected[open_ms] = row
        oldest = int(batch[0][0])
        if oldest <= start_ms or oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(REQUEST_SLEEP_SECONDS)
    return [collected[key] for key in sorted(collected)]


def parse_rows(rows):
    result, previous_close = [], None
    for row in rows:
        close = float(row[4])
        result.append({
            "open_time": int(row[0]), "close_time": int(row[6]),
            "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": close,
            "quote_volume": float(row[7]), "trades": float(row[8]),
            "taker_buy_quote": float(row[10]),
            "abs_return": abs(pct_change(previous_close, close)) if previous_close else 0.0,
        })
        previous_close = close
    return result


def build_universe():
    info = api_get("/api/v3/exchangeInfo")
    ticker = api_get("/api/v3/ticker/24hr")
    ticker_map = {row.get("symbol"): row for row in ticker if row.get("symbol")}
    symbols = []
    for item in info.get("symbols", []):
        symbol, base = item.get("symbol"), item.get("baseAsset")
        if not symbol or not base or item.get("quoteAsset") != QUOTE_ASSET:
            continue
        if item.get("status") != "TRADING" or base in EXCLUDED_BASES:
            continue
        if float((ticker_map.get(symbol) or {}).get("quoteVolume") or 0.0) < MIN_QUOTE_VOLUME_24H:
            continue
        symbols.append(symbol)
    return sorted(symbols)


def pre_hourly_metrics(rows, index):
    prior = rows[max(0, index - 24):index]
    returns = [pct_change(a["close"], b["close"]) for a, b in zip(prior, prior[1:])]
    return {
        "volume_24h": sum(row["quote_volume"] for row in prior),
        "volatility_24h": pstdev(returns),
    }


def event_identifier(symbol, start_ms, subject_class="WINNER", matched=None):
    raw = f"{RESEARCH_VERSION}|{subject_class}|{symbol}|{start_ms}|{matched or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def detect_winner_events(symbol, rows, now_ms):
    """Detect trough-to-later-high moves; a high before its trough never counts."""
    events, seen_starts = [], set()
    index, total = EVENT_TROUGH_LOOKBACK_HOURS, len(rows)
    minimum_start_ms = now_ms - EVENT_LOOKBACK_DAYS * 86400000
    while index < total:
        trough_start = max(0, index - EVENT_TROUGH_LOOKBACK_HOURS)
        trough_index = min(range(trough_start, index + 1), key=lambda i: rows[i]["low"])
        trough_price, threshold_index, cursor = rows[trough_index]["low"], None, trough_index
        search_end = min(total, trough_index + EVENT_HORIZON_HOURS + 1)
        while cursor < search_end:
            if rows[cursor]["low"] < trough_price:
                trough_index, trough_price = cursor, rows[cursor]["low"]
                search_end = min(total, trough_index + EVENT_HORIZON_HOURS + 1)
            if pct_change(trough_price, rows[cursor]["high"]) >= WINNER_LEVELS[0]:
                threshold_index = cursor
                break
            cursor += 1
        if threshold_index is None:
            index += 1
            continue
        if rows[trough_index]["open_time"] < minimum_start_ms:
            index = threshold_index + 1
            continue
        if rows[trough_index]["open_time"] in seen_starts:
            index += 1
            continue
        horizon_stop = trough_index + EVENT_HORIZON_HOURS + 1
        available_stop = min(total, horizon_stop)
        path = rows[trough_index:available_stop]
        peak_index = trough_index + max(range(len(path)), key=lambda i: path[i]["high"])
        peak_price = rows[peak_index]["high"]
        max_gain = pct_change(trough_price, peak_price)
        levels = {}
        for level in WINNER_LEVELS:
            hit = next((r for r in path if pct_change(trough_price, r["high"]) >= level), None)
            if hit:
                levels[str(int(level))] = iso_from_ms(hit["open_time"])
        metrics = pre_hourly_metrics(rows, trough_index)
        events.append({
            "event_id": event_identifier(symbol, rows[trough_index]["open_time"]),
            "research_version": RESEARCH_VERSION, "subject_class": "WINNER",
            "symbol": symbol, "matched_winner_event_id": None,
            "event_start_ms": rows[trough_index]["open_time"],
            "event_start_time_utc": iso_from_ms(rows[trough_index]["open_time"]),
            "threshold_time_utc": iso_from_ms(rows[threshold_index]["open_time"]),
            "peak_time_utc": iso_from_ms(rows[peak_index]["open_time"]),
            "horizon_end_time_utc": iso_from_ms(rows[min(total - 1, horizon_stop - 1)]["close_time"]),
            "event_status": "CLOSED" if horizon_stop <= total else "OPEN",
            "start_price": trough_price, "peak_price": peak_price,
            "max_gain_pct": max_gain,
            "max_drawdown_pct": min(pct_change(trough_price, r["low"]) for r in path),
            "highest_level_reached": max((x for x in WINNER_LEVELS if max_gain >= x), default=None),
            "levels_reached": levels, "pre_volume_24h": metrics["volume_24h"],
            "pre_volatility_24h": metrics["volatility_24h"],
            "analyzed_at_utc": utc_now().isoformat(),
            "raw": {"detection_interval": "1h", "horizon_hours": EVENT_HORIZON_HOURS},
        })
        seen_starts.add(rows[trough_index]["open_time"])
        index = max(
            index + 1,
            horizon_stop,
        )
    return events


def nearest_index(rows, timestamp_ms):
    found = [i for i, row in enumerate(rows) if row["open_time"] <= timestamp_ms]
    return found[-1] if found else None


def control_metrics_at(rows, timestamp_ms):
    index = nearest_index(rows, timestamp_ms)
    if index is None or index < 24 or index + EVENT_HORIZON_HOURS >= len(rows):
        return None
    start_price = rows[index]["close"]
    path = rows[index:index + EVENT_HORIZON_HOURS + 1]
    peak_i = max(range(len(path)), key=lambda i: path[i]["high"])
    gain = pct_change(start_price, path[peak_i]["high"])
    if gain >= CONTROL_MAX_GAIN_PCT:
        return None
    pre = pre_hourly_metrics(rows, index)
    return {
        "start_price": start_price, "peak_price": path[peak_i]["high"],
        "peak_time_utc": iso_from_ms(path[peak_i]["open_time"]),
        "max_gain_pct": gain,
        "max_drawdown_pct": min(pct_change(start_price, r["low"]) for r in path),
        "pre_volume_24h": pre["volume_24h"], "pre_volatility_24h": pre["volatility_24h"],
    }


def choose_matched_control(winner, hourly_by_symbol, used):
    target_volume = max(float(winner.get("pre_volume_24h") or 0.0), 1.0)
    target_volatility = float(winner.get("pre_volatility_24h") or 0.0)
    candidates = []
    for symbol, rows in hourly_by_symbol.items():
        if symbol == winner["symbol"] or (winner["event_id"], symbol) in used:
            continue
        metrics = control_metrics_at(rows, winner["event_start_ms"])
        if not metrics:
            continue
        volume = max(float(metrics["pre_volume_24h"] or 0.0), 1.0)
        distance = abs(math.log10(volume) - math.log10(target_volume)) + abs(
            float(metrics["pre_volatility_24h"] or 0.0) - target_volatility
        )
        candidates.append((distance, symbol, metrics))
    if not candidates:
        return None
    _, symbol, metrics = min(candidates, key=lambda item: (item[0], item[1]))
    used.add((winner["event_id"], symbol))
    return {
        "event_id": event_identifier(symbol, winner["event_start_ms"], "NEAR_MATCH_CONTROL", winner["event_id"]),
        "research_version": RESEARCH_VERSION, "subject_class": "NEAR_MATCH_CONTROL",
        "symbol": symbol, "matched_winner_event_id": winner["event_id"],
        "event_start_ms": winner["event_start_ms"],
        "event_start_time_utc": winner["event_start_time_utc"],
        "threshold_time_utc": None, "peak_time_utc": metrics["peak_time_utc"],
        "horizon_end_time_utc": iso_from_ms(winner["event_start_ms"] + EVENT_HORIZON_HOURS * 3600000),
        "event_status": "CLOSED", "start_price": metrics["start_price"],
        "peak_price": metrics["peak_price"], "max_gain_pct": metrics["max_gain_pct"],
        "max_drawdown_pct": metrics["max_drawdown_pct"], "highest_level_reached": None,
        "levels_reached": {}, "pre_volume_24h": metrics["pre_volume_24h"],
        "pre_volatility_24h": metrics["pre_volatility_24h"],
        "analyzed_at_utc": utc_now().isoformat(),
        "raw": {"matched_to": winner["event_id"], "matching": "volume+volatility"},
    }


def grouped_sums(values, size):
    return [sum(values[i:i + size]) for i in range(0, len(values) - size + 1, size)]


def feature_snapshot(rows, target_ms, btc_rows=None):
    index = nearest_index(rows, target_ms)
    if index is None or index < BASELINE_5M_BARS + 36:
        return None
    baseline = rows[index - 36 - BASELINE_5M_BARS:index - 36]
    current = rows[:index + 1]
    closes = [r["close"] for r in current]
    volumes = [r["quote_volume"] for r in current]
    trades = [r["trades"] for r in current]
    taker = [r["taker_buy_quote"] for r in current]
    base_vol = [r["quote_volume"] for r in baseline]
    base_trades = [r["trades"] for r in baseline]
    base_returns = [r["abs_return"] for r in baseline]
    v15, v1h, v24h = sum(volumes[-3:]), sum(volumes[-12:]), sum(volumes[-288:])
    t15 = sum(trades[-3:])
    b15v, b1hv = grouped_sums(base_vol, 3), grouped_sums(base_vol, 12)
    b15t, b15r = grouped_sums(base_trades, 3), grouped_sums(base_returns, 3)
    recent = current[-288:]
    returns24 = [pct_change(a, b) for a, b in zip(closes[-289:-1], closes[-288:])] if len(closes) >= 289 else []

    def period_return(bars):
        return pct_change(closes[-bars - 1], closes[-1]) if len(closes) > bars else None

    btc_return = None
    if btc_rows:
        bi = nearest_index(btc_rows, target_ms)
        if bi is not None and bi >= 288:
            btc_return = pct_change(btc_rows[bi - 288]["close"], btc_rows[bi]["close"])
    ret24 = period_return(288)
    return {
        "feature_time_utc": iso_from_ms(rows[index]["close_time"]), "price": closes[-1],
        "return_15m_pct": period_return(3), "return_1h_pct": period_return(12),
        "return_3h_pct": period_return(36), "return_6h_pct": period_return(72),
        "return_12h_pct": period_return(144), "return_24h_pct": ret24,
        "btc_return_24h_pct": btc_return,
        "excess_vs_btc_24h_pct": ret24 - btc_return if ret24 is not None and btc_return is not None else None,
        "quote_volume_15m": v15, "quote_volume_1h": v1h, "quote_volume_24h": v24h,
        "volume_z_15m": zscore(v15, b15v), "trade_z_15m": zscore(t15, b15t),
        "return_z_15m": zscore(abs(period_return(3) or 0.0), b15r),
        "volume_mult_15m": v15 / (statistics.median(b15v) or 1.0),
        "volume_mult_1h": v1h / (statistics.median(b1hv) or 1.0),
        "taker_buy_ratio_15m": sum(taker[-3:]) / v15 if v15 > 0 else 0.0,
        "realized_volatility_24h": pstdev(returns24),
        "range_compression_24h": pct_change(min(r["low"] for r in recent), max(r["high"] for r in recent)),
        "raw": {"source_interval": "5m", "baseline_days": 7},
    }


def first_pre_event_anomaly(rows, event_start_ms):
    start_i = nearest_index(rows, event_start_ms - PRE_EVENT_HOURS * 3600000)
    end_i = nearest_index(rows, event_start_ms - 1)
    if start_i is None or end_i is None:
        return None
    first_price = rows[start_i]["close"]
    for index in range(max(start_i, BASELINE_5M_BARS + 36), end_i + 1):
        snapshot = feature_snapshot(rows, rows[index]["close_time"])
        if not snapshot:
            continue
        components = (
            snapshot["volume_z_15m"] >= ANOMALY_VOLUME_Z,
            snapshot["trade_z_15m"] >= ANOMALY_TRADE_Z,
            snapshot["return_z_15m"] >= ANOMALY_RETURN_Z,
        )
        if sum(components) >= 2:
            return {
                "time_utc": snapshot["feature_time_utc"], "price": snapshot["price"],
                "gain_before_pct": pct_change(first_price, snapshot["price"]),
                "hours_before_start": (event_start_ms - rows[index]["close_time"]) / 3600000.0,
            }
    return None


def enrich_and_save_event(event, btc_rows, now_ms):
    detail_start = event["event_start_ms"] - DETAIL_HISTORY_BEFORE_HOURS * 3600000
    detail_end = min(last_closed_end_ms(300000, now_ms), event["event_start_ms"] + EVENT_HORIZON_HOURS * 3600000)
    rows = parse_rows(fetch_klines_range(event["symbol"], "5m", detail_start, detail_end))
    if not rows:
        raise ValueError("5m detail history is empty")
    anomaly = first_pre_event_anomaly(rows, event["event_start_ms"])
    event.update({
        "first_anomaly_time_utc": anomaly["time_utc"] if anomaly else None,
        "first_anomaly_price": anomaly["price"] if anomaly else None,
        "gain_before_first_anomaly_pct": anomaly["gain_before_pct"] if anomaly else None,
        "hours_anomaly_before_start": anomaly["hours_before_start"] if anomaly else None,
    })
    save_winner_event(event)
    saved = 0
    for offset in PRE_EVENT_OFFSETS_HOURS:
        snapshot = feature_snapshot(rows, event["event_start_ms"] - offset * 3600000, btc_rows)
        if not snapshot:
            continue
        snapshot.update({
            "event_id": event["event_id"], "research_version": RESEARCH_VERSION,
            "subject_class": event["subject_class"], "symbol": event["symbol"],
            "offset_hours": offset,
        })
        save_pre_event_feature(snapshot)
        saved += 1
    return saved


def main():
    init_db()
    now, now_ms = utc_now(), int(utc_now().timestamp() * 1000)
    hour_end = last_closed_end_ms(3600000, now_ms)
    hour_start = hour_end - HOURLY_HISTORY_DAYS * 86400000
    print("=" * 80)
    print("BINANCE WINNER ANATOMY V2 - RETROSPECTIVE RESEARCH")
    print(f"UTC: {now.isoformat()} | Research: {RESEARCH_VERSION}")
    print("=" * 80)
    try:
        symbols = build_universe()
    except Exception as error:
        save_data_issue("WINNER_UNIVERSE_FAILED", str(error), now.isoformat())
        raise

    hourly_by_symbol, winner_events = {}, []
    for index, symbol in enumerate(symbols, 1):
        try:
            hourly = parse_rows(fetch_klines_range(symbol, "1h", hour_start, hour_end))
            hourly_by_symbol[symbol] = hourly
            events = detect_winner_events(symbol, hourly, now_ms)
            winner_events.extend(events)
            print(f"[{index}/{len(symbols)}] {symbol} | events={len(events)}")
        except Exception as error:
            save_data_issue("WINNER_HOURLY_SCAN_FAILED", f"{symbol}: {type(error).__name__}: {error}", now.isoformat())
            print(f"HATA | {symbol} | {type(error).__name__}: {error}")
        time.sleep(REQUEST_SLEEP_SECONDS)

    winner_events.sort(key=lambda e: (e["event_start_ms"], e["max_gain_pct"]), reverse=True)
    selected = winner_events[:MAX_DETAILED_EVENTS_PER_RUN]
    used, controls = set(), []
    for winner in [e for e in selected if e["event_status"] == "CLOSED"]:
        control = choose_matched_control(winner, hourly_by_symbol, used)
        if control:
            controls.append(control)
    if selected:
        earliest = min(e["event_start_ms"] for e in selected + controls)
        btc_rows = parse_rows(fetch_klines_range(
            "BTCUSDT", "5m", earliest - DETAIL_HISTORY_BEFORE_HOURS * 3600000,
            last_closed_end_ms(300000, now_ms),
        ))
    else:
        btc_rows = []

    saved_events = saved_features = 0
    for event in selected + controls:
        try:
            saved_features += enrich_and_save_event(event, btc_rows, now_ms)
            saved_events += 1
            print(f"SAVE | {event['subject_class']} | {event['symbol']} | max={event['max_gain_pct']:+.2f}% | {event['event_status']}")
        except Exception as error:
            save_data_issue("WINNER_DETAIL_FAILED", f"{event['symbol']}: {type(error).__name__}: {error}", now.isoformat())
            print(f"HATA | {event['symbol']} | {type(error).__name__}: {error}")

    counts = {int(level): sum(e["max_gain_pct"] >= level for e in winner_events) for level in WINNER_LEVELS}
    print("=" * 80)
    print("WINNER ANATOMY OZETI")
    print(f"Evren: {len(symbols)} | Winner olayi: {len(winner_events)}")
    print(f"Detay winner: {len(selected)} | Eslesmis kontrol: {len(controls)}")
    print(f"Kaydedilen olay: {saved_events} | Pre-event snapshot: {saved_features}")
    print("Seviye sayilari: " + json.dumps(counts, sort_keys=True))
    print("OPEN olaylar resmi karsilastirmaya girmez; 72 saat dolunca kapanir.")
    print("=" * 80)


if __name__ == "__main__":
    main()
