"""Two preregistered SHORT hypotheses; pure closed-hour decisions, no orders."""
import hashlib
import json
import statistics

from long_short_swing import HOUR,MINUTE,check_bars,finite,atr,pivots,obstacle

VERSION='SHORT_RETEST_RESEARCH_V2_2026_10_22'
MODELS=('FAILED_BREAKOUT_SHORT','BEAR_RETEST_SHORT')
CONFIG=dict(history_hours=169,failed_breakout_range_hours=72,bear_range_hours=24,
            break_max_age_hours=24,reentry_max_hours=6,retest_max_age_hours=12,
            retest_tolerance_atr=.25,stop_atr_buffer=.25,max_chase_atr=1.,
            max_stop_pct=12.,max_move_from_origin_pct=25.,min_quote_volume_24h=2_000_000,
            max_buy_share_3h=.5,min_known_net_room_r=2.,round_trip_cost_pct=.30,
            entry_valid_minutes=15,reset_inside_hours=6)
CONFIG_HASH=hashlib.sha256(json.dumps(CONFIG,sort_keys=True).encode()).hexdigest()


def geometry(plan,price):
    if not finite(price) or price<=0:return 'INVALID_PRICE'
    risk=plan['stop']-price
    if risk<=0 or 100*risk/price>CONFIG['max_stop_pct']:return 'INVALID_RISK'
    if price>plan['trigger']:return 'CONFIRMATION_NOT_HELD'
    if plan['trigger']-price>CONFIG['max_chase_atr']*plan['atr_1h']:return 'LATE_CHASE'
    if 100*(1-price/plan['origin_price'])>CONFIG['max_move_from_origin_pct']:return 'ALREADY_EXTENDED'
    support=plan.get('obstacle')
    if support is not None and (price-support-price*CONFIG['round_trip_cost_pct']/100)/risk<CONFIG['min_known_net_room_r']:
        return 'INSUFFICIENT_NET_KNOWN_ROOM'
    return None


def pattern(rows,model,a):
    """Freeze a pre-break level, then require a real retest and fresh rejection.

    Most recent valid break/retest wins before geometry; no future pivot or
    outcome is used to pick among patterns. No retest means no signal.
    """
    i=len(rows)-1;last=rows[i]
    n=CONFIG['failed_breakout_range_hours'] if model==MODELS[0] else CONFIG['bear_range_hours']
    for b in range(i-2,max(n-1,i-CONFIG['break_max_age_hours']-1),-1):
        prior=rows[b-n:b]
        level=max(x['high'] for x in prior) if model==MODELS[0] else min(x['low'] for x in prior)
        if model==MODELS[0]:
            if rows[b]['close']<=level:continue
            f=next((k for k in range(b+1,min(b+CONFIG['reentry_max_hours'],i-2)+1)
                    if rows[k]['close']<level),None)
            if f is None:continue
            start=f+1
        else:
            if rows[b]['close']>=level:continue
            highs=pivots(rows[max(0,b-72):b],'high')
            if len(highs)<2 or highs[-1]['price']>=highs[-2]['price']:continue
            f=b;start=max(b+1,i-CONFIG['retest_max_age_hours'])
        if any(x['close']>=level for x in rows[f:i+1]):continue
        for r in range(i-1,start-1,-1):
            test=rows[r]
            if test['high']<level-CONFIG['retest_tolerance_atr']*a or test['close']>=level:continue
            if not last['close']<test['low']<=rows[i-1]['close']:continue
            anchor=max(x['high'] for x in rows[b if model==MODELS[0] else r:i+1])
            return dict(break_index=b,reentry_index=f,retest_index=r,level=level,
                        trigger=test['low'],anchor=anchor,range_floor=min(x['low'] for x in prior),
                        origin=max(x['high'] for x in rows[b:i+1]))
    return None


def assess(symbol,rows,now_ms,model):
    p=dict(version=VERSION,config_hash=CONFIG_HASH,symbol=symbol,direction='SHORT',model=model,
           observed_ms=now_ms,stage='REJECTED',reason='NO_RETEST_SETUP',research_only=True,
           actionable=False,win_probability=None)
    try:
        if model not in MODELS:raise ValueError('UNKNOWN_SHORT_MODEL')
        rows=[b for b in rows if b['close_ms']<now_ms][-CONFIG['history_hours']:]
        if len(rows)<CONFIG['history_hours']:raise ValueError('HISTORY_MISSING_169H')
        check_bars(rows,HOUR,now_ms)
        last=rows[-1]
        if now_ms-last['close_ms']>CONFIG['entry_valid_minutes']*MINUTE:raise ValueError('STALE_CONFIRMATION')
        for row in rows:
            q,buy=row.get('quote_volume'),row.get('taker_buy_quote')
            if not finite(q) or q<0 or not finite(buy) or not 0<=buy<=q:raise ValueError('VOLUME_OR_TAKER_MISSING')
        q24=sum(x['quote_volume'] for x in rows[-24:]);p['quote_volume_24h']=q24
        if q24<CONFIG['min_quote_volume_24h']:raise ValueError('ILLIQUID')
        if last['close']>=last['open']:raise ValueError('NO_BEARISH_CONFIRMATION')
        q3=sum(x['quote_volume'] for x in rows[-3:])
        if q3<=0:raise ValueError('FLOW_MISSING')
        flow=sum(x['taker_buy_quote'] for x in rows[-3:])/q3
        if flow>=CONFIG['max_buy_share_3h']:raise ValueError('SELL_PRESSURE_NOT_CONFIRMED')
        baseline=statistics.median(rows[-1-24*d]['quote_volume'] for d in range(1,8))
        if baseline<=0 or last['quote_volume']<=0:raise ValueError('VOLUME_BASELINE_UNAVAILABLE')
        a=atr(rows);pat=pattern(rows,model,a)
        if not pat:return p
        price=last['close'];support=obstacle(rows,price,'SHORT')
        if model==MODELS[0] and pat['range_floor']<price:
            support=max(support,pat['range_floor']) if support is not None else pat['range_floor']
        p.update(price=price,confirmation_ms=last['close_ms'],trigger=pat['trigger'],
                 broken_level=pat['level'],break_ms=rows[pat['break_index']]['close_ms'],
                 reentry_ms=rows[pat['reentry_index']]['close_ms'],retest_ms=rows[pat['retest_index']]['close_ms'],
                 entry_valid_until_ms=last['close_ms']+CONFIG['entry_valid_minutes']*MINUTE,
                 atr_1h=a,stop=pat['anchor']+CONFIG['stop_atr_buffer']*a,
                 origin_price=pat['origin'],obstacle=support,buy_share_3h=flow,
                 volume_multiple=last['quote_volume']/baseline,
                 move_before_signal_pct=100*(1-price/pat['origin']))
        reason=geometry(p,price)
        if reason:raise ValueError(reason)
        risk=p['stop']-price
        p.update(stage='READY',reason=model,risk_pct=100*risk/price,
                 known_room_r=(price-support)/risk if support is not None else None,
                 known_net_room_r=(price-support-price*.003)/risk if support is not None else None,
                 trailing_activation=price-2*risk)
        return p
    except (ValueError,KeyError,TypeError) as exc:
        p.update(stage='REJECTED',reason=str(exc));return p
