"""Offline review of an exported Railway log snapshot; never changes production.

python audits/model_consistency_report.py --evidence audits/model_review_20261015/runtime_evidence.json --contract audits/model_review_20261015/frozen_contract.json
All math is descriptive plan feasibility, not estimated profitability.
"""
import argparse
import json
import math
from collections import Counter
from datetime import datetime


def epoch(text):
    d=datetime.fromisoformat(text.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('naive time')
    return d.timestamp()


def records(bundle, stream, prefix, cutoff):
    out=[];issues=Counter()
    for row in bundle['streams'].get(stream,[]):
        try:
            at=epoch(row['timestamp'])
            if at>cutoff:
                issues['future_excluded']+=1;continue
            msg=row['message']
            if not msg.startswith(prefix+' '):
                issues['other_log_excluded']+=1;continue
            body=json.loads(msg[len(prefix)+1:])
            if not isinstance(body,(dict,list)):
                raise ValueError('not structured evidence')
            out.append((at,body))
        except (ValueError,TypeError,KeyError,AttributeError):
            issues['unparseable_or_interleaved']+=1
    return sorted(out,key=lambda x:x[0]),dict(issues)


def feasibility(e, direction):
    """Valid risk/reward ordering is mandatory before economic statistics."""
    try:
        entry,stop,target,cost,minimum_r,minimum_room=(float(e[k]) for k in
            ('entry','stop','target','cost_pct','min_net_r','required_room_pct'))
        if not all(math.isfinite(x) for x in (entry,stop,target,cost,minimum_r,minimum_room)):
            raise ValueError('nonfinite')
        if min(entry,stop,target)<=0 or min(cost,minimum_r,minimum_room)<0:
            raise ValueError('bad input')
        ordered=(stop<entry<target if direction=='LONG' else target<entry<stop if direction=='SHORT' else False)
        if not ordered:return {'state':'INVALID_GEOMETRY'}
        risk=abs(stop/entry-1)*100
        reward=(target/entry-1)*100*(1 if direction=='LONG' else -1)
        required=max(minimum_room,minimum_r*risk+(1+minimum_r)*cost)
        return {'state':'ORDERED_LEVELS','risk_pct':risk,'reward_pct':reward,
                'cost_pct':cost,'net_target_pct':reward-cost,
                'net_r':(reward-cost)/(risk+cost),
                'required_reward_pct':required,'reward_shortfall_pct':max(0,required-reward),
                'target_not_above_cost':reward<=cost,
                'passes_economic_gates':reward>=required}
    except (ValueError,TypeError,KeyError):
        return {'state':'MISSING_OR_INVALID_INPUT'}


def review(bundle,contract):
    scope=bundle['scope'];cutoff=epoch(scope['cutoff_utc'])
    for key in ('source_sha256','strategy_id'):
        if scope.get(key)!=contract[key]:raise ValueError('cohort mismatch: '+key)
    if scope.get('commit')!=contract['production_commit']:raise ValueError('commit mismatch')
    identities,id_issues=records(bundle,'identity','LS_STRATEGY_EFFECTIVE',cutoff)
    if not identities or any(p.get('id')!=contract['strategy_id'] or p.get('source_sha256')!=contract['source_sha256'] for _,p in identities):
        raise ValueError('runtime identity not verified')
    geometry,g_issues=records(bundle,'geometry','STRUCTURAL_PLAN_V4',cutoff)
    directions,d_issues=records(bundle,'direction','PRODUCTION_DIRECTION_AUDIT',cutoff)
    executions,x_issues=records(bundle,'execution','INDEPENDENT_PAPER_SUMMARY',cutoff)
    coverage,c_issues=records(bundle,'coverage','FUTURES_UNIVERSE_COVERAGE',cutoff)
    scans=[];seen=set();excluded=Counter();direction_index={}
    for at,d in directions:
        if d.get('strategy_id')!=contract['strategy_id']:
            excluded['other_direction_cohort']+=1;continue
        direction_index.setdefault(d.get('symbol'),[]).append((at,d))
    for at,g in geometry:
        e=g.get('evidence') or {}
        if e.get('version')!=contract['geometry_version']:
            excluded['other_geometry_version']+=1;continue
        symbol,side=g.get('symbol'),g.get('direction')
        if not symbol or side not in ('LONG','SHORT'):
            excluded['invalid_symbol_or_side']+=1;continue
        key=(symbol,side,int(at//300))
        if key in seen:
            excluded['repeated_symbol_direction_5m_bucket']+=1;continue
        seen.add(key)
        prior=[d for t,d in direction_index.get(symbol,[]) if 0<=at-t<=1]
        d=prior[0] if len(prior)==1 else None
        aligned=bool(d and d.get('eligible') and d.get('direction')==side)
        # A directionless fallback plan is still geometry research, never a
        # direction-approved proposal. Log adjacency is explicitly a proxy join.
        scans.append({'symbol':symbol,'direction':side,'observed_epoch':at,
            'direction_match':'UNIQUE_LOG_ADJACENCY' if d else 'MISSING_OR_AMBIGUOUS',
            'direction_eligible':aligned if d else None,
            'direction_reasons':d.get('decision_reasons',[]) if d else None,
            'geometry_reason':e.get('reason','UNKNOWN'),
            'geometry_qualified':bool(g.get('qualified')),
            'obstacle_timeframe':(e.get('target_band') or {}).get('timeframe'),
            'setup_type':(e.get('legacy_plan') or {}).get('setup_type'),
            'economics':feasibility(e,side),
            'paired_v3_qualified':(g.get('comparison') or {}).get('v3_qualified'),
            'paired_v4_qualified':(g.get('comparison') or {}).get('v4_qualified')})
    ordered=[r for r in scans if r['economics']['state']=='ORDERED_LEVELS']
    approved=[r for r in scans if r['direction_eligible'] is True]
    final_pass=[r for r in approved if r['geometry_qualified']]
    summary=executions[-1][1] if executions else None
    groups=summary.get('groups',{}) if summary else {}
    signals=sum(int(g.get('total_signals',0)) for g in groups.values())
    parsed_times=[r['observed_epoch'] for r in scans]
    return {'version':contract['version'],'production_commit':scope['commit'],
        'strategy_id':contract['strategy_id'],'source_sha256':scope['source_sha256'],
        'cutoff_utc':scope['cutoff_utc'],'scope':'RETAINED_DEEP_ANALYSIS_LOGS_NOT_FULL_UNIVERSE',
        'read_only':True,'production_rules_changed':False,'edge_proven':False,
        'observation_span_seconds':max(parsed_times)-min(parsed_times) if parsed_times else None,
        'raw_geometry_records':len(geometry),'distinct_scan_records':len(scans),
        'distinct_symbols':len({r['symbol'] for r in scans}),
        'not_independent_trades':True,'excluded':dict(excluded),
        'log_issues':{'geometry':g_issues,'direction':d_issues,'identity':id_issues,'execution':x_issues,'coverage':c_issues},
        'retrieval_may_be_capped':any(len(v)>=scope.get('retrieval_limit_per_stream',250) for v in bundle['streams'].values()),
        'geometry_reasons':dict(Counter(r['geometry_reason'] for r in scans)),
        'overlapping_obstacle_frames':dict(Counter(r['obstacle_timeframe'] for r in scans if r['geometry_reason']=='ENTRY_INSIDE_OPPOSING_ZONE')),
        'direction_joined':sum(r['direction_eligible'] is not None for r in scans),
        'direction_eligible':len(approved),'direction_and_geometry_passed':len(final_pass),
        'direction_eligible_geometry_reasons':dict(Counter(r['geometry_reason'] for r in approved)),
        'ordered_level_count':len(ordered),
        'ordered_targets_not_above_assumed_cost':sum(r['economics']['target_not_above_cost'] for r in ordered),
        'ordered_economic_gate_passes':sum(r['economics']['passes_economic_gates'] for r in ordered),
        'latest_coverage':coverage[-1][1] if coverage else None,
        'paper_ledger':{'available':summary is not None,'all_retained_cohorts':True,
                        'total_signals':signals if summary else None,'groups':groups},
        'confirmation_delay':{'status':'NOT_ESTIMABLE_NO_TRIGGERED_SAMPLE' if summary and signals==0 else 'REQUIRES_EVENT_LEVEL_JOIN',
                              'mean_seconds':None},
        'feature_ablation':{'status':'DIRECTION_DECISIONS_ONLY_NO_MATCHED_NET_PNL',
                            'incremental_profitability_established':False},
        'economic_formula':contract['required_reward_formula'],
        'cases':scans,'limitations':contract['limitations']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',required=True);parser.add_argument('--contract',required=True)
    args=parser.parse_args()
    with open(args.evidence) as f:bundle=json.load(f)
    with open(args.contract) as f:contract=json.load(f)
    print(json.dumps(review(bundle,contract),indent=2,ensure_ascii=False,allow_nan=False))
