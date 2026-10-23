"""Closed-bar chart hypotheses, not a fitted classifier or a profit claim.

Every candidate has a historical structure, a fresh close through its boundary,
a structural stop and an honest first obstacle. Derivatives never vote on price
direction. Fibonacci is an annotated measurement, never a second price vote.
"""
import hashlib
import json
import math
from statistics import median

VERSION = 'PATTERN_STRUCTURE_RESEARCH_V2_1_2026_10_23'
MINUTE = 60_000
HOUR = 60 * MINUTE
INTERVALS = {'15m': 15 * MINUTE, '30m': 30 * MINUTE, '1h': HOUR, '4h': 4 * HOUR}
SETUP_INTERVALS = ('15m', '30m')
CONFIG = dict(pivot_right=2, min_bars=120, max_bars=220, min_quote_volume_24h=2_000_000,
              atr_period=14, stop_buffer_atr=.2, break_buffer_atr=.05,
              max_chase_atr=.75, max_risk_pct=12., min_reward_risk=1.5,
              round_trip_cost_pct=.30, max_notices_24h=10, max_active_plans=10,
              setup_intervals=SETUP_INTERVALS,
              max_hold_hours={'15m': 72, '30m': 72}, ready_minutes=15,
              retest_lookback_bars=6, retest_touch_atr=.35,
              hypothesis_status='UNVALIDATED_RESEARCH_ONLY')
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()
LABELS = {'RANGE_BREAK': 'Yatay aralık kırılımı', 'ASCENDING_TRIANGLE': 'Yükselen üçgen',
          'DESCENDING_TRIANGLE': 'Alçalan üçgen', 'SYMMETRIC_TRIANGLE': 'Simetrik üçgen',
          'FALLING_WEDGE': 'Düşen kama', 'RISING_WEDGE': 'Yükselen kama',
          'FLAG': 'Bayrak', 'PENNANT': 'Flama', 'DOUBLE_BOTTOM': 'Çift dip',
          'DOUBLE_TOP': 'Çift tepe', 'INVERSE_HEAD_SHOULDERS': 'Ters omuz baş omuz',
          'HEAD_SHOULDERS': 'Omuz baş omuz'}


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate(rows, step, now_ms=None, minimum=1, fresh=False):
    if len(rows) < minimum:
        raise ValueError('INSUFFICIENT_CLOSED_HISTORY')
    for i, b in enumerate(rows):
        t, end = b['open_ms'], b['close_ms']
        if not isinstance(t, int) or t % step or end != t + step - 1:
            raise ValueError('CANDLE_CLOCK_INVALID')
        if not all(finite(b[k]) and b[k] > 0 for k in ('open', 'high', 'low', 'close')):
            raise ValueError('CANDLE_PRICE_INVALID')
        if not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']:
            raise ValueError('CANDLE_OHLC_INVALID')
        if i and t != rows[i-1]['open_ms'] + step:
            raise ValueError('CANDLE_GAP')
        if now_ms is not None and (end >= now_ms or b.get('received_ms', end) > now_ms):
            raise ValueError('FUTURE_OR_OPEN_CANDLE')
    if fresh and (now_ms is None or not 0 < now_ms - rows[-1]['close_ms'] <= step):
        raise ValueError('STALE_CLOSED_HISTORY')


def atr(rows):
    values = [max(b['high']-b['low'], abs(b['high']-rows[i-1]['close']),
                  abs(b['low']-rows[i-1]['close'])) for i, b in enumerate(rows) if i]
    return sum(values[-CONFIG['atr_period']:]) / min(len(values), CONFIG['atr_period'])


def pivots(rows):
    """Right confirmation is explicit. A pivot cannot exist at its own timestamp."""
    out = []
    for i in range(2, len(rows)-2):
        for kind, key, sign in [('H', 'high', 1), ('L', 'low', -1)]:
            v = sign * rows[i][key]
            if all(v > sign*rows[j][key] for j in (i-2, i-1)) and all(
                    v >= sign*rows[j][key] for j in (i+1, i+2)):
                out.append(dict(i=i, kind=kind, price=rows[i][key],
                                open_ms=rows[i]['open_ms'], known_ms=rows[i+2]['close_ms']))
    return out


