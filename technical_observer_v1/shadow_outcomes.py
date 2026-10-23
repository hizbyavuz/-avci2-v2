"""Frozen first-snapshot observation windows. No inferred entry or trading PnL.

All later candidate updates are retained, but do not become independent samples.
Missing/late 1m data remains unresolved, never a win, loss or fabricated candle.
"""
import math

from structure_engine.outcomes import measure
from structure_engine.runtime import SOURCES
from .shadow_config import VERSION, OPS
from . import shadow_store as store


def validate_path(rows, start, end, now):
    expected = start
    for b in rows:
        if b['source'] not in SOURCES: return 'UNVERIFIED_SOURCE'
        if b['open_ms'] != expected or b['close_ms'] != expected+60_000-1: return 'DATA_GAP'
        if b['close_ms'] >= now or b['received_ms'] > now: return 'UNAVAILABLE_CANDLE'
        if not all(isinstance(b[k],(int,float)) and math.isfinite(b[k]) and b[k]>0
                   for k in ('open','high','low','close')): return 'INVALID_PRICE'
        if not b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']:
            return 'INVALID_OHLC'
        expected += 60_000
    return 'COMPLETE' if expected==end else 'INCOMPLETE_HORIZON'


def barrier_contacts(side, stop, target, rows):
    """Reference-line touches only. Same-minute ordering is explicitly unknown."""
    stop = float(stop) if stop is not None else None
    target = float(target) if target is not None else None
    stop_ms = target_ms = None
    for b in rows:
        if stop is not None and stop_ms is None and (b['low']<=stop if side=='LONG' else b['high']>=stop):
            stop_ms=b['close_ms']
        if target is not None and target_ms is None and (b['high']>=target if side=='LONG' else b['low']<=target):
            target_ms=b['close_ms']
    first = ('SAME_MINUTE_ORDER_UNKNOWN' if stop_ms is not None and stop_ms==target_ms else
             'STOP_REFERENCE' if stop_ms is not None and (target_ms is None or stop_ms<target_ms) else
             'TARGET_REFERENCE' if target_ms is not None else 'NEITHER')
    return dict(stop_touch_ms=stop_ms,target_touch_ms=target_ms,first=first,
                stop_available=stop is not None,target_available=target is not None,
                entry_assumed=False,trade_result=None)


def variant(side, levels, rows, observed_ms, identity):
    raw = measure(dict(direction=side,asof_ms=observed_ms,plan_eligible=False),rows)
    # Strip names that could be mistaken for an executed or validated paper plan.
    movement = {k:raw.get(k) for k in ('status','raw_mfe_pct','raw_mae_pct','reach_minutes')}
    movement.update(reference_open=rows[0]['open'] if rows else None,
                    reference_kind='NEXT_FULL_MINUTE_OPEN_NOT_ENTRY')
    return dict(id=identity,side=side,levels=levels,raw_movement=movement,
                contacts=barrier_contacts(side,levels.get('stop'),levels.get('target'),rows),
                pnl=None,entry_assumed=False)


def measure_pending(c,source,now,directory):
    pending = c.execute('''SELECT g.*,s.evidence FROM groups_v1 g
        JOIN scans s ON s.id=g.scan_id LEFT JOIN outcomes o ON o.group_id=g.id
        WHERE o.group_id IS NULL AND g.end_ms<=? ORDER BY g.end_ms LIMIT ?''',
        (now,OPS['measurement_batch'])).fetchall()
    result = dict(checked=0,complete=0,unresolved=0,waiting_data=0)
    for g in pending:
        if not store.limits(directory)['ok']:
            result['resource_paused']=True; break
        rows = [dict(b) for b in source.execute('''SELECT open_ms,close_ms,open,high,low,close,source,received_ms
            FROM futures_closed_klines WHERE symbol=? AND interval='1m'
            AND open_ms>=? AND open_ms<? AND close_ms<? AND received_ms<=? ORDER BY open_ms''',
            (g['symbol'],g['start_ms'],g['end_ms'],now,now)).fetchall()]
        state=validate_path(rows,g['start_ms'],g['end_ms'],now)
        result['checked']+=1
        if state!='COMPLETE' and now<g['end_ms']+OPS['measurement_grace_ms']:
            result['waiting_data']+=1; continue
        pair=store.read_blob(directory,g['evidence'])
        old=[]; new=[]
        if state=='COMPLETE':
            for p in pair['old']['decisions']:
                old.append(variant(p['direction'],dict(entry=p['entry_reference'],stop=p['stop'],target=p['target']),
                                   rows,g['observed_ms'],p['event_id']))
            for ref in pair['new_records']:
                record=store.read_blob(directory,ref['evidence'])
                new.append(variant(record['direction'],record['display']['levels'],rows,
                                   g['observed_ms'],record['record_hash']))
        payload=dict(version=VERSION,group_id=g['id'],symbol=g['symbol'],status=state,
                     first_scan_id=g['scan_id'],reference_start_ms=g['start_ms'],end_ms=g['end_ms'],
                     measured_ms=now,cohort=g['cohort'],pair_ready=bool(g['pair_ready']),
                     representative='FIRST_SNAPSHOT_ONLY_LATER_UPDATES_NOT_INDEPENDENT',
                     path=rows,old=old,new=new,executed_entry=False,trade_pnl=None,
                     missing_data_is_not_loss=True,decision_effect='NONE')
        evidence=store.write_blob(directory,payload)
        c.execute('INSERT INTO outcomes VALUES(?,?,?,?,?)',
                  (g['id'],now,int(state=='COMPLETE'),evidence,store.pack(dict(status=state,
                   old_variants=len(old),new_variants=len(new)))))
        c.commit();result['complete' if state=='COMPLETE' else 'unresolved']+=1
    return result
