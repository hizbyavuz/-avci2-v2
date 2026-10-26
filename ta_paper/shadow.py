"""Bybit-only observed-book execution study and 72h paths. Never a trade/plan.

Entry is a sampled book VWAP plus declared residual slippage, NOT a fill claim.
Exits use 1m bars and explicit cost assumptions. Funding mark is a minute-open
proxy. Results live in shadow_trials, never Binance plans/performance.
"""
from dataclasses import replace
import json

from .core import CFG, MINUTE, finite, quantize, exit_on_bar, funding_cost
from .features import encoded, event, REVISION
from .io import book_quote, book_check
from .observations import HORIZON, record_setup, setup_id

NOTIONAL = 200.0
FEE = .0006  # Declared modelling assumption, not the user's account fee tier.
REACH = (.03, .05, .07, .10, .15)


def init(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS shadow_trials(
        id TEXT PRIMARY KEY,symbol TEXT,status TEXT,next_ms INTEGER,payload TEXT)''')
    store.db.commit()


def save(store, p, now):
    store.db.execute('UPDATE shadow_trials SET status=?,next_ms=?,payload=? WHERE id=?',
                     (p['shadow_status'],now,encoded(p),p['id']))
    store.db.commit()


def enqueue(store, original, market):
    init(store)
    p = dict(original, id='BYBIT:'+setup_id(original), market=market,
             shadow_status='WAIT_ENTRY', shadow_version=REVISION,
             hypothetical_notional=NOTIONAL, fee_each_side_assumed=FEE,
             path_start_ms=original['observed_ms']//MINUTE*MINUTE,
             path_end_ms=original['entry_ms']+HORIZON,
             execution_model='OBSERVED_BOOK_ENTRY_MODELLED_1M_EXIT', automatic_orders=False)
    store.db.execute('INSERT OR IGNORE INTO shadow_trials VALUES(?,?,?,?,?)',
                     (p['id'],p['symbol'],p['shadow_status'],p['entry_ms'],encoded(p)))
    store.db.commit()
    # Resume the same job after restart; old timestamps and provenance survive.
    if not store.db.execute('SELECT 1 FROM observations WHERE id=?',(p['id'],)).fetchone():
        path_p=dict(p,entry_ms=p['path_start_ms'])
        record_setup(store,path_p,p['observed_ms'],market,'BYBIT_OBSERVATION_ONLY')
        store.db.execute('UPDATE observations SET end_ms=? WHERE id=?',(p['path_end_ms'],p['id']))
        store.db.commit()


def capture_entry(store, client, p):
    now=client.now()
    if now>p['expires_ms']:
        p.update(shadow_status='ENTRY_MISSED',entry_issue='NO_TIMELY_BOOK')
        return
    raw=client.request('/v5/market/orderbook',category='linear',symbol=p['symbol'],limit=100)
    r=raw['result'];now=client.now()
    if r.get('s')!=p['symbol']:
        raise ValueError('BOOK_SYMBOL_MISMATCH')
    book=dict(E=int(r['ts']),bids=r['b'],asks=r['a'])
    event(store,p['id'],'SHADOW_ENTRY_BOOK',book['E'],now,dict(book=book,source_venue='BYBIT'))
    if now>p['expires_ms']:
        p.update(shadow_status='ENTRY_MISSED',entry_issue='BOOK_ARRIVED_LATE');return
    quote=book_quote(book,now);sign=1 if p['direction']=='LONG' else -1
    best=quote['ask'] if sign==1 else quote['bid']
    if sign*(best-p['entry_limit'])>0 or sign*(best-p['stop'])<=0 or sign*(p['target']-best)<=0:
        p.update(shadow_status='ENTRY_REJECTED',entry_issue='CHASE_OR_INVALID_LEVELS');return
    lot=p['market'].get('bybit_lot_filter') or {}
    quantity=quantize(NOTIONAL/best,finite(lot['qtyStep'],True))
    if quantity<finite(lot['minOrderQty']) or quantity*best<finite(lot.get('minNotionalValue',5)):
        p.update(shadow_status='ENTRY_REJECTED',entry_issue='EXCHANGE_MINIMUM');return
    maximum=lot.get('maxMktOrderQty',lot.get('maxOrderQty'))
    if maximum is not None and quantity>finite(maximum,True):
        p.update(shadow_status='ENTRY_REJECTED',entry_issue='EXCHANGE_MAXIMUM');return
    depth=book_check(book,now,p['direction'],NOTIONAL,quantity=quantity)
    entry=depth['vwap']*(1+sign*CFG.entry_slippage)
    if sign*(entry-p['entry_limit'])>0 or sign*(p['target']-entry)<=0:
        p.update(shadow_status='ENTRY_REJECTED',entry_issue='COST_ADJUSTED_CHASE');return
    distance=sign*(entry-p['stop'])
    assumed_exit_spread=quote['spread']/2
    planned_loss=quantity*(distance+FEE*(entry+p['stop'])+p['stop']*(CFG.stop_slippage+assumed_exit_spread))
    p.update(shadow_status='OPEN_MODEL',entry=entry,entry_ms=now,quantity=quantity,
             notional=entry*quantity,planned_loss=planned_loss,book=depth,
             assumed_exit_half_spread=assumed_exit_spread,
             entry_delay_actual_ms=now-p['observed_ms'],check_ms=now//MINUTE*MINUTE,
             path_end_ms=now//MINUTE*MINUTE+HORIZON,
             horizon_definition='72H_FROM_ENTRY_MINUTE_OPEN_PARTIAL_LAST_MINUTE_EXCLUDED',
             entry_mark='SAMPLED_BOOK_NOT_ACTUAL_FILL',funding_status='NOT_YET_MEASURED')
    store.db.execute('UPDATE observations SET end_ms=? WHERE id=?',(p['path_end_ms'],p['id']))
    store.db.commit()


def config(p):
    half=p.get('assumed_exit_half_spread',0)
    return replace(CFG,fee_each_side=FEE,target_slippage=CFG.target_slippage+half,
                   stop_slippage=CFG.stop_slippage+half,stress_stop_slippage=CFG.stress_stop_slippage+half)


def advance_path(store,p,now):
    if p['shadow_status']!='OPEN_MODEL':return
    if not p.get('preentry_checked'):
        start=p['observed_ms']//MINUTE*MINUTE
        end=p['entry_ms']//MINUTE*MINUTE
        rows={r[0]:json.loads(r[1]) for r in store.db.execute(
            "SELECT open_ms,payload FROM candles WHERE symbol=? AND tf='1m' AND open_ms>=? AND open_ms<?",
            (p['symbol'],start,end))}
        for t in range(start,end,MINUTE):
            if t not in rows:
                if now>p['path_end_ms']+30*MINUTE:
                    p.update(shadow_status='UNRESOLVED_PATH',first_missing_ms=t)
                return
            if exit_on_bar(p,rows[t],config(p)):
                ambiguous=t<p['observed_ms']
                p.update(shadow_status='PRE_ENTRY_MINUTE_AMBIGUOUS' if ambiguous else 'CANCELLED_BEFORE_ENTRY',
                         outcome_issue='LEVEL_TOUCHED_BEFORE_BOOK_CAPTURE')
                return
        p['preentry_checked']=True
    end=min(p['path_end_ms'],(now-CFG.publication_delay_ms)//MINUTE*MINUTE)
    expected=p['check_ms']
    rows=store.db.execute("SELECT payload FROM candles WHERE symbol=? AND tf='1m' AND open_ms>=? "
                          'AND open_ms<? ORDER BY open_ms',(p['symbol'],expected,end)).fetchall()
    for record in rows:
        row=json.loads(record[0])
        if row[0]!=expected:break
        if row[0]>=p['entry_ms']:
            p['entry_validation_passed']=True
        result=exit_on_bar(p,row,config(p))
        if result:
            if row[0]<p['entry_ms']:
                p.update(shadow_status='ENTRY_MINUTE_AMBIGUOUS',
                         outcome_issue='BARRIER_MAY_PRECEDE_BOOK_CAPTURE',possible_exit=result)
            else:
                p.update(result,shadow_status='FUNDING_PENDING',funding_status='PENDING')
            return
        p['entry_validation_passed']=True
        expected+=MINUTE
    p['check_ms']=expected
    if expected>=p['path_end_ms']:
        p['shadow_status']='OPEN_AT_HORIZON'
    elif now>p['path_end_ms']+30*MINUTE:
        p.update(shadow_status='UNRESOLVED_PATH',first_missing_ms=expected)


def settle_funding(store,client,p):
    """One HTTP request per invocation. Missing rates/marks never mean zero."""
    interval=int(p['market'].get('funding_interval_minutes') or 0)*MINUTE
    if interval not in (60*MINUTE,240*MINUTE,480*MINUTE):
        raise ValueError('FUNDING_INTERVAL_UNVERIFIED')
    if 'funding_rows' not in p:
        raw=client.request('/v5/market/funding/history',category='linear',symbol=p['symbol'],
                           startTime=p['entry_ms']-interval-1,endTime=p['exit_interval_end'],limit=200)
        result=raw['result']
        if result.get('category')!='linear':raise ValueError('FUNDING_CATEGORY')
        rows=[]
        for r in result['list']:
            if r['symbol']!=p['symbol']:raise ValueError('FUNDING_SYMBOL')
            rows.append(dict(symbol=p['symbol'],fundingTime=int(r['fundingRateTimestamp']),fundingRate=finite(r['fundingRate'])))
        rows.sort(key=lambda x:x['fundingTime'])
        times=[r['fundingTime'] for r in rows]
        if (not times or times[0]>p['entry_ms'] or times[-1]>p['exit_interval_end'] or
                p['exit_interval_end']-times[-1]>=interval or
                any(b-a!=interval for a,b in zip(times,times[1:]))):
            raise ValueError('FUNDING_GAP_OR_INTERVAL_CHANGE')
        event(store,p['id'],'SHADOW_FUNDING_RAW',p['exit_interval_end'],client.now(),dict(rows=rows))
        p['funding_rows']=[r for r in rows if p['entry_ms']<=r['fundingTime']<=p['exit_interval_end']]
        return
    pending=next((r for r in p['funding_rows'] if 'markPrice' not in r),None)
    if pending is not None:
        t=pending['fundingTime'];start=t//MINUTE*MINUTE
        raw=client.request('/v5/market/mark-price-kline',category='linear',symbol=p['symbol'],
                           interval='1',start=start,end=start+MINUTE-1,limit=1)
        result=raw['result'];rows=result['list']
        if result.get('symbol')!=p['symbol'] or result.get('category')!='linear' or len(rows)!=1 or int(rows[0][0])!=start:
            raise ValueError('FUNDING_MARK_MISSING')
        pending['markPrice']=finite(rows[0][1],True)
        event(store,p['id'],'SHADOW_MARK_'+str(t),t,client.now(),dict(raw=rows,price_role='MINUTE_OPEN_PROXY'))
        return
    cost,uncertain=funding_cost(p,p['funding_rows'],p['exit_interval_end'])
    net=p['pnl_before_funding']-cost
    p.update(shadow_status='CLOSED_MODEL',funding_status='MEASURED_RATE_MARK_OPEN_PROXY',
             funding_cost=cost,funding_boundary_uncertain=uncertain,net_pnl_modelled=net,
             net_r_modelled=net/p['planned_loss'],
             stress_net_r_modelled=(p['stress_pnl_before_funding']-cost)/p['planned_loss'])


def path_summary(store,p,now):
    """Continuation after observed entry; partial entry minute excluded explicitly."""
    if 'entry_mark' not in p:return
    start=(p['entry_ms']//MINUTE+1)*MINUTE
    end=min(p['path_end_ms'],(now-CFG.publication_delay_ms)//MINUTE*MINUTE)
    expected=start;sign=1 if p['direction']=='LONG' else -1
    mfe=mae=0.;reach={str(int(x*100)):None for x in REACH}
    horizons={str(h):dict(raw_mfe=0.,raw_mae=0.,raw_reach={str(int(x*100)):None for x in REACH}) for h in (4,24,72)}
    for record in store.db.execute("SELECT payload FROM candles WHERE symbol=? AND tf='1m' AND open_ms>=? "
                                    'AND open_ms<? ORDER BY open_ms',(p['symbol'],start,end)):
        row=json.loads(record[0])
        if row[0]!=expected:break
        best=row[2] if sign==1 else row[3];worst=row[3] if sign==1 else row[2]
        favorable=sign*(best/p['entry']-1);adverse=sign*(worst/p['entry']-1)
        mfe=max(mfe,favorable);mae=min(mae,adverse)
        for threshold in REACH:
            k=str(int(threshold*100))
            if reach[k] is None and favorable>=threshold:
                reach[k]=dict(start_ms=row[0],end_ms=row[6],elapsed_upper_ms=row[6]-p['entry_ms'])
        for h,summary in horizons.items():
            if row[6]<p['entry_ms']+int(h)*60*MINUTE:
                summary['raw_mfe']=max(summary['raw_mfe'],favorable)
                summary['raw_mae']=min(summary['raw_mae'],adverse)
                for threshold in REACH:
                    k=str(int(threshold*100))
                    if summary['raw_reach'][k] is None and favorable>=threshold:
                        summary['raw_reach'][k]=dict(start_ms=row[0],end_ms=row[6])
        expected+=MINUTE
    for h,summary in horizons.items():
        summary['complete_full_minutes']=expected>=min(p['path_end_ms'],(p['entry_ms']+int(h)*60*MINUTE)//MINUTE*MINUTE)
    p['path_summary']=dict(raw_mfe=mfe,raw_mae=mae,reach=reach,through_ms=expected,
        complete=expected>=p['path_end_ms'],continuous_observed_minutes=(expected-start)//MINUTE,
        first_partial_minute_excluded=True,not_executable_returns=True,
        after_exit_included=True,post_signal_only=True,horizons_hours=horizons,
        entry_validation_passed=p.get('entry_validation_passed',False))


def tick(store,client,stop_before_ms):
    init(store);now=client.now()
    if now+15_000>=stop_before_ms or client.gate.state()['remaining_seconds']:return
    if now-int(store.setting('shadow_tick_ms') or 0)<15_000:return
    row=store.db.execute("SELECT * FROM shadow_trials WHERE next_ms<=? ORDER BY "
                         "CASE WHEN status='WAIT_ENTRY' THEN 0 WHEN status='FUNDING_PENDING' THEN 1 ELSE 2 END,next_ms,id LIMIT 1",(now,)).fetchone()
    if not row:return
    store.setting('shadow_tick_ms',now);p=json.loads(row['payload'])
    try:
        if p['shadow_status']=='WAIT_ENTRY':capture_entry(store,client,p)
        advance_path(store,p,client.now())
        if p['shadow_status']=='FUNDING_PENDING':settle_funding(store,client,p)
        path_summary(store,p,client.now())
        p['consecutive_errors']=0
    except (ValueError,KeyError,TypeError,RuntimeError,OSError) as exc:
        p['consecutive_errors']=p.get('consecutive_errors',0)+1
        p['last_error']=str(exc)[:120]
        event(store,p['id'],'SHADOW_ERROR_'+str(now),now,client.now(),dict(reason=p['last_error']))
        if p['shadow_status']=='WAIT_ENTRY' and client.now()>p['expires_ms']:
            p.update(shadow_status='ENTRY_MISSED',entry_issue=p['last_error'])
        elif p['consecutive_errors']>=3 and p['shadow_status']=='FUNDING_PENDING':
            p.update(shadow_status='FUNDING_UNRESOLVED',funding_status=p['last_error'])
    terminal=p['shadow_status'] not in ('WAIT_ENTRY','OPEN_MODEL','FUNDING_PENDING')
    finished=terminal and now>p['path_end_ms']+60*MINUTE
    # Terminal objects remain in the database, never silently removed.
    save(store,p,2**62 if finished else client.now()+(30_000 if p['shadow_status'] in ('WAIT_ENTRY','FUNDING_PENDING') else 5*MINUTE))


def health(store):
    init(store)
    return dict(version=REVISION,source_venue='BYBIT',automatic_orders=False,
        statuses={r[0]:r[1] for r in store.db.execute('SELECT status,COUNT(*) FROM shadow_trials GROUP BY status')},
        paths={r[0]:r[1] for r in store.db.execute('SELECT status,COUNT(*) FROM observations GROUP BY status')},
        feature_jobs=store.db.execute('SELECT COUNT(*) FROM feature_jobs').fetchone()[0],
        feature_events=store.db.execute('SELECT COUNT(*) FROM feature_events').fetchone()[0],
        not_pooled_with_binance=True)