def trend(rows):
    ps = pivots(rows)
    highs = [p['price'] for p in ps if p['kind'] == 'H']
    lows = [p['price'] for p in ps if p['kind'] == 'L']
    if len(highs) < 2 or len(lows) < 2:
        return 'UNKNOWN'
    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return 'UP'
    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return 'DOWN'
    return 'RANGE'


def fibonacci(rows, direction):
    """Most recent confirmed directional leg. Anchors never use later pivots."""
    ps = pivots(rows)
    first, last = ('L', 'H') if direction == 'LONG' else ('H', 'L')
    for end in reversed(ps):
        starts = [p for p in ps if p['kind'] == first and p['i'] < end['i']]
        if end['kind'] != last or not starts:
            continue
        start = starts[-1]
        sign = 1 if direction == 'LONG' else -1
        distance = sign*(end['price']-start['price'])
        if distance <= 0:
            continue
        return dict(start=start, end=end, known_ms=max(start['known_ms'], end['known_ms']),
                    retracements={str(r): end['price']-sign*r*distance for r in (.382, .5, .618)},
                    projections={str(r): start['price']+sign*r*distance for r in (1.272, 1.618)},
                    decision_effect='NONE', note='0.5 is midpoint, not a Fibonacci ratio')
    return None


def geometry(direction, entry, stop, target):
    if direction not in ('LONG', 'SHORT') or not all(finite(v) and v > 0 for v in (entry, stop, target)):
        return dict(valid=False, reason='INVALID_PLAN_PRICE')
    sign = 1 if direction == 'LONG' else -1
    risk = 100*sign*(entry-stop)/entry
    gain = 100*sign*(target-entry)/entry
    cost = CONFIG['round_trip_cost_pct']
    rr = (gain-cost)/(risk+cost) if risk > 0 else None
    valid = risk > 0 and gain > cost
    return dict(valid=valid, risk_pct=risk, gain_pct=gain, rr_after_assumed_cost=rr,
                assumed_round_trip_cost_pct=cost, funding_included=False,
                reason='OK' if valid else 'STOP_OR_TARGET_ALREADY_PASSED')


def _line(points):
    x = sum(p['i'] for p in points)/len(points)
    y = sum(p['price'] for p in points)/len(points)
    denom = sum((p['i']-x)**2 for p in points)
    slope = sum((p['i']-x)*(p['price']-y) for p in points)/denom if denom else 0.
    return slope, y-slope*x


def _at(line, i):
    return line[0]*i+line[1]


