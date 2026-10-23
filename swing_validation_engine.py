"""Frozen Swing decision/exit functions with a separately tested replay adapter."""
import hashlib, math, statistics
from collections import defaultdict,Counter
import numpy as np
import pandas as pd
from long_short_swing import CONFIG,CONFIG_HASH,HOUR,MINUTE,assess,geometry
from long_short_swing_outcomes import initial,advance
DAY=24*HOUR
SEED='SWING_V1_INDEPENDENT_20261023'

def candle(r,period=HOUR):
    return dict(open_ms=int(r[0]),close_ms=int(r[0])+period-1,open=float(r[1]),high=float(r[2]),low=float(r[3]),close=float(r[4]),quote_volume=float(r[5]),taker_buy_quote=float(r[6]))

def screen(symbol,arr,start,end):
    """Cheap necessary conditions prefilter; frozen assess makes every decision."""
    if len(arr)<169:return [],[],{}
    roof=pd.Series(arr[:,2]).rolling(72).max().shift(1).to_numpy()
    baseline=np.nanmedian(np.array([pd.Series(arr[:,5]).shift(24*d).to_numpy() for d in range(1,8)]),axis=0)
    eligible=(arr[:,4]>roof)&(arr[:,5]>=3*baseline)&(arr[:,6]>.5*arr[:,5])
    records=[];ready=[];active=None;inside=0;counts=Counter()
    for i in range(168,len(arr)):
        r=arr[i];t=int(r[0])+HOUR
        if t<start or t>=end:continue
        if active is not None:
            inside=inside+1 if r[4]<=active['trigger'] else 0
            if inside>=CONFIG['reset_inside_hours']:active=None
        if not eligible[i]:continue
        p=assess(symbol,[candle(x) for x in arr[i-168:i+1]],t+999)
        counts[p['reason']]+=1
        if p['stage']!='READY' or p['direction']!='LONG':continue
        ready.append(t)
        if active is not None:counts['EPISODE_SUPPRESSED']+=1;continue
        active=p;inside=0;p['id']=f'{symbol}:{t}';p['signal_time']=t;p['entry_ms']=t+MINUTE
        records.append(p)
    return records,ready,dict(counts)

def control_symbol(symbol,time,available,ready):
    choices=sorted(s for s in available if s!=symbol and s not in ready)
    if not choices:return None
    value=int(hashlib.sha256(f'{SEED}:{symbol}:{time}'.encode()).hexdigest(),16)
    return choices[value%len(choices)]

def control_plan(p,symbol,entry):
    risk_pct=(p['entry']-p['stop'])/p['entry']
    return dict(p,symbol=symbol,entry=entry,stop=entry*(1-risk_pct),atr_1h=entry*p['atr_1h']/p['entry'],control=True)

