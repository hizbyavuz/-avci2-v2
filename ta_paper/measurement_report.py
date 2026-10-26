"""Read-only cohort report: python -m ta_paper.measurement_report --database PATH --venue BYBIT."""
import argparse
from collections import Counter,defaultdict
import json
import math
from pathlib import Path
import sqlite3


def summarize_shadow(rows):
    resolved=[r for r in rows if r['shadow_status']=='CLOSED_MODEL' and r.get('net_r_modelled') is not None]
    days=defaultdict(list)
    for r in resolved:days[int(r['entry_ms'])//86400000].append(r['net_r_modelled'])
    means=[sum(v)/len(v) for v in days.values()]
    avg=sum(means)/len(means) if means else None
    se=math.sqrt(sum((v-avg)**2 for v in means)/(len(means)-1)/len(means)) if len(means)>1 else None
    complete=[r for r in rows if r.get('path_summary',{}).get('complete') and r.get('entry_validation_passed')]
    return dict(status='UNVALIDATED_OBSERVATIONAL_MODEL',candidates=len(rows),
        statuses=dict(Counter(r['shadow_status'] for r in rows)),closed_modelled=len(resolved),
        mean_net_r_modelled=sum(r['net_r_modelled'] for r in resolved)/len(resolved) if resolved else None,
        entry_days=len(means),equal_day_mean_r=avg,day_standard_error=se,
        raw_reach_complete_paths={k:dict(hits=sum(r['path_summary']['reach'][k] is not None for r in complete),
            denominator=len(complete)) for k in ('3','5','7','10','15')},
        incomplete_paths=len(rows)-len(complete),unresolved_are_not_losses=True,
        raw_reach_is_not_net_profit=True,actual_exchange_fills=False,
        cancelled_or_missed_entries_are_not_trades=True)


def report(database,venue):
    path=Path(database).resolve()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result=dict(source_venue=venue,not_pooled_across_venues=True)
        if 'feature_events' in tables:
            result['features']={r[0]:r[1] for r in db.execute('SELECT kind,COUNT(*) FROM feature_events GROUP BY kind')}
        result['path_statuses']={r[0]:r[1] for r in db.execute('SELECT status,COUNT(*) FROM observations GROUP BY status')}
        if venue=='BYBIT':
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM shadow_trials')] if 'shadow_trials' in tables else []
            result['all']=summarize_shadow(rows)
            result['long']=summarize_shadow([r for r in rows if r['direction']=='LONG'])
            result['short']=summarize_shadow([r for r in rows if r['direction']=='SHORT'])
            result['delivery_states']={r[0]:r[1] for r in db.execute("SELECT state,COUNT(*) FROM outbox WHERE key LIKE 'watch:%' GROUP BY state")}
        else:
            from .core import descriptive_stats
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM plans')]
            result['paper_account']=descriptive_stats(rows)
            result['plan_statuses']=dict(Counter(r['status'] for r in rows))
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--venue',choices=['BINANCE','BYBIT'],required=True)
    args=parser.parse_args();print(json.dumps(report(args.database,args.venue),indent=2,allow_nan=False))


if __name__=='__main__':main()