def _shape_candidates(rows, a):
    """Both directions use the same geometry; their results stay separate."""
    prior = rows[:-1]
    n = len(rows)-1
    ps = pivots(prior)
    result = []
    # Fixed historical windows; the current trigger bar is never in the box.
    for width in (16, 32, 64):
        start = n-width
        hp = [p for p in ps if p['kind'] == 'H' and p['i'] >= start]
        lp = [p for p in ps if p['kind'] == 'L' and p['i'] >= start]
        if len(hp) < 2 or len(lp) < 2:
            continue
        upper, lower = _line(hp), _line(lp)
        # Pivot regression must describe actual candles, not a line through noise.
        if any(abs(p['price']-_at(upper, p['i'])) > .65*a for p in hp):
            continue
        if any(abs(p['price']-_at(lower, p['i'])) > .65*a for p in lp):
            continue
        if any(b['high'] > _at(upper, i)+.75*a or b['low'] < _at(lower, i)-.75*a
               for i, b in enumerate(prior) if i >= start):
            continue
        old_width = _at(upper, start)-_at(lower, start)
        new_width = _at(upper, n)-_at(lower, n)
        if old_width < 1.5*a or new_width < .3*a:
            continue
        u, l = upper[0]*width/a, lower[0]*width/a
        flat_u, flat_l = abs(u) <= .5, abs(l) <= .5
        converges = .15 <= new_width/old_width <= .8
        models = []
        if flat_u and flat_l:
            models.append('RANGE_BREAK')
        elif converges and flat_u and l > .5:
            models.append('ASCENDING_TRIANGLE')
        elif converges and flat_l and u < -.5:
            models.append('DESCENDING_TRIANGLE')
        elif converges and u < -.5 and l > .5:
            models.append('SYMMETRIC_TRIANGLE')
        elif converges and u < -.5 and l < -.5:
            models.append('FALLING_WEDGE')
        elif converges and u > .5 and l > .5:
            models.append('RISING_WEDGE')
        pole_start = max(0, start-8)
        pole = prior[start]['close']-prior[pole_start]['close']
        if abs(pole) >= 4*a and width <= 32:
            if converges and u < 0 < l:
                models.append('PENNANT')
            elif .7 <= new_width/old_width <= 1.3 and abs(u-l) <= .75 and pole*(u+l) <= 0:
                models.append('FLAG')
        for model in models:
            for direction, line, stop_pivot in [('LONG', upper, lp[-1]), ('SHORT', lower, hp[-1])]:
                if model in ('FLAG', 'PENNANT') and (pole > 0) != (direction == 'LONG'):
                    continue
                result.append(dict(model=model, direction=direction, start_i=start,
                                   boundary=line, stop_anchor=stop_pivot, height=old_width,
                                   anchors=hp+lp))
    # Reversal patterns: lows/highs in sequence and intervening neckline peaks.
    for direction, kind in [('LONG', 'L'), ('SHORT', 'H')]:
        sign = 1 if direction == 'LONG' else -1
        lows = [dict(p, oriented=sign*p['price']) for p in ps if p['kind'] == kind and p['i'] >= n-80]
        highs = [dict(p, oriented=sign*p['price']) for p in ps if p['kind'] != kind and p['i'] >= n-80]
        if len(lows) >= 2:
            one, two = lows[-2:]
            peaks = [p for p in highs if one['i'] < p['i'] < two['i']]
            if peaks and 4 <= two['i']-one['i'] <= 48 and abs(one['price']-two['price']) <= .75*a:
                peak = max(peaks, key=lambda p: p['oriented'])
                height = peak['oriented']-min(one['oriented'], two['oriented'])
                if height >= 1.5*a:
                    result.append(dict(model='DOUBLE_BOTTOM' if sign == 1 else 'DOUBLE_TOP',
                        direction=direction, start_i=one['i'], boundary=(0., peak['price']),
                        stop_anchor=two, height=height, anchors=[one, peak, two]))
        if len(lows) >= 3:
            left, head, right = lows[-3:]
            p1 = [p for p in highs if left['i'] < p['i'] < head['i']]
            p2 = [p for p in highs if head['i'] < p['i'] < right['i']]
            if (p1 and p2 and head['oriented'] < min(left['oriented'], right['oriented'])-.5*a
                    and abs(left['price']-right['price']) <= a and right['i']-left['i'] >= 8):
                peaks = [max(p1, key=lambda p: p['oriented']), max(p2, key=lambda p: p['oriented'])]
                neck = _line(peaks)
                height = sign*_at(neck, head['i'])-head['oriented']
                if height >= 1.5*a:
                    result.append(dict(model='INVERSE_HEAD_SHOULDERS' if sign == 1 else 'HEAD_SHOULDERS',
                        direction=direction, start_i=left['i'], boundary=neck,
                        stop_anchor=right, height=height, anchors=[left, peaks[0], head, peaks[1], right]))
    return result


def unbroken_levels(rows, direction, entry):
    sign = 1 if direction == 'LONG' else -1
    kind = 'H' if sign == 1 else 'L'
    result = []
    for p in pivots(rows):
        if p['kind'] != kind or sign*(p['price']-entry) <= 0:
            continue
        if any(sign*(b['close']-p['price']) > 0 for b in rows[p['i']+1:]):
            continue
        result.append(p['price'])
    return result


