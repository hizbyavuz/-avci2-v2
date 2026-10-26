"""Bounded, independent 72h observation. Never creates portfolio fills or signals."""
import hashlib
import json

from .core import CFG, MINUTE, closed_bars, ema, atr
from .io import DataUnavailable, encode

HORIZON = 72*60*MINUTE
GAP_GRACE = 30*MINUTE
GAP_RETRY = MINUTE


def setup_id(p):
    return hashlib.sha256(f"{p['config_hash']}:{p['symbol']}:{p['candle_ms']}".encode()).hexdigest()


def context_fields(p,bars,context,btc_trend=None):
    sign=1 if p['direction']=='LONG' else -1
    context_atr=atr(context,14)
    e=ema([r[4] for r in context],20)[-1]
    distance=sign*(p['reference_entry']-p['stop'])
    extreme=max(r[2] for r in bars[-60:]) if sign==1 else min(r[3] for r in bars[-60:])
    return dict(extension_4h_atr=sign*(p['reference_entry']-e)/context_atr if context_atr else None,
                room_60_bars_r=sign*(extreme-p['reference_entry'])/distance if distance>0 else None,
                btc_agreement=None if btc_trend is None else 'NEUTRAL' if btc_trend==0 else
                'SAME' if btc_trend==sign else 'OPPOSITE')


def record_setup(store,p,now,market,admission='ASSESSING'):
    """Called before portfolio admission, including every rejected valid setup."""
    p.setdefault('id',setup_id(p))
    p.update({k:p.get(k,v) for k,v in store.provenance.items()})
    payload=dict(p,market=market,admission=admission,observational_only=True,
                 path_anchor='INTENDED_ENTRY_TIME',path_horizon_ms=HORIZON)
    start=int(p['entry_ms'])
    store.db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?) '
                     'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, '
                     'start_ms=CASE WHEN observations.attempts=0 THEN excluded.start_ms ELSE observations.start_ms END, '
                     'end_ms=CASE WHEN observations.attempts=0 THEN excluded.end_ms ELSE observations.end_ms END, '
                     'cursor_ms=CASE WHEN observations.attempts=0 THEN excluded.cursor_ms ELSE observations.cursor_ms END',
                     (p['id'],p['symbol'],start,start+HORIZON,start,'COLLECTING',now,0,0,encode(payload)))
    store.db.commit()


def first_missing(store,symbol,start,end):
    expected=start
    for row in store.db.execute("SELECT open_ms FROM candles WHERE symbol=? AND tf='1m' "
                                'AND open_ms>=? AND open_ms<? ORDER BY open_ms',(symbol,start,end)):
        if row[0]!=expected:
            return expected
        expected+=MINUTE
    return expected if expected<end else None


