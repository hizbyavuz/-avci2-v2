"""Causal structure -> level -> observed condition -> risk/room explanation.

Higher-timeframe direction is context, never a compulsory unanimity gate.
WATCH is distinct from CONDITION_MET; neither means an exchange fill.
All drawing coordinates are stored alongside the decision that used them.
"""
import math
from statistics import median

from .config import CONFIG as C, CONFIG_HASH, HOUR, PERIODS, VERSION, identity


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(rows, interval, now_ms):
    step = PERIODS[interval]
    if len(rows) < C['minimum_bars'][interval]:
        raise ValueError('INSUFFICIENT_HISTORY:'+interval)
    for i, b in enumerate(rows):
        t, end = b['open_ms'], b['close_ms']
        if not isinstance(t, int) or t % step or end != t+step-1:
            raise ValueError('INVALID_CLOCK:'+interval)
        if end >= now_ms or b.get('received_ms', end) > now_ms:
            raise ValueError('FUTURE_OR_OPEN_CANDLE:'+interval)
        if i and t != rows[i-1]['open_ms']+step:
            raise ValueError('CANDLE_GAP:'+interval)
        if not all(finite(b[k]) and b[k] > 0 for k in ('open', 'high', 'low', 'close')):
            raise ValueError('INVALID_PRICE:'+interval)
        if not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']:
            raise ValueError('INVALID_OHLC:'+interval)
        if not finite(b.get('quote_volume')) or b['quote_volume'] < 0:
            raise ValueError('INVALID_VOLUME:'+interval)
    if not 0 < now_ms-rows[-1]['close_ms'] <= step:
        raise ValueError('STALE_HISTORY:'+interval)


def atr_series(rows):
    tr, result = [], []
    for i, b in enumerate(rows):
        prev = rows[i-1]['close'] if i else b['open']
        tr.append(max(b['high']-b['low'], abs(b['high']-prev), abs(b['low']-prev)))
        result.append(sum(tr[-C['atr_period']:])/len(tr[-C['atr_period']:]))
    return result


def significant_swings(rows):
    """Alternating 2/2 pivots, >=3 bars and >=1.5 past ATR between extremes.

    The first seed is NOT itself significant until a qualifying opposite leg
    exists. Same-side replacement uses only already-confirmed extremes; each
    assessment saves its own snapshot instead of repainting historical cards.
    """
    av = atr_series(rows)
    raw, out = [], []
    for i in range(C['pivot_left'], len(rows)-C['pivot_right']):
        found = []
        for kind, field, sign in [('H', 'high', 1), ('L', 'low', -1)]:
            value = sign*rows[i][field]
            if (all(value > sign*b[field] for b in rows[i-2:i]) and
                    all(value >= sign*b[field] for b in rows[i+1:i+3])):
                found.append(dict(kind=kind, price=rows[i][field], i=i,
                                  open_ms=rows[i]['open_ms'], known_ms=rows[i+2]['close_ms'], atr=av[i]))
        # One outside candle has unknowable intrabar high/low order: no two pivots.
        if len(found) == 1:
            raw.extend(found)
    for p in raw:
        if not out:
            out.append(p)
            continue
        prev = out[-1]
        if prev['kind'] == p['kind']:
            sign = 1 if p['kind'] == 'H' else -1
            if sign*(p['price']-prev['price']) > 0:
                out[-1] = p
        elif (p['i']-prev['i'] >= C['significant_leg_min_bars'] and
              (1 if p['kind'] == 'H' else -1)*(p['price']-prev['price'])
              >= C['significant_leg_min_atr']*max(p['atr'], prev['atr']) > 0):
            out.append(p)
    return out if len(out) >= 2 else []


def describe(rows):
    swings = significant_swings(rows)
    highs = [p for p in swings if p['kind'] == 'H']
    lows = [p for p in swings if p['kind'] == 'L']
    direction = 'UNKNOWN'
    if len(highs) >= 2 and len(lows) >= 2:
        up = highs[-1]['price'] > highs[-2]['price'] and lows[-1]['price'] > lows[-2]['price']
        down = highs[-1]['price'] < highs[-2]['price'] and lows[-1]['price'] < lows[-2]['price']
        direction = 'UP' if up else 'DOWN' if down else 'RANGE'
    return dict(direction=direction, swings=swings, last_close_ms=rows[-1]['close_ms'])


