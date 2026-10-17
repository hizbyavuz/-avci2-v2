"""Symmetric closed-5m start hypotheses. Paper evidence only, no publisher.

No preceding 10% move or 4h trend requirement. These are declared hypotheses,
not a validated edge. Missing candles, a late price or stale BTC fail closed.
"""
import hashlib
import json
import sqlite3
import statistics
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

from long_short_major_move_v1_1_frozen import atr,finite
from long_short_major_move_store import SOURCES,quote,record_research

VERSION='EARLY_START_OBSERVER_V1_2026_10_17'
RULES=dict(version=VERSION,interval_ms=300000,history_bars=96,box_bars=12,
           compression_ratio=.8,volume_multiple=1.5,relative_strength_pp=0.,
           chase_atr=.5,stop_buffer_atr=.15,max_stop_pct=8.,min_quote_volume=8000000,
           measurement_hours=3,research_cooldown_hours=3,telegram_enabled=False,
           prior_major_leg_required=False,forming_candles=False)


@lru_cache(maxsize=1)
def observer_id():
    return hashlib.sha256(json.dumps(RULES,sort_keys=True).encode()+Path(__file__).read_bytes()).hexdigest()


def closed_rows(c,symbol,now):
    rows=c.execute('''SELECT open_ms,open,high,low,close,volume,close_ms,source
        FROM futures_closed_klines WHERE symbol=? AND interval='5m' AND close_ms<?
        ORDER BY open_ms DESC LIMIT ?''',(symbol,now-250,RULES['history_bars'])).fetchall()
    if any(r[7] not in SOURCES for r in rows):raise ValueError('UNVERIFIED_5m')
    return [list(r[:7]) for r in reversed(rows)]


def validate(rows,now):
    if len(rows)!=RULES['history_bars']:raise ValueError('HISTORY_MISSING_5m')
    if not 250<=now-rows[-1][6]<RULES['interval_ms']:raise ValueError('STALE_5m')
    for i,r in enumerate(rows):
        if (r[0]%300000 or r[6]!=r[0]+299999 or r[6]>=now-249 or
                not all(finite(v) and v>0 for v in r[1:5]) or not finite(r[5]) or r[5]<0 or
                not r[3]<=min(r[1],r[4])<=max(r[1],r[4])<=r[2] or
                i and r[0]-rows[i-1][0]!=300000):raise ValueError('INVALID_5m')


