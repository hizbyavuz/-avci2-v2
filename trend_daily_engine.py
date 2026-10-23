"""Daily 20/10 channel trend following. Pure functions, closed UTC candles only."""
from dataclasses import asdict
from bisect import bisect_right
import hashlib
import json
import math
from trend_breakout_engine import Candle, DAY

RULES = dict(version=1, entry_days=20, exit_days=10, atr_days=20, stop_n=2,
             universe_days=30, minimum_history=100, universe_size=50,
             max_positions=6, max_direction=4, cost_pct=.30, stress_cost_pct=.60,
             initial_equity=10000., equity_risk=.01, parameter_variants=0)
RULES_HASH = hashlib.sha256(json.dumps(RULES, sort_keys=True).encode()).hexdigest()


class DailySeries:
    def __init__(self, bars):
        self.bars = tuple(bars)
        self.times = [b.close_ms for b in bars]
        self.by_open = {b.open_ms: i for i,b in enumerate(bars)}
        self.n = [None]*len(bars)
        self.gaps = [0]
        trs = []
        for i,b in enumerate(bars):
            if (b.open_ms % DAY or b.close_ms != b.open_ms+DAY or
                (i and b.open_ms <= bars[i-1].open_ms) or
                not all(math.isfinite(v) for v in (b.open,b.high,b.low,b.close,b.volume,b.quote_volume)) or
                min(b.open,b.low,b.high,b.close)<=0 or min(b.volume,b.quote_volume)<0 or
                b.low>min(b.open,b.close) or b.high<max(b.open,b.close)):
                raise ValueError('INVALID_DAILY_CANDLE')
            gap = i>0 and b.open_ms!=bars[i-1].close_ms
            self.gaps.append(self.gaps[-1]+int(gap))
            tr=max(b.high-b.low,abs(b.high-bars[i-1].close),abs(b.low-bars[i-1].close)) if i else b.high-b.low
            trs.append(tr)
            if i==19:self.n[i]=sum(trs)/20
            elif i>19:self.n[i]=(self.n[i-1]*19+tr)/20

    def index(self,t):return bisect_right(self.times,t)-1


def evaluate(symbol, candles, at):
    s=candles if isinstance(candles,DailySeries) else DailySeries(candles)
    i=s.index(at)
    out=dict(symbol=symbol,time=at,status='NO_SIGNAL',rules_hash=RULES_HASH)
    if i<30:return dict(out,reason='INSUFFICIENT_HISTORY')
    b=s.bars[i]
    if b.close_ms!=at:return dict(out,reason='STALE_OR_UNCLOSED_DATA')
    if s.gaps[i+1]:return dict(out,reason='DATA_GAP')
    prior=s.bars[i-20:i]
    high=max(x.high for x in prior);low=min(x.low for x in prior)
    side='LONG' if b.close>high else 'SHORT' if b.close<low else None
    if not side:return dict(out,reason='NO_BREAKOUT')
    n=s.n[i]
    if not n or n<=0:return dict(out,reason='INVALID_ATR')
    return dict(out,status='SIGNAL',reason='20_DAY_CLOSE_BREAKOUT',side=side,n=n,
                level=high if side=='LONG' else low,channel_high=high,channel_low=low,
                close=b.close,volume_30d=sum(x.quote_volume for x in s.bars[i-29:i+1]))


def make_plan(signal,entry,entry_ms,source='futures'):
    if signal.get('status')!='SIGNAL' or not math.isfinite(entry) or entry<=0:
        raise ValueError('INVALID_ENTRY_OR_SIGNAL')
    sign=1 if signal['side']=='LONG' else -1
    risk=2*signal['n']
    return dict(signal,status='OPEN',entry=entry,entry_ms=entry_ms,initial_stop=entry-sign*risk,
                stop=entry-sign*risk,risk=risk,risk_pct=100*risk/entry,data_source=source,
                funding_pct=0.,funding_events=0,mfe_r=0.,mae_r=0.)


def update_stop(plan,candles,at):
    """After close t, use the ten days BEFORE the just-closed day; effective from t."""
    s=candles if isinstance(candles,DailySeries) else DailySeries(candles)
    i=s.index(at)
    if i<10 or s.bars[i].close_ms!=at or s.gaps[i+1]!=s.gaps[i-9]:
        raise ValueError('STOP_HISTORY_UNAVAILABLE')
    prior=s.bars[i-10:i]
    reference=min(b.low for b in prior) if plan['side']=='LONG' else max(b.high for b in prior)
    stop=max(plan['stop'],reference) if plan['side']=='LONG' else min(plan['stop'],reference)
    return dict(stop=stop,previous_stop=plan['stop'],channel_reference=reference,effective_ms=at)


def stop_exit(plan,bar):
    sign=1 if plan['side']=='LONG' else -1
    if sign*(bar.open-plan['stop'])<=0:
        daily_open=bar.open_ms%DAY==0
        return dict(exit=bar.open if daily_open else plan['stop'],exit_ms=bar.open_ms,
                    exit_reason='GAP_STOP' if daily_open else 'STOP')
    if (bar.low<=plan['stop'] if sign==1 else bar.high>=plan['stop']):
        return dict(exit=plan['stop'],exit_ms=None,exit_reason='STOP')
    return None


def choose(candidates,active,limited):
    current=list(active);take=[];skip=[]
    for p in sorted(candidates,key=lambda p:(-p['volume_30d'],p['symbol'])):
        if any(x['symbol']==p['symbol'] for x in current):reason='SAME_SYMBOL_OPEN'
        elif limited and (len(current)>=6 or sum(x['side']==p['side'] for x in current)>=4):reason='LİMİT NEDENİYLE ATLANDI'
        else:take.append(p);current.append(p);continue
        skip.append(dict(p,status='SKIPPED',reason=reason))
    return take,skip


def funding_pct(side,events,entry_ms,exit_ms):
    sign=1 if side=='LONG' else -1
    return 100*sign*sum(e['rate'] for e in events if entry_ms<int(e['time'])//1000*1000<=exit_ms)


def result(plan,exit_price,funding,cost=.30):
    sign=1 if plan['side']=='LONG' else -1
    gross=100*sign*(exit_price/plan['entry']-1)
    return dict(gross_return_pct=gross,funding_pct=funding,cost_pct=cost,
                net_r=(gross-cost-funding)/plan['risk_pct'],
                stress_net_r=(gross-.60-funding)/plan['risk_pct'],
                funding_effect_r=-funding/plan['risk_pct'])


def excursions(plan,bars,exit_price=None):
    values=[(b.high,b.low) for b in bars]
    if exit_price is not None:values.append((exit_price,exit_price))
    if not values:return dict(mfe_r=plan.get('mfe_r',0),mae_r=plan.get('mae_r',0))
    hi=max(v[0] for v in values);lo=min(v[1] for v in values)
    favorable=hi-plan['entry'] if plan['side']=='LONG' else plan['entry']-lo
    adverse=plan['entry']-lo if plan['side']=='LONG' else hi-plan['entry']
    return dict(mfe_r=max(plan.get('mfe_r',0),favorable/plan['risk'],0),
                mae_r=max(plan.get('mae_r',0),adverse/plan['risk'],0))


def direction_five(plan,bars):
    rows=[b for b in bars if plan['entry_ms']<=b.open_ms<plan['entry_ms']+5*DAY]
    if len(rows)!=5 or any(b.open_ms!=plan['entry_ms']+i*DAY for i,b in enumerate(rows)):return None
    v=excursions(dict(plan,mfe_r=0.,mae_r=0.),rows)
    return v['mfe_r']>v['mae_r']