def replay(p,hours,minute_loader,end):
    """1h path uses a stop fixed at that hour's start; 4h changes act afterwards.

    First/last partial hours and every stop-containing hour use original minute
    advance. Non-stop full hours have exactly the same extrema/block update.
    """
    start=p['entry_ms'];g=initial(p,start,p['entry']);at=start;expiry=g['expires_ms'];resolution=Counter();updates=[]
    while at<min(expiry,end) and g['exit'] is None:
        hour=at//HOUR*HOUR;b=hours.get(hour);until=min(hour+HOUR,expiry,end)
        if b is None:return dict(status='DATA_GAP',missing_ms=hour,entry=p['entry'],entry_ms=start,expires_ms=expiry)
        partial=at!=hour or until!=hour+HOUR
        stop_hit=b['low']<=g['stop']
        oldstop=g['stop']
        if partial or stop_hit:
            try:raw=minute_loader(p['symbol'],hour);rows=[x for x in raw if at<=x['open_ms']<until]
            except Exception:rows=[]
            expected=(until-at)//MINUTE
            valid=len(rows)==expected and all(x['open_ms']==at+i*MINUTE for i,x in enumerate(rows))
            if valid:
                g=advance(p,g,rows);resolution['MINUTE_PATH']+=1
            elif stop_hit and not partial:
                price=min(b['open'],g['stop']);g['exit']=dict(kind='STOP',at_ms=hour,time_upper_ms=hour+HOUR-1,price=price,gross_pct=100*(price/p['entry']-1));resolution['MISSING_MINUTE_STOP_FIRST']+=1
            else:return dict(status='DATA_GAP',missing_ms=at,entry=p['entry'],entry_ms=start,expires_ms=expiry,reason='PARTIAL_HOUR_MINUTES_MISSING')
        else:
            if b['high']>=g['trailing_activation']:g['trailing_armed']=True
            if hour%(4*HOUR)==0:g['block']=dict(low=b['low'],high=b['high'],count=0)
            block=g['block']
            if block:
                block.update(low=min(block['low'],b['low']),high=max(block['high'],b['high']),count=block['count']+60)
            if until%(4*HOUR)==0:
                if block and block['count']==240 and g['trailing_armed']:g['stop']=max(g['stop'],block['low']-.25*p['atr_1h'])
                g['block']=None
            g['cursor_ms']=until;g['last_close']=b['close'];resolution['HOURLY_EQUIVALENT']+=1
        if g['stop']!=oldstop:updates.append(dict(effective_ms=until,stop=g['stop']))
        at=until
    if not g['exit']:return dict(status='PENDING',entry=p['entry'],entry_ms=start,expires_ms=expiry,stop=g['stop'],updates=updates,resolution=dict(resolution))
    e=g['exit'];risk_pct=100*g['initial_risk']/p['entry']
    return dict(status='CLOSED',entry=p['entry'],entry_ms=start,expires_ms=expiry,exit=e,risk_pct=risk_pct,gross_pct=e['gross_pct'],holding_hours=(e['at_ms']-start)/HOUR,updates=updates,resolution=dict(resolution))

def select_positions(plans,outcomes,limit=None):
    active=[];chosen=[];skipped=[];peak=0
    # No per-symbol veto: original episode reset may open another plan while
    # an earlier same-symbol plan is open. Every plan consumes one risk slot.
    for p in sorted(plans,key=lambda v:(v['entry_ms'],-v['quote_volume_24h'],v['symbol'])):
        t=p['entry_ms'];active=[e for e in active if e>t]
        o=outcomes.get(p['id'],{})
        if limit is not None and len(active)>=limit:skipped.append(p['id']);continue
        chosen.append(p['id']);exit_=o.get('exit')
        # Data gaps never free a slot early based on an unobserved exit.
        active.append(exit_['time_upper_ms']+1 if exit_ else t+168*HOUR);peak=max(peak,len(active))
    return dict(selected=chosen,skipped=skipped,peak=peak)

def day_stats(rows,key='net_r'):
    d=defaultdict(list)
    for r in rows:
        if r.get(key) is not None:d[r['signal_time']//DAY].append(r[key])
    means=[statistics.mean(d[t]) for t in sorted(d)]
    n=len(means)
    if not n:return dict(days=0,mean=None,se=None,hac7_se=None)
    mean=statistics.mean(means);se=statistics.stdev(means)/math.sqrt(n) if n>1 else None
    # Calendar-day lags, not adjacent signal-day indices.
    centered={t:statistics.mean(v)-mean for t,v in d.items()}
    variance=sum(v*v for v in centered.values())
    for lag in range(1,8):variance+=2*(1-lag/8)*sum(v*centered.get(t-lag,0.) for t,v in centered.items())
    hac=math.sqrt(max(0.,variance))/n
    return dict(days=n,mean=mean,se=se,hac7_se=hac,daily={str(t):statistics.mean(v) for t,v in sorted(d.items())})

def comparison(rows,controls):
    pairs=[]
    for r in rows:
        c=controls.get(r['id'])
        if c and r.get('net_r') is not None and c.get('net_r') is not None:
            pairs.append(dict(signal_time=r['signal_time'],net_r=r['net_r']-c['net_r'],net_pct=r['net_pct']-c['net_pct']))
    ds=day_stats(pairs);ps=day_stats(pairs,'net_pct')
    return dict(pairs=len(pairs),unpaired=len(rows)-len(pairs),daily_r=ds,daily_pct=ps,significant=bool(ds['se'] is not None and ds['mean']>2*ds['se']))
