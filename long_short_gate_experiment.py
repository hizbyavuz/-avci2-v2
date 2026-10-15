"""Paired point-in-time gate experiment. Never authorizes alerts or orders.

Only one gate changes per arm, except the explicitly named combined arm.
The stop alternative is an observed eight-closed-5m-bar extreme, chosen before
future prices arrive. Targets and production thresholds never move to fit R.
"""
import copy
import json
import math
import time
from collections import Counter,defaultdict

VERSION='PAIRED_GATE_EXPERIMENT_V1_2026_10_15'
_last_slot=None

def variants(plan,gate,decision,evidence,k5,atr_pct):
    from long_short_data_opportunity_v2 import partial_fields
    p=copy.deepcopy(plan);g=copy.deepcopy(gate)
    config=g.get('structural_plan_v3') or {}
    cost=float(config.get('cost_pct',.3));min_r=float(config.get('min_net_r',1))
    room=float(config.get('required_room_pct',.9))
    direction=decision.get('direction')
    missing=partial_fields(evidence) if not evidence.get('ready') else []
    hard=list(dict.fromkeys(decision.get('hard_blockers') or []))
    if float(atr_pct)>=4 and 'volatility_floor_veto' not in hard:hard.append('volatility_floor_veto')
    # Score/edge and venue/liquidity vetoes stay present in every arm.
    common=[x for x in decision.get('decision_reasons',[]) if x!='derivatives_incomplete']
    common+= [x for x in hard if x!='derivatives_incomplete']
    if direction not in ('LONG','SHORT'):common.append('NO_DIRECTION')
    def arm(name,local=False,single=False,no_room=False,no_r=False):
        q=dict(p);reasons=list(dict.fromkeys(common))
        if not evidence.get('ready') and not (single and missing and len(missing)==1):
            reasons.append('DERIVATIVE_DATA')
        if direction!=q.get('direction'):reasons.append('DIRECTION_PLAN_MISMATCH')
        if local:
            lows=k5.get('low',[])[-8:];highs=k5.get('high',[])[-8:]
            if len(lows)!=8 or len(highs)!=8:reasons.append('LOCAL_STOP_HISTORY_MISSING')
            else:q['invalidation']=min(map(float,lows)) if q.get('direction')=='LONG' else max(map(float,highs))
        try:
            entry,stop,target=(float(q[k]) for k in ('trigger_level','invalidation','target1'))
            lo,hi=float(q['retest_low']),float(q['retest_high'])
            valid=all(math.isfinite(x) and x>0 for x in (entry,stop,target,lo,hi))
            valid=valid and (stop<lo<=hi<=entry<target if q['direction']=='LONG' else target<entry<=lo<=hi<stop)
            if not valid:raise ValueError('INVALID_LEVELS')
            reward=abs(target/entry-1)*100;risk=abs(stop/entry-1)*100
            net_r=(reward-cost)/(risk+cost)
            if not g.get('level_ok'):reasons.append('LEVEL_QUALITY')
            if config.get('reason') not in ('OK','INSUFFICIENT_GEOMETRY'):reasons.append(config.get('reason') or 'STRUCTURE_INVALID')
            if not no_room and reward<room:reasons.append('ROOM_FLOOR')
            if not no_r and net_r<min_r:reasons.append('NET_R')
        except (KeyError,ValueError,TypeError):
            reasons.append('INVALID_LEVELS');risk=reward=net_r=None
        return dict(name=name,admitted=not reasons,blockers=list(dict.fromkeys(reasons)),
            plan=q,risk_pct=risk,reward_pct=reward,net_r=net_r,cost_pct=cost,
            required_room_pct=0 if no_room else room,min_net_r=-1e300 if no_r else min_r)
    arms=[arm('BASELINE'),arm('ONE_MISSING_ONLY',single=True),
          arm('LOCAL_STOP_ONLY',local=True),arm('ROOM_FLOOR_ONLY_REMOVED',no_room=True),
          arm('NET_R_ONLY_REMOVED',no_r=True),arm('LOCAL_STOP_AND_ONE_MISSING',local=True,single=True)]
    return dict(version=VERSION,research_only=True,missing=missing,arms=arms,
        confirmation_scope='CLOSED_BAR_PROBES_NOT_PRODUCTION_EXECUTION',
        baseline_production_eligible=bool(decision.get('eligible') and g.get('qualified_precheck')))

