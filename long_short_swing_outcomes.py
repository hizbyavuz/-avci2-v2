"""Versioned minute replay: discovery, executable paper plan, and costs separate."""
import copy
from long_short_swing import CONFIG, HOUR, MINUTE, check_bars, finite

VERSION='SWING_OUTCOME_V1_2026_10_22'
THRESHOLDS=(5, 10, 20, 30, 50, 100)


def initial(p, reference_ms, entry):
    if reference_ms % MINUTE or not finite(entry) or entry <= 0:
        raise ValueError('ENTRY_MUST_BE_FULL_MINUTE_OPEN')
    sign=1 if p['direction']=='LONG' else -1
    if sign*(entry-p['stop']) <= 0:
        raise ValueError('ENTRY_ALREADY_INVALID')
    risk=sign*(entry-p['stop'])
    return dict(version=VERSION, entry_ms=reference_ms, entry=entry, cursor_ms=reference_ms,
                expires_ms=reference_ms+CONFIG['max_hold_hours']*HOUR,
                stop=p['stop'], initial_risk=risk, trailing_activation=entry+sign*CONFIG['trailing_arm_r']*risk,
                trailing_armed=False, block=None, exit=None, missing_ms=None,
                raw_mfe_pct=0., raw_mae_pct=0., raw_reach={},
                in_plan_mfe_lower_pct=0., in_plan_mfe_upper_pct=0., in_plan_reach={},
                in_plan_mae_pct=0., ambiguous_stop_minutes=0, checkpoints={}, last_close=entry)


def advance(p, state, rows):
    g=copy.deepcopy(state);sign=1 if p['direction']=='LONG' else -1;entry=g['entry']
    for b in rows:
        t=b['open_ms']
        if t >= g['expires_ms']:
            break
        if t != g['cursor_ms']:
            g['missing_ms']=g['cursor_ms'];break
        try:
            check_bars([b], MINUTE)
        except (ValueError, KeyError, TypeError):
            g['missing_ms']=t;break
        g['missing_ms']=None
        favorable=100*sign*((b['high'] if sign==1 else b['low'])/entry-1)
        adverse=-100*sign*((b['low'] if sign==1 else b['high'])/entry-1)
        g['raw_mfe_pct']=max(g['raw_mfe_pct'], favorable)
        g['raw_mae_pct']=max(g['raw_mae_pct'], adverse)
        for x in THRESHOLDS:
            if favorable >= x:g['raw_reach'].setdefault(str(x), t)
        if g['exit'] is None:
            stop_hit=b['low'] <= g['stop'] if sign==1 else b['high'] >= g['stop']
            # On the stop minute we know the open was reached before the exit,
            # but do not know whether the favorable wick preceded it.
            lower=max(0., 100*sign*(b['open']/entry-1)) if stop_hit else max(0., favorable)
            g['in_plan_mfe_lower_pct']=max(g['in_plan_mfe_lower_pct'], lower)
            g['in_plan_mfe_upper_pct']=max(g['in_plan_mfe_upper_pct'], favorable)
            exit_price=(min(b['open'],g['stop']) if sign==1 else max(b['open'],g['stop'])) if stop_hit else None
            plan_adverse=-100*sign*((exit_price if stop_hit else (b['low'] if sign==1 else b['high']))/entry-1)
            g['in_plan_mae_pct']=max(g['in_plan_mae_pct'], plan_adverse)
            for x in THRESHOLDS:
                if lower >= x:g['in_plan_reach'].setdefault(str(x), t)
            if stop_hit:
                g['ambiguous_stop_minutes']+=int(favorable > lower)
                g['exit']=dict(kind='STOP', at_ms=t, time_upper_ms=b['close_ms'],
                               price=exit_price, gross_pct=100*sign*(exit_price/entry-1))
            else:
                if sign*((b['high'] if sign==1 else b['low'])-g['trailing_activation']) >= 0:
                    g['trailing_armed']=True
                if t % (4*HOUR)==0:
                    g['block']=dict(low=b['low'], high=b['high'], count=0)
                block=g['block']
                if block:
                    block.update(low=min(block['low'],b['low']), high=max(block['high'],b['high']), count=block['count']+1)
                if (t+MINUTE) % (4*HOUR)==0:
                    if block and block['count']==240 and g['trailing_armed']:
                        candidate=block['low']-CONFIG['stop_atr_buffer']*p['atr_1h'] if sign==1 else block['high']+CONFIG['stop_atr_buffer']*p['atr_1h']
                        g['stop']=max(g['stop'],candidate) if sign==1 else min(g['stop'],candidate)
                    g['block']=None
                if t+MINUTE >= g['expires_ms']:
                    g['exit']=dict(kind='TIMEOUT', at_ms=b['close_ms'], time_upper_ms=b['close_ms'],
                                   price=b['close'], gross_pct=100*sign*(b['close']/entry-1))
        g.update(cursor_ms=t+MINUTE, last_close=b['close'])
        elapsed=(g['cursor_ms']-g['entry_ms'])/HOUR
        if elapsed in (24,72,168):
            gross=g['exit']['gross_pct'] if g['exit'] else 100*sign*(b['close']/entry-1)
            g['checkpoints'][str(int(elapsed))]=dict(mark_to_horizon_gross_pct=gross,
                fee_slippage_only_pct=gross-CONFIG['round_trip_cost_pct'],
                open_plan=g['exit'] is None, raw_mfe_pct=g['raw_mfe_pct'],
                in_plan_mfe_lower_pct=g['in_plan_mfe_lower_pct'])
    return g


