"""Read-only per-direction follow-through from existing point-in-time records.
Never infer a predicted direction after the move. Raw price paths and hypothetical
trade PnL are separate; missing/unmatured paths stay outside accuracy denominators.
"""
import json
import math
import time
from collections import Counter, defaultdict
from long_short_trade_ledger import summarize

VERSION='REJECTION_DIRECTION_REPORT_V1_2026_10_15'
HORIZONS=(15,30,60,180)
_last_emitted_slot=None


def report(c, now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='forward_labels'").fetchone()
    labels={}
    if exists:
        labels={(r[0],r[1]):(r[2],json.loads(r[3])) for r in c.execute('''
            SELECT f.observation_id,f.horizon_minutes,f.state,f.payload FROM forward_labels f
            JOIN rejection_audit_episodes e ON e.observation_id=f.observation_id''')}
    groups=defaultdict(list)
    for r in c.execute('SELECT * FROM rejection_audit_episodes ORDER BY observation_id'):
        key=(r['strategy_id'],r['data_cohort'],r['block_class'],r['direction'])
        groups[key].append(r)
    result=[]
    for (strategy,cohort,block,direction),rows in groups.items():
        pnls=[]; reasons=Counter(); horizons={}
        for r in rows:
            p=json.loads(r['payload']); v=json.loads(r['result'] or '{}');v.setdefault('state',r['state']);pnls.append(v)
            why=[p.get('structure_reason')] if block=='STRUCTURE_BLOCKED' else p.get('hard_blockers',[])+p.get('direction_reasons',[])
            for reason in dict.fromkeys(why or [p.get('data_reason') or block]):
                if reason:reasons[str(reason)]+=1
        for horizon in HORIZONS:
            counts=Counter(); raw=[];signed=[]; favourable=[];adverse=[]
            reach=Counter()
            for r in rows:
                start=(r['observed_ms']//60000+1)*60000
                if now_ms<start+horizon*60000:counts['pending']+=1;continue
                state,p=labels.get((r['observation_id'],horizon),('MISSING',{}))
                if state!='RESOLVED':counts['unresolved']+=1;continue
                try:
                    ret=float(p['return_pct']);up=float(p['max_up_pct']);down=float(p['max_down_pct'])
                    if not all(math.isfinite(x) for x in (ret,up,down)):raise ValueError()
                except (KeyError,ValueError,TypeError):counts['unresolved']+=1;continue
                counts['resolved']+=1;raw.append(ret)
                counts['up' if ret>1e-9 else 'down' if ret< -1e-9 else 'flat']+=1
                if direction not in ('LONG','SHORT'):continue
                dr=ret if direction=='LONG' else -ret
                signed.append(dr);favourable.append(up if direction=='LONG' else -down);adverse.append(down if direction=='LONG' else -up)
                counts['correct' if dr>1e-9 else 'wrong' if dr< -1e-9 else 'direction_flat']+=1
                side='up' if direction=='LONG' else 'down'
                for pct in (3,5,7,10,15):
                    if (p.get('hits') or {}).get(f'{side}_{pct}pct_first_full_minute') is not None:reach[str(pct)]+=1
            horizons[str(horizon)]={k:counts[k] for k in ('resolved','pending','unresolved','up','down','flat','correct','wrong','direction_flat')}
            horizons[str(horizon)].update(
                mean_raw_return_pct=sum(raw)/len(raw) if raw else None,
                mean_directional_return_pct=sum(signed)/len(signed) if signed else None,
                correct_pct=100*counts['correct']/len(signed) if signed else None,
                mean_max_favourable_pct=sum(favourable)/len(favourable) if favourable else None,
                mean_max_adverse_pct=sum(adverse)/len(adverse) if adverse else None,
                reach_counts=dict(reach),direction_evaluated=len(signed))
        result.append(dict(strategy_id=strategy,data_cohort=cohort,block_class=block,direction=direction,
            episodes=len(rows),reasons=dict(reasons),hypothetical_net_pnl=summarize(pnls),horizons=horizons))
    return dict(version=VERSION,asof_ms=now_ms,price_scope='GROSS_PRICE_PATH_NOT_TRADE_PROFIT',
        sample_scope='PRIMARY_RECORDED_EPISODES_NOT_FULL_MARKET_RECALL',groups=result)


def emit(result):
    global _last_emitted_slot
    slot=result['asof_ms']//300000
    if slot==_last_emitted_slot:return
    _last_emitted_slot=slot
    # One bounded line per cohort/direction, so large combined JSON is not truncated.
    for group in result['groups']:
        print('REJECTION_DIRECTION_RESULT '+json.dumps(dict(version=VERSION,asof_ms=result['asof_ms'],
            price_scope=result['price_scope'],**group),allow_nan=False),flush=True)
