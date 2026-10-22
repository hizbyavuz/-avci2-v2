"""Closed-hour swing hypotheses. Pure functions; no orders or network I/O.

Thresholds are preregistered research assumptions, not estimated probabilities.
LONG and SHORT have distinct hypotheses. Old MAJOR rules remain untouched.
"""
import hashlib
import json
import math
import statistics

HOUR = 3_600_000
MINUTE = 60_000
VERSION = 'SWING_STRUCTURE_RESEARCH_V1_2026_10_22'
CONFIG = dict(history_hours=169, range_hours=72, min_quote_volume_24h=2_000_000,
              long_volume_multiple=3., short_volume_multiple=2.,
              long_buy_share=.50, short_buy_share=.45,
              max_move_from_origin_pct=25., stop_atr_buffer=.25,
              max_stop_pct=12., max_chase_atr=1., min_known_room_r=2.,
              trailing_arm_r=2., max_hold_hours=168,
              round_trip_cost_pct=.30, entry_valid_minutes=15,
              max_notices_per_day=6, reset_inside_hours=6)
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def check_bars(rows, period, now_ms=None):
    if not rows:
        raise ValueError('HISTORY_MISSING')
    for i, b in enumerate(rows):
        if (not all(finite(b.get(k)) and b[k] > 0 for k in ('open', 'high', 'low', 'close'))
                or not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']):
            raise ValueError('INVALID_OHLC')
        if b['open_ms'] % period or b['close_ms'] != b['open_ms'] + period - 1:
            raise ValueError('INVALID_BAR_TIME')
        if i and b['open_ms'] != rows[i-1]['open_ms'] + period:
            raise ValueError('DATA_GAP')
        if now_ms is not None and b['close_ms'] >= now_ms:
            raise ValueError('UNCLOSED_BAR')


def atr(rows, n=14):
    return statistics.mean(max(b['high']-b['low'], abs(b['high']-a['close']),
                               abs(b['low']-a['close'])) for a, b in zip(rows[-n-1:-1], rows[-n:]))


def pivots(rows, field):
    """A pivot is available only after two subsequent CLOSED bars."""
    out = []
    for i in range(2, len(rows)-2):
        v = rows[i][field]
        neighbors = [rows[j][field] for j in (i-2, i-1, i+1, i+2)]
        if (field == 'high' and v > max(neighbors)) or (field == 'low' and v < min(neighbors)):
            out.append(dict(price=v, at_ms=rows[i]['open_ms'], known_ms=rows[i+2]['close_ms'], index=i))
    return out


def four_hour(rows):
    groups = {}
    for b in rows:
        groups.setdefault(b['open_ms']//(4*HOUR), []).append(b)
    result = []
    for group in groups.values():
        if len(group) != 4 or group[0]['open_ms'] % (4*HOUR):
            continue
        result.append(dict(open_ms=group[0]['open_ms'], close_ms=group[-1]['close_ms'],
                           open=group[0]['open'], high=max(x['high'] for x in group),
                           low=min(x['low'] for x in group), close=group[-1]['close']))
    return result


def obstacle(rows, price, direction):
    bars = four_hour(rows)
    field = 'high' if direction == 'LONG' else 'low'
    sign = 1 if direction == 'LONG' else -1
    valid = []
    for p in pivots(bars, field):
        # This is an untouched level, not every historical pivot. Broken levels
        # may matter again, but that is a separate hypothesis, not this model.
        if sign*(p['price']-price) <= 0:
            continue
        if any(sign*(b[field]-p['price']) >= 0 for b in bars[p['index']+1:]):
            continue
        valid.append(p['price'])
    return min(valid, key=lambda v: abs(v-price)) if valid else None


def geometry(p, price):
    sign = 1 if p['direction'] == 'LONG' else -1
    risk = sign*(price-p['stop'])
    if risk <= 0 or risk/price*100 > CONFIG['max_stop_pct']:
        return 'INVALID_RISK'
    if sign*(price-p['trigger']) < 0:
        return 'BREAK_NOT_HELD'
    if sign*(price-p['trigger']) > CONFIG['max_chase_atr']*p['atr_1h']:
        return 'LATE_CHASE'
    origin_move = sign*(price/p['origin_price']-1)*100
    if origin_move > CONFIG['max_move_from_origin_pct']:
        return 'ALREADY_EXTENDED'
    level = p.get('obstacle')
    if level is not None and sign*(level-price)/risk < CONFIG['min_known_room_r']:
        return 'INSUFFICIENT_KNOWN_ROOM'
    return None


def assess(symbol, rows, now_ms, btc_rows=None):
    p = dict(version=VERSION, config_hash=CONFIG_HASH, symbol=symbol, observed_ms=now_ms,
             stage='REJECTED', direction=None, reason='NO_SETUP', research_only=True)
    try:
        if len(rows) < CONFIG['history_hours']:
            raise ValueError('HISTORY_MISSING_169H')
        rows = rows[-CONFIG['history_hours']:]
        check_bars(rows, HOUR, now_ms)
        if now_ms-rows[-1]['close_ms'] > CONFIG['entry_valid_minutes']*MINUTE:
            raise ValueError('STALE_CONFIRMATION')
        for b in rows:
            q, buy = b.get('quote_volume'), b.get('taker_buy_quote')
            if not finite(q) or q < 0 or not finite(buy) or not 0 <= buy <= q:
                raise ValueError('VOLUME_OR_TAKER_MISSING')
        last, prior = rows[-1], rows[-73:-1]
        q24 = sum(b['quote_volume'] for b in rows[-24:])
        p['quote_volume_24h'] = q24
        if q24 < CONFIG['min_quote_volume_24h']:
            raise ValueError('ILLIQUID')
        roof, floor = max(b['high'] for b in prior), min(b['low'] for b in prior)
        baseline = statistics.median(rows[-1-24*d]['quote_volume'] for d in range(1, 8))
        if baseline <= 0 or last['quote_volume'] <= 0:
            raise ValueError('VOLUME_BASELINE_UNAVAILABLE')
        vol = last['quote_volume']/baseline
        buy = last['taker_buy_quote']/last['quote_volume']
        price = last['close']
        direction = 'LONG' if price > roof else 'SHORT' if price < floor else None
        p.update(price=price, range_high=roof, range_low=floor, volume_multiple=vol,
                 buy_share=buy, confirmation_ms=last['close_ms'], atr_1h=atr(rows))
        if not direction:
            return p
        p.update(direction=direction, trigger=roof if direction == 'LONG' else floor,
                 model='BASE_BREAKOUT_LONG' if direction == 'LONG' else 'FAILED_BOUNCE_SHORT',
                 entry_valid_until_ms=last['close_ms']+CONFIG['entry_valid_minutes']*MINUTE)
        sign = 1 if direction == 'LONG' else -1
        p['origin_price'] = min(b['low'] for b in rows[:-1]) if sign == 1 else max(b['high'] for b in rows[:-1])
        p['move_before_signal_pct'] = sign*(price/p['origin_price']-1)*100
        if sign == 1:
            if vol < CONFIG['long_volume_multiple'] or buy <= CONFIG['long_buy_share']:
                raise ValueError('LONG_VOLUME_OR_BUY_PRESSURE_WEAK')
            supports = pivots(prior, 'low')
            anchor = supports[-1]['price'] if supports else floor
            p['stop'] = anchor-CONFIG['stop_atr_buffer']*p['atr_1h']
        else:
            highs = pivots(prior, 'high')
            if len(highs) < 2 or highs[-1]['price'] >= highs[-2]['price']:
                raise ValueError('NO_CONFIRMED_LOWER_HIGH')
            if vol < CONFIG['short_volume_multiple'] or buy >= CONFIG['short_buy_share']:
                raise ValueError('SHORT_VOLUME_OR_SELL_PRESSURE_WEAK')
            p['stop'] = highs[-1]['price']+CONFIG['stop_atr_buffer']*p['atr_1h']
        p['obstacle'] = obstacle(rows, price, direction)
        reason = geometry(p, price)
        if reason:
            raise ValueError(reason)
        risk = sign*(price-p['stop'])
        p.update(stage='READY', reason=p['model'], risk_pct=100*risk/price,
                 known_room_r=sign*(p['obstacle']-price)/risk if p['obstacle'] else None,
                 trailing_activation=price+sign*CONFIG['trailing_arm_r']*risk,
                 btc_return_24h_pct=None, relative_return_24h_pct=None)
        if btc_rows:
            try:
                check_bars(btc_rows[-25:], HOUR, now_ms)
                if len(btc_rows) < 25 or btc_rows[-1]['close_ms'] != last['close_ms']:
                    raise ValueError('BTC_ALIGNMENT')
                btc_ret=100*(btc_rows[-1]['close']/btc_rows[-25]['close']-1)
                p.update(btc_return_24h_pct=btc_ret,
                         relative_return_24h_pct=100*(price/rows[-25]['close']-1)-btc_ret)
            except ValueError:
                p['btc_context_issue']='MISSING_OR_UNALIGNED'
        return p
    except (ValueError, KeyError, TypeError) as exc:
        p.update(stage='REJECTED', reason=str(exc))
        return p


def rank(plans):
    # Transparent queue policy, not a probability of success. All unselected
    # plans remain in the research set, allowing the ranking to be evaluated.
    return sorted(plans, key=lambda p: (-p['volume_multiple'], p['risk_pct'], p['symbol']))


def message(p):
    side='🟢 LONG' if p['direction']=='LONG' else '🔴 SHORT'
    why=('72 saatlik aralıktan hacimli çıkış' if p['direction']=='LONG'
         else 'Daha düşük tepe sonrası 72 saatlik desteğin kaybı')
    room=(f"İlk bilinen engel: {p['obstacle']:.8g} · alan/risk {p['known_room_r']:.2f}R"
          if p.get('obstacle') else 'Üst yapıda doğrulanmış engel yok; ödül/risk tahmini yok.')
    return (f"{side} · {p['symbol']} · ARAŞTIRMA SİNYALİ\n{why}\n"
            f"Gözlenen fiyat: {p['price']:.8g} · kırılan seviye: {p['trigger']:.8g}\n"
            f"Yapı stopu: {p['stop']:.8g} · mesafe %{p['risk_pct']:.2f}\n{room}\n"
            f"Hacim: aynı saatin 7 günlük medyanının {p['volume_multiple']:.2f} katı\n"
            f"Agresif alış payı: %{100*p['buy_share']:.1f}\n"
            f"Son 7 günlük {'dipten yükseliş' if p['direction']=='LONG' else 'tepeden düşüş'}: "
            f"%{p['move_before_signal_pct']:.1f}\n"
            f"Kağıt plan: +2R sonrası kapanmış 4 saatlik yapıyla takip; en fazla 7 gün.\n"
            '2R bir takip eşiğidir, fiyat hedefi değildir. Eşikler henüz kârlılık kanıtı taşımıyor.')
