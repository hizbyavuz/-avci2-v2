"""Equal-day reports and predeclared paired calendar-block comparisons."""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ta_paper.replay import portfolio
from .data import DAY, START, END, PROTOCOL, SEED
from .download import atomic

HYPOTHESES=('taker','oi','funding','relative')
UNKNOWN={'OPEN_AT_HORIZON','UNRESOLVED_DATA','FUNDING_UNRESOLVED'}


def day_values(rows,field='net_r'):
    days=defaultdict(list)
    for p in rows:
        if p['status']=='CLOSED' and p.get(field) is not None:days[int(p['entry_ms'])//DAY].append(p[field])
    return {d:float(np.mean(v)) for d,v in days.items()}


def summary(rows,field='net_r'):
    closed=[p for p in rows if p['status']=='CLOSED' and p.get(field) is not None]
    daily=day_values(closed,field);values=list(daily.values())
    return dict(events=len(rows),unique_event_ids=len({p['id'] for p in rows}),statuses=dict(Counter(p['status'] for p in rows)),
                closed=len(closed),entry_days=len(daily),calendar_weeks=len({d//7 for d in daily}),
                day_mean_r=float(np.mean(values)) if values else None,
                day_standard_error=float(np.std(values,ddof=1)/math.sqrt(len(values))) if len(values)>1 else None,
                trade_mean_r=float(np.mean([p[field] for p in closed])) if closed else None,
                win_rate=sum(p[field]>0 for p in closed)/len(closed) if closed else None,
                incomplete=sum(p['status'] in UNKNOWN for p in rows),
                note='Days/weeks remain dependent; standard error is descriptive. Cancellations are not trades.')


def sensitivity(rows,field='net_r_1_5'):
    days=defaultdict(list)
    for p in rows:
        if p['status']=='CLOSED' and p.get(field) is not None:days[int(p['entry_ms'])//DAY].append(p[field])
        elif p['status'] in UNKNOWN:days[int(p['entry_ms'])//DAY].append(-1.)
    return dict(unknown_assumption_r=-1,day_mean_r=float(np.mean([np.mean(v) for v in days.values()])) if days else None,
                note='Sensitivity only: -1R is neither an imputed outcome nor a guaranteed worst case.')


def comparison(left,right,field='net_r_1_5',label='',iterations=None):
    a=day_values(left,field);b=day_values(right,field);common=sorted(a.keys()&b.keys())
    n=iterations or PROTOCOL['pass_requirements']['bootstrap_iterations']
    result=dict(paired_entry_days=len(common),left_days=len(a),right_days=len(b),
                left_only_days=len(a.keys()-b.keys()),right_only_days=len(b.keys()-a.keys()),
                mean_day_difference_r=None,one_sided_adjusted_lower_bound=None,
                alpha=.05/24,iterations=n,calendar_blocks=0)
    if not common:return result
    values={d:a[d]-b[d] for d in common}
    weeks=list(range((START//DAY)//7,((END//DAY)+1)//7+1))
    totals=np.array([sum(v for d,v in values.items() if d//7==w) for w in weeks])
    counts=np.array([sum(d//7==w for d in common) for w in weeks])
    result.update(mean_day_difference_r=float(np.mean(list(values.values()))),
                  calendar_blocks=len(weeks),supported_blocks=int(np.sum(counts>0)))
    if np.sum(counts>0)<2:return result
    seed=int(hashlib.sha256((SEED+':'+label).encode()).hexdigest()[:16],16)
    rng=np.random.default_rng(seed);draws=rng.integers(0,len(weeks),size=(n,len(weeks)))
    den=counts[draws].sum(axis=1);num=totals[draws].sum(axis=1)
    samples=num[den>0]/den[den>0]
    result['one_sided_adjusted_lower_bound']=float(np.quantile(samples,.05/24))
    result['descriptive_95pct_interval']=[float(x) for x in np.quantile(samples,[.025,.975])]
    return result


def top_removed(rows):
    coin=defaultdict(float)
    for p in rows:
        if p['status']=='CLOSED':coin[p['symbol']]+=p['net_r']
    top=sorted(coin,key=lambda s:(-coin[s],s))[:3]
    return top,[p for p in rows if p['symbol'] not in top]


def hypothesis_report(h,direction,strategy,all_events,links,portfolio_bundle=None):
    source=[p for p in strategy if p['direction']==direction]
    known=[p for p in source if p['hypotheses'].get(h) is not None]
    chosen=[p for p in known if p['hypotheses'][h]]
    controls={};matched={};coverage={}
    for kind in ('random_direction','random_timing'):
        rows=[];pairs_left=[];pairs_right=[];missing=Counter()
        for p in chosen:
            other=all_events.get(links.get(p['id'],{}).get(kind))
            if other is None:missing['NO_CONTROL']+=1;continue
            if other['hypotheses'].get(h) is None:missing['FEATURE_UNAVAILABLE']+=1;continue
            rows.append(other)
            if p['status']=='CLOSED' and other['status']=='CLOSED':
                pairs_left.append(p)
                # Matched event's day is the original event day, explicitly.
                pairs_right.append(dict(other,entry_ms=p['entry_ms']))
        controls[kind]=rows
        matched[kind]=comparison(pairs_left,pairs_right,label=h+direction+kind+':matched')
        matched[kind]['day_anchor']='original strategy event UTC entry day'
        coverage[kind]=dict(missing=dict(missing),mapped=len(rows),unique=len({p['id'] for p in rows}))
    comparisons={'baseline':comparison(chosen,known,label=h+direction+'baseline')}
    comparisons.update({k:comparison(chosen,v,label=h+direction+k) for k,v in controls.items()})
    base=summary(chosen);stress=summary(chosen,'net_r_1_5');top,reduced=top_removed(chosen)
    reduced_summary=summary(reduced,'net_r_1_5');required=PROTOCOL['pass_requirements']
    checks=dict(minimum_closed=base['closed']>=required['minimum_closed_per_side'],
                minimum_entry_days=base['entry_days']>=required['minimum_entry_days_per_side'],
                stress_positive=stress['day_mean_r'] is not None and stress['day_mean_r']>0,
                without_top3_positive=reduced_summary['day_mean_r'] is not None and reduced_summary['day_mean_r']>0)
    for name,c in comparisons.items():
        checks[name+'_paired_days']=c['paired_entry_days']>=required['minimum_entry_days_per_side']
        checks[name+'_advantage']=c['one_sided_adjusted_lower_bound'] is not None and c['one_sided_adjusted_lower_bound']>0
    insufficient=not checks['minimum_closed'] or not checks['minimum_entry_days'] or any(not checks[k] for k in checks if k.endswith('_paired_days'))
    verdict='INCONCLUSIVE_SAMPLE_OR_COMMON_COVERAGE' if insufficient else 'RESEARCH_CANDIDATE_NOT_VALIDATED' if all(checks.values()) else 'DID_NOT_PASS'
    if portfolio_bundle is None:
        combined=[p for p in strategy if p['hypotheses'].get(h) is True]
        portfolio_bundle=portfolio(combined)
    joint,rejections,equity=portfolio_bundle
    limited=[p for p in joint if p['direction']==direction]
    return dict(hypothesis=h,direction=direction,verdict=verdict,checks=checks,
                source_setups=len(source),feature_available=len(known),feature_missing=len(source)-len(known),selected=len(chosen),
                base=base,cost_1_5=stress,removed_top3=top,without_top3_cost_1_5=reduced_summary,
                unknown_sensitivity=sensitivity(chosen),comparisons=comparisons,matched_event_comparisons=matched,
                control_coverage=coverage,control_summaries={k:summary(v,'net_r_1_5') for k,v in controls.items()},
                portfolio_approximation=dict(base=summary(limited),cost_1_5_same_base_admissions=summary(limited,'net_r_1_5'),
                    rejections=rejections,realized_equity=equity,
                    warning='Shared LONG/SHORT ledger; equity/rejections refer to the combined portfolio. 72h-open/unknown cases retain their slot. No forced exit or live-execution claim.'),
                by_btc_regime={regime:summary([p for p in chosen if p['features']['btc_regime']==regime],'net_r_1_5') for regime in ('UP','DOWN','SIDEWAYS','UNKNOWN')},
                by_signal_month={month:summary([p for p in chosen if __import__('datetime').datetime.fromtimestamp(p['observed_ms']/1000,__import__('datetime').timezone.utc).strftime('%Y-%m')==month],'net_r_1_5') for month in ['2025-10','2025-11','2025-12',*[f'2026-{i:02}' for i in range(1,8)]]})


def build_report(output):
    from .study import read_gzip
    output=Path(output);events={};links={};counts=Counter();issues=Counter();oi_issues=Counter()
    for path in sorted((output/'outcomes').glob('*.json.gz')):
        data=read_gzip(path);links.update(data['links']);counts.update(data.get('counts',{}))
        for p in data['events']:
            if p['id'] in events and p!=events[p['id']]:raise ValueError('CONFLICTING_OUTCOME_ID')
            events[p['id']]=p
        issues.update(x['reason'] for x in data.get('data_issues',[]))
        oi_issues.update(x['reason'] for x in data.get('oi_quality',{}).get('issues',[]))
    strategy=[p for p in events.values() if p['arm']=='strategy']
    base_portfolio,base_rejections,base_equity=portfolio(strategy)
    portfolios={h:portfolio([p for p in strategy if p['hypotheses'].get(h) is True]) for h in HYPOTHESES}
    report=dict(status='EXPLORATORY_VALIDATION_NO_LIVE_PROFITABILITY_CLAIM',
                protocol=PROTOCOL,identity=json.loads((output/'run_identity.json').read_text()),
                run_status=json.loads((output/'status.json').read_text()),
                universe=json.loads((output/'universe.json').read_text()),counts=dict(counts),
                coarse_execution_issues=dict(issues),oi_issues=dict(oi_issues),
                baseline={side:dict(base=summary([p for p in strategy if side=='ALL' or p['direction']==side]),
                    cost_1_5=summary([p for p in strategy if side=='ALL' or p['direction']==side],'net_r_1_5')) for side in ('ALL','LONG','SHORT')},
                baseline_portfolio=dict(base=summary(base_portfolio),cost_1_5_same_base_admissions=summary(base_portfolio,'net_r_1_5'),
                    rejections=base_rejections,realized_equity=base_equity,
                    sides={s:summary([p for p in base_portfolio if p['direction']==s],'net_r_1_5') for s in ('LONG','SHORT')}),
                hypotheses={h:{side:hypothesis_report(h,side,strategy,events,links,portfolios[h]) for side in ('LONG','SHORT')} for h in HYPOTHESES})
    atomic(output/'report.json',report)
    compact={h:{s:dict(verdict=r['verdict'],closed=r['base']['closed'],days=r['base']['entry_days'],
                 day_mean_r=r['base']['day_mean_r'],day_mean_r_1_5=r['cost_1_5']['day_mean_r'],missing_features=r['feature_missing'])
                 for s,r in sides.items()} for h,sides in report['hypotheses'].items()}
    atomic(output/'summary.json',dict(baseline=report['baseline'],hypotheses=compact,status=report['run_status']))
    print(json.dumps({'phase':'REPORT_COMPLETE','summary':compact}),flush=True)
    return report