def _breakouts(symbol, interval, rows, context_rows, now_ms):
    if interval not in SETUP_INTERVALS:
        raise ValueError('UNSUPPORTED_SETUP_INTERVAL')
    step = INTERVALS[interval]
    validate(rows, step, now_ms, CONFIG['min_bars'], True)
    validate(context_rows, INTERVALS['4h'], now_ms, 30, True)
    a = atr(rows[:-1])
    if a <= 0:
        return []
    n, entry = len(rows)-1, rows[-1]['close']
    candidates = []
    for shape in _shape_candidates(rows, a):
        direction = shape['direction']; sign = 1 if direction == 'LONG' else -1
        boundary = shape['boundary']; trigger = _at(boundary, n)
        # Breakout must be new and the completed structure must still be intact.
        known_i = max(p['i']+2 for p in shape['anchors'])
        if any(sign*(rows[i]['close']-_at(boundary, i)) > CONFIG['break_buffer_atr']*a
               for i in range(known_i, n)):
            continue
        if sign*(entry-trigger) <= CONFIG['break_buffer_atr']*a:
            continue
        stop = shape['stop_anchor']['price']-sign*CONFIG['stop_buffer_atr']*a
        obstacles = unbroken_levels(rows, direction, entry)+unbroken_levels(context_rows, direction, entry)
        projected = trigger+sign*shape['height']
        # A closer obstacle is never skipped to make risk/reward look better.
        if obstacles:
            target = min(obstacles, key=lambda v: sign*(v-entry))
            if sign*(projected-entry) > 0 and sign*(projected-entry) < sign*(target-entry):
                target, target_source = projected, 'MEASURED_MOVE_PROJECTION'
            else:
                target_source = 'HISTORICAL_PIVOT'
        else:
            target, target_source = projected, 'MEASURED_MOVE_PROJECTION'
        g = geometry(direction, entry, stop, target)
        reasons = []
        if not g['valid']: reasons.append(g['reason'])
        if sign*(entry-trigger) > CONFIG['max_chase_atr']*a: reasons.append('PRICE_EXTENDED')
        if g.get('risk_pct', float('inf')) > CONFIG['max_risk_pct']: reasons.append('RISK_TOO_WIDE')
        if g.get('rr_after_assumed_cost') is None or g['rr_after_assumed_cost'] < CONFIG['min_reward_risk']:
            reasons.append('INSUFFICIENT_ROOM_AFTER_ASSUMED_COST')
        anchors = [{k: p[k] for k in ('kind', 'price', 'open_ms', 'known_ms')} for p in shape['anchors']]
        eid = hashlib.sha256(json.dumps([VERSION, symbol, interval, direction, shape['model'], anchors],
                                      sort_keys=True).encode()).hexdigest()
        volumes = [b.get('quote_volume') for b in rows[-25:-1]]
        baseline = median(volumes) if all(finite(v) and v > 0 for v in volumes) else None
        q, buy = rows[-1].get('quote_volume'), rows[-1].get('taker_buy_quote')
        supporting = dict(volume_multiple=q/baseline if baseline and finite(q) and q >= 0 else None,
                          taker_buy_share=buy/q if finite(q) and q > 0 and finite(buy) and 0 <= buy <= q else None,
                          baseline='prior_24_setup_candles_median', decision_effect='NONE')
        candidates.append(dict(version=VERSION, config_hash=CONFIG_HASH, id=eid, symbol=symbol,
            interval=interval, direction=direction, model=shape['model'], anchors=anchors,
            structure_start_ms=rows[shape['start_i']]['open_ms'], confirmation_ms=rows[-1]['close_ms'],
            expires_ms=rows[-1]['close_ms']+CONFIG['ready_minutes']*MINUTE,
            stage='REJECTED' if reasons else 'READY', reasons=reasons, research_only=True,
            entry_style='BREAK',
            entry=entry, trigger=trigger, stop=stop, target=target, target_source=target_source,
            nearest_obstacle=min(obstacles, key=lambda v: sign*(v-entry)) if obstacles else None,
            atr=a, geometry=g, context_4h=trend(context_rows), fibonacci=fibonacci(rows[:-1], direction), supporting=supporting,
            max_hold_hours=CONFIG['max_hold_hours'][interval], auxiliary_decision_effect='NONE'))
    return candidates


