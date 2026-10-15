"""Isolated missing-data episodes and 3/4 research. No alerts or orders.

Forward labels are gross price observations, never executable trade PnL.
The same horizons and native price reference are used for 3/4 and 4/4.
"""
import json
import math
import os
import signal
import sqlite3
import threading
import time

VERSION='LS_DATA_OPPORTUNITY_V2_2026-10-13'
FIELDS=('oi_change_1h','funding_pct','taker_ratio','long_short_ratio')

def root():
    return os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state'

def connect():
    os.makedirs(root(),exist_ok=True)
    c=sqlite3.connect(os.path.join(root(),'data_opportunity_v2.db'),timeout=10)
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
      CREATE TABLE IF NOT EXISTS episodes(id INTEGER PRIMARY KEY,symbol TEXT,started_ms INTEGER,
        recovered_ms INTEGER,initial_reason TEXT,version TEXT);
      CREATE UNIQUE INDEX IF NOT EXISTS one_open_episode ON episodes(symbol) WHERE recovered_ms IS NULL;
      CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY,episode_id INTEGER,observed_ms INTEGER,reason TEXT);
      CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,symbol TEXT,observed_ms INTEGER,
        cohort TEXT,direction TEXT,price REAL,payload TEXT,version TEXT,
        UNIQUE(symbol,observed_ms,cohort));
      CREATE TABLE IF NOT EXISTS forward_labels(observation_id INTEGER,horizon_minutes INTEGER,
        state TEXT,payload TEXT,checked_ms INTEGER,PRIMARY KEY(observation_id,horizon_minutes));
    ''')
    return c

def partial_fields(e):
    """Exactly one genuinely missing field, with verified native provenance."""
    if e.get('source')!='BINANCE_UM_FUTURES_REST' or not e.get('market_verified'):
        return None
    valid=[]
    for k,fresh in (('oi_change_1h','oi_fresh'),('funding_pct','funding_fresh'),
                    ('taker_ratio','taker_fresh'),('long_short_ratio','ls_fresh')):
        try:
            ok=e.get(fresh) and math.isfinite(float(e[k])) and not isinstance(e[k],bool)
            if k=='oi_change_1h':ok=ok and e.get('oi_unit')=='BASE_ASSET'
            if k in ('taker_ratio','long_short_ratio'):ok=ok and float(e[k])>0
            if k=='taker_ratio':ok=ok and e.get('taker_coverage_ready') and e.get('taker_source') in ('BINANCE_UM_FUTURES_WEBSOCKET','BINANCE_UM_FUTURES_KLINE')
        except (KeyError,TypeError,ValueError):ok=False
        if ok:valid.append(k)
    return [k for k in FIELDS if k not in valid] if len(valid)==3 else None

def current_price(symbol,now_ms):
    path=os.path.join(root(),'binance_futures_kline_shadow.db')
    try:
        with sqlite3.connect('file:'+os.path.abspath(path)+'?mode=ro',uri=True,timeout=3) as c:
            r=c.execute('SELECT price,event_ms,received_ms,source FROM futures_last_price WHERE symbol=?',(symbol,)).fetchone()
        if r and r[3]=='BINANCE_UM_FUTURES_WEBSOCKET' and 0<=now_ms-r[1]<=30000 and 0<=now_ms-r[2]<=30000:
            p=float(r[0])
            if p>0 and math.isfinite(p):return p
    except (sqlite3.Error,OSError,TypeError,ValueError):pass
    return None

def pending_symbols(now_ms=None):
    """Keep recent research coins on native feeds after shortlist departure."""
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    path=os.path.join(root(),'data_opportunity_v2.db')
    try:
        with sqlite3.connect('file:'+os.path.abspath(path)+'?mode=ro',uri=True,timeout=2) as c:
            return [r[0] for r in c.execute('SELECT symbol FROM observations WHERE observed_ms>=? GROUP BY symbol ORDER BY MIN(observed_ms) LIMIT 60',(now_ms-185*60000,))]
    except (sqlite3.Error,OSError):return []

def observe(symbol,cohort,direction,payload,now_ms=None,c=None):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    own=c is None
    c=connect() if own else c
    try:
        c.execute('INSERT OR IGNORE INTO observations(symbol,observed_ms,cohort,direction,price,payload,version) VALUES(?,?,?,?,?,?,?)',
            (symbol,now_ms,cohort,direction,current_price(symbol,now_ms),json.dumps(payload),VERSION))
        if own:c.commit()
    finally:
        if own:c.close()

def blocked(symbol,reason,now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    with connect() as c:
        r=c.execute('SELECT id FROM episodes WHERE symbol=? AND recovered_ms IS NULL',(symbol,)).fetchone()
        if r:episode=r[0]
        else:
            episode=c.execute('INSERT INTO episodes(symbol,started_ms,initial_reason,version) VALUES(?,?,?,?)',
                (symbol,now_ms,str(reason),VERSION)).lastrowid
            observe(symbol,'DATA_BLOCKED','NONE',{'episode_id':episode,'reason':str(reason)},now_ms,c)
        c.execute('INSERT INTO samples(episode_id,observed_ms,reason) VALUES(?,?,?)',(episode,now_ms,str(reason)))
    c.close()

def record_analysis(a,partial=False,now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    p=a.payload or {};d=p.get('direction_engine') or {}
    if partial:
        missing=partial_fields(p.get('native_derivative_evidence') or {})
        if not missing:return
    elif not p.get('native_chain_v2') or not p.get('derivatives_ready'):return
    else:missing=[]
    eligible=bool(d.get('direction') in ('LONG','SHORT') and p.get('actionable_liquidity_ok')
        and (p.get('structure_gate') or {}).get('qualified_precheck') and float((p.get('t15') or {}).get('atr_pct',99))<4)
    data={'research_only':True,'missing':missing,'eligible_observation':eligible,
        'strategy_identity':p.get('strategy_identity'),
        'analyst_status':getattr(a,'status',None),
        'stage':'DIRECTION_AND_STRUCTURE_PRECHECK_NOT_ENTRY_CONFIRMATION',
        'btc_regime':p.get('market_regime'),'quote_volume_24h':p.get('quote_volume_24h'),
        'long_score':a.long_score,'short_score':a.short_score,'direction_engine':d,
        'structure_gate':p.get('structure_gate'),'setup_plan':p.get('setup_plan'),
        'evidence':p.get('native_derivative_evidence'),'scope':'gross_price_observation_not_trade_pnl'}
    with connect() as c:
        if not partial:
            c.execute('UPDATE episodes SET recovered_ms=? WHERE symbol=? AND recovered_ms IS NULL',(now_ms,a.symbol))
        observe(a.symbol,'PARTIAL_3_OF_4' if partial else 'FULL_4_OF_4',d.get('direction','NONE'),data,now_ms,c)
    c.close()

def label(price,direction,bars,start_ms,horizon):
    if price is None:raise ValueError('no_fresh_native_reference_price')
    if not bars:raise ValueError('missing_native_path')
    wanted=[start_ms+i*60000 for i in range(horizon)]
    mapping={int(b[0]):b for b in bars}
    if any(t not in mapping for t in wanted):raise ValueError('missing_native_path')
    path=[mapping[t] for t in wanted]
    end=(float(path[-1][4])/price-1)*100
    up=max(0,max((float(b[2])/price-1)*100 for b in path))
    down=min(0,min((float(b[3])/price-1)*100 for b in path))
    hits={}
    for target in (3,5,7,10,15):
        for side in ('up','down'):
            hit=next((i+1 for i,b in enumerate(path) if
                ((float(b[2])/price-1)*100>=target if side=='up' else (1-float(b[3])/price)*100>=target)),None)
            hits[f'{side}_{target}pct_first_full_minute']=hit
    return {'scope':'gross_price_only_no_fee_funding_or_execution_assumption','return_pct':end,
        'directional_return_pct':end if direction=='LONG' else -end if direction=='SHORT' else None,
        'max_up_pct':up,'max_down_pct':down,'hits':hits,
        'path_starts_at_next_full_minute':True}

def resolve(now_ms=None):
    from long_short_live_pool import native_futures_path_bars
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    with connect() as c:
        rows=c.execute('''SELECT id,symbol,observed_ms,direction,price FROM observations o
            WHERE (SELECT count(*) FROM forward_labels f WHERE f.observation_id=o.id AND f.state='RESOLVED')<4
            ORDER BY COALESCE((SELECT MAX(checked_ms) FROM forward_labels f WHERE f.observation_id=o.id),0),id LIMIT 500''').fetchall()
        for oid,symbol,observed,direction,price in rows:
            start=(observed//60000+1)*60000
            for horizon in (15,30,60,180):
                if now_ms<start+horizon*60000:continue
                old=c.execute('SELECT state,checked_ms FROM forward_labels WHERE observation_id=? AND horizon_minutes=?',(oid,horizon)).fetchone()
                if old and (old[0]=='RESOLVED' or now_ms-old[1]<300000):continue
                try:
                    if price is None:raise ValueError('no_fresh_native_reference_price')
                    data=label(price,direction,native_futures_path_bars(symbol,start,horizon),start,horizon)
                    state='RESOLVED'
                except (ValueError,RuntimeError,sqlite3.Error,OSError) as e:
                    state='UNRESOLVED';data={'reason':str(e)[:200]}
                c.execute('INSERT OR REPLACE INTO forward_labels VALUES(?,?,?,?,?)',(oid,horizon,state,json.dumps(data),now_ms))
        c.commit()
        counts=dict(c.execute('SELECT cohort,count(*) FROM observations GROUP BY cohort').fetchall())
        labels=dict(c.execute('SELECT state,count(*) FROM forward_labels GROUP BY state').fetchall())
        print('V2_DATA_OPPORTUNITY_SUMMARY',json.dumps({'observations':counts,'labels':labels,
            'qualified_observations':dict(c.execute("SELECT cohort,count(*) FROM observations WHERE json_extract(payload,'$.eligible_observation')=1 GROUP BY cohort").fetchall()),
            'recovered_data_blocks':c.execute('SELECT count(*) FROM episodes WHERE recovered_ms IS NOT NULL').fetchone()[0],
            'open_data_blocks':c.execute('SELECT count(*) FROM episodes WHERE recovered_ms IS NULL').fetchone()[0],
            'research_only':True,'version':VERSION}),flush=True)
    c.close()

    # Separate audit: never promotes observations into production signals.
    from long_short_rejection_audit import resolve as resolve_rejections
    resolve_rejections(now_ms=now_ms)

def main():
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        try:resolve()
        except Exception as e:print('V2_DATA_OPPORTUNITY_ERROR',type(e).__name__,str(e)[:160],flush=True)
        stop.wait(60)

if __name__=='__main__':main()
