"""Bounded read-only diagnostic: saved decisions versus subsequent native bars.

Not a backtest or executable-trade return. Extrema omit the partial signal
minute, and all counts are correlated observation rows, not independent trades.
"""
import json
import math
import os
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

VALID = {'BINANCE_UM_FUTURES_WEBSOCKET', 'BINANCE_UM_FUTURES_REST', 'BINANCE_UM_FUTURES_ARCHIVE'}


def movement(price, observed_ms, rows, horizon=15):
    start = ((observed_ms + 59999)//60000)*60000
    end = ((observed_ms + horizon*60000)//60000)*60000
    wanted = list(range(start, end+1, 60000))
    by_time = {int(r[0]):r for r in rows}
    if not all(t in by_time for t in wanted):
        return {'state':'UNRESOLVED_MISSING_PATH'}
    path = [by_time[t] for t in wanted]
    if any(r[5] not in VALID or int(r[1]) != int(r[0])+59999 or
           not all(math.isfinite(float(v)) and float(v)>0 for v in r[2:5]) or
           not r[3] <= r[4] <= r[2] for r in path):
        return {'state':'UNRESOLVED_INVALID_PATH'}
    return dict(state='MEASURED', endpoint_ms=end+59999,
        effective_minutes=round((end+59999-observed_ms)/60000,3),
        return_pct=100*(path[-1][4]/price-1),
        up_excursion_pct=max(0,100*(max(r[2] for r in path)/price-1)),
        down_excursion_pct=max(0,100*(1-min(r[3] for r in path)/price)))


def build(root, now_ms=None):
    from long_short_chart_first import STRATEGY_ID
    now_ms = int(time.time()*1000) if now_ms is None else now_ms
    since, cutoff = now_ms-3600000, now_ms-16*60000
    a=sqlite3.connect(f'file:{(Path(root)/"chart_first_v1.db").resolve()}?mode=ro',uri=True,timeout=5)
    k=sqlite3.connect(f'file:{(Path(root)/"binance_futures_kline_shadow.db").resolve()}?mode=ro',uri=True,timeout=5)
    groups=defaultdict(list); examples=[]; unresolved=Counter(); seen=set(); total=0
    try:
        records=a.execute('''SELECT observed_ms,symbol,direction,stage,reason,price,payload
            FROM assessments WHERE observed_ms>=? AND observed_ms<=?
            ORDER BY observed_ms,id LIMIT 3001''',(since,cutoff)).fetchall()
        for stamp,symbol,side,stage,reason,price,payload in records[:3000]:
            p=json.loads(payload)
            if p.get('strategy_id')!=STRATEGY_ID:continue
            if reason=='LIQUIDITY_FLOOR' or reason.startswith(('FRESH_','CHART_','DAILY_','VERIFIED_')):continue
            if not isinstance(price,(float,int)) or not math.isfinite(price) or price<=0:continue
            key=(symbol,stamp//900000,side,stage,reason)
            if key in seen:continue
            seen.add(key);total+=1
            start=((stamp+59999)//60000)*60000
            end=((stamp+15*60000)//60000)*60000
            rows=k.execute('''SELECT open_ms,close_ms,high,low,close,source
                FROM futures_closed_klines WHERE symbol=? AND interval='1m'
                AND open_ms>=? AND open_ms<=? ORDER BY open_ms''',(symbol,start,end)).fetchall()
            m=movement(price,stamp,rows)
            if m['state']!='MEASURED':unresolved[reason]+=1;continue
            sign=1 if side=='LONG' else -1 if side=='SHORT' else 0
            m.update(symbol=symbol,observed_ms=stamp,direction=side,stage=stage,reason=reason,price=price,
                directional_return_pct=sign*m['return_pct'] if sign else None,
                directional_excursion_pct=m['up_excursion_pct' if sign==1 else 'down_excursion_pct'] if sign else None)
            groups[(reason,side,stage)].append(m)
            if sign:examples.append(m)
        summary=[]
        for (reason,side,stage),items in sorted(groups.items()):
            known=[x for x in items if x['directional_return_pct'] is not None]
            summary.append(dict(reason=reason,direction=side,stage=stage,observations=len(items),
                symbols=len({x['symbol'] for x in items}),
                mean_directional_return_pct=round(sum(x['directional_return_pct'] for x in known)/len(known),4) if known else None,
                endpoint_above_030pct=sum(x['directional_return_pct']>.30 for x in known),
                excursion_at_least_1pct=sum(x['directional_excursion_pct']>=1 for x in known)))
        ranked=[];unique=set()
        for x in sorted(examples,key=lambda x:x['directional_excursion_pct'],reverse=True):
            if x['symbol'] in unique:continue
            unique.add(x['symbol']);ranked.append(x)
            if len(ranked)>=10:break
        return dict(strategy_id=STRATEGY_ID,observed_ms=now_ms,window_start_ms=since,
            matured_until_ms=cutoff,horizon_minutes=15,observations=total,
            truncated=len(records)>3000,unresolved=dict(unresolved),groups=summary,examples=ranked,
            scope='SAVED_DECISIONS_FORWARD_MOVEMENT_NOT_EXECUTABLE_TRADES',
            extrema='COMPLETE_MINUTES_ONLY_EXCLUDES_PARTIAL_SIGNAL_MINUTE')
    finally:a.close();k.close()


if __name__=='__main__':
    root=os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state'
    try:print('CHART_SELECTION_AUDIT '+json.dumps(build(root)),flush=True)
    except Exception as exc:print('CHART_SELECTION_AUDIT_UNAVAILABLE '+type(exc).__name__,flush=True)
