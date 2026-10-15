"""Versioned chart-first manual decision support. No scores or order execution.

Rules are declared hypotheses, not calibrated win probabilities. Daily candles
are exact UTC aggregates of six verified, closed Binance Futures 4h candles.
Auxiliary derivatives remain context; V3 reversals require native hourly flow and price agreement.
"""
import hashlib
import json
import math
import os
from pathlib import Path
from long_short_chart_setups import setups, level_watch, geometry, quality_limit
from long_short_chart_flow import reversal_reason, flow_note

VERSION = 'CHART_FIRST_STRUCTURE_V4_2026_10_15'
CONFIG = dict(target_timeframes=('15m','1h','4h','1d'), pivot_confirmation_bars=2, daily_min_bars=20,
              approach_atr15=1.0, chase_atr5=1.0, level_tolerance_atr5=0.10,
              candle_grace_ms=20000, quote_max_age_ms=5000,
              min_quote_volume=25000000, round_trip_cost_pct=0.30,
              max_initial_notices_per_cycle=3, cooldown_ms=3600000,
              horizon_minutes=180, min_net_reward_risk=1.0,
              compression_bars=6, compression_range_ratio=0.80,
              compression_width_atr=2.5, stop_buffer_atr=0.10,
              pullback_lookback_bars=24, pullback_min_atr=0.5,
              pullback_turn_max_bars=4, reversal_flow_window_minutes=60,
              reversal_flow_majority=0.5, reversal_price_response_min_pct=0.0)
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()
STRATEGY_ID = hashlib.sha256((CONFIG_HASH+VERSION).encode()+Path(__file__).read_bytes()+
    Path(__file__).with_name('long_short_chart_first_worker.py').read_bytes()+
    Path(__file__).with_name('long_short_chart_setups.py').read_bytes()+
    Path(__file__).with_name('long_short_chart_outcomes.py').read_bytes()+
    Path(__file__).with_name('long_short_chart_flow.py').read_bytes()+
    Path(__file__).with_name('long_short_chart_lifecycle.py').read_bytes()+
    Path(__file__).with_name('long_short_chart_dispatch_audit.py').read_bytes()).hexdigest()
PERIODS = {'5m':300000, '15m':900000, '1h':3600000, '4h':14400000, '1d':86400000}


def enabled():
    return os.getenv('LS_CHART_FIRST_V1', '0') == '1'


