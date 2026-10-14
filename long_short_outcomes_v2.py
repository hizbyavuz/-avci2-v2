"""Cost-aware outcomes for V2 delivered signals, isolated from legacy labels."""
import json
import math
import sqlite3
from datetime import datetime,timezone,timedelta

def label_path(direction,entry,stop,target,bars,cost_pct,funding_pct=0.0):
    """Executable gap stop, stop-first ambiguity, and post-entry excursions."""
    if direction not in ('LONG','SHORT'):
        raise ValueError('invalid signal direction')
    if (any(not math.isfinite(float(v)) for v in (entry,stop,target,cost_pct,funding_pct))
            or min(entry,stop,target)<=0 or cost_pct<0):
        raise ValueError('invalid price or costs')
    long=direction=='LONG'
    if not ((stop<entry<target) if long else (target<entry<stop)):
        raise ValueError('invalid signal geometry')
    if not bars:raise ValueError('missing path')
    for i,b in enumerate(bars):
        if (len(b)<5 or any(not math.isfinite(float(v)) or float(v)<=0 for v in b[1:5])
                or not float(b[3])<=min(float(b[1]),float(b[4]))<=max(float(b[1]),float(b[4]))<=float(b[2])
                or (i and int(b[0])-int(bars[i-1][0])!=60000)):
            raise ValueError('invalid or gapped candle path')
    result='TIMEOUT';exit_price=float(bars[-1][4]);exit_ms=int(bars[-1][0])+60000
    mfe=mae=0.0
    for b in bars:
        opened,high,low=float(b[1]),float(b[2]),float(b[3])
        adverse=(low/entry-1)*100 if long else (1-high/entry)*100
        favorable=(high/entry-1)*100 if long else (1-low/entry)*100
        gap_stop=opened<=stop if long else opened>=stop
        gap_target=opened>=target if long else opened<=target
        stop_hit=low<=stop if long else high>=stop
        target_hit=high>=target if long else low<=target
        # Intraminute ordering is unknown. Do not count a possible rally after
        # an earlier stop as favorable performance of an open position.
        if gap_target and not gap_stop:
            result='TP1';exit_price=target;exit_ms=int(b[0])
            mfe=max(mfe,abs(target/entry-1)*100)
            break
        if gap_stop or stop_hit:
            result='STOP';exit_price=opened if gap_stop else stop
            # The rest of a stopped candle is after exit (or unordered).
            # Its deeper low/high must not inflate open-position MAE.
            exit_adverse=(exit_price/entry-1)*100*(1 if long else -1)
            mae=min(mae,exit_adverse)
            exit_ms=int(b[0])+(0 if gap_stop else 60000);break
        mae=min(mae,adverse)
        mfe=max(mfe,favorable)
        if target_hit:
            mfe=min(mfe,abs(target/entry-1)*100)
            result='TP1';exit_price=target;exit_ms=int(b[0])+60000;break
    gross=(exit_price/entry-1)*100*(1 if long else -1)
    risk=abs(entry-stop)/entry*100
    return dict(result=result,exit_price=exit_price,exit_ms=exit_ms,gross_return_pct=gross,
                net_return_pct=gross-cost_pct-funding_pct,round_trip_cost_pct=cost_pct,
                funding_cost_pct=funding_pct,mfe_pct=mfe,mae_pct=mae,
                excursion_method='CONSERVATIVE_1M_OHLC_EXIT_CLIPPED',
                intrabar_excursion_exact=False,
                net_r=(gross-cost_pct-funding_pct)/(risk+cost_pct))