def zone(pivot, interval):
    width = C['zone_half_width_atr']*pivot['atr']
    return dict(low=pivot['price']-width, high=pivot['price']+width, center=pivot['price'],
                timeframe=interval, anchor=pivot, reason='CONFIRMED_SIGNIFICANT_SWING',
                known_ms=pivot['known_ms'])


def zones(rows, interval):
    result = []
    for p in significant_swings(rows):
        z = zone(p, interval)
        after = rows[p['i']+1:]
        broken = any(b['close'] > z['high'] if p['kind'] == 'H' else b['close'] < z['low'] for b in after)
        if not broken:
            z['role'] = 'RESISTANCE' if p['kind'] == 'H' else 'SUPPORT'
            result.append(z)
    return result


def payoff(direction, entry, stop, target):
    if direction not in ('LONG', 'SHORT') or not all(finite(x) and x > 0 for x in (entry, stop)):
        return dict(valid=False, reason='INVALID_PLAN_PRICE', net_r=None)
    sign = 1 if direction == 'LONG' else -1
    risk = 100*sign*(entry-stop)/entry
    if risk <= 0:
        return dict(valid=False, reason='STOP_WRONG_SIDE', net_r=None)
    if target is None:
        return dict(valid=False, reason='FIRST_OBSTACLE_UNKNOWN', risk_pct=risk, net_r=None)
    if not finite(target) or target <= 0:
        return dict(valid=False, reason='INVALID_PLAN_PRICE', net_r=None)
    gain = 100*sign*(target-entry)/entry
    cost = C['assumed_round_trip_cost_pct']
    reason = ('TARGET_WRONG_SIDE' if gain <= 0 else 'COST_EXCEEDS_ROOM' if gain <= cost
              else 'LOW_NET_REWARD_RISK' if (gain-cost)/(risk+cost) < C['minimum_plan_net_r'] else 'OK')
    return dict(valid=reason == 'OK', reason=reason, risk_pct=risk, room_pct=gain,
                assumed_cost_pct=cost, net_r=(gain-cost)/(risk+cost), funding_included=False)


def _obstacle(levels, direction, price):
    role = 'RESISTANCE' if direction == 'LONG' else 'SUPPORT'
    sign = 1 if direction == 'LONG' else -1
    choices = []
    for z in levels:
        edge = z['low'] if sign == 1 else z['high']
        if z['role'] == role and sign*(edge-price) > 0:
            choices.append((sign*(edge-price), z))
    return min(choices, key=lambda x: x[0])[1] if choices else None


def _hypotheses(rows, interval):
    """Current approach or first close through the last significant H/L."""
    past = rows[:-1]
    ps = significant_swings(past)
    if not ps:
        return []
    av = atr_series(past)[-1]
    if av <= 0:
        return []
    latest, previous = rows[-1], rows[-2]
    result = []
    for direction, kind, opposite, sign in [('LONG', 'H', 'L', 1), ('SHORT', 'L', 'H', -1)]:
        bounds = [p for p in ps if p['kind'] == kind]
        if not bounds:
            continue
        p = bounds[-1]
        opposing = [q for q in ps if q['kind'] == opposite]
        if not opposing:
            continue
        invalidator = opposing[-1]
        z = zone(p, interval)
        trigger = (z['high'] if sign == 1 else z['low'])+sign*C['break_buffer_atr']*av
        if any(sign*(b['close']-trigger) > 0 for b in past if b['open_ms'] > p['open_ms']):
            continue
        after = [b for b in rows if b['open_ms'] >= invalidator['open_ms']]
        adverse = min(b['low'] for b in after) if sign == 1 else max(b['high'] for b in after)
        stop = adverse-sign*C['stop_buffer_atr']*av
        crossed = sign*(latest['close']-trigger) > 0 and sign*(previous['close']-trigger) <= 0
        near = 0 <= sign*(trigger-latest['close']) <= C['approach_atr']*av
        approaching = sign*(latest['close']-previous['close']) > 0
        if not crossed and not (near and approaching):
            continue
        # Stop must still be on the invalid side of the actual boundary.
        if sign*(trigger-stop) <= 0:
            continue
        model = 'LEVEL_BREAK'
        extremes = [q for q in ps if q['kind'] == opposite]
        if (len(extremes) >= 2 and extremes[-2]['open_ms'] < p['open_ms'] < extremes[-1]['open_ms']
                and abs(extremes[-1]['price']-extremes[-2]['price']) <= C['double_extreme_tolerance_atr']*av):
            model = 'DOUBLE_BOTTOM' if sign == 1 else 'DOUBLE_TOP'
        starts = [q for q in ps if q['kind'] == opposite and q['open_ms'] < p['open_ms']]
        fib = None
        if starts:
            a, b = starts[-1], p
            if p['i']-a['i'] >= C['significant_leg_min_bars']:
                distance = sign*(b['price']-a['price'])
                fib = dict(start=a, end=b, reason='SIGNIFICANT_LEG_ENDING_AT_DECISION_LEVEL',
                           retracements={str(r): b['price']-sign*r*distance for r in (.382, .5, .618)},
                           decision_effect='ANNOTATION_ONLY')
        result.append(dict(direction=direction, model=model, stage='CONDITION_MET' if crossed else 'WATCH',
                           condition='CLOSE_THROUGH_ZONE' if crossed else 'APPROACHING_ZONE',
                           decision_zone=z, trigger=trigger, stop=stop, atr=av,
                           invalidation_anchor=invalidator, stop_extreme=adverse,
                           evidence_swings=ps[-6:], fibonacci=fib,
                           origin_ms=latest['close_ms'] if crossed else None,
                           confirmation_ms=latest['close_ms'] if crossed else None))
    return result