def init(c):
    c.executescript('''CREATE TABLE IF NOT EXISTS gate_experiment_cursor(version TEXT PRIMARY KEY,last_id INTEGER);
      CREATE TABLE IF NOT EXISTS gate_experiment_episodes(observation_id INTEGER PRIMARY KEY,
        symbol TEXT,direction TEXT,observed_ms INTEGER,strategy_id TEXT,data_cohort TEXT,payload TEXT);
      CREATE INDEX IF NOT EXISTS gate_experiment_sampling ON gate_experiment_episodes(symbol,direction,strategy_id,observed_ms);
      CREATE TABLE IF NOT EXISTS gate_experiment_results(observation_id INTEGER,arm TEXT,confirmation TEXT,
        state TEXT,checked_ms INTEGER,payload TEXT,PRIMARY KEY(observation_id,arm,confirmation));''')

def ingest(c,now_ms):
    init(c)
    old=c.execute('SELECT last_id FROM gate_experiment_cursor WHERE version=?',(VERSION,)).fetchone()
    cursor=old[0] if old else 0
    rows=c.execute('''SELECT * FROM observations WHERE id>? AND observed_ms<=?
        AND json_valid(payload) AND json_extract(payload,'$.gate_experiment.version')=?
        ORDER BY id LIMIT 2000''',(cursor,now_ms,VERSION)).fetchall()
    for row in rows:
        p=json.loads(row['payload']);experiment=p.get('gate_experiment')
        cursor=row['id']
        if not experiment or experiment.get('version')!=VERSION:continue
        identity=(p.get('strategy_identity') or {}).get('id','UNKNOWN')
        direction=(p.get('setup_plan') or {}).get('direction','NONE')
        previous=c.execute('SELECT MAX(observed_ms) FROM gate_experiment_episodes WHERE symbol=? AND direction=? AND strategy_id=?',
            (row['symbol'],direction,identity)).fetchone()[0]
        # Up to 180m waiting for a probe plus 180m holding, same sample for all arms.
        if previous is not None and row['observed_ms']-previous<361*60000:continue
        c.execute('INSERT OR IGNORE INTO gate_experiment_episodes VALUES(?,?,?,?,?,?,?)',
            (row['id'],row['symbol'],direction,row['observed_ms'],identity,row['cohort'],json.dumps(experiment,allow_nan=False)))
    c.execute('INSERT OR REPLACE INTO gate_experiment_cursor VALUES(?,?)',(VERSION,cursor));c.commit()