def assess(symbol, interval, rows, context_rows, now_ms):
    """Fresh breaks plus a separately specified, confirmed first retest.

    Retest uses a boundary frozen at the original break. Previous bar must touch
    and hold that level; current bar must close beyond the retest bar's extreme.
    The stop invalidates this retest, not the original full pattern. No threshold
    was loosened to meet a requested signal count or accuracy.
    """
    plans = _breakouts(symbol, interval, rows, context_rows, now_ms)
    sign_for = {'LONG': 1, 'SHORT': -1}
    current, retest = rows[-1], rows[-2]
    for ago in range(2, CONFIG['retest_lookback_bars']+1):
        prefix = rows[:-ago]
        if len(prefix) < CONFIG['min_bars']:
            continue
        historical_now = prefix[-1]['close_ms']+1
        historical_context = [b for b in context_rows if b['close_ms'] < historical_now]
        if len(historical_context) < 30:
            continue
        # Outer validation checks availability at NOW. This is a reconstruction
        # of a historical shape for a current retest, not a backdated signal.
        clean = [{k: v for k, v in b.items() if k != 'received_ms'} for b in prefix]
        clean_context = [{k: v for k, v in b.items() if k != 'received_ms'} for b in historical_context]
        origins = _breakouts(symbol, interval, clean, clean_context, historical_now)
        for p in origins:
            sign = sign_for[p['direction']]
            trigger, a = p['trigger'], p['atr']
            since = rows[len(prefix):-1]
            # Do not revive a failed original structure or an already completed
            # target. The current retest can wick below/above the frozen level.
            if any(sign*(b['close']-trigger) < -CONFIG['break_buffer_atr']*a or
                   sign*((b['low'] if sign == 1 else b['high'])-p['stop']) <= 0 or
                   sign*((b['high'] if sign == 1 else b['low'])-p['target']) >= 0 for b in since):
                continue
            touch = retest['low'] if sign == 1 else retest['high']
            confirmation = retest['high'] if sign == 1 else retest['low']
            if not (abs(touch-trigger) <= CONFIG['retest_touch_atr']*a and
                    sign*(retest['close']-trigger) >= 0 and sign*(current['close']-confirmation) > 0):
                continue
            stop = touch-sign*CONFIG['stop_buffer_atr']*a
            entry = current['close']
            obstacles = unbroken_levels(rows, p['direction'], entry)+unbroken_levels(context_rows, p['direction'], entry)
            targets = obstacles+[p['target']]
            target = min(targets, key=lambda v: sign*(v-entry))
            g = geometry(p['direction'], entry, stop, target)
            q, buy = current.get('quote_volume'), current.get('taker_buy_quote')
            vols = [b.get('quote_volume') for b in rows[-25:-1]]
            baseline = median(vols) if all(finite(v) and v > 0 for v in vols) else None
            supporting = dict(volume_multiple=q/baseline if baseline and finite(q) and q >= 0 else None,
                taker_buy_share=buy/q if finite(q) and q > 0 and finite(buy) and 0 <= buy <= q else None,
                baseline='prior_24_setup_candles_median', decision_effect='NONE')
            updated = dict(p, id=hashlib.sha256((p['id']+':RETEST:'+str(retest['open_ms'])).encode()).hexdigest(),
                           origin_id=p['id'], origin_confirmation_ms=p['confirmation_ms'],
                           origin_reconstructed=True, entry_style='RETEST', entry=entry, stop=stop,
                           target=target, target_source=p['target_source'] if target == p['target'] else 'HISTORICAL_PIVOT',
                           retest_bar_ms=retest['open_ms'], geometry=g, confirmation_ms=current['close_ms'],
                           expires_ms=current['close_ms']+CONFIG['ready_minutes']*MINUTE,
                           supporting=supporting, context_4h=trend(context_rows), fibonacci=fibonacci(rows[:-1], p['direction']))
            ok, _ = executable(updated, entry)
            updated.update(stage='READY' if ok else 'REJECTED', reasons=[] if ok else ['RETEST_GEOMETRY_INVALID'])
            plans.append(updated)
    return list({p['id']: p for p in plans}.values())


def executable(p, price):
    g = geometry(p['direction'], price, p['stop'], p['target'])
    sign = 1 if p['direction'] == 'LONG' else -1
    valid = (g['valid'] and g['risk_pct'] <= CONFIG['max_risk_pct'] and
             g['rr_after_assumed_cost'] >= CONFIG['min_reward_risk'] and
             CONFIG['break_buffer_atr']*p['atr'] < sign*(price-p['trigger']) <= CONFIG['max_chase_atr']*p['atr'])
    return valid, g


