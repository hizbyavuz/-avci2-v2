"""Independent, versioned paper execution: includes failed/suppressed deliveries.

Entry is the next full 1m candle open after signal observation (0-60s delay).
Stops/targets never move. Fees and signal-book slippage are assumptions, not fills.
Missing paths/funding remain unresolved. Existing outcome tables are untouched.
"""
import json
import math
import os
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime

from long_short_outcomes_v2 import label_path

VERSION = 'INDEPENDENT_PAPER_V1_2026_10_15'
HORIZON_MINUTES = 180  # existing protocol, not chosen from observed performance


def pending_symbols(db_path, now_ms=None):
    """Keep post-signal candles subscribed even after a setup leaves the pool."""
    if not os.path.isfile(db_path):return []
    now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
    with closing(sqlite3.connect('file:'+os.path.abspath(db_path)+'?mode=ro',uri=True,timeout=3)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():return []
        rows=c.execute('''SELECT id,symbol FROM events WHERE stage_to='TRIGGERED'
            AND json_valid(payload_json) AND json_extract(payload_json,'$._native_chain_v2')=1
            AND unixepoch(event_time_utc) BETWEEN ? AND ? ORDER BY id''',
            (now_ms//1000-(HORIZON_MINUTES+1)*60,now_ms//1000)).fetchall()
        finished=set()
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='independent_paper_v1'").fetchone():
            finished={r[0] for r in c.execute("SELECT event_id FROM independent_paper_v1 WHERE state IN ('RESOLVED','ENTRY_MISSED','INVALID_SIGNAL')")}
    return list(dict.fromkeys(s for eid,s in rows if eid not in finished and isinstance(s,str) and s.endswith('USDT')))


def evaluate(direction, stop, target, bars, entry_ms, now_ms, cost_pct, min_net_r=1.0, required_room_pct=0.0):
    if now_ms < entry_ms + 60000:
        return {'state': 'PENDING'}
    count = min(HORIZON_MINUTES, (now_ms-entry_ms)//60000)
    path = [b for b in bars if entry_ms <= int(b[0]) < entry_ms+count*60000]
    # Validate the prefix as it is consumed: a gap after an exit cannot erase it.
    valid = []
    entry = None
    for i in range(count):
        if i >= len(path) or int(path[i][0]) != entry_ms+i*60000:
            return {'state': 'DATA_UNRESOLVED', 'reason': 'MISSING_1M_PATH'}
        b = path[i]
        if (len(b) < 7 or int(b[6]) != int(b[0])+59999 or
                any(not math.isfinite(float(v)) or float(v) <= 0 for v in b[1:5]) or
                not float(b[3]) <= min(float(b[1]),float(b[4])) <= max(float(b[1]),float(b[4])) <= float(b[2])):
            return {'state': 'DATA_UNRESOLVED', 'reason': 'INVALID_1M_CANDLE'}
        if entry is None:
            entry = float(b[1])
            if direction not in ('LONG','SHORT'):
                raise ValueError('invalid direction')
            if not (stop < entry < target if direction=='LONG' else target < entry < stop):
                return {'state': 'ENTRY_MISSED', 'reason': 'LEVEL_PASSED_BEFORE_ENTRY', 'entry_price': entry}
            risk, reward = abs(entry-stop)/entry*100, abs(target-entry)/entry*100
            if reward < required_room_pct:
                return {'state': 'ENTRY_MISSED', 'reason': 'ROOM_LOST_BEFORE_ENTRY', 'entry_price': entry}
            if (reward-cost_pct)/(risk+cost_pct) < min_net_r:
                return {'state': 'ENTRY_MISSED', 'reason': 'NET_R_LOST_BEFORE_ENTRY', 'entry_price': entry}
        valid.append(b)
        # Evaluate only once at a barrier or the current end of the prefix.
        hit = (float(b[3])<=stop or float(b[2])>=target) if direction=='LONG' else (float(b[2])>=stop or float(b[3])<=target)
        if hit or i == count-1:
            result = label_path(direction, entry, stop, target, valid, cost_pct)
            if result['result'] != 'TIMEOUT' or count == HORIZON_MINUTES:
                return {**result, 'state': 'PRICE_RESOLVED', 'entry_price': entry,
                        'entry_ms': entry_ms, 'holding_minutes_upper_bound': (result['exit_ms']-entry_ms)/60000}
    return {'state': 'PENDING', 'entry_price': entry, 'entry_ms': entry_ms}


def summarize(rows):
    counts = Counter(r['state'] for r in rows)
    done = [r for r in rows if r['state']=='RESOLVED']
    values = [r['net_r'] for r in done]
    gains, losses = sum(max(v,0) for v in values), -sum(min(v,0) for v in values)
    return {'total_signals':len(rows), 'states':dict(counts), 'closed':len(done),
            'net_wins':sum(v>0 for v in values), 'net_losses':sum(v<0 for v in values),
            'win_rate_pct':100*sum(v>0 for v in values)/len(values) if values else None,
            'expectancy_net_r':sum(values)/len(values) if values else None,
            'profit_factor':gains/losses if losses else None,
            'profit_factor_status':'DEFINED' if losses else 'NO_LOSSES_OR_NO_SAMPLE',
            'mean_net_return_pct':sum(r['net_return_pct'] for r in done)/len(done) if done else None,
            'unresolved_count':counts['DATA_UNRESOLVED'],
            'pessimistic_one_r_sensitivity':((sum(values)-counts['DATA_UNRESOLVED'])/
                (len(values)+counts['DATA_UNRESOLVED'])) if len(values)+counts['DATA_UNRESOLVED'] else None,
            'sensitivity_note':'-1R per unresolved is a sensitivity assumption, not a maximum gap loss',
            'edge_proven':False}


def resolve(db_path, now_ms=None, bars_reader=None, funding_reader=None):
    from long_short_live_pool import native_futures_path_bars
    from long_short_native_derivatives import relay_get, SOURCE, number
    now_ms = int(time.time()*1000) if now_ms is None else int(now_ms)
    bars_reader = bars_reader or native_futures_path_bars
    funding_reader = funding_reader or relay_get
    with closing(sqlite3.connect(db_path,timeout=15)) as c:
        c.row_factory=sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            print('INDEPENDENT_PAPER_WAITING events table not created',flush=True)
            return {'version':VERSION,'state':'WAITING_FOR_EVENTS_TABLE'}
        c.execute('''CREATE TABLE IF NOT EXISTS independent_paper_v1(
            event_id INTEGER PRIMARY KEY, checked_ms INTEGER, state TEXT, payload TEXT)''')
        c.execute('CREATE INDEX IF NOT EXISTS ix_independent_event_stage_time ON events(stage_to,event_time_utc)')
        c.commit()
        # Native-only selection happens BEFORE the work limit. Completed legacy
        # events and repeated unresolved rows cannot starve newer signals.
        events=c.execute('''SELECT e.* FROM events e LEFT JOIN independent_paper_v1 p ON p.event_id=e.id
            WHERE e.stage_to='TRIGGERED' AND json_valid(e.payload_json)
            AND json_extract(e.payload_json,'$._native_chain_v2')=1
            AND (p.state IS NULL OR p.state NOT IN ('RESOLVED','ENTRY_MISSED','INVALID_SIGNAL'))
            ORDER BY COALESCE(p.checked_ms,0),e.id LIMIT 50''').fetchall()
        for ev in events:
            p=json.loads(ev['payload_json'])
            base={'version':VERSION,'symbol':ev['symbol'],'direction':ev['direction'],
                  'strategy_cohort':(p.get('_strategy_identity') or {}).get('cohort_id','LEGACY_UNVERSIONED'),
                  'telegram_status':ev['telegram_status'],'signal_time':ev['event_time_utc'],
                  'horizon_minutes':HORIZON_MINUTES,
                  'execution_model':'NEXT_FULL_1M_OPEN_PLUS_SIGNAL_BOOK_COST_PROXY',
                  'actual_fills':False,'edge_proven':False}
            try:
                gate=p.get('_trigger_execution_gate') or {}
                if not gate.get('qualified'):
                    result={'state':'INVALID_SIGNAL','reason':'EXECUTION_GATE_NOT_QUALIFIED'}
                else:
                    start=datetime.fromisoformat(ev['event_time_utc'].replace('Z','+00:00'))
                    if start.tzinfo is None:raise ValueError('naive signal timestamp')
                    signal_ms=int(start.timestamp()*1000)
                    begin=(signal_ms//60000+1)*60000
                    cost=float(gate['round_trip_cost_pct'])
                    if not math.isfinite(cost) or cost<0:raise ValueError('invalid costs')
                    stop,target=float(p['invalidation']),float(p['target1'])
                    if not all(math.isfinite(x) and x>0 for x in (stop,target)):raise ValueError('invalid levels')
                    sg=json.loads(p.get('structure_gate_json') or '{}')
                    version=sg.get('structural_plan_v3',{})
                    base['geometry_version']=version.get('version','LEGACY_NATIVE_V2')
                    base['geometry_config_hash']=version.get('config_hash','LEGACY')
                    base['market_regime']=sg.get('_market_regime','UNKNOWN')
                    base['setup_type']=sg.get('_v3_setup_type','UNKNOWN')
                    result=evaluate(ev['direction'],stop,target,bars_reader(ev['symbol'],begin,HORIZON_MINUTES),
                                    begin,now_ms,cost,float(gate.get('min_net_t1_r',1.0)),
                                    float(gate.get('required_room_pct',0.0)))
                    if result['state']=='PRICE_RESOLVED':
                        rates=funding_reader('/funding',{'symbol':ev['symbol'],'startTime':str(begin),
                                             'endTime':str(result['exit_ms'])})
                        if rates.get('source')!=SOURCE or rates.get('symbol')!=ev['symbol'] or not isinstance(rates.get('rates'),list):
                            raise ValueError('unverified funding')
                        sign=1 if ev['direction']=='LONG' else -1
                        funding=0.0
                        seen=set()
                        for r in rates['rates']:
                            stamp=int(r['fundingTime'])
                            if r.get('symbol')!=ev['symbol'] or stamp in seen:raise ValueError('invalid funding rows')
                            seen.add(stamp)
                            if begin<=stamp<result['exit_ms']:
                                funding+=sign*number(r['fundingRate'])*100*number(r['markPrice'],True)/result['entry_price']
                        risk=abs(result['entry_price']-stop)/result['entry_price']*100
                        result.update(state='RESOLVED',funding_cost_pct=funding,
                            net_return_pct=result['net_return_pct']-funding,
                            net_r=(result['net_return_pct']-funding)/(risk+cost))
            except Exception as exc:
                result={'state':'DATA_UNRESOLVED','reason':type(exc).__name__+':'+str(exc)[:160]}
            row={**base,**result}
            c.execute('INSERT OR REPLACE INTO independent_paper_v1 VALUES(?,?,?,?)',
                      (ev['id'],now_ms,row['state'],json.dumps(row,allow_nan=False)))
            c.commit()
        groups=defaultdict(list)
        for raw, in c.execute('SELECT payload FROM independent_paper_v1'):
            row=json.loads(raw)
            groups[(row.get('strategy_cohort','LEGACY_UNVERSIONED'),
                    row.get('geometry_version','UNKNOWN'),row.get('geometry_config_hash','UNKNOWN'))].append(row)
        report={'version':VERSION,'groups':{':'.join(k):summarize(v) for k,v in groups.items()},
                'telegram_independent':True,'horizon_minutes':HORIZON_MINUTES,'edge_proven':False}
        print('INDEPENDENT_PAPER_SUMMARY '+json.dumps(report,allow_nan=False),flush=True)
        return report


if __name__ == '__main__':
    path=os.getenv('LS_LIVE_DB','long_short_live_pool.db')
    if not os.path.isfile(path):
        print('INDEPENDENT_PAPER_WAITING live database not created',flush=True)
    else:
        resolve(path)