def entry_probe(plan,observed_ms,bars,now_ms,mode):
    """Causal probes: only fully closed bars after observation; entry next open.
    No claim to reproduce book, volume, body or production retest confirmation.
    """
    start=(observed_ms//60000+1)*60000
    if mode=='NEXT_MINUTE':return start,None
    mapping={int(b[0]):b for b in bars}
    end=min(start+180*60000,now_ms//60000*60000)
    long=plan['direction']=='LONG';trigger=float(plan['trigger_level']);confirmed=False
    for t in range(start,end,60000):
        b=mapping.get(t)
        if b is None:return None,'DATA_UNRESOLVED'
        if len(b)<7 or int(b[6])!=t+59999:return None,'DATA_UNRESOLVED'
        if any(not math.isfinite(float(v)) or float(v)<=0 for v in b[1:5]):return None,'DATA_UNRESOLVED'
        if not float(b[3])<=min(float(b[1]),float(b[4]))<=max(float(b[1]),float(b[4]))<=float(b[2]):return None,'DATA_UNRESOLVED'
        if float(b[3])<=float(plan['invalidation']) if long else float(b[2])>=float(plan['invalidation']):
            return None,'INVALIDATED_BEFORE_ENTRY'
        close=float(b[4]);beyond=close>trigger if long else close<trigger
        if not confirmed:
            # Use a full 5m candle whose open is after the observation.
            if (t+60000)%300000 or t-4*60000<start:continue
            if beyond:
                if mode=='CLOSE_5M':return t+60000,None
                confirmed=True
            continue
        touched=float(b[3])<=float(plan['retest_high']) and float(b[2])>=float(plan['retest_low'])
        if touched and beyond:return t+60000,None
    return None,'NO_CONFIRMATION' if now_ms>=start+180*60000 else 'PENDING'

def resolve(now_ms=None):
    from long_short_data_opportunity_v2 import connect
    from long_short_live_pool import native_futures_path_bars
    from long_short_rejection_audit import outcome
    from long_short_native_derivatives import relay_get
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    c=connect();c.row_factory=__import__('sqlite3').Row
    try:
        ingest(c,now_ms)
        rows=c.execute('''SELECT e.* FROM gate_experiment_episodes e WHERE EXISTS
            (SELECT 1 FROM json_each(e.payload,'$.arms') a WHERE json_extract(a.value,'$.admitted')=1)
            AND (SELECT COUNT(*) FROM gate_experiment_results r WHERE r.observation_id=e.observation_id
              AND r.state NOT IN ('PENDING','DATA_UNRESOLVED')) <
              3*(SELECT COUNT(*) FROM json_each(e.payload,'$.arms') a WHERE json_extract(a.value,'$.admitted')=1)
            ORDER BY COALESCE((SELECT MAX(checked_ms) FROM gate_experiment_results r WHERE r.observation_id=e.observation_id),0)
            LIMIT 25''').fetchall()
        cache={}
        def funding(path,params):
            key=json.dumps([path,params],sort_keys=True)
            if key not in cache:cache[key]=relay_get(path,params)
            return cache[key]
        for row in rows:
            p=json.loads(row['payload']);start=(row['observed_ms']//60000+1)*60000
            try:bars=native_futures_path_bars(row['symbol'],start,180)
            except Exception:bars=[]
            for arm in p['arms']:
                if not arm['admitted']:continue
                for mode in ('NEXT_MINUTE','CLOSE_5M','RETEST_CLOSE'):
                    old=c.execute('SELECT state,checked_ms FROM gate_experiment_results WHERE observation_id=? AND arm=? AND confirmation=?',
                        (row['observation_id'],arm['name'],mode)).fetchone()
                    if old and (old['state'] not in ('PENDING','DATA_UNRESOLVED') or now_ms-old['checked_ms']<60000):continue
                    begin,state=entry_probe(arm['plan'],row['observed_ms'],bars,now_ms,mode)
                    if begin is None:result={'state':state}
                    else:
                        plan=arm['plan']
                        # outcome uses the next full minute; backdate by 1ms to
                        # select exactly the causal probe entry, not its candle.
                        snap=dict(symbol=row['symbol'],direction=plan['direction'],observed_ms=begin-1,
                            stop=plan['invalidation'],target=plan['target1'],cost_pct=arm['cost_pct'])
                        result=outcome(snap,now_ms,native_futures_path_bars,funding)
                    result.update(research_only=True,confirmation_probe=mode,production_equivalent=False)
                    c.execute('INSERT OR REPLACE INTO gate_experiment_results VALUES(?,?,?,?,?,?)',
                        (row['observation_id'],arm['name'],mode,result['state'],now_ms,json.dumps(result,allow_nan=False)))
            c.commit()
        emit_report(c,now_ms)
    finally:c.close()

def emit_report(c,now_ms):
    global _last_slot
    if _last_slot==now_ms//300000:return
    from long_short_trade_ledger import summarize
    results={(r['observation_id'],r['arm'],r['confirmation']):json.loads(r['payload'])
        for r in c.execute('SELECT * FROM gate_experiment_results')}
    groups=defaultdict(list)
    for row in c.execute('SELECT * FROM gate_experiment_episodes'):
        for arm in json.loads(row['payload'])['arms']:
            groups[(row['strategy_id'],row['data_cohort'],row['direction'],arm['name'])].append((row,arm))
    if not groups:return
    _last_slot=now_ms//300000
    for (strategy,cohort,direction,arm_name),rows in groups.items():
        blocks=Counter(reason for _,arm in rows for reason in arm['blockers'])
        modes={mode:summarize([results.get((r['observation_id'],arm_name,mode),{'state':'PENDING'})
            for r,a in rows if a['admitted']]) for mode in ('NEXT_MINUTE','CLOSE_5M','RETEST_CLOSE')}
        print('PAIRED_GATE_RESULT '+json.dumps(dict(version=VERSION,strategy_id=strategy,data_cohort=cohort,
            direction=direction,arm=arm_name,paired_episodes=len(rows),admitted=sum(a['admitted'] for _,a in rows),
            blockers=dict(blocks),outcomes=modes,research_only=True,production_equivalent=False),allow_nan=False),flush=True)
