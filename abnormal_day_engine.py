"""Fixed abnormal-day continuation rules. No network, orders or optimisation."""
from bisect import bisect_right
import hashlib
import json
import math
import random
import statistics
from trend_breakout_engine import Candle, DAY, HOUR

RULES = dict(version=1, return_days=90, std_ddof=1, z_threshold=2., atr_days=14,
             stop_atr=2., hours=24, universe_size=20, universe_days=30,
             minimum_history_days=120, max_positions=5, max_direction=3,
             cost_pct=.30, stress_cost_pct=.60, control_seed=20261023,
             initial_equity=10000., equity_risk=.01, parameter_variants=0)
RULES_HASH = hashlib.sha256(json.dumps(RULES,sort_keys=True).encode()).hexdigest()


class DailySeries:
    def __init__(self,bars):
        self.bars=tuple(bars);self.times=[b.close_ms for b in bars]
        self.by_open={b.open_ms:i for i,b in enumerate(bars)}
        self.returns=[None]*len(bars);self.atr=[None]*len(bars);self.gaps=[0]
        tr=[];segment=[]
        for i,b in enumerate(bars):
            if (b.open_ms%DAY or b.close_ms!=b.open_ms+DAY or
                (i and b.open_ms<=bars[i-1].open_ms) or
                not all(math.isfinite(v) for v in (b.open,b.high,b.low,b.close,b.volume,b.quote_volume)) or
                min(b.open,b.high,b.low,b.close)<=0 or min(b.volume,b.quote_volume)<0 or
                b.low>min(b.open,b.close) or b.high<max(b.open,b.close)):
                raise ValueError('INVALID_DAILY_CANDLE')
            gap=i>0 and b.open_ms!=bars[i-1].close_ms
            self.gaps.append(self.gaps[-1]+int(gap))
            if i and not gap:self.returns[i]=b.close/bars[i-1].close-1
            if gap:segment=[]
            v=max(b.high-b.low,abs(b.high-bars[i-1].close),abs(b.low-bars[i-1].close)) if i and not gap else b.high-b.low
            segment.append(v)
            if len(segment)==14:self.atr[i]=sum(segment)/14
            elif len(segment)>14:self.atr[i]=(self.atr[i-1]*13+v)/14

    def index(self,at):return bisect_right(self.times,at)-1


def evaluate(symbol,candles,at):
    s=candles if isinstance(candles,DailySeries) else DailySeries(candles)
    i=s.index(at);base=dict(symbol=symbol,time=at,status='NO_SIGNAL',rules_hash=RULES_HASH)
    if i<91:return dict(base,reason='INSUFFICIENT_HISTORY')
    b=s.bars[i]
    if b.close_ms!=at:return dict(base,reason='STALE_OR_UNCLOSED_DATA')
    past=s.returns[i-90:i]
    if any(v is None for v in past) or s.returns[i] is None or s.atr[i] is None:
        return dict(base,reason='DATA_GAP')
    m=statistics.mean(past);sd=statistics.stdev(past)
    if sd<=0:return dict(base,reason='ZERO_STANDARD_DEVIATION')
    r=s.returns[i];z=(r-m)/sd
    side='LONG' if z>=2 else 'SHORT' if z<=-2 else None
    return dict(base,status='SIGNAL' if side else 'NORMAL',reason='ABNORMAL_DAY' if side else 'NO_ABNORMAL_DAY',
                side=side,r=r,m=m,s=sd,z=z,atr=s.atr[i],signal_day_ms=b.open_ms,close=b.close)


def make_plan(signal,entry,entry_ms,hours=24):
    if signal.get('side') not in ('LONG','SHORT') or entry<=0 or not math.isfinite(entry):raise ValueError('INVALID_PLAN')
    risk=2*signal['atr'];sign=1 if signal['side']=='LONG' else -1
    if risk<=0:raise ValueError('INVALID_ATR')
    return dict(signal,status='OPEN',entry=entry,entry_ms=entry_ms,deadline_ms=entry_ms+hours*HOUR,
                risk=risk,risk_pct=100*risk/entry,stop=entry-sign*risk,hours=hours)


def emergency_exit(plan,bar):
    sign=1 if plan['side']=='LONG' else -1
    if sign*(bar.open-plan['stop'])<=0:
        hourly_open=bar.open_ms%HOUR==0
        return dict(exit=bar.open if hourly_open else plan['stop'],exit_ms=bar.open_ms,exit_reason='GAP_STOP' if hourly_open else 'STOP')
    if (bar.low<=plan['stop'] if sign==1 else bar.high>=plan['stop']):
        return dict(exit=plan['stop'],exit_ms=bar.close_ms-1,exit_reason='STOP',time_resolution='1h')
    return None


def timed_exit(plan,price,at):
    if at<plan['deadline_ms']:return None
    if price<=0 or not math.isfinite(price):raise ValueError('INVALID_EXIT_PRICE')
    return dict(exit=price,exit_ms=at,exit_reason='TIME_24H' if plan['hours']==24 else 'TIME_48H',
                exit_delay_ms=at-plan['deadline_ms'])


def funding_pct(side,events,entry_ms,exit_ms):
    return (1 if side=='LONG' else -1)*100*sum(e['rate'] for e in events if entry_ms<int(e['time'])//1000*1000<=exit_ms)


def result(plan,exit_price,funding,cost=.30):
    gross=(1 if plan['side']=='LONG' else -1)*100*(exit_price/plan['entry']-1)
    net=gross-cost-funding
    return dict(gross_return_pct=gross,cost_pct=cost,funding_pct=funding,net_return_pct=net,
                net_r=net/plan['risk_pct'],stress_net_return_pct=gross-.60-funding,
                stress_net_r=(gross-.60-funding)/plan['risk_pct'])


def choose(candidates,active,limited=True):
    taken=[];skips=[];current=list(active)
    for p in sorted(candidates,key=lambda x:(-abs(x['z']),x['symbol'])):
        if any(q['symbol']==p['symbol'] for q in current):reason='SAME_SYMBOL_OPEN'
        elif limited and (len(current)>=5 or sum(q['side']==p['side'] for q in current)>=3):reason='LİMİT NEDENİYLE ATLANDI'
        else:taken.append(p);current.append(p);continue
        skips.append(dict(p,status='SKIPPED',reason=reason))
    return taken,skips


def select_control(signal,normal_days,seed=20261023):
    """Retrospective matching only. Never consulted by evaluate/live trading.

    One independent uniform draw with replacement per signal; hash-derived
    per-signal seeds make the result invariant to iteration/parallel order.
    Pool selection never reads future trade returns.
    """
    from datetime import datetime,timezone
    month=lambda t:datetime.fromtimestamp(t/1000,timezone.utc).strftime('%Y-%m')
    pool=sorted((d for d in normal_days if d['symbol']==signal['symbol'] and
                 month(d['signal_day_ms'])==month(signal['signal_day_ms']) and abs(d['z'])<1),key=lambda d:d['time'])
    if not pool:return None
    key=f"{seed}:{signal['symbol']}:{signal['time']}:{signal['side']}"
    rng=random.Random(int(hashlib.sha256(key.encode()).hexdigest(),16))
    return dict(rng.choice(pool),side=signal['side'],status='CONTROL',matched_signal_ms=signal['time'])
