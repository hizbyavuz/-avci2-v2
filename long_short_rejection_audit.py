"""Frozen-plan counterfactuals for observed/rejected candidates, never signals.

Sampling starts 2026-10-15 03:46 Europe/Istanbul. First observation per
symbol/direction/source-cohort in a non-overlapping 180-minute window is primary.
This is an early-entry hypothesis, NOT a measured missed executable opportunity.
"""
import json
import math
import os
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime

from long_short_trade_ledger import evaluate, summarize, HORIZON_MINUTES

VERSION = 'REJECTION_AUDIT_V1_2026_10_15'
CUTOFF_MS = int(datetime.fromisoformat('2026-10-15T03:46:00+03:00').timestamp()*1000)
TERMINAL = ('RESOLVED', 'NO_FROZEN_PLAN', 'ENTRY_MISSED', 'INVALID_OBSERVATION')


def init(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS rejection_audit_cursor(version TEXT PRIMARY KEY,last_id INTEGER);
        CREATE TABLE IF NOT EXISTS rejection_audit_episodes(
            observation_id INTEGER PRIMARY KEY,symbol TEXT,direction TEXT,observed_ms INTEGER,
            strategy_id TEXT,data_cohort TEXT,block_class TEXT,checked_ms INTEGER,
            state TEXT,payload TEXT,result TEXT);
        CREATE INDEX IF NOT EXISTS rejection_audit_sampling ON
            rejection_audit_episodes(symbol,direction,strategy_id,data_cohort,observed_ms);
        CREATE INDEX IF NOT EXISTS rejection_observations_time ON observations(observed_ms);
    ''')


def frozen_snapshot(row):
    p=json.loads(row['payload']); plan=p.get('setup_plan') or {}
    gate=p.get('structure_gate') or {}; d=p.get('direction_engine') or {}
    direction=plan.get('direction')
    if direction not in ('LONG','SHORT'):direction=row['direction'] or 'NONE'
    if row['cohort']=='DATA_BLOCKED':group='DATA_BLOCKED'
    elif row['cohort']=='NOT_SELECTED':group='SCAN_SELECTION_BLOCKED'
    elif row['cohort']!='FULL_4_OF_4':group='PARTIAL_DATA'
    elif not d.get('eligible'):group='DIRECTION_OR_TRADABILITY_BLOCKED'
    elif not gate.get('qualified_precheck'):group='STRUCTURE_BLOCKED'
    else:group='PRECHECK_PASSED_NOT_ENTRY_CONFIRMED'
    identity=p.get('strategy_identity') or gate.get('_strategy_identity') or {}
    return {'version':VERSION,'symbol':row['symbol'],'direction':direction,
        'observed_ms':row['observed_ms'],'observed_price':row['price'],
        'strategy_id':identity.get('id','LEGACY_IDENTITY_UNAVAILABLE'),
        'data_cohort':row['cohort'],'block_class':group,
        'direction_reasons':d.get('decision_reasons',[]),
        'hard_blockers':d.get('hard_blockers',[]),
        'structure_reason':(gate.get('structural_plan_v3') or {}).get('reason'),
        'structure_reasons':gate.get('reasons',[]),
        'missing':p.get('missing',[]),'data_reason':p.get('reason'),
        'stop':plan.get('invalidation'),'target':plan.get('target1'),
        'planned_trigger':plan.get('trigger_level'),
        'cost_pct':(gate.get('structural_plan_v3') or {}).get('cost_pct',gate.get('minimum_round_trip_cost_pct')),
        'btc_regime':p.get('btc_regime'),'quote_volume_24h':p.get('quote_volume_24h'),
        'model':'NEXT_FULL_1M_OPEN_WITHOUT_ENTRY_OR_R_GATE_FIXED_OBSERVED_LEVELS',
        'research_only':True,'actual_fills':False,'execution_proven':False}


def ingest(c,now_ms):
    old=c.execute('SELECT last_id FROM rejection_audit_cursor WHERE version=?',(VERSION,)).fetchone()
    cursor=old[0] if old else 0
    rows=c.execute('''SELECT * FROM observations WHERE id>? AND observed_ms>=?
        ORDER BY id LIMIT 500''',(cursor,CUTOFF_MS)).fetchall()
    for row in rows:
        # Do not freeze a future-dated record using an earlier evaluation clock.
        if row['observed_ms']>now_ms:break
        state='PENDING'
        try:
            snap=frozen_snapshot(row)
            json.dumps(snap,allow_nan=False)
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            state='INVALID_OBSERVATION'
            snap={'symbol':row['symbol'],'direction':row['direction'] or 'NONE',
                'observed_ms':row['observed_ms'],'strategy_id':'UNKNOWN',
                'data_cohort':row['cohort'],'block_class':'INVALID_OBSERVATION',
                'reason':type(exc).__name__,'research_only':True}
        prior=c.execute('''SELECT observed_ms FROM rejection_audit_episodes
            WHERE symbol=? AND direction=? AND strategy_id=? AND data_cohort=?
            ORDER BY observed_ms DESC LIMIT 1''',(snap['symbol'],snap['direction'],snap['strategy_id'],snap['data_cohort'])).fetchone()
        if not prior or row['observed_ms']>=prior[0]+(HORIZON_MINUTES+1)*60000:
            c.execute('INSERT OR IGNORE INTO rejection_audit_episodes VALUES(?,?,?,?,?,?,?,0,?,?,NULL)',
                (row['id'],snap['symbol'],snap['direction'],snap['observed_ms'],snap['strategy_id'],snap['data_cohort'],
                 snap['block_class'],state,json.dumps(snap,allow_nan=False)))
        cursor=row['id']
    c.execute('INSERT OR REPLACE INTO rejection_audit_cursor VALUES(?,?)',(VERSION,cursor))
    c.commit()


def outcome(snap,now_ms,bars_reader,funding_reader):
    from long_short_native_derivatives import SOURCE, number
    try:
        stop,target,cost=(float(snap[k]) for k in ('stop','target','cost_pct'))
        if (snap['direction'] not in ('LONG','SHORT') or min(stop,target)<=0 or cost<0
                or not all(math.isfinite(v) for v in (stop,target,cost))):raise ValueError('bad plan')
    except (ValueError,TypeError,KeyError):
        return {'state':'NO_FROZEN_PLAN','reason':'NO_POINT_IN_TIME_DIRECTION_LEVELS_OR_COST'}
    begin=(snap['observed_ms']//60000+1)*60000
    if now_ms<begin+60000:return {'state':'PENDING'}
    try:
        # Deliberately bypass room/R/confirmation filters only in this isolated
        # research calculation, otherwise it would merely repeat the rejection.
        result=evaluate(snap['direction'],stop,target,
            bars_reader(snap['symbol'],begin,HORIZON_MINUTES),begin,now_ms,cost,
            min_net_r=-1e300,required_room_pct=0.0)
        if result['state']!='PRICE_RESOLVED':return result
        rates=funding_reader('/funding',{'symbol':snap['symbol'],'startTime':str(begin),'endTime':str(result['exit_ms'])})
        if (rates.get('source')!=SOURCE or rates.get('symbol')!=snap['symbol']
                or not isinstance(rates.get('rates'),list)):raise ValueError('UNVERIFIED_FUNDING')
        funding=0.0; seen=set(); sign=1 if snap['direction']=='LONG' else -1
        for row in rates['rates']:
            stamp=int(row['fundingTime'])
            if row.get('symbol')!=snap['symbol'] or stamp in seen:raise ValueError('INVALID_FUNDING_ROWS')
            seen.add(stamp)
            if begin<=stamp<result['exit_ms']:
                funding+=sign*number(row['fundingRate'])*100*number(row['markPrice'],True)/result['entry_price']
        net=result['net_return_pct']-funding
        risk=abs(result['entry_price']-stop)/result['entry_price']*100
        result.update(state='RESOLVED',funding_cost_pct=funding,net_return_pct=net,net_r=net/(risk+cost),
            additional_cost_sensitivity_pct={'plus_0.3_percentage_points':net-.3,'plus_0.7_percentage_points':net-.7})
        return result
    except Exception as exc:
        return {'state':'DATA_UNRESOLVED','reason':type(exc).__name__+':'+str(exc)[:160]}


def report(c):
    groups=defaultdict(list); samples=[]
    rows=c.execute('SELECT * FROM rejection_audit_episodes ORDER BY observation_id').fetchall()
    for row in rows:
        result=json.loads(row['result'] or '{}'); result.setdefault('state',row['state'])
        key=':'.join((row['strategy_id'],row['data_cohort'],row['block_class']))
        groups[key].append(result)
        if len(samples)<12:
            samples.append({'symbol':row['symbol'],'direction':row['direction'],'observed_ms':row['observed_ms'],'class':row['block_class'],
                **{k:result.get(k) for k in ('state','result','reason','net_return_pct','holding_minutes_upper_bound')}})
    source_counts=dict(c.execute('SELECT cohort,count(*) FROM observations WHERE observed_ms>=? GROUP BY cohort',(CUTOFF_MS,)).fetchall())
    return {'version':VERSION,'cutoff_utc':'2026-10-15T00:46:00Z',
        'scope':'RECORDED_ANALYSIS_AND_DATA_BLOCKS_NOT_FULL_MARKET_RECALL',
        'sampling':'FIRST_PER_SYMBOL_DIRECTION_STRATEGY_DATA_COHORT_NONOVERLAPPING_181_MIN',
        'observations':source_counts,'primary_episodes':len(rows),
        'states':dict(Counter(r['state'] for r in rows)),
        'groups':{k:summarize(v) for k,v in groups.items()},'sample':samples,
        'costs':'FROZEN_ANALYST_COST_PROXY_PLUS_HISTORICAL_FUNDING_NOT_ACTUAL_FILLS',
        'interpretation':'HYPOTHETICAL_EARLY_ENTRY_NOT_PROOF_OF_MISSED_EXECUTABLE_TRADES',
        'research_only':True,'edge_proven':False}


def resolve(now_ms=None,db_path=None,bars_reader=None,funding_reader=None):
    from long_short_data_opportunity_v2 import root
    from long_short_live_pool import native_futures_path_bars
    from long_short_native_derivatives import relay_get
    now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
    db_path=db_path or os.path.join(root(),'data_opportunity_v2.db')
    c=sqlite3.connect(db_path,timeout=10); c.row_factory=sqlite3.Row
    try:
        init(c); ingest(c,now_ms)
        rows=c.execute('''SELECT * FROM rejection_audit_episodes
            WHERE state IN ('PENDING','DATA_UNRESOLVED') AND checked_ms<?
            ORDER BY checked_ms,observation_id LIMIT 50''',(now_ms-60000,)).fetchall()
        for row in rows:
            result=outcome(json.loads(row['payload']),now_ms,bars_reader or native_futures_path_bars,funding_reader or relay_get)
            c.execute('UPDATE rejection_audit_episodes SET checked_ms=?,state=?,result=? WHERE observation_id=?',
                (now_ms,result['state'],json.dumps(result,allow_nan=False),row['observation_id']))
            c.commit()
        from long_short_rejection_direction_report import report as direction_report, emit
        directional=direction_report(c,now_ms)
        emit(directional)
        result=report(c)
        print('REJECTION_AUDIT_SUMMARY '+json.dumps(result,allow_nan=False),flush=True)
        result['directional_report']=directional
        return result
    finally:c.close()