def _retests(rows, interval):
    result = []
    for ago in range(1, C['retest_bars']+1):
        origin_rows = rows[:-ago]
        for p in _hypotheses(origin_rows, interval):
            if p['stage'] != 'CONDITION_MET':
                continue
            sign = 1 if p['direction'] == 'LONG' else -1
            z = p['decision_zone']
            after = rows[-ago:]
            boundary = z['low'] if sign == 1 else z['high']
            # Preserve the frozen origin stop; never loosen it after a break.
            if any((b['low'] <= p['stop'] or b['close'] < boundary) if sign == 1
                   else (b['high'] >= p['stop'] or b['close'] > boundary) for b in after):
                continue
            def confirms(b):
                return (b['low'] <= z['high'] and b['high'] >= z['low']
                        and sign*(b['close']-p['trigger']) > 0 and sign*(b['close']-b['open']) > 0)
            if confirms(after[-1]) and not any(confirms(b) for b in after[:-1]):
                p.update(condition='RETEST_HELD', confirmation_ms=after[-1]['close_ms'])
                result.append(p)
    return result


def assess(symbol, frames, now_ms, interval='15m'):
    if interval not in ('15m', '30m'):
        raise ValueError('UNSUPPORTED_SETUP_INTERVAL')
    out = dict(version=VERSION, config_hash=CONFIG_HASH, symbol=symbol, interval=interval,
               asof_ms=now_ms, status='VALID', data_errors=[], decisions=[], context={}, levels=[])
    for tf in (interval, '1h', '4h'):
        try:
            validate(frames.get(tf, []), tf, now_ms)
        except (ValueError, KeyError, TypeError) as exc:
            out['data_errors'].append(str(exc))
    if out['data_errors']:
        out['status'] = 'DATA_INVALID'
        return out
    frames = {tf: frames[tf][-C['maximum_bars']:] for tf in (interval, '1h', '4h')}
    rows = frames[interval]
    out['setup_close_ms'] = rows[-1]['close_ms']
    out['context'] = {tf: describe(frames[tf]) for tf in (interval, '1h', '4h')}
    out['levels'] = [z for tf in (interval, '1h', '4h') for z in zones(frames[tf], tf)]
    volume_start = rows[-1]['close_ms']+1-24*HOUR
    out['quote_volume_24h'] = sum(b['quote_volume'] for b in rows if b['open_ms'] >= volume_start)
    if out['quote_volume_24h'] < C['min_quote_volume_24h']:
        out['status'] = 'ILLIQUID'
        return out
    hypotheses = _hypotheses(rows, interval)+_retests(rows, interval)
    for p in hypotheses:
        direction = p['direction']
        sign = 1 if direction == 'LONG' else -1
        p['reference_price'] = rows[-1]['close']
        p['entry_reference'] = p['trigger'] if p['stage'] == 'WATCH' else p['reference_price']
        obstacle = _obstacle(out['levels'], direction, p['entry_reference'])
        p['first_obstacle'] = obstacle
        p['target'] = (obstacle['low'] if sign == 1 else obstacle['high']) if obstacle else None
        p['payoff'] = payoff(direction, p['entry_reference'], p['stop'], p['target'])
        p['warnings'] = []
        opposite = 'DOWN' if sign == 1 else 'UP'
        one = out['context']['1h']['direction']
        p['scenario'] = ('COUNTERTREND_CHANGE' if one == opposite else
                         'CONTINUATION' if one in ('UP', 'DOWN') else 'RANGE_OR_UNCLEAR_BREAK')
        for tf in ('1h', '4h'):
            if out['context'][tf]['direction'] == opposite:
                p['warnings'].append('AGAINST_'+tf+'_STRUCTURE')
            elif out['context'][tf]['direction'] == 'UNKNOWN':
                p['warnings'].append('UNRESOLVED_'+tf+'_STRUCTURE')
        if not p['payoff']['valid']:
            p['warnings'].append(p['payoff']['reason'])
        if p['stage'] == 'CONDITION_MET' and sign*(p['reference_price']-p['trigger']) > C['max_chase_atr']*p['atr']:
            p['stage'] = 'LATE'
            p['warnings'].append('PRICE_EXTENDED')
        p['plan_eligible'] = p['stage'] == 'CONDITION_MET' and p['payoff']['valid']
        p['idea_id'] = identity(VERSION, CONFIG_HASH, symbol, interval, direction,
                                p['decision_zone']['anchor']['open_ms'])
        p['event_id'] = identity(p['idea_id'], p['stage'], p['condition'], p['confirmation_ms'])
        p['asof_ms'], p['symbol'], p['interval'] = now_ms, symbol, interval
        p['context_directions'] = {tf: out['context'][tf]['direction'] for tf in ('1h', '4h')}
        baseline = median(b['quote_volume'] for b in rows[-25:-1])
        p['volume_multiple'] = rows[-1]['quote_volume']/baseline if baseline > 0 else None
        out['decisions'].append(p)
    # A retest is more specific than a simultaneous approach to the same level.
    chosen = {}
    priority = {'RETEST_HELD': 0, 'CLOSE_THROUGH_ZONE': 1, 'APPROACHING_ZONE': 2}
    for p in sorted(out['decisions'], key=lambda p: (priority[p['condition']], -int(p['origin_ms'] or 0))):
        chosen.setdefault(p['idea_id'], p)
    out['decisions'] = list(chosen.values())
    if out['decisions']:
        from .annotations import analyze
        out['chart_analysis'] = analyze(rows)
    out['reason'] = 'EXPLAINED_CANDIDATES' if out['decisions'] else 'NO_CURRENT_LEVEL_EVENT'
    return out