def collect(store,client,max_requests=6,stop_before_ms=None):
    """At most six bounded requests per minute, sharing the normal rate gate.

    Sparse returned bars are archived; holes remain explicit and get three
    bounded repair attempts after the horizon. No fabricated zero-volume bars.
    """
    now=client.now()
    if now-int(store.setting('observation_tick_ms') or 0)<MINUTE:
        return 0
    store.setting('observation_tick_ms',now)
    jobs=store.db.execute("SELECT * FROM observations WHERE status IN ('COLLECTING','REPAIRING') "
                          'AND next_fetch_ms<=? ORDER BY next_fetch_ms,id LIMIT ?',
                          (now,max_requests)).fetchall()
    used=0
    for job in jobs:
        if stop_before_ms is not None and client.now()>=stop_before_ms:
            break
        start=job['cursor_ms']
        complete_until=min(job['end_ms'],(now-CFG.publication_delay_ms)//MINUTE*MINUTE)
        repairs=job['repairs']
        if start>=job['end_ms']:
            missing=first_missing(store,job['symbol'],job['start_ms'],job['end_ms'])
            if missing is None:
                store.db.execute("UPDATE observations SET status='COMPLETE' WHERE id=?",(job['id'],))
                store.db.commit();continue
            if repairs>=3:
                store.db.execute("UPDATE observations SET status='PARTIAL' WHERE id=?",(job['id'],))
                store.event('OBSERVATION_UNRESOLVED',job['id'],{'first_missing_ms':missing},now)
                continue
            start=missing
            repairs+=1
        end=min(complete_until,start+1000*MINUTE)
        if end<=start:
            store.db.execute('UPDATE observations SET next_fetch_ms=? WHERE id=?',(now+MINUTE,job['id']))
            store.db.commit();continue
        missing=first_missing(store,job['symbol'],start,end)
        status='COLLECTING'
        try:
            if missing is not None:
                used+=1
                count=max(1,min(1000,(end-start+MINUTE-1)//MINUTE))
                raw=client.candles(job['symbol'],'1m',limit=count,startTime=start,endTime=end-1)
                rows=[]
                seen=set(); invalid=0
                for row in raw:
                    try:
                        valid=closed_bars([row],MINUTE,now,minimum=1,require_current=False)
                        if not start<=valid[0][0]<end or valid[0][0] in seen:
                            raise ValueError('OBSERVATION_RANGE_OR_DUPLICATE')
                        seen.add(valid[0][0]);rows.extend(valid)
                    except (ValueError,TypeError,IndexError):
                        invalid+=1
                if invalid:
                    store.event('OBSERVATION_INVALID_ROWS',job['id'],{'count':invalid,'start_ms':start},now)
                store.candles(job['symbol'],'1m',sorted(rows,key=lambda r:r[0]))
            cursor=end if job['cursor_ms']<job['end_ms'] else job['end_ms']
            if cursor>=job['end_ms']:
                missing=first_missing(store,job['symbol'],job['start_ms'],job['end_ms'])
                status='COMPLETE' if missing is None else 'REPAIRING' if repairs<3 else 'PARTIAL'
            delay=MINUTE if status=='COLLECTING' else 5*MINUTE
            store.db.execute('UPDATE observations SET cursor_ms=?,status=?,next_fetch_ms=?,attempts=attempts+1,repairs=? WHERE id=?',
                             (cursor,status,now+delay,repairs,job['id']))
            store.db.commit()
        except (DataUnavailable,ValueError,KeyError,TypeError) as exc:
            reason=str(exc) if isinstance(exc,DataUnavailable) else type(exc).__name__
            store.event('OBSERVATION_ERROR',job['id'],{'reason':reason},now)
            store.db.execute('UPDATE observations SET next_fetch_ms=?,attempts=attempts+1,status=? WHERE id=?',
                             (now+5*MINUTE,'COLLECTING' if isinstance(exc,DataUnavailable) else 'INVALID_DATA',job['id']))
            store.db.commit()
            if isinstance(exc,DataUnavailable):
                break
    return used


def funnel_report(store,include_coverage=True):
    """SQL aggregates keep heartbeat memory bounded as evidence accumulates."""
    db=store.db
    def groups(table,field,where=''):
        return {r[0]:r[1] for r in db.execute(f'SELECT {field},COUNT(*) FROM {table} {where} GROUP BY {field}')}
    def count(table,where=''):
        return db.execute(f'SELECT COUNT(*) FROM {table} {where}').fetchone()[0]
    states=groups('plans','status')
    result=dict(evaluated=count('assessments'),
        setups=count('assessments',"WHERE json_extract(payload,'$.status')='SETUP'"),
        accepted=sum(states.values()),
        entered=count('plans',"WHERE json_type(payload,'$.actual_entry_reference') IS NOT NULL"),
        closed=states.get('CLOSED',0),cancelled=states.get('CANCELLED',0),
        unresolved=states.get('DATA_HOLD',0)+states.get('UNRESOLVED_DATA',0),
        funding_pending=states.get('FUNDING_PENDING',0),statuses=states,
        admission_reasons=groups('observations',"json_extract(payload,'$.admission')"),
        cancellations=groups('plans',"coalesce(json_extract(payload,'$.cancel_reason'),'UNKNOWN')","WHERE status='CANCELLED'"),
        observation_statuses=groups('observations','status'),
        scan_count=count('events',"WHERE kind='SCAN_HEALTH'"),
        low_coverage_scans=count('events',"WHERE kind='SCAN_HEALTH' AND json_extract(payload,'$.coverage_below_95')=1"),
        unresolved_are_not_losses=True,cancellations_are_not_trades=True)
    if include_coverage:
        keys=('scan_slot','universe','attempted','assessed','coverage_fraction','coverage_below_95','elapsed_seconds')
        result['coverage']=[{k:s.get(k) for k in keys} for s in
            (json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE kind='SCAN_HEALTH' ORDER BY id"))]
    return result