def assess(symbol,rows,btc,price,qv,now):
    p=dict(version=VERSION,symbol=symbol,observed_ms=now,price=price,
           stage='REJECTED',reason=None,direction='NONE',observation_only=True)
    def reject(reason):p['reason']=reason;return p
    if not finite(qv) or qv<RULES['min_quote_volume']:return reject('LIQUIDITY')
    if not finite(price) or price<=0:return reject('FRESH_PRICE_MISSING')
    try:validate(rows,now);validate(btc,now)
    except ValueError as exc:return reject(str(exc))
    if rows[-1][6]!=btc[-1][6]:return reject('BTC_NOT_TIME_ALIGNED_5m')
    pre=rows[-13:-1];older=rows[:-13];a=atr(rows[:-1])
    normal_range=statistics.mean(r[2]-r[3] for r in older)
    volumes=[sum(r[5] for r in rows[i:i+3]) for i in range(0,len(rows)-3,3)]
    normal_volume=statistics.median(volumes)
    if min(normal_range,normal_volume,a)<=0:return reject('BASELINE_EMPTY')
    compression=statistics.mean(r[2]-r[3] for r in pre)/normal_range
    volume=sum(r[5] for r in rows[-3:])/normal_volume
    if compression>RULES['compression_ratio']:return reject('NO_COMPRESSION')
    if volume<RULES['volume_multiple']:return reject('NO_VOLUME_EXPANSION')
    high=max(r[2] for r in pre);low=min(r[3] for r in pre)
    close=rows[-1][4];side='LONG' if close>high else 'SHORT' if close<low else None
    if side is None:return reject('NO_CLOSED_BREAK')
    sign=1 if side=='LONG' else -1;trigger=high if sign==1 else low
    ret=(close/rows[-4][4]-1)*100;btc_ret=(btc[-1][4]/btc[-4][4]-1)*100
    if sign*ret<=0 or sign*(ret-btc_ret)<=RULES['relative_strength_pp']:
        return reject('NO_RELATIVE_DIRECTION')
    if sign*(price-trigger)<0:return reject('BREAK_NOT_HELD')
    if sign*(price-trigger)>RULES['chase_atr']*a:return reject('LATE_CHASE')
    structural=min(low,rows[-1][3]) if sign==1 else max(high,rows[-1][2])
    stop=structural-sign*RULES['stop_buffer_atr']*a;risk=sign*(price-stop)/price*100
    if not 0<risk<=RULES['max_stop_pct']:return reject('STOP_TOO_DISTANT')
    p.update(stage='READY',reason='OBSERVATION_ONLY',direction=side,model='EARLY_START_'+side,
             trigger=trigger,stop=stop,risk_pct=risk,confirmation_ms=rows[-1][6],
             entry_valid_until_ms=rows[-1][6]+300000,objective=price*(1+sign*.10),
             expires_ms=now+RULES['measurement_hours']*3600000,
             measurement_hours=RULES['measurement_hours'],research_cooldown_hours=RULES['research_cooldown_hours'],
             features=dict(atr5=a,compression_ratio=compression,volume_15m_multiple=volume,
                           return_15m_pct=ret,btc_return_15m_pct=btc_ret,relative_strength_15m_pp=ret-btc_ret),
             source='BINANCE_UM_FUTURES',trailing_enabled=False)
    return p


class EarlyScanner:
    def __init__(self):
        self.marks={};self.retries={};self.latest={};self.run_at=0;self.log_at=0

    def run(self,c,kdb,items):
        tick=time.monotonic()
        if tick<self.run_at:return
        self.run_at=tick+5;now=int(time.time()*1000);btc=None;checked=0;added=Counter()
        active={r[0] for r in items}
        for mapping in (self.marks,self.retries,self.latest):
            for symbol in list(mapping):
                if symbol not in active:del mapping[symbol]
        for symbol,qv,_,_ in items:
            try:
                r=kdb.execute("SELECT close_ms FROM futures_closed_klines WHERE symbol=? AND interval='5m' AND close_ms<? ORDER BY open_ms DESC LIMIT 1",(symbol,now-250)).fetchone()
                mark=r[0] if r else None
                if symbol in self.marks and mark==self.marks[symbol] and tick<self.retries.get(symbol,float('inf')):continue
                self.marks[symbol]=mark;checked+=1
                if btc is None:btc=closed_rows(kdb,'BTCUSDT',now)
                rows=closed_rows(kdb,symbol,now);price=quote(kdb,symbol)
                p=assess(symbol,rows,btc,price,qv,int(time.time()*1000))
                self.latest[symbol]=p['reason']
                if any(x in p['reason'] for x in ('MISSING','STALE','ALIGNED')):self.retries[symbol]=tick+10
                else:self.retries.pop(symbol,None)
                if record_research(c,p,observer_id()):
                    added[p['direction']]+=1
                    print('EARLY_OBSERVER_REFERENCE '+json.dumps(dict(observer_id=observer_id(),signal=p)),flush=True)
            except (sqlite3.Error,ValueError) as exc:
                self.latest[symbol]='DATA_UNAVAILABLE';self.retries[symbol]=tick+10
        c.commit()
        if added or tick>=self.log_at:
            self.log_at=tick+60
            print('EARLY_OBSERVER_SCAN '+json.dumps(dict(version=VERSION,universe=len(items),
                checked=checked,reasons=dict(Counter(self.latest.values())),new_references=dict(added),
                elapsed_ms=round((time.monotonic()-tick)*1000),telegram_enabled=False)),flush=True)
