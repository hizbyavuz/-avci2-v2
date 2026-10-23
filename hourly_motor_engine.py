"""Saatlik Motor v1: pure causal rules shared by replay and paper runner.

UTC milliseconds; close_ms is an exclusive boundary. No exchange order code.
EMA uses SMA seed, ATR14 uses Wilder smoothing. Missing candles reset the
indicator seed; gaps are never filled with synthetic prices.
"""
import bisect
from collections import deque
import hashlib
import json
import math
from trend_breakout_engine import Candle, HOUR, DAY, MINUTE

RULES = dict(version=1, universe=30, volume_days=30, ema_fast=20, ema_slow=50,
             relative_days=14, atr=14, max_daily_extension=2., channel_hours=24,
             volume_hours=20, volume_multiple=1.5, stop_buffer=.5, target_r=1.5,
             hold_hours=2, cost_pct=.30, stress_cost_pct=.60, daily_quota=1)
RULES_HASH = hashlib.sha256(json.dumps(RULES, sort_keys=True).encode()).hexdigest()
STABLECOINS = frozenset('USDCUSDT BUSDUSDT TUSDUSDT FDUSDUSDT USDPUSDT USDEUSDT USDDUSDT DAIUSDT USTUSDT USTCUSDT USD1USDT RLUSDUSDT XUSDUSDT UUSDT EURIUSDT AEURUSDT'.split())


class Series:
    def __init__(self, bars, interval):
        self.bars = tuple(bars); self.interval = interval
        self.opens = [b.open_ms for b in bars]; self.closes = [b.close_ms for b in bars]
        self.atr=[]; self.ema20=[]; self.ema50=[]; self.segment=[]
        tr=[]; closes=[]; atr=fast=slow=None; start=0
        for i,b in enumerate(bars):
            if (b.close_ms-b.open_ms != interval or b.open_ms % interval or
                (i and b.open_ms <= bars[i-1].open_ms) or
                not all(math.isfinite(v) for v in (b.open,b.high,b.low,b.close,b.volume,b.quote_volume)) or
                min(b.open,b.high,b.low,b.close)<=0 or min(b.volume,b.quote_volume)<0 or
                b.low>min(b.open,b.close) or b.high<max(b.open,b.close)):
                raise ValueError('INVALID_CANDLE')
            if i and b.open_ms != bars[i-1].close_ms:
                start=i;tr=[];closes=[];atr=fast=slow=None
            n=i-start+1
            true=max(b.high-b.low,abs(b.high-bars[i-1].close),abs(b.low-bars[i-1].close)) if n>1 else b.high-b.low
            tr.append(true);closes.append(b.close)
            atr=sum(tr)/14 if n==14 else (atr*13+true)/14 if n>14 else None
            fast=sum(closes)/20 if n==20 else fast+(b.close-fast)*2/21 if n>20 else None
            slow=sum(closes)/50 if n==50 else slow+(b.close-slow)*2/51 if n>50 else None
            self.atr.append(atr);self.ema20.append(fast);self.ema50.append(slow);self.segment.append(start)
        self.high24=self._prior_extreme(True);self.low24=self._prior_extreme(False)
        self.volume_sum=[0.]
        for b in bars:self.volume_sum.append(self.volume_sum[-1]+b.volume)

    def _prior_extreme(self, high):
        values=[b.high if high else b.low for b in self.bars];q=deque();out=[]
        for i,v in enumerate(values):
            while q and q[0]<i-24:q.popleft()
            out.append(values[q[0]] if i>=24 and q else None)
            while q and (values[q[-1]]<=v if high else values[q[-1]]>=v):q.pop()
            q.append(i)
        return out

    def index(self,t):return bisect.bisect_right(self.closes,t)-1


def daily_direction(symbol, daily, btc, at):
    """One list per UTC day. Intraday candles are never consulted."""
    d=daily if isinstance(daily,Series) else Series(daily,DAY)
    b=btc if isinstance(btc,Series) else Series(btc,DAY)
    boundary=at//DAY*DAY;i=d.index(boundary);j=b.index(boundary)
    result=dict(symbol=symbol,daily_at=boundary,direction=None,status='VALID')
    if i<0 or j<0 or d.closes[i]!=boundary or b.closes[j]!=boundary:
        return dict(result,status='UNVERIFIED',reason='DAILY_OR_BTC_MISSING')
    if i-d.segment[i]<49 or j-b.segment[j]<14:
        return dict(result,status='UNVERIFIED',reason='INDICATOR_WARMUP_OR_GAP')
    close=d.bars[i].close;ema20=d.ema20[i];ema50=d.ema50[i];atr=d.atr[i]
    r=close/d.bars[i-14].close-1;br=b.bars[j].close/b.bars[j-14].close-1
    result.update(daily_close=close,ema20=ema20,ema50=ema50,daily_atr=atr,return14=r,btc_return14=br)
    if abs(close-ema20)>2*atr:return dict(result,reason='EXTENDED')
    direction='LONG' if close>ema20>ema50 and r>br else 'SHORT' if close<ema20<ema50 and r<br else None
    return dict(result,direction=direction,reason='DAILY_RULES_MET' if direction else 'NOT_ON_LIST')


