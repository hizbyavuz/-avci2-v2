"""Signal-time observations only. Never votes, ranks, vetoes or fetches network data."""
import hashlib
import json
import sqlite3
from functools import lru_cache
from pathlib import Path

from long_short_major_move import finite
from long_short_major_move_store import SOURCES, db, root
from long_short_chart_flow import hourly_flow
from long_short_native_derivatives import validate

VERSION='MAJOR_CONTEXT_OBSERVER_V1_2026_10_16'
RULES=dict(flow_window_minutes=60,flow_neutral_buy_share=.5,
           oi_window_minutes=60,native_max_age_ms=180000,history_max_age_ms=660000,
           signal_decision_effect='NONE',oi_is_directional_vote=False,
           funding_is_directional_vote=False,account_ratio_is_not_position_size=True)


@lru_cache(maxsize=1)
def observer_id():
    return hashlib.sha256(json.dumps(RULES,sort_keys=True).encode()+Path(__file__).read_bytes()).hexdigest()


def flow_alignment(flow,direction,now_ms):
    if direction not in ('LONG','SHORT'):return 'NO_DIRECTION'
    if (flow.get('status')!='OK' or not finite(flow.get('buy_share')) or
        not 0<=flow['buy_share']<=1 or not finite(flow.get('price_return_pct')) or
        not 0<=now_ms-flow.get('end_ms',now_ms+1)<=300000 or
        flow.get('end_ms',0)-flow.get('start_ms',0)!=3600000):return 'MISSING'
    sign=1 if direction=='LONG' else -1
    pressure=sign*(flow['buy_share']-.5);response=sign*flow['price_return_pct']
    if pressure>0 and response>0:return 'SUPPORT'
    if pressure<0 and response<0:return 'CONFLICT'
    if pressure==0 or response==0:return 'NEUTRAL'
    return 'MIXED'


def aligned_price(c,symbol,start,end,now_ms):
    if not (start%300000==0 and end-start==3600000 and end<=now_ms-250):
        raise ValueError('OI_PRICE_WINDOW_INVALID')
    rows=c.execute("""SELECT close_ms,close,source,received_ms FROM futures_closed_klines
        WHERE symbol=? AND interval='5m' AND close_ms IN (?,?) ORDER BY close_ms""",
        (symbol,start-1,end-1)).fetchall()
    if len(rows)!=2 or [r[0] for r in rows]!=[start-1,end-1]:raise ValueError('OI_PRICE_WINDOW_MISSING')
    if any(r[2] not in SOURCES or r[3]>now_ms or not finite(r[1]) or r[1]<=0 for r in rows):
        raise ValueError('OI_PRICE_WINDOW_UNVERIFIED')
    return dict(start_ms=start,end_ms=end,start_price=rows[0][1],end_price=rows[1][1],
                return_pct=(rows[1][1]/rows[0][1]-1)*100)


def classify(context,direction):
    """OI build/unwind and funding cost are explanatory groups, never good/bad votes."""
    labels=dict(flow=flow_alignment(context['flow'],direction,context['captured_ms']),
                oi_price='MISSING',funding_cost='MISSING',availability='PARTIAL')
    n=context['native'];price=context.get('oi_price_window')
    sign=1 if direction=='LONG' else -1
    if n.get('oi_fresh') and price:
        delta=n['oi_change_1h'];ret=sign*price['return_pct']
        movement='WITH_SIGNAL' if ret>0 else 'AGAINST_SIGNAL' if ret<0 else 'FLAT_PRICE'
        participation='BUILD' if delta>0 else 'UNWIND' if delta<0 else 'FLAT_OI'
        labels['oi_price']=movement+'_'+participation
    if n.get('funding_fresh'):
        value=sign*n['funding_pct']
        labels['funding_cost']='COST' if value>0 else 'RECEIPT' if value<0 else 'ZERO'
    if labels['flow']!='MISSING' and labels['oi_price']!='MISSING' and n.get('funding_fresh') and n.get('ls_fresh'):
        labels['availability']='COMPLETE'
    elif labels['flow']=='MISSING' and all(not n.get(k) for k in ('oi_fresh','funding_fresh','ls_fresh')):
        labels['availability']='MISSING'
    return labels


def capture(kdb,symbol,direction,now_ms,flow=None):
    out=dict(version=VERSION,observer_id=observer_id(),captured_ms=now_ms,symbol=symbol,
             direction=direction,flow={},native={},raw_native=None,oi_price_window=None,issues=[])
    try:
        out['flow']=hourly_flow(kdb,symbol,now_ms) if flow is None else json.loads(json.dumps(flow))
        if flow_alignment(out['flow'],direction,now_ms)=='MISSING':out['issues'].append('FLOW_UNAVAILABLE_OR_STALE')
    except (sqlite3.Error,AttributeError,TypeError,ValueError):
        out['flow']={'status':'UNAVAILABLE'};out['issues'].append('FLOW_READ_FAILED')
    try:
        c=db(root()/'native_derivatives_v2.db',True)
        try:row=c.execute('SELECT received_ms,payload FROM snapshots WHERE symbol=?',(symbol,)).fetchone()
        finally:c.close()
        if not row:raise ValueError('NATIVE_SNAPSHOT_MISSING')
        out['received_ms']=row[0];raw=json.loads(row[1]);out['raw_native']=raw
        if not 0<=now_ms-row[0]<=RULES['native_max_age_ms']:raise ValueError('NATIVE_RECEIPT_STALE_OR_FUTURE')
        if not 0<=now_ms-raw['observed_ms']<=RULES['native_max_age_ms']:raise ValueError('NATIVE_OBSERVED_STALE_OR_FUTURE')
        n=validate(raw,symbol,now_ms=now_ms,allow_partial=True);out['native']=n
        # Existing reader permits a small clock tolerance; observational evidence
        # does not accept future fields or later revisions at signal time.
        field_stamps={'oi_fresh':[(raw.get('oi') or {}).get('time')]+[r.get('timestamp') for r in raw.get('oi_history',[])],
                      'funding_fresh':[(raw.get('mark') or {}).get('time')],
                      'ls_fresh':[r.get('timestamp') for r in raw.get('long_short',[])]}
        for field,stamps in field_stamps.items():
            if n.get(field) and (not stamps or any(not finite(t) or t>now_ms for t in stamps)):
                n[field]=False;out['issues'].append('FUTURE_OR_INVALID_'+field)
        out['issues']+=list(n.get('field_errors',{}).values())
        if n.get('oi_fresh'):
            history=sorted(raw['oi_history'],key=lambda r:r['timestamp'])[-13:]
            try:out['oi_price_window']=aligned_price(kdb,symbol,int(history[0]['timestamp']),int(history[-1]['timestamp']),now_ms)
            except (sqlite3.Error,ValueError,AttributeError) as exc:out['issues'].append(str(exc)[:100])
    except (OSError,sqlite3.Error,ValueError,KeyError,TypeError,AttributeError) as exc:
        out['issues'].append(str(exc)[:100]);out['native']={}
    out['groups']=classify(out,direction)
    return out


def record(c,key,phase,context):
    c.execute('''INSERT OR IGNORE INTO context_snapshots
        (id,phase,observed_ms,symbol,observer_id,payload) VALUES(?,?,?,?,?,?)''',
        (key,phase,context['captured_ms'],context['symbol'],context['observer_id'],json.dumps(context)))


def compact(context):
    """Retain report keys in the idea; full raw evidence lives in one immutable row."""
    return {key:context[key] for key in ('version','observer_id','captured_ms','groups','issues')}
