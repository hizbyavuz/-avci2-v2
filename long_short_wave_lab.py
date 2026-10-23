"""Independent, deterministic wave research. No network, orders or delivery.

Rules are provisional hypotheses, NOT a calibrated probability or a frozen Avci
revision. All prices belong to Binance USD-M linear futures. See WAVE_LAB.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import statistics as st

from long_short_v4_structure import resample_complete_5m, previous_utc_day_week_levels

VERSION = "WAVE_LAB_V1_DRAFT_2026_10_22"
VENUE = "BINANCE_UM_FUTURES"
SOURCES = {VENUE + suffix for suffix in ("_WEBSOCKET", "_REST", "_ARCHIVE")}
MINUTE = 60_000


@dataclass(frozen=True)
class Rules:
    history_bars: int = 2880
    min_5m: int = 960
    pivot_span: int = 2
    compression_bars: int = 12
    baseline_bars: int = 24
    max_compression_ratio: float = 0.75
    max_approach_atr: float = 0.75
    max_extension_atr: float = 0.75
    pullback_min_atr: float = 0.5
    pullback_max_atr: float = 3.0
    stop_buffer_atr: float = 0.15
    watch_volume_ratio: float = 0.8
    breakout_volume_ratio: float = 1.2
    min_room_r: float = 1.2
    setup_ttl_hours: int = 6
    cooldown_hours: int = 24
    scan_lag_ms: int = 120_000
    entry_delay_ms: int = 60_000
    fee_bps_side: float = 5.0
    slippage_bps_side: float = 10.0
    horizons_minutes: tuple = (60, 120, 180, 1440, 4320)
    reach_pct: tuple = (3, 5, 7, 10, 15)


RULES = Rules()
CONFIG_HASH = hashlib.sha256(json.dumps(
    {"version": VERSION, **asdict(RULES)}, sort_keys=True).encode()).hexdigest()


def validate_bars(rows, step_ms, end_ms=None):
    """Strict native OHLC, closed timestamp, ordering, alignment and gap check."""
    if not rows:
        return "NO_BARS"
    for i, b in enumerate(rows):
        try:
            op, hi, lo, cl, vol = [float(b[k]) for k in
                                  ("open", "high", "low", "close", "volume")]
            t, end = int(b["open_ms"]), int(b["close_ms"])
            if not all(math.isfinite(v) for v in (op, hi, lo, cl, vol)):
                return "NONFINITE_BAR"
            if not (0 < lo <= min(op, cl) <= max(op, cl) <= hi) or vol < 0:
                return "BAD_OHLCV"
            if t % step_ms or end != t + step_ms - 1:
                return "BAD_BAR_TIME"
            if end_ms is not None and end > end_ms:
                return "UNFINISHED_OR_FUTURE_BAR"
            if b.get("source") not in SOURCES:
                return "WRONG_OR_UNKNOWN_VENUE"
            if i and t - int(rows[i-1]["open_ms"]) != step_ms:
                return "MISSING_DUPLICATE_OR_UNORDERED_BAR"
        except (KeyError, ValueError, TypeError, OverflowError):
            return "MALFORMED_BAR"
    return None


def atr(rows, n=14):
    return st.mean(max(b["high"]-b["low"], abs(b["high"]-p["close"]),
                       abs(b["low"]-p["close"]))
                   for p, b in zip(rows[-n-1:-1], rows[-n:]))


def pivots(rows, span=2):
    """A pivot exists only after span right-hand bars have CLOSED."""
    highs, lows = [], []
    for i in range(span, len(rows)-span):
        others = rows[i-span:i] + rows[i+1:i+span+1]
        if rows[i]["high"] > max(b["high"] for b in others):
            highs.append(rows[i]["high"])
        if rows[i]["low"] < min(b["low"] for b in others):
            lows.append(rows[i]["low"])
    return highs, lows


def structure(rows):
    highs, lows = pivots(rows, RULES.pivot_span)
    if len(highs) < 2 or len(lows) < 2:
        return "UNCLEAR"
    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return "UP"
    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return "DOWN"
    return "RANGE"


def oriented(rows, sign):
    if sign == 1:
        return rows
    return [{**b, "open": -b["open"], "close": -b["close"],
             "high": -b["low"], "low": -b["high"]} for b in rows]


def prepare(rows, asof_ms, observed_ms):
    error = validate_bars(rows, 5*MINUTE, asof_ms)
    if error:
        return {"health": "INVALID", "reason": error}
    if rows[-1]["close_ms"] != asof_ms:
        return {"health": "INVALID", "reason": "LATEST_CLOSED_BAR_MISSING"}
    if not 0 <= observed_ms-asof_ms <= RULES.scan_lag_ms:
        return {"health": "INVALID", "reason": "SCAN_DELAY"}
    if len(rows) < RULES.min_5m:
        return {"health": "INVALID", "reason": "INSUFFICIENT_HISTORY"}
    tf = {5: rows}
    for m in (15, 60, 240):
        tf[m] = resample_complete_5m(rows, m)
        expected = ((asof_ms+1)//(m*MINUTE))*(m*MINUTE)-1
        minimum = {15: 48, 60: 48, 240: 16}[m]
        if len(tf[m]) < minimum or tf[m][-1]["close_ms"] != expected:
            return {"health": "INVALID", "reason": f"INCOMPLETE_{m}M_HISTORY"}
    a = atr(tf[15])
    baseline = st.mean(b["volume"] for b in rows[-39:-3])
    if a <= 0 or baseline <= 0:
        return {"health": "INVALID", "reason": "ZERO_RANGE_OR_VOLUME"}
    # Three 5m bars vs prior 36 bars, not raw volume vs another coin.
    volume_ratio = st.mean(b["volume"] for b in rows[-3:])/baseline
    return {"health": "OK", "timeframes": tf, "atr_15m": a,
            "volume_ratio": volume_ratio,
            "trend_1h": structure(tf[60][-72:]),
            "trend_4h": structure(tf[240][-48:]),
            "return_1h_pct": 100*(rows[-1]["close"]/rows[-13]["close"]-1),
            "completed_day_week": previous_utc_day_week_levels(rows, asof_ms+1)}


def proposals(symbol, features):
    """Return every strategy/direction diagnostic, including rejected cases."""
    if features["health"] != "OK":
        return []
    result = []
    a = features["atr_15m"]
    for direction, sign in (("LONG", 1), ("SHORT", -1)):
        tf = {m: oriented(b, sign) for m, b in features["timeframes"].items()}
        b5, b15, h1 = tf[5], tf[15], tf[60]
        price = b5[-1]["close"]
        for strategy in ("TREND_PULLBACK", "COMPRESSION_BREAKOUT"):
            checks = {}
            if strategy == "TREND_PULLBACK":
                highs, lows = pivots(h1[-72:], RULES.pivot_span)
                checks["hourly_structure"] = structure(h1[-72:]) == "UP"
                start = len(b15)-24
                peak_i = max(range(start, len(b15)-3), key=lambda i: b15[i]["high"])
                peak = b15[peak_i]["high"]
                pull = b15[peak_i+1:]
                trough = min(b["low"] for b in pull)
                depth = (peak-trough)/a
                checks["pullback_depth"] = RULES.pullback_min_atr <= depth <= RULES.pullback_max_atr
                checks["protected_hourly_low"] = bool(lows and trough > lows[-1])
                checks["recovery"] = price > b5[-3]["close"] and price > trough + 0.15*a
                # Keep trigger BEFORE recovery bar; freeze it in the episode.
                trigger = max(b["high"] for b in b5[-4:-1])
                stop = trough-RULES.stop_buffer_atr*a
            else:
                n, base = RULES.compression_bars, RULES.baseline_bars
                box, old = b15[-n-1:-1], b15[-n-base-1:-n-1]
                trigger = max(b["high"] for b in box)
                floor = min(b["low"] for b in box)
                width = trigger-floor
                old_width = max(b["high"] for b in old)-min(b["low"] for b in old)
                checks["compressed_range"] = old_width > 0 and width/old_width <= RULES.max_compression_ratio
                checks["pressure_toward_level"] = min(b["low"] for b in box[-4:]) > min(b["low"] for b in box[:4])
                checks["near_level"] = price >= trigger-RULES.max_approach_atr*a
                stop = min(b["low"] for b in box[-4:])-RULES.stop_buffer_atr*a
            checks["activity"] = features["volume_ratio"] >= RULES.watch_volume_ratio
            checks["valid_stop"] = stop < min(price, trigger)
            checks["not_extended"] = price-trigger <= RULES.max_extension_atr*a
            # Only levels already observed before the signal; no invented target.
            hourly_highs, _ = pivots(h1[-120:], RULES.pivot_span)
            local_highs, _ = pivots(b15[-120:-1], RULES.pivot_span)
            daily_levels = [sign*v["high" if sign == 1 else "low"]
                            for v in features.get("completed_day_week", {}).values()
                            if v["complete"]]
            levels = [x for x in hourly_highs+local_highs+daily_levels
                      if x > max(price, trigger)+0.1*a]
            target = min(levels) if levels else None
            reference = max(price, trigger)
            risk = reference-stop
            rr = (target-reference)/risk if target is not None and risk > 0 else None
            result.append({"symbol": symbol, "strategy": strategy, "direction": direction,
                           "qualified_watch": all(checks.values()), "checks": checks,
                           "failed_checks": [k for k, v in checks.items() if not v],
                           "signal_price": sign*price, "trigger": sign*trigger,
                           "stop": sign*stop, "target": sign*target if target is not None else None,
                           "room_r_at_signal": rr, "atr": a,
                           "volume_ratio": features["volume_ratio"],
                           "trend_1h": features["trend_1h"], "trend_4h": features["trend_4h"],
                           "counter_4h": features["trend_4h"] == ("DOWN" if sign == 1 else "UP"),
                           "return_1h_pct": features["return_1h_pct"],
                           "execution_authorized": False})
    return result


TERMINAL = {"INVALIDATED", "EXPIRED", "LATE"}


def transition(event, bars, observed_ms, volume_ratio):
    """No signal timestamp backdating. Missing path suspends state progression."""
    if event["state"] in TERMINAL:
        return event["state"], "TERMINAL"
    sign = 1 if event["direction"] == "LONG" else -1
    if not bars or validate_bars(bars, 5*MINUTE, observed_ms):
        return event["state"], "PATH_UNAVAILABLE"
    if bars[0]["open_ms"] != event["last_bar_ms"]+1:
        return event["state"], "PATH_UNAVAILABLE"
    if any((b["low"] <= event["stop"] if sign == 1 else b["high"] >= event["stop"]) for b in bars):
        return "INVALIDATED", "PROTECTED_LEVEL_LOST"
    if observed_ms-event["signal_ms"] >= RULES.setup_ttl_hours*3_600_000:
        return "EXPIRED", "SETUP_TIMEOUT"
    price = bars[-1]["close"]
    extension = sign*(price-event["trigger"])/event["atr"]
    if event["state"] == "CONFIRMED":
        return ("INVALIDATED", "BREAKOUT_LEVEL_LOST") if extension < 0 else ("CONFIRMED", "HELD")
    if extension > RULES.max_extension_atr:
        return "LATE", "EXTENDED_BREAKOUT"
    if extension > 0:
        if event["state"] == "BREAKOUT":
            if all(sign*(b["close"]-event["trigger"]) > 0 for b in bars):
                return "CONFIRMED", "NEXT_CLOSED_BAR_HELD_LEVEL"
            return "WATCH", "INTERVENING_CLOSE_LOST_LEVEL"
        if volume_ratio >= RULES.breakout_volume_ratio:
            return "BREAKOUT", "CLOSED_ABOVE_BELOW_TRIGGER"
    return "WATCH", "WAITING_FOR_BREAKOUT"


def paper_outcome(event, bars, horizon_min, now_ms, funding_cost_pct=None):
    """Hypothetical full-size first-barrier trade + separate raw horizon path.

    Prices are candle proxies, never executable quotes. Funding unknown => net
    return remains null. 1m ambiguity is stop-first, including gap exits.
    """
    due = ((event["signal_ms"]+RULES.entry_delay_ms+MINUTE-1)//MINUTE)*MINUTE
    end = due+horizon_min*MINUTE-1
    if now_ms <= end:
        return {"status": "PENDING", "entry_due_ms": due}
    path = [b for b in bars if due <= b["open_ms"] <= end]
    error = validate_bars(path, MINUTE, now_ms-1)
    if error or len(path) != horizon_min or path[0]["open_ms"] != due or path[-1]["close_ms"] != end:
        return {"status": "DATA_FAILURE", "reason": error or "INCOMPLETE_HORIZON"}
    sign = 1 if event["direction"] == "LONG" else -1
    slip = RULES.slippage_bps_side/10_000
    raw_entry = path[0]["open"]
    entry = raw_entry*(1+sign*slip)
    target, stop = event.get("target"), event["stop"]
    raw_return = lambda p: sign*(p/raw_entry-1)*100
    modeled_return = lambda p: sign*(p/entry-1)*100
    raw_mfe = max(0.0, max(raw_return(b["high"] if sign == 1 else b["low"]) for b in path))
    raw_mae = min(0.0, min(raw_return(b["low"] if sign == 1 else b["high"]) for b in path))
    reach = {str(p): next(((b["open_ms"]-due)//MINUTE for b in path
                          if raw_return(b["high"] if sign == 1 else b["low"]) >= p), None)
             for p in RULES.reach_pct}
    observation = {"entry_due_ms": due, "entry_price_model": entry, "raw_entry_price": raw_entry,
                   "raw_horizon_mfe_pct": raw_mfe, "raw_horizon_mae_pct": raw_mae,
                   "raw_horizon_return_pct": raw_return(path[-1]["close"]),
                   "raw_reach_minutes": reach,
                   "pre_signal_return_1h_pct": event.get("return_1h_pct"),
                   "gain_before_confirmation_pct": event.get("gain_before_confirmation_pct"),
                   "execution_model": "1M_OHLC_FIXED_COST_PROXY_NOT_QUOTES"}
    if target is None or sign*(entry-stop) <= 0 or sign*(target-entry) <= 0:
        return {**observation, "status": "OBSERVATION_ONLY", "reason": "NO_VALID_ENTRY_LEVELS"}
    rr = sign*(target-entry)/(sign*(entry-stop))
    if rr < RULES.min_room_r:
        return {**observation, "status": "OBSERVATION_ONLY", "reason": "INSUFFICIENT_ROOM", "entry_room_r": rr}
    if sign*(entry-event["trigger"])/event["atr"] > RULES.max_extension_atr:
        return {**observation, "status": "OBSERVATION_ONLY", "reason": "ENTRY_TOO_LATE"}
    barrier, exit_raw, exit_ms = "TIMEOUT", path[-1]["close"], path[-1]["close_ms"]
    trade_reach = {str(p): None for p in RULES.reach_pct}
    for b in path:
        stop_hit = b["low"] <= stop if sign == 1 else b["high"] >= stop
        target_hit = b["high"] >= target if sign == 1 else b["low"] <= target
        if stop_hit:
            barrier, exit_raw, exit_ms = "STOP", (min(b["open"], stop) if sign == 1 else max(b["open"], stop)), b["open_ms"]
            break
        # On the target candle only credit levels up to target, not its full high.
        favorable = b["high"] if sign == 1 else b["low"]
        if target_hit:
            favorable = target
        for p in RULES.reach_pct:
            if trade_reach[str(p)] is None and raw_return(favorable) >= p:
                trade_reach[str(p)] = (b["open_ms"]-due)//MINUTE
        if target_hit:
            barrier, exit_raw, exit_ms = "TARGET", target, b["open_ms"]
            break
    exit_price = exit_raw*(1-sign*slip)
    gross_after_slippage = modeled_return(exit_price)
    # Entry and exit fees on their own notionals, expressed / initial notional.
    fee_pct = RULES.fee_bps_side/100*(1+exit_price/entry)
    modeled = gross_after_slippage-fee_pct
    net = None if funding_cost_pct is None else modeled-funding_cost_pct
    risk_pct = abs(entry-stop)/entry*100
    return {**observation, "status": "LABELED", "first_barrier": barrier,
            "exit_price_model": exit_price, "exit_bar_ms": exit_ms,
            "modeled_net_excluding_funding_pct": modeled,
            "funding_known": funding_cost_pct is not None, "net_pct": net,
            "net_r": net/risk_pct if net is not None else None,
            "modeled_r_excluding_funding": modeled/risk_pct,
            "entry_room_r": rr, "reach_before_exit_minutes": trade_reach}


def render_watch(event):
    long = event["direction"] == "LONG"
    icon, side = ("🟢", "üstünde") if long else ("🔴", "altında")
    strategy = "Geri çekilme sonrası devam" if event["strategy"] == "TREND_PULLBACK" else "Sıkışmadan çıkış"
    target = f'{event["target"]:.8g}' if event.get("target") is not None else "Henüz belirlenemedi"
    room = f'{event["room_r_at_signal"]:.2f}' if event.get("room_r_at_signal") is not None else "Belirsiz"
    return (f'{icon} {event["direction"]} İÇİN İZLE | {event["symbol"]}\n'
            f'5 dk mum {event["trigger"]:.8g} {side} kapanırsa fikir güçlenir.\n'
            f'Şu an fiyat: {event["signal_price"]:.8g}\n'
            f'Beklenen: Kapanış → ardından seviyeyi koruması.\n'
            f'Tip: {strategy}\nFikir bozulur: {event["stop"]:.8g}\n'
            f'İlk engel: {target}\nGetiri/risk: {room} (masraf öncesi)\n'
            f'Deneme gözlemi; giriş teyidi değil.')