def resolve(db_path):
    from long_short_live_pool import native_futures_path_bars
    from long_short_native_derivatives import relay_get,SOURCE
    now=datetime.now(timezone.utc)
    with sqlite3.connect(db_path,timeout=15) as c:
        c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE IF NOT EXISTS confirmed_outcomes_v2(event_id INTEGER PRIMARY KEY, evaluated_at TEXT, payload TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS outcome_issues_v2(event_id INTEGER PRIMARY KEY, checked_at TEXT, reason TEXT)')
        events=c.execute("""SELECT e.* FROM events e LEFT JOIN confirmed_outcomes_v2 o ON o.event_id=e.id
            LEFT JOIN outcome_issues_v2 i ON i.event_id=e.id
            WHERE e.stage_to='TRIGGERED' AND e.telegram_status='SENT' AND o.event_id IS NULL
            AND e.telegram_sent_time_utc IS NOT NULL AND json_valid(e.payload_json)
            AND json_extract(e.payload_json,'$._native_chain_v2')=1
            ORDER BY COALESCE(i.checked_at,''),e.id LIMIT 25""").fetchall()
        for ev in events:
            p=json.loads(ev['payload_json'] or '{}')
            if not p.get('_native_chain_v2'):continue
            start=datetime.fromisoformat(ev['telegram_sent_time_utc'])
            if now-start<timedelta(minutes=181):continue
            try:
                entry=float(ev['price']);stop=float(p['invalidation']);target=float(p['target1'])
                begin=int(start.timestamp()//60)*60000
                bars=native_futures_path_bars(ev['symbol'],begin,181)
                mapping={int(b[0]):b for b in bars}
                if any(begin+i*60000 not in mapping for i in range(181)):raise ValueError('missing_1m_path')
                first=mapping[begin]
                if start.timestamp()*1000>begin and (float(first[3])<=stop or float(first[2])>=target if ev['direction']=='LONG'
                    else float(first[2])>=stop or float(first[3])<=target):
                    raise ValueError('delivery_minute_barrier_order_unresolved')
                path=[mapping[begin+i*60000] for i in range(1,181)]
                gate=p.get('_trigger_execution_gate') or {}
                cost=float(gate['round_trip_cost_pct'])
                result=label_path(ev['direction'],entry,stop,target,path,cost)
                rates=relay_get('/funding',{'symbol':ev['symbol'],'startTime':str(int(start.timestamp()*1000)),
                    'endTime':str(result['exit_ms'])})
                if rates.get('source')!=SOURCE or rates.get('symbol')!=ev['symbol'] or not isinstance(rates.get('rates'),list):
                    raise ValueError('unverified_funding_path')
                sign=1 if ev['direction']=='LONG' else -1
                from long_short_native_derivatives import number
                if any(r.get('symbol')!=ev['symbol'] for r in rates['rates']):raise ValueError('funding symbol mismatch')
                funding=sum(sign*number(r['fundingRate'])*100*number(r['markPrice'],True)/entry for r in rates['rates']
                    if start.timestamp()*1000<=int(r['fundingTime'])<result['exit_ms'])
                result=label_path(ev['direction'],entry,stop,target,path,cost,funding)
                result.update(symbol=ev['symbol'],direction=ev['direction'],entry_price=entry,
                    entry_minute_excluded=True,
                    entry_time_utc=start.isoformat(),version='LS_NATIVE_CHAIN_V2_2026-10-13',source=SOURCE)
                c.execute('INSERT INTO confirmed_outcomes_v2 VALUES(?,?,?)',(ev['id'],now.isoformat(),json.dumps(result)))
                c.execute('DELETE FROM outcome_issues_v2 WHERE event_id=?',(ev['id'],))
                print('V2_CONFIRMED_OUTCOME',ev['id'],json.dumps(result),flush=True)
            except Exception as e:
                c.execute('INSERT OR REPLACE INTO outcome_issues_v2 VALUES(?,?,?)',(ev['id'],now.isoformat(),str(e)[:200]))
                print('V2_OUTCOME_UNRESOLVED',ev['id'],str(e)[:160],flush=True)
        c.commit()
        print('V2_CONFIRMED_TRADE_SUMMARY',json.dumps({'completed':c.execute('SELECT count(*) FROM confirmed_outcomes_v2').fetchone()[0],
            'data_issues':c.execute('SELECT count(*) FROM outcome_issues_v2').fetchone()[0]}),flush=True)