def message(p):
    from .presentation import card
    c = card(p)
    f = lambda v: f'{v:.8g}' if v is not None else 'Bilinmiyor'
    lines = [f"{c['status']} | {p['symbol']} | {p['direction']} | {p['interval']}",
             c['reason'], c['condition'],
             f"Son kapanış: {f(c['last'])}",
             f"Giriş referansı: {f(c['entry'])}" if c['eligible'] else 'Giriş: YOK — bu kayıt işlem planı değildir.',
             f"{c['stop_label']}: {f(c['stop'])}",
             f"{c['target_label']}: {f(c['target'])}"]
    g = p['payoff']
    if g.get('net_r') is not None:
        lines.append(f"Hedef alanı %{g['room_pct']:.2f} / stop mesafesi %{g['risk_pct']:.2f} / masraf sonrası {g['net_r']:.2f} R")
    for tf, state in p['context_directions'].items():
        translated={'UP':'yükseliş','DOWN':'düşüş','RANGE':'yatay','UNKNOWN':'belirsiz'}[state]
        lines.append(f"{tf}: {translated}")
    lines.append('Seviyeler karar anına aittir; gerçekleşmiş emir/giriş değildir. Fiyat değişirse uygunluk değişebilir.')
    lines.append('Araştırma · otomatik emir yok · %0,30 toplam komisyon/kayma varsayımı · funding hariç.')
    return '\n'.join(lines)