def result(p, g, funding=None, funding_coverage=None):
    """Funding events need settlement time, rate, and mark price, plus coverage.

    funding_coverage=(start,end) certifies a COMPLETE source extract, not merely
    min/max timestamps of rows. No data does not mean zero funding. Return is
    on initial notional (no leverage); quantities remain fixed. Ambiguous exit
    minute settlements are charged only if they cost us in the conservative view.
    """
    complete=g['cursor_ms'] >= g['expires_ms'] and g['missing_ms'] is None
    exit_=g.get('exit');sign=1 if p['direction']=='LONG' else -1
    fee_only=exit_['gross_pct']-CONFIG['round_trip_cost_pct'] if exit_ else None
    net=None;funding_cost=None
    end=exit_['time_upper_ms'] if exit_ else g['cursor_ms']
    known=bool(funding_coverage and funding_coverage[0] <= g['entry_ms'] and funding_coverage[1] >= end and funding is not None)
    if known:
        funding_cost=0.
        seen=set()
        for f in funding:
            if not all(finite(f.get(k)) for k in ('time_ms','rate','mark_price')) or f['mark_price'] <= 0 or f['time_ms'] in seen:
                raise ValueError('INVALID_OR_DUPLICATE_FUNDING')
            seen.add(f['time_ms'])
            if g['entry_ms'] <= f['time_ms'] <= end:
                cost=100*sign*f['rate']*f['mark_price']/g['entry']
                ambiguous=exit_ and exit_['kind']=='STOP' and exit_['at_ms'] <= f['time_ms'] <= end
                funding_cost+=max(0.,cost) if ambiguous else cost
        if fee_only is not None:net=fee_only-funding_cost
    # A missing bar can gap beyond a stop. This sensitivity is explicitly an
    # assumption, not a mathematical worst case or a measured trade result.
    missing_sensitivity=(min(fee_only if fee_only is not None else 0.,
                             -100*g['initial_risk']/g['entry']-CONFIG['round_trip_cost_pct'])
                         if g['missing_ms'] is not None and exit_ is None else fee_only)
    return dict(version=VERSION, status='COMPLETE' if complete else 'DATA_GAP' if g['missing_ms'] is not None else 'PENDING',
                plan_closed=exit_ is not None, exit=exit_, fee_slippage_only_pct=fee_only,
                full_net_pct=net, funding_cost_pct=funding_cost, funding_status='COMPLETE' if known else 'UNKNOWN',
                missing_assumed_stop_fee_only_pct=missing_sensitivity,
                raw_mfe_pct=g['raw_mfe_pct'], raw_reach=g['raw_reach'],
                in_plan_mfe_lower_pct=g['in_plan_mfe_lower_pct'],
                in_plan_mfe_upper_pct=g['in_plan_mfe_upper_pct'], in_plan_reach=g['in_plan_reach'],
                in_plan_mae_pct=g['in_plan_mae_pct'], checkpoints=g['checkpoints'])