def evaluate(symbol, hourly, direction, at, group='MAIN'):
    """Same signal function for historical replay/live and both controls.

    A: opposite breakout in a listed coin. B: either breakout in a coin on
    neither directional list. Data-invalid daily rows are not control B.
    """
    h=hourly if isinstance(hourly,Series) else Series(hourly,HOUR)
    result=dict(symbol=symbol,time=at,group=group,status='NO_SIGNAL',rules_hash=RULES_HASH)
    i=h.index(at)
    if h.interval!=HOUR or direction['symbol']!=symbol:raise ValueError('WRONG_INPUT')
    if i<24 or h.closes[i]!=at or i-h.segment[i]<24:
        return dict(result,reason='HOURLY_WARMUP_OR_GAP')
    if direction['daily_at']!=at//DAY*DAY or direction['status']!='VALID':
        return dict(result,reason='DAILY_UNVERIFIED')
    allowed=direction['direction'];b=h.bars[i]
    if group=='MAIN':sides=[allowed] if allowed else []
    elif group=='CONTROL_A':sides=[('SHORT' if allowed=='LONG' else 'LONG')] if allowed else []
    elif group=='CONTROL_B':sides=['LONG','SHORT'] if allowed is None else []
    else:raise ValueError('UNKNOWN_GROUP')
    mean=(h.volume_sum[i]-h.volume_sum[i-20])/20
    if mean<=0 or b.volume<1.5*mean:return dict(result,reason='VOLUME')
    for side in sides:
        sign=1 if side=='LONG' else -1;level=h.high24[i] if sign==1 else h.low24[i]
        if sign*(b.close-level)<=0:continue
        atr=h.atr[i]
        return dict(result,status='SIGNAL',side=side,signal_close=b.close,signal_open_ms=b.open_ms,
                    level=level,atr=atr,stop=b.low-.5*atr if sign==1 else b.high+.5*atr,
                    volume_ratio=b.volume/mean,daily=direction,reason='BREAKOUT_VOLUME')
    return dict(result,reason='NO_PERMITTED_BREAKOUT')


def make_plan(signal,entry,entry_ms):
    if signal['status']!='SIGNAL':raise ValueError('NO_SIGNAL')
    sign=1 if signal['side']=='LONG' else -1;risk=sign*(entry-signal['stop'])
    p=dict(signal,entry=entry,entry_ms=entry_ms,deadline_ms=entry_ms+2*HOUR)
    if not math.isfinite(entry) or entry<=0 or risk<=0:return dict(p,status='REJECTED',reason='ENTRY_BEYOND_STOP')
    target=entry+sign*1.5*risk
    if target<=0:return dict(p,status='REJECTED',reason='NONPOSITIVE_TARGET')
    return dict(p,status='PLAN',risk=risk,risk_pct=100*risk/entry,target=target)


def universe_at(all_bars,at):
    ranking=[]
    for s,bars in all_bars.items():
        if s in STABLECOINS or not s.endswith('USDT') or '_' in s:continue
        past=[b for b in bars if b.close_ms<=at]
        if not past:continue
        begin=max(at-30*DAY,past[0].open_ms)
        recent=[b for b in past if b.open_ms>=begin]
        expected=(at-begin)//DAY
        if len(recent)!=expected or not recent or any(b.open_ms!=begin+i*DAY for i,b in enumerate(recent)):continue
        ranking.append(dict(symbol=s,volume_30d=sum(b.quote_volume for b in recent),selection_ms=at,last_used_ms=recent[-1].close_ms,observed_days=len(recent)))
    return sorted(ranking,key=lambda r:(-r['volume_30d'],r['symbol']))[:30]


def funding_percent(side,events,entry_ms,exit_ms):
    sign=1 if side=='LONG' else -1
    return sign*100*sum(f['rate'] for f in events if entry_ms<(int(f['time'])//1000)*1000<=exit_ms)


def result(plan,exit_price,funding_pct,cost_pct=.30):
    sign=1 if plan['side']=='LONG' else -1;gross=sign*100*(exit_price/plan['entry']-1)
    net=gross-cost_pct-funding_pct
    return dict(gross_pct=gross,cost_pct=cost_pct,funding_pct=funding_pct,net_pct=net,net_r=net/plan['risk_pct'])
