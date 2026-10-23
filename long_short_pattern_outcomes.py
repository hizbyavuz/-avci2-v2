"""One fixed-quantity paper plan: first TP or SL, otherwise time expiry.

Unknown paths stay unknown. Raw subsequent price movement is deliberately
separate from the executable plan; fees are assumptions and funding needs an
explicit complete settlement extract before a full-net result can exist.
"""
from copy import deepcopy
from long_short_patterns import CONFIG, HOUR, MINUTE, validate, executable, finite

VERSION = 'PATTERN_OUTCOME_V1_2026_10_23'


def initial(p, entry_ms, price):
    ok, g = executable(p, price)
    if entry_ms % MINUTE or entry_ms > p['expires_ms'] or not ok:
        raise ValueError('ENTRY_EXPIRED_OR_GEOMETRY_INVALID')
    return dict(version=VERSION, entry_ms=entry_ms, entry=price, geometry=g,
                cursor_ms=entry_ms, expires_ms=entry_ms+p['max_hold_hours']*HOUR,
                exit=None, gap_ms=None, raw_mfe_pct=0., raw_mae_pct=0.,
                plan_mfe_lower_pct=0., plan_mfe_upper_pct=0., last_close=price,
                checkpoints={}, raw_reach={}, plan_reach={})


def advance(p, state, rows):
    s = deepcopy(state)
    sign = 1 if p['direction'] == 'LONG' else -1
    ret = lambda price: 100*sign*(price/s['entry']-1)
    for b in rows:
        t = b['open_ms']
        if t >= s['expires_ms']:
            break
        if t != s['cursor_ms']:
            s['gap_ms'] = s['cursor_ms']; break
        try:
            validate([b], MINUTE)
        except (KeyError, ValueError, TypeError):
            s['gap_ms'] = t; break
        s['gap_ms'] = None
        favorable = b['high'] if sign == 1 else b['low']
        adverse = b['low'] if sign == 1 else b['high']
        s['raw_mfe_pct'] = max(s['raw_mfe_pct'], ret(favorable))
        s['raw_mae_pct'] = max(s['raw_mae_pct'], -ret(adverse))
        for threshold in (3, 5, 10, 20, 30, 50, 100):
            if ret(favorable) >= threshold:
                s['raw_reach'].setdefault(str(threshold), t)
        if s['exit'] is None:
            stop_hit = sign*(adverse-p['stop']) <= 0
            target_hit = sign*(favorable-p['target']) >= 0
            # At a gapped open the ordering is known; inside a minute stop first.
            open_target = sign*(b['open']-p['target']) >= 0
            if open_target:
                kind, price = 'TP', p['target']  # conservative limit fill, no favorable gap bonus
                lower = max(0., ret(price))
            elif stop_hit:
                kind = 'SL'
                price = min(b['open'], p['stop']) if sign == 1 else max(b['open'], p['stop'])
                lower = max(0., ret(b['open']))
            elif target_hit:
                kind, price = 'TP', p['target']
                lower = max(0., ret(price))
            else:
                kind, price, lower = None, None, max(0., ret(favorable))
            s['plan_mfe_lower_pct'] = max(s['plan_mfe_lower_pct'], lower)
            upper = max(0., ret(favorable)) if stop_hit and not open_target else lower
            s['plan_mfe_upper_pct'] = max(s['plan_mfe_upper_pct'], upper)
            for threshold in (3, 5, 10, 20, 30, 50, 100):
                if lower >= threshold:
                    s['plan_reach'].setdefault(str(threshold), t)
            if kind:
                s['exit'] = dict(kind=kind, price=price, at_ms=t,
                                 time_upper_ms=t if open_target or sign*(b['open']-p['stop']) <= 0 else b['close_ms'],
                                 gross_pct=ret(price), same_minute_both=stop_hit and target_hit and not open_target)
            elif t+MINUTE == s['expires_ms']:
                s['exit'] = dict(kind='TIMEOUT', price=b['close'], at_ms=b['close_ms'],
                                 time_upper_ms=b['close_ms'], gross_pct=ret(b['close']), same_minute_both=False)
        s.update(cursor_ms=t+MINUTE, last_close=b['close'])
        hours = (s['cursor_ms']-s['entry_ms'])/HOUR
        if hours in (1, 4, 24, 72, 168):
            gross = s['exit']['gross_pct'] if s['exit'] else ret(b['close'])
            s['checkpoints'][str(int(hours))] = dict(mark_or_realized_gross_pct=gross,
                fee_only_pct=gross-CONFIG['round_trip_cost_pct'], plan_open=s['exit'] is None)
    return s


def result(p, s, funding=None, coverage=None):
    ex = s['exit']
    fee_only = ex['gross_pct']-CONFIG['round_trip_cost_pct'] if ex else None
    end = ex['time_upper_ms'] if ex else s['cursor_ms']
    complete_funding = (funding is not None and coverage is not None and
                        coverage[0] <= s['entry_ms'] and coverage[1] >= end)
    cost, seen = None, set()
    if complete_funding:
        cost = 0.
        sign = 1 if p['direction'] == 'LONG' else -1
        for f in funding:
            if (not all(finite(f.get(k)) for k in ('time_ms', 'rate', 'mark_price')) or
                    f['mark_price'] <= 0 or f['time_ms'] in seen):
                raise ValueError('INVALID_FUNDING_LEDGER')
            seen.add(f['time_ms'])
            if s['entry_ms'] <= f['time_ms'] <= end:
                amount = 100*sign*f['rate']*f['mark_price']/s['entry']
                ambiguous = ex and ex['at_ms'] <= f['time_ms'] <= end
                cost += max(0., amount) if ambiguous else amount
    return dict(version=VERSION, plan_state='CLOSED' if ex else 'UNKNOWN_GAP' if s['gap_ms'] else 'OPEN',
                observation_complete=s['cursor_ms'] >= s['expires_ms'],
                missing_ms=s['gap_ms'], exit=ex, fee_slippage_only_pct=fee_only,
                funding_status='COMPLETE' if complete_funding else 'UNKNOWN',
                funding_cost_pct=cost, full_net_pct=fee_only-cost if fee_only is not None and cost is not None else None,
                raw_mfe_pct=s['raw_mfe_pct'], plan_mfe_lower_pct=s['plan_mfe_lower_pct'],
                plan_mfe_upper_pct=s['plan_mfe_upper_pct'], raw_reach=s['raw_reach'],
                plan_reach=s['plan_reach'], checkpoints=s['checkpoints'])
