"""Pure, causal strategy, sizing and one-minute execution model.

All thresholds are a predeclared research hypothesis, not estimated edge.
Rates are fractions (0.001 = 0.1%), quantities are base-asset units.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import hashlib
import json
import math

MINUTE = 60_000


@dataclass(frozen=True)
class Config:
    version: str = "TA_PAPER_V1_1_20261026"
    entry_ms: int = 30 * MINUTE
    context_ms: int = 240 * MINUTE
    history_bars: int = 120
    fast_ema: int = 20
    slow_ema: int = 50
    slope_bars: int = 3
    pullback_bars: int = 6
    atr_period: int = 14
    stop_atr_buffer: float = 0.1
    max_trigger_range_atr: float = 1.5
    max_chase_atr: float = 0.25
    min_stop_fraction: float = 0.004
    max_stop_fraction: float = 0.05
    target_gross_r: float = 2.0
    min_net_reward_risk: float = 1.25
    min_quote_volume: float = 2_000_000
    min_listing_days: int = 30
    max_spread_fraction: float = 0.001
    max_scan_delay_ms: int = 5 * MINUTE
    max_book_age_ms: int = 5_000
    publication_delay_ms: int = 2_000
    entry_delay_ms: int = 2_000
    max_pending_ms: int = 10 * MINUTE
    fee_each_side: float = 0.0005
    entry_slippage: float = 0.0002
    target_slippage: float = 0.0002
    stop_slippage: float = 0.001
    stress_stop_slippage: float = 0.01
    paper_initial_equity: float = 1000.0
    risk_fraction: float = 0.005
    max_open_risk_fraction: float = 0.01
    daily_loss_fraction: float = 0.02
    max_positions: int = 2
    max_same_direction: int = 1
    max_notional_equity: float = 1.0
    indicative_leverage: int = 3
    max_margin_equity: float = 0.25
    cooldown_ms: int = 24 * 60 * MINUTE

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


CFG = Config()


def finite(value, positive=False):
    v = float(value)
    if not math.isfinite(v) or (positive and v <= 0):
        raise ValueError("INVALID_NUMBER")
    return v


def closed_bars(rows, period, now, minimum=80, require_current=True, cfg=CFG):
    result = []
    for row in rows:
        if len(row) < 7:
            raise ValueError("MALFORMED_CANDLE")
        start, end = int(row[0]), int(row[6])
        if end + cfg.publication_delay_ms >= now:
            continue
        o, h, l, c = [finite(v, True) for v in row[1:5]]
        volume = finite(row[5])
        if (start % period or end != start + period - 1 or
                not l <= min(o, c) <= max(o, c) <= h or volume < 0):
            raise ValueError("INVALID_CANDLE")
        if result and start != int(result[-1][0]) + period:
            raise ValueError("CANDLE_GAP_OR_DUPLICATE")
        result.append([start, o, h, l, c, volume, end])
    if len(result) < minimum:
        raise ValueError("INSUFFICIENT_CANDLES")
    expected = ((now - cfg.publication_delay_ms) // period - 1) * period
    if require_current and result[-1][0] != expected:
        raise ValueError("STALE_CANDLES")
    return result


def ema(values, period):
    if len(values) < period:
        raise ValueError("EMA_WARMUP")
    out = [None] * (period - 1) + [sum(values[:period]) / period]
    alpha = 2 / (period + 1)
    for value in values[period:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def atr(rows, period):
    trs = [max(r[2] - r[3], abs(r[2] - rows[i - 1][4]),
               abs(r[3] - rows[i - 1][4])) for i, r in enumerate(rows) if i]
    return sum(trs[-period:]) / period


def assess(symbol, entry_rows, context_rows, now, cfg=CFG):
    bars = closed_bars(entry_rows, cfg.entry_ms, now, cfg=cfg)
    context = closed_bars(context_rows, cfg.context_ms, now, cfg=cfg)
    last, prev = bars[-1], bars[-2]
    p = dict(symbol=symbol, observed_ms=now, candle_ms=last[0],
             config_hash=cfg.fingerprint, version=cfg.version, status="REJECTED")
    if now - last[6] > cfg.max_scan_delay_ms:
        return dict(p, reason="LATE_SCAN")
    e = ema([r[4] for r in bars], cfg.fast_ema)
    f = ema([r[4] for r in context], cfg.fast_ema)
    s = ema([r[4] for r in context], cfg.slow_ema)
    trend = (1 if context[-1][4] > f[-1] > s[-1] and f[-1] > f[-1-cfg.slope_bars]
             else -1 if context[-1][4] < f[-1] < s[-1] and f[-1] < f[-1-cfg.slope_bars]
             else 0)
    a = atr(bars, cfg.atr_period)
    p.update(trend=trend, atr=a, ema_entry=e[-1], ema_context=f[-1],
             volume_ratio=last[5] / max(1e-12, sum(r[5] for r in bars[-21:-1])/20),
             reference_entry=last[4], reason="NO_TREND")
    if not trend:
        return p
    p['direction'] = "LONG" if trend == 1 else "SHORT"
    # One explicitly defined setup: cross EMA20 after a pullback and close
    # beyond the immediately preceding candle's high/low, in the 4h trend.
    reclaim = (prev[4] <= e[-2] and last[4] > e[-1] and last[4] > prev[2]
               if trend == 1 else prev[4] >= e[-2] and last[4] < e[-1] and last[4] < prev[3])
    if not reclaim:
        return dict(p, reason="NO_PULLBACK_RECLAIM")
    if a <= 0 or last[2] - last[3] > cfg.max_trigger_range_atr * a:
        return dict(p, reason="EXTENDED_TRIGGER")
    tail = bars[-cfg.pullback_bars:]
    stop = (min(r[3] for r in tail) - cfg.stop_atr_buffer*a if trend == 1
            else max(r[2] for r in tail) + cfg.stop_atr_buffer*a)
    distance = trend*(last[4]-stop)
    if not cfg.min_stop_fraction <= distance/last[4] <= cfg.max_stop_fraction:
        return dict(p, reason="STOP_DISTANCE_OUTSIDE_LIMITS")
    p.update(status="SETUP", reason="PULLBACK_RECLAIM", stop=stop,
             target=last[4] + trend*cfg.target_gross_r*distance,
             entry_limit=last[4] + trend*cfg.max_chase_atr*a,
             expires_ms=now+cfg.max_pending_ms,
             entry_ms=((now+cfg.entry_delay_ms)//MINUTE+1)*MINUTE)
    return p


def quantize(value, step, upward=False):
    d, unit = Decimal(str(value)), Decimal(str(step))
    if not d.is_finite() or not unit.is_finite() or unit <= 0:
        raise ValueError("INVALID_TICK_OR_STEP")
    rounding = ROUND_CEILING if upward else ROUND_FLOOR
    return float((d/unit).to_integral_value(rounding=rounding)*unit)


def market_rules(market):
    fs = {f['filterType']: f for f in market['filters']}
    price = fs['PRICE_FILTER']
    lot = fs.get('MARKET_LOT_SIZE', fs.get('LOT_SIZE'))
    if not lot or float(lot.get('stepSize', 0)) <= 0:
        lot = fs['LOT_SIZE']
    notional = fs.get('MIN_NOTIONAL', fs.get('NOTIONAL', {}))
    return dict(tick=finite(price['tickSize'], True), step=finite(lot['stepSize'], True),
                min_qty=finite(lot['minQty']), max_qty=finite(lot['maxQty'], True),
                min_notional=finite(notional.get('notional', notional.get('minNotional', 0))))


def sizing(p, market, equity, available_risk, opening_price=None, cfg=CFG, *, reference_is_quote=False):
    sign = 1 if p['direction'] == 'LONG' else -1
    ref = finite(opening_price if opening_price is not None else p['reference_entry'], True)
    rules = market_rules(market)
    # Entry is modelled market execution, exits are also conservatively charged
    # taker fees. No assertion that an unsubmitted limit order would fill.
    half_spread = 0.0 if reference_is_quote else p.get('entry_half_spread', 0.0)
    entry = ref*(1+sign*(cfg.entry_slippage+half_spread))
    stop = quantize(p['stop'], rules['tick'], upward=sign == -1)
    target = quantize(p['target'], rules['tick'], upward=sign == -1)
    if sign*(entry-p['entry_limit']) > 1e-12:
        raise ValueError("LATE_ENTRY")
    if sign*(entry-stop) <= 0 or sign*(target-entry) <= 0:
        raise ValueError("INVALID_PLAN_GEOMETRY")
    distance = sign*(entry-stop)/entry
    if not cfg.min_stop_fraction <= distance <= cfg.max_stop_fraction:
        raise ValueError("STOP_DISTANCE_OUTSIDE_LIMITS")
    stop_fill = stop*(1-sign*cfg.stop_slippage)
    target_fill = target*(1-sign*cfg.target_slippage)
    loss_unit = sign*(entry-stop_fill) + cfg.fee_each_side*(entry+stop_fill)
    win_unit = sign*(target_fill-entry) - cfg.fee_each_side*(entry+target_fill)
    stress_fill = stop*(1-sign*cfg.stress_stop_slippage)
    stress_unit = sign*(entry-stress_fill) + cfg.fee_each_side*(entry+stress_fill)
    rr = win_unit/loss_unit
    if rr < cfg.min_net_reward_risk:
        raise ValueError("COST_ADJUSTED_REWARD_TOO_SMALL")
    budget = min(equity*cfg.risk_fraction, available_risk)
    cap = min(equity*cfg.max_notional_equity,
              equity*cfg.max_margin_equity*cfg.indicative_leverage)
    quantity = quantize(min(budget/loss_unit, cap/entry, rules['max_qty']), rules['step'])
    if (quantity <= 0 or quantity < rules['min_qty'] or
            quantity*entry < rules['min_notional']):
        raise ValueError("BELOW_EXCHANGE_MINIMUM")
    return dict(entry=entry, stop=stop, target=target, quantity=quantity,
                notional=quantity*entry, planned_loss=quantity*loss_unit,
                stress_loss=quantity*stress_unit, target_net=quantity*win_unit,
                net_rr=rr, break_even_win_rate=1/(1+rr),
                indicative_leverage=cfg.indicative_leverage,
                indicative_margin=quantity*entry/cfg.indicative_leverage,
                liquidation_status="NOT_MODELLED_NO_ACCOUNT_OR_MARGIN_TIERS",
                funding_included=False, account_basis="EXAMPLE_PAPER_ACCOUNT",
                tick=rules['tick'])


def exit_on_bar(p, bar, cfg=CFG):
    """One-minute path; stop wins if both barriers occur. Gaps use open.

    Bar time is an interval, not a fabricated exact exit timestamp.
    """
    sign = 1 if p['direction'] == 'LONG' else -1
    o, h, l = bar[1:4]
    stop_hit = l <= p['stop'] if sign == 1 else h >= p['stop']
    target_hit = h > p['target'] if sign == 1 else l < p['target']
    if not (stop_hit or target_hit):
        return None
    if stop_hit:
        base = min(o, p['stop']) if sign == 1 else max(o, p['stop'])
        exit_price = base*(1-sign*cfg.stop_slippage)
        stress_price = base*(1-sign*cfg.stress_stop_slippage)
    else:
        # Conservative target: no positive price improvement on a gap.
        exit_price = p['target']*(1-sign*cfg.target_slippage)
        stress_price = exit_price
    def pnl(price):
        return p['quantity']*(sign*(price-p['entry']) - cfg.fee_each_side*(p['entry']+price))
    return dict(reason="STOP" if stop_hit else "TARGET", exit_price=exit_price,
                stress_exit_price=stress_price, pnl_before_funding=pnl(exit_price),
                stress_pnl_before_funding=pnl(stress_price),
                exit_interval_start=bar[0], exit_interval_end=bar[6],
                same_minute_ambiguous=bool(stop_hit and target_hit),
                gap_stop=bool(stop_hit and sign*(o-p['stop']) < 0))


def funding_cost(p, rows, end_ms):
    sign = 1 if p['direction'] == 'LONG' else -1
    total, boundary_uncertain = 0.0, False
    seen = set()
    for row in rows:
        t = int(row['fundingTime'])
        if row.get('symbol') != p['symbol'] or t in seen:
            raise ValueError("INVALID_FUNDING_HISTORY")
        seen.add(t)
        if p['entry_ms'] <= t <= end_ms:
            amount = sign*p['quantity']*finite(row['markPrice'], True)*finite(row['fundingRate'])
            # Exit time inside its minute is unknown. Debit at this boundary
            # conservatively, never assume an ambiguous credit was received.
            uncertain = t >= p.get('exit_interval_start', end_ms+1)
            boundary_uncertain |= uncertain
            total += max(0.0, amount) if uncertain else amount
    return total, boundary_uncertain


def risk_room(equity, daily_start_equity, daily_pnl, open_plans, direction, cfg=CFG):
    if len(open_plans) >= cfg.max_positions:
        raise ValueError("MAX_OPEN_POSITIONS")
    if sum(p['direction'] == direction for p in open_plans) >= cfg.max_same_direction:
        raise ValueError("SAME_DIRECTION_CLUSTER_LIMIT")
    used = sum(p['planned_loss'] for p in open_plans)
    room = min(equity*cfg.max_open_risk_fraction-used,
               daily_start_equity*cfg.daily_loss_fraction+min(daily_pnl, 0)-used)
    if equity <= 0 or room <= 0:
        raise ValueError("PORTFOLIO_OR_DAILY_RISK_LIMIT")
    return room


def descriptive_stats(plans):
    resolved = [p for p in plans if p['status'] == 'CLOSED' and p.get('net_r') is not None]
    daily = {}
    for p in resolved:
        daily.setdefault(p['entry_ms']//86_400_000, []).append(p['net_r'])
    means = [sum(v)/len(v) for v in daily.values()]
    avg = sum(means)/len(means) if means else None
    se = (math.sqrt(sum((v-avg)**2 for v in means)/(len(means)-1)/len(means))
          if len(means) > 1 else None)
    return dict(status="UNVALIDATED", n=len(resolved), entry_days=len(daily),
                wins=sum(p['net_r'] > 0 for p in resolved),
                mean_net_r=sum(p['net_r'] for p in resolved)/len(resolved) if resolved else None,
                day_mean_r=avg, day_standard_error=se,
                mean_stress_r=sum(p['stress_net_r'] for p in resolved)/len(resolved) if resolved else None,
                note="Descriptive only; days may remain dependent; no calibrated prediction probability.")
