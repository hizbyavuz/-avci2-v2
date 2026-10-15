"""Independent, read-only validation of saved decisions; no scoring or orders.

180m is the declared primary endpoint; 15/60m are diagnostics. Cohorts and
horizons never pool. Direction-only benchmark is an equal LONG/SHORT choice
on the identical asset/time endpoint, NOT an executable strategy benchmark.
Reference costs exclude actual fills and funding; no profitability certification.
"""
import json
import math
import os
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

VERSION='CHART_RELIABILITY_REPORT_V1_2026_10_15'
PRIMARY_HORIZON=180


def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def stats(values):
    values=[x for x in values if finite(x)]
    n=len(values)
    wins=sum(x>0 for x in values)
    positive=sum(max(0,x) for x in values)
    negative=-sum(min(0,x) for x in values)
    return dict(n=n,positive=wins,negative=sum(x<0 for x in values),
        mean_pct=sum(values)/n if n else None,
        median_pct=sorted(values)[n//2] if n%2 else
                   (sum(sorted(values)[n//2-1:n//2+1])/2 if n else None),
        profit_factor=positive/negative if negative else None,
        profit_factor_status='DEFINED' if negative else 'NO_LOSSES_OR_NO_SAMPLE')


def summarize(rows, now_ms):
    groups={};seen=set();duplicates=0
    for row in rows:
        p=json.loads(row['payload']);h=row['horizon']
        sid=p.get('strategy_id','LEGACY_UNKNOWN')
        stage=row['stage']
        identity=p.get('episode_id') or row['event_key']
        if stage.startswith('DELIVERED_'):
            key=(sid,identity,stage,h)
        else:
            # Correlated scans remain observations, not independent trades.
            key=(sid,row['symbol'],row['observed_ms']//900000,row['direction'],stage,row['reason'],h)
        if key in seen:duplicates+=1;continue
        seen.add(key)
        gkey=(sid,stage,h)
        g=groups.setdefault(gkey,dict(strategy_id=sid,version=p.get('version'),stage=stage,horizon=h,
            primary_endpoint=h==PRIMARY_HORIZON,records=0,pending=0,missing=0,unresolved=0,
            resolved=0,symbols=set(),utc_days=set(),reasons=Counter(),barriers=Counter(),
            net=[],directional=[],pessimistic=[],daily_net=defaultdict(list),
            directions=Counter(),models=Counter(),scenarios=Counter(),invalid_entry_geometry=0))
        g['records']+=1;g['symbols'].add(row['symbol']);day=row['observed_ms']//86400000
        g['utc_days'].add(day);g['reasons'][row['reason']]+=1;g['directions'][row['direction']]+=1
        g['models'][p.get('model','UNCLASSIFIED')]+=1;g['scenarios'][p.get('scenario','UNKNOWN')]+=1
        if row['state'] is None:
            if now_ms<row['observed_ms']+(h+1)*60000:g['pending']+=1
            else:g['missing']+=1
            continue
        v=json.loads(row['outcome_payload'])
        if finite(v.get('directional_return_pct')):g['directional'].append(v['directional_return_pct'])
        if row['state']!='RESOLVED':
            g['unresolved']+=1
            if finite(v.get('pessimistic_net_return_pct')):g['pessimistic'].append(v['pessimistic_net_return_pct'])
            continue
        g['resolved']+=1;g['barriers'][v.get('first_level') or 'HORIZON']+=1
        if stage=='DELIVERED_READY' and finite(v.get('model_net_return_pct')):
            g['net'].append(v['model_net_return_pct']);g['daily_net'][day].append(v['model_net_return_pct'])
            geom=p.get('geometry') or {}
            if geom and (not geom.get('valid') or
                    p.get('risk_reward_filter_enabled',True) and
                    geom.get('net_reward_risk',0)<p.get('min_net_reward_risk',1)-1e-10):
                g['invalid_entry_geometry']+=1
    result=[]
    for g in groups.values():
        net=g.pop('net');directional=g.pop('directional');pess=g.pop('pessimistic')
        days=g.pop('daily_net');g['symbols']=len(g['symbols']);g['utc_days']=len(g['utc_days'])
        g['reference_net']=stats(net);g['direction_only']=stats(directional)
        g['direction_only']['equal_random_direction_mean_pct']=0.0 if directional else None
        g['direction_only']['scope']='RAW_ENDPOINT_DIRECTION_TEST_NOT_TRADES_OR_STOP_TARGET_COMPARISON'
        g['daily_reference_net']=[dict(utc_day=d,count=len(v),mean_pct=sum(v)/len(v)) for d,v in sorted(days.items())]
        g['known_boundary_pessimistic']=stats(net+pess)
        g['sensitivity_complete']=not (g['missing'] or g['unresolved']>len(pess))
        g['status']='UNVALIDATED_REFERENCE_MODEL'
        g['limitations']=['Costs are assumed; actual spread/slippage/funding not measured',
                         'Correlated observations and concurrent positions are not independent',
                         'No held-out executable strategy comparison has passed']
        result.append(g)
    return dict(groups=result,deduplicated_rows=duplicates,edge_proven=False)


def rejection_review(c, root, now_ms):
    """Fresh bounded diagnostic independent of the historical outcome backlog.

    Every selection is made from saved ex-ante decisions, ordered by time.
    Raw endpoint movement does not establish an executable missed opportunity.
    """
    from long_short_chart_first import STRATEGY_ID
    from long_short_chart_outcomes import outcome
    path=Path(root)/'binance_futures_kline_shadow.db'
    if not path.is_file():return dict(status='NATIVE_PATH_UNAVAILABLE')
    rows=c.execute('''SELECT * FROM assessments WHERE observed_ms>=? AND observed_ms<=?
        AND stage='REJECTED' AND direction IN ('LONG','SHORT')
        AND json_extract(payload,'$.strategy_id')=? ORDER BY observed_ms,id LIMIT 601''',
        (now_ms-3600000,now_ms-16*60000,STRATEGY_ID)).fetchall()
    k=sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True,timeout=3)
    groups={};seen=set();examples=[];missing=0
    try:
        for r in rows[:600]:
            p=json.loads(r['payload']);key=(r['symbol'],r['observed_ms']//900000,r['direction'],r['reason'])
            if key in seen:continue
            seen.add(key)
            if not finite(p.get('price')) or p['price']<=0:continue
            g=groups.setdefault(r['reason'],dict(reason=r['reason'],observations=0,missing=0,values=[],symbols=set()))
            g['observations']+=1;g['symbols'].add(r['symbol'])
            start=r['observed_ms']//60000*60000
            end=(r['observed_ms']+15*60000+59999)//60000*60000
            bars=k.execute('''SELECT open_ms,open,high,low,close,volume,close_ms
                FROM futures_closed_klines WHERE symbol=? AND interval='1m' AND open_ms>=? AND open_ms<?
                AND source IN ('BINANCE_UM_FUTURES_WEBSOCKET','BINANCE_UM_FUTURES_REST','BINANCE_UM_FUTURES_ARCHIVE')
                ORDER BY open_ms''',(r['symbol'],start,end)).fetchall()
            try:v=outcome(p,bars,15,p.get('cost_assumption_pct',.30))
            except (ValueError,TypeError,KeyError):g['missing']+=1;missing+=1;continue
            signed=v['directional_return_pct'];g['values'].append(signed)
            examples.append(dict(symbol=r['symbol'],direction=r['direction'],reason=r['reason'],
                observed_ms=r['observed_ms'],price=p['price'],endpoint_ms=v['endpoint_ms'],
                signed_endpoint_pct=round(signed,4),first_level=v.get('first_level'),
                barrier_status=v.get('first_barrier_status')))
    finally:k.close()
    output=[]
    for g in groups.values():
        values=g.pop('values');g['symbols']=len(g['symbols']);g['raw_directional_endpoint']=stats(values)
        g['endpoint_above_030pct']=sum(x>.30 for x in values);output.append(g)
    ranked=sorted(examples,key=lambda x:x['signed_endpoint_pct'])
    return dict(status='MEASURED',strategy_id=STRATEGY_ID,horizon_minutes=15,
        scope='REJECTED_OBSERVATIONS_RAW_ENDPOINT_NOT_EXECUTABLE_TRADES',
        observations=sum(g['observations'] for g in output),measured=len(examples),missing=missing,
        truncated=len(rows)>600,groups=output,
        examples_worst=ranked[:3],examples_best=list(reversed(ranked[-3:])),
        counts_are_correlated=True,selection_policy='FIRST_PER_SYMBOL_SIDE_REASON_15M_BUCKET_IN_LAST_HOUR')


def build(root, now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    path=Path(root)/'chart_first_v1.db'
    c=sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True,timeout=3)
    c.row_factory=sqlite3.Row
    try:
        # Separate budgets: failed/data-only scans can never displace deliveries.
        rows=[];truncated={}
        for delivered,cap,window in ((True,6000,30*86400000),(False,12000,86400000)):
            query="""WITH horizons(h) AS (VALUES(15),(60),(180))
              SELECT a.*,h.h AS horizon,o.state,o.payload AS outcome_payload
              FROM assessments a CROSS JOIN horizons h LEFT JOIN outcomes o
              ON o.assessment_id=a.id AND o.horizon=h.h
              WHERE a.observed_ms>=? AND (a.stage LIKE 'DELIVERED_%')=?
              ORDER BY a.observed_ms DESC,a.id DESC,h.h LIMIT ?"""
            batch=c.execute(query,(now_ms-window,delivered,cap+1)).fetchall()
            truncated['deliveries' if delivered else 'observations']=len(batch)>cap
            rows.extend(batch[:cap])
        data=summarize(rows,now_ms)
        active=[]
        for r in c.execute('SELECT * FROM episodes WHERE ended_ms IS NULL ORDER BY created_ms LIMIT 50'):
            p=json.loads(r['payload'])
            active.append(dict(symbol=r['symbol'],stage=r['stage'],delivery_stage=r['delivery_stage'],
                age_minutes=round((now_ms-r['created_ms'])/60000,1),strategy_id=p.get('strategy_id'),
                lifecycle_reason=p.get('lifecycle_reason'),path_status=p.get('path_guard',{}).get('status')))
        gates=[]
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='dispatch_audit'").fetchone():
            gates=[dict(r) for r in c.execute('''SELECT strategy_id,action,reason,COUNT(*) AS deduplicated_events,
                COUNT(DISTINCT symbol) AS symbols FROM dispatch_audit WHERE observed_ms>=?
                GROUP BY strategy_id,action,reason''',(now_ms-3600000,))]
        return dict(version=VERSION,observed_ms=now_ms,primary_horizon_minutes=PRIMARY_HORIZON,
            truncated=truncated,active=active,dispatch_last_hour=gates,
            rejection_review=rejection_review(c,root,now_ms),**data)
    finally:c.close()


def emit(root):
    report=build(root)
    target=Path(root)/'chart_reliability_latest.json'
    temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    temporary.replace(target)
    print('CHART_RELIABILITY_HEALTH '+json.dumps({k:v for k,v in report.items() if k not in ('groups','rejection_review')}),flush=True)
    print('CHART_REJECTION_REVIEW '+json.dumps(report['rejection_review']),flush=True)
    for g in report['groups']:
        print('CHART_RELIABILITY_COHORT '+json.dumps(g),flush=True)
    return report


if __name__=='__main__':
    emit(os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state')