def daily_from_4h(rows):
    groups = {}
    for r in rows:
        groups.setdefault(int(r[0]) // PERIODS['1d'], []).append(r)
    daily = []
    for day, bars in sorted(groups.items()):
        bars = sorted(bars, key=lambda r:r[0])
        start = day * PERIODS['1d']
        if [int(r[0]) for r in bars] != [start+i*PERIODS['4h'] for i in range(6)]:
            continue
        if any(int(r[6]) != int(r[0])+PERIODS['4h']-1 for r in bars):
            continue
        daily.append([start, float(bars[0][1]), max(float(r[2]) for r in bars),
                      min(float(r[3]) for r in bars), float(bars[-1][4]),
                      sum(float(r[5]) for r in bars), start+PERIODS['1d']-1])
    return daily


def pivots(rows):
    highs, lows = [], []
    for i in range(2, len(rows)-2):
        high, low = float(rows[i][2]), float(rows[i][3])
        if high > max(float(r[2]) for r in rows[i-2:i]) and high >= max(float(r[2]) for r in rows[i+1:i+3]):
            highs.append((int(rows[i][0]), high))
        if low < min(float(r[3]) for r in rows[i-2:i]) and low <= min(float(r[3]) for r in rows[i+1:i+3]):
            lows.append((int(rows[i][0]), low))
    return highs, lows


def describe(rows):
    highs, lows = pivots(rows)
    close = float(rows[-1][4])
    high = highs[-1][1] if highs else None
    low = lows[-1][1] if lows else None
    direction = 'RANGE'
    if high is not None and close > high:
        direction = 'UP'
    elif low is not None and close < low:
        direction = 'DOWN'
    elif len(highs)>=2 and len(lows)>=2:
        if highs[-1][1]>highs[-2][1] and lows[-1][1]>lows[-2][1]:direction='UP'
        elif highs[-1][1]<highs[-2][1] and lows[-1][1]<lows[-2][1]:direction='DOWN'
    elif len(rows)>=20:
        older,recent=rows[-20:-10],rows[-10:]
        if max(r[2] for r in recent)>max(r[2] for r in older) and min(r[3] for r in recent)>min(r[3] for r in older):direction='UP'
        elif max(r[2] for r in recent)<max(r[2] for r in older) and min(r[3] for r in recent)<min(r[3] for r in older):direction='DOWN'
    return dict(direction=direction, high=high, low=low, highs=highs, lows=lows,
                close=close, close_ms=int(rows[-1][6]), bars=len(rows))


def average_range(rows):
    pairs=list(zip(rows[-15:-1],rows[-14:]))
    return sum(max(float(b[2])-float(b[3]), abs(float(b[2])-float(a[4])),
                   abs(float(b[3])-float(a[4]))) for a,b in pairs)/len(pairs)


def volume_context(rows):
    base=sum(float(r[5]) for r in rows[-21:-1])/20
    return round(float(rows[-1][5])/base, 2) if base>0 else None


def supporting_data(evidence):
    e=evidence or {}
    values={}
    for field, fresh in (('oi_change_1h','oi_fresh'),('taker_ratio','taker_fresh'),
                         ('funding_pct','funding_fresh'),('long_short_ratio','ls_fresh')):
        value=e.get(field)
        values[field]=float(value) if (e.get(fresh) and isinstance(value,(int,float))
            and not isinstance(value,bool) and math.isfinite(value)) else None
    return values


def first_obstacle(frames, direction, trigger, atr5):
    sign=1 if direction=='LONG' else -1
    levels=[]
    for tf,rows in frames.items():
        highs,lows=pivots(rows)
        for stamp,level in (highs if sign==1 else lows):
            if sign*(level-trigger)>CONFIG['level_tolerance_atr5']*atr5:
                levels.append((sign*(level-trigger),level,tf,stamp))
    levels.sort()
    return (levels[0][1],levels[0][2]) if levels else (None,None)


def assess(symbol, frames, price, quote_volume, now_ms, evidence=None, flow=None, comparison_v3=False):
    out=dict(version=VERSION,config_hash=CONFIG_HASH,symbol=symbol,observed_ms=now_ms,
             price=price, direction='NONE', stage='REJECTED', reason=None,
             context={}, support=supporting_data(evidence), warnings=[])
    out['strategy_id']=STRATEGY_ID
    out['target_policy']='ALL_TIMEFRAMES_V3' if comparison_v3 else 'STRUCTURAL_15M_PLUS_V4'
    out['outcome_schema']='PARTIAL_MINUTE_V2'
    out['cost_assumption_pct']=CONFIG['round_trip_cost_pct']
    out['hourly_flow']=flow or {'status':'UNAVAILABLE'}
    def reject(reason):
        out['reason']=reason
        return out
    if not math.isfinite(float(quote_volume)) or quote_volume<CONFIG['min_quote_volume']:
        return reject('LIQUIDITY_FLOOR')
    if not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:
        return reject('FRESH_PRICE_UNAVAILABLE')
    frames=dict(frames)
    for tf in ('5m','15m','1h','4h'):
        rows=frames.get(tf) or []
        if len(rows)<30:return reject('CHART_HISTORY_MISSING_'+tf)
        period=PERIODS[tf]
        if not 250<=now_ms-int(rows[-1][6])<=period+CONFIG['candle_grace_ms']:
            return reject('CHART_STALE_'+tf)
        if any(int(b[0])-int(a[0])!=period for a,b in zip(rows,rows[1:])):
            return reject('CHART_GAP_'+tf)
        for r in rows:
            if (int(r[6])!=int(r[0])+period-1 or
                not all(math.isfinite(float(v)) and float(v)>0 for v in r[1:5]) or
                not float(r[3])<=min(float(r[1]),float(r[4]))<=max(float(r[1]),float(r[4]))<=float(r[2])):
                return reject('CHART_INVALID_'+tf)
    frames['1d']=daily_from_4h(frames['4h'])
    daily=frames['1d']
    if len(daily)<CONFIG['daily_min_bars']:return reject('DAILY_CONTEXT_MISSING')
    if now_ms-int(daily[-1][6])>PERIODS['1d']+CONFIG['candle_grace_ms']:
        return reject('DAILY_CONTEXT_STALE')
    context={tf:describe(rows) for tf,rows in frames.items()}
    # Persist compact context, while all raw candles remain in their native DB.
    out['context']={tf:{k:v for k,v in c.items() if k not in ('highs','lows')}
                    for tf,c in context.items()}
    out['daily_source']='UTC_AGGREGATE_OF_CLOSED_BINANCE_FUTURES_4H'
    out['volume_5m_multiple']=volume_context(frames['5m'])
    out['volume_15m_multiple']=volume_context(frames['15m'])
    one=context['1h'];four=context['4h'];local=context['5m'];mid=context['15m']
    if one['direction']=='RANGE':return reject('HOURLY_STRUCTURE_UNCLEAR')
    direction='LONG' if one['direction']=='UP' else 'SHORT'
    sign=1 if direction=='LONG' else -1
    out['direction']=direction
    opposing='DOWN' if sign==1 else 'UP'
    scenario='CONTINUATION'
    if four['direction']==opposing:
        scenario='REVERSAL_ATTEMPT'
        out['scenario']=scenario
        out['warnings'].append('Ana yöne ters, kısa vadeli dönüş adayı; 4 saatlik yön değişimi doğrulanmadı.')
        reason=reversal_reason(out['hourly_flow'],direction)
        if reason:return reject(reason)
    if context['1d']['direction']==opposing:
        out['warnings'].append('Günlük yön bu fikre ters; uzun vadeli yön değişimi doğrulanmış değil.')
    if mid['direction']==opposing:
        out['warnings'].append('15 dakikalık hareket hâlâ ters yönde; kısa dönüşün başarısız olma riski var.')
    out['scenario']=scenario
    patterns=setups(frames['5m'], direction, local['highs'], local['lows'], CONFIG)
    if not comparison_v3:
        level=level_watch(frames['5m'],direction,local['highs'],local['lows'],CONFIG)
        if level is not None:patterns.append(level)
    if not patterns:return reject('NO_DEFINED_SETUP')
    evaluated=[assess_pattern(dict(out), pattern, frames, price, now_ms) for pattern in patterns]
    # Prefer a fresh executable hypothesis to an unconfirmed watch. Within a
    # stage retain declared model order, never rank by later market outcomes.
    selected=next((p for stage in ('READY','WATCH') for p in evaluated if p['stage']==stage), evaluated[0])
    selected['pattern_evaluations']=[dict(model=p['model'], stage=p['stage'], reason=p['reason'],
                                         trigger=p['trigger'], stop=p['stop']) for p in evaluated]
    return selected


def assess_pattern(out, pattern, frames, price, now_ms):
    direction=out['direction'];sign=1 if direction=='LONG' else -1
    symbol=out['symbol']
    def reject(reason):
        out['reason']=reason
        return out
    out.update(pattern)
    trigger,stop=pattern['trigger'],pattern['stop']
    if sign*(trigger-stop)<=0 or sign*(price-stop)<=0:return reject('LOCAL_IDEA_INVALIDATED')
    a5=average_range(frames['5m']);a15=average_range(frames['15m'])
    if a5<=0 or a15<=0:return reject('INVALID_RANGE')
    out.update(atr5=a5,atr15=a15)
    if sign*(price-trigger)>CONFIG['chase_atr5']*a5:return reject('ENTRY_ALREADY_EXTENDED')
    if sign*(trigger-price)>CONFIG['approach_atr15']*a15:return reject('ENTRY_NOT_NEAR')
    # 5m controls execution timing; 15m+ supplies the first structural target.
    # Keep the nearer 5m hurdle visible, never relabel it as already crossed.
    # Anchoring to the trigger still rejects a crossed structural target.
    target_frames=frames if out['target_policy']=='ALL_TIMEFRAMES_V3' else {
        tf:frames[tf] for tf in CONFIG['target_timeframes']}
    obstacle,obstacle_tf=first_obstacle(target_frames,direction,trigger,a5)
    micro,_=first_obstacle({'5m':frames['5m']},direction,trigger,a5)
    out['intermediate_level']=micro if (micro is not None and obstacle is not None and
        sign*(micro-trigger)>0 and sign*(obstacle-micro)>0) else None
    out['obstacle']=obstacle
    out['obstacle_timeframe']=obstacle_tf
    entry=max(price,trigger) if sign==1 else min(price,trigger)
    g=geometry(direction,entry,stop,obstacle,CONFIG['round_trip_cost_pct'])
    out['geometry']=g
    out['room_pct']=g.get('gross_room_pct')
    if obstacle is None:return reject('FIRST_OBSTACLE_UNKNOWN')
    if not g['valid']:return reject('OBSTACLE_WITHIN_COST_ALLOWANCE')
    out['reference_reward_risk']=g['gross_room_pct']/g['invalidation_pct']
    if g['net_reward_risk']+1e-10<CONFIG['min_net_reward_risk']:
        return reject('NET_REWARD_BELOW_STRUCTURAL_RISK')
    ready=pattern['model_ready'] and sign*(price-trigger)>=0
    # The dispatcher has always required a <=75s-old confirmation. The scan
    # must not advertise an already expired break as READY for another 5m.
    if pattern['model_ready'] and not 250<=now_ms-int(pattern['confirmation_ms'])<=75000:
        return reject('CONFIRMATION_WINDOW_EXPIRED')
    entry_limit=trigger+sign*CONFIG['chase_atr5']*a5
    boundary=quality_limit(direction,stop,obstacle,CONFIG['round_trip_cost_pct'],
                           CONFIG['min_net_reward_risk'])
    entry_limit=min(entry_limit,boundary) if sign==1 else max(entry_limit,boundary)
    out.update(stage='READY' if ready else 'WATCH',reason='OK',entry_limit=entry_limit)
    out['setup_key']=hashlib.sha256(json.dumps([STRATEGY_ID,symbol,direction,pattern['model'],pattern['model_anchor_ms'],trigger,stop],
                                              separators=(',',':')).encode()).hexdigest()
    return out


def support_notes(p):
    """Interpret auxiliary observations without pretending to know trader intent."""
    s=p.get('support') or {};side=p['direction'];notes=[]
    flow=s.get('taker_ratio')
    if flow is not None:
        aligned=flow>1 if side=='LONG' else flow<1
        notes.append('Agresif alış ve satış hacmi dengede.' if flow==1 else
                     'Agresif işlem akışı grafik yönünü '+('destekliyor.' if aligned else 'henüz desteklemiyor.'))
    oi=s.get('oi_change_1h')
    if oi is not None:
        notes.append('Açık pozisyon miktarı değişmedi.' if oi==0 else
                     'Açık pozisyon miktarı '+('artıyor; bu tek başına long/short ayrımı yapmaz.' if oi>0 else 'azalıyor; kapanan pozisyonlar hareketi etkiliyor olabilir.'))
    ls=s.get('long_short_ratio');fund=s.get('funding_pct')
    if ls is not None and fund is not None:
        if ls>2 and fund>0:notes.append('LONG hesapları kalabalık ve taşıma oranı pozitif; ters hareket riski dikkate alınmalı.')
        elif ls<.5 and fund<0:notes.append('SHORT hesapları kalabalık ve taşıma oranı negatif; ters hareket riski dikkate alınmalı.')
    return notes


from long_short_chart_messages import message