def message(p, price, context=None):
    """Compact card; show observed confirmation separately from entry conditions."""
    from datetime import datetime, timezone
    g = geometry(p['direction'], price, p['stop'], p['target'])
    f = lambda x: f'{x:.8g}'
    long = p['direction'] == 'LONG'
    sign = 1 if long else -1
    side = 'üstünde' if long else 'altında'
    dot = '🟢' if long else '🔴'
    interval = {'15m': '15 dk', '30m': '30 dk'}[p['interval']]
    trend_name = {'UP': 'yükseliş ↑', 'DOWN': 'düşüş ↓', 'RANGE': 'yatay ↔'}.get(p['context_4h'], 'belirsiz')
    retest = p.get('entry_style') == 'RETEST'
    limit = p['trigger']+sign*CONFIG['max_chase_atr']*p['atr']
    expiry = datetime.fromtimestamp(p['expires_ms']/1000, timezone.utc).strftime('%H:%M UTC')
    rows = [f'{dot} {p["symbol"]} · {p["direction"]} ADAYI | DENEME',
            f'📊 {interval} · {LABELS[p["model"]]}',
            '✅ '+('Geri test sonrası kapanış teyidi geldi.' if retest else 'İlk kırılım kapanışla teyit edildi.'),
            '',
            f'💰 Giriş referansı / şu an: {f(price)}',
            f'🔔 Beklenen: fiyat {f(p["trigger"])} {side} kalsın; giriş şartları korunsun.',
            f'🚫 Kovalama sınırı: {f(limit)}; '+('üstünden' if long else 'altından')+' takip etme.',
            f'🛑 SL: {f(p["stop"])} · mesafe %{g["risk_pct"]:.2f}',
            f'🎯 TP: {f(p["target"])} · alan %{g["gain_pct"]:.2f}',
            f'⚖️ Masraf sonrası ödül/risk: {g["rr_after_assumed_cost"]:.2f}R',
            f'⌛ Plan geçerliliği: {expiry} · fiyat değişirse R yeniden hesaplanır.',
            '',
            '📐 Hesap: '+('geri test ucu' if retest else 'formasyonun geçersizlik noktası')+' + 0,20 ATR tamponu (mum oynaklığı).',
            '🎯 Hedef dayanağı: '+('henüz aşılmamış fiyat engeli.' if p['target_source'] == 'HISTORICAL_PIVOT' else 'formasyon boyu tahmini; doğrulanmış destek/direnç değil.'),
            f'🧭 4 saatlik yapı: {trend_name} · ATR(14): {f(p["atr"])}']
    supporting = p.get('supporting', {})
    volume, buy = supporting.get('volume_multiple'), supporting.get('taker_buy_share')
    parts = []
    if finite(volume): parts.append(f'Hacim {volume:.2f}× (önceki 24 mum medyanına göre)')
    if finite(buy): parts.append(f'Alış %{100*buy:.0f} / satış %{100*(1-buy):.0f}')
    if parts: rows.append('📦 '+' · '.join(parts))
    fib = p.get('fibonacci')
    if fib:
        rows.append(f'📏 Fib dayanağı: {f(fib["start"]["price"])} → {f(fib["end"]["price"])}')
        rows.append('Fib: '+' · '.join(f'%{100*float(k):g} {f(v)}' for k,v in fib['retracements'].items() if k != '0.5'))
    else:
        rows.append('📏 Fib: uygun teyitli fiyat hareketi yok.')
    native = (context or {}).get('native') or {}
    values = []
    for key, label, fresh in [('oi_change_1h','OI (açık pozisyon) 1 sa','oi_fresh'),
                              ('funding_pct','Funding (taşıma oranı)','funding_fresh')]:
        value = native.get(key)
        values.append(f'{label}: %{value:.3g}' if finite(value) and native.get(fresh) else f'{label}: veri yok/eski')
    rows.append('ℹ️ '+' · '.join(values))
    rows += ['', f'📝 Deneme: ücret/kayma varsayımı %{CONFIG["round_trip_cost_pct"]:.2f}; funding hariç. Fib ve türevler destek bilgisidir.',
             f'Kağıt giriş: mesajdan sonraki ilk tam 1 dk açılışı; şartlar bozulursa iptal. SL/TP veya en çok {p["max_hold_hours"]} saatte kapanır.',
             f'Otomatik emir yok · başarı oranı ölçülmedi · #{p["id"][:10]}']
    return '\n'.join(rows)
