"""Native-candle chart scenarios, durable notifications and forward evidence.

Legacy analysis keeps running for comparison. This worker alone owns new
chart-first messages when enabled. It has no exchange order API.
"""
import hashlib
import json
import math
import os
import signal
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime, timezone

from long_short_chart_first import CONFIG, CONFIG_HASH, STRATEGY_ID, VERSION, assess, enabled, message

STOP=threading.Event()
VALID={'BINANCE_UM_FUTURES_WEBSOCKET','BINANCE_UM_FUTURES_ARCHIVE','BINANCE_UM_FUTURES_REST'}


def root():
    return os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state'


def connect():
    os.makedirs(root(),exist_ok=True)
    c=sqlite3.connect(os.path.join(root(),'chart_first_v1.db'),timeout=10)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
      CREATE TABLE IF NOT EXISTS manifests(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY, event_key TEXT UNIQUE,
        observed_ms INTEGER,symbol TEXT,direction TEXT,stage TEXT,reason TEXT,price REAL,payload TEXT);
      CREATE TABLE IF NOT EXISTS episodes(id TEXT PRIMARY KEY,symbol TEXT,direction TEXT,
        created_ms INTEGER,stage TEXT,delivery_stage TEXT,ended_ms INTEGER,payload TEXT);
      CREATE INDEX IF NOT EXISTS episodes_symbol ON episodes(symbol,created_ms);
      CREATE TABLE IF NOT EXISTS outcomes(assessment_id INTEGER,horizon INTEGER,state TEXT,
        checked_ms INTEGER,payload TEXT,PRIMARY KEY(assessment_id,horizon));
      CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY,episode_id TEXT,observed_ms INTEGER,
        stage TEXT,price REAL,payload TEXT);
    ''')
    c.execute('INSERT OR IGNORE INTO manifests VALUES(?,?)',(STRATEGY_ID,json.dumps(dict(version=VERSION,config_hash=CONFIG_HASH,config=CONFIG))))
    c.commit()
    return c


def active_symbols():
    """Bounded support-data demand; price decisions never wait for this queue."""
    path=os.path.join(root(),'chart_first_v1.db')
    if not os.path.isfile(path):return []
    try:
        with sqlite3.connect('file:'+os.path.abspath(path)+'?mode=ro',uri=True,timeout=2) as c:
            active=[r[0] for r in c.execute('SELECT symbol FROM episodes WHERE ended_ms IS NULL ORDER BY created_ms DESC LIMIT 32')]
            recent=[r[0] for r in c.execute("SELECT DISTINCT symbol FROM assessments WHERE observed_ms>=? AND stage IN ('WATCH','READY') LIMIT 32",(int(time.time()*1000)-300000,))]
            return list(dict.fromkeys(active+recent))[:48]
    except sqlite3.Error:return []


def candles(con,symbol):
    frames={}
    for tf,count in (('5m',120),('15m',120),('1h',120),('4h',220)):
        rows=con.execute('''SELECT open_ms,open,high,low,close,volume,close_ms,source
          FROM futures_closed_klines WHERE symbol=? AND interval=? ORDER BY open_ms DESC LIMIT ?''',
          (symbol,tf,count)).fetchall()
        if len(rows)<count or any(r[7] not in VALID for r in rows):
            raise ValueError('VERIFIED_HISTORY_MISSING_'+tf)
        frames[tf]=[list(r[:7]) for r in reversed(rows)]
    return frames


def quote(con,symbol,now_ms):
    r=con.execute('SELECT price,event_ms,received_ms,source FROM futures_last_price WHERE symbol=?',(symbol,)).fetchone()
    if not r or r[3]!='BINANCE_UM_FUTURES_WEBSOCKET':return None
    if not all(0<=now_ms-int(t)<=CONFIG['quote_max_age_ms'] for t in r[1:3]):return None
    p=float(r[0])
    return p if math.isfinite(p) and p>0 else None


def record(c,p):
    # One snapshot per changed structural idea / state per 15m, not a duplicate
    # every polling tick. Rejections retain both the reason and subsequent path.
    key=hashlib.sha256(json.dumps([STRATEGY_ID,p['symbol'],p['observed_ms']//900000,
        p['direction'],p['stage'],p['reason'],p.get('trigger'),p.get('stop')]).encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO assessments(event_key,observed_ms,symbol,direction,stage,reason,price,payload) VALUES(?,?,?,?,?,?,?,?)',
        (key,p['observed_ms'],p['symbol'],p['direction'],p['stage'],p['reason'],p['price'],json.dumps(p,ensure_ascii=False)))


def live_stage(p,price,last_close,closed_ms,now_ms):
    """Evaluate the immutable episode levels; never move the stop/obstacle."""
    sign=1 if p['direction']=='LONG' else -1
    if sign*(price-p['stop'])<=0:return 'BROKEN'
    if p.get('obstacle') and sign*(price-p['obstacle'])>=0:return 'OBSTACLE_REACHED'
    if now_ms-p['created_ms']>=CONFIG['horizon_minutes']*60000:return 'EXPIRED'
    if p['stage']=='READY':return 'READY'
    if sign*(price-p['entry_limit'])>0:return 'EXPIRED'
    if (sign*(last_close-p['trigger'])>0 and sign*(price-p['trigger'])>=0
            and 250<=now_ms-closed_ms<=75000):
        if p.get('obstacle') and sign*(p['obstacle']-price)/price*100<=CONFIG['round_trip_cost_pct']:
            return 'EXPIRED'
        return 'READY'
    return 'WATCH'


def path_outcome(p,bars,start,horizon):
    mapping={int(r[0]):r for r in bars}
    wanted=[start+i*60000 for i in range(horizon)]
    if any(t not in mapping for t in wanted):raise ValueError('MISSING_NATIVE_PATH')
    path=[mapping[t] for t in wanted];price=p['price']
    if not price or price<=0:raise ValueError('MISSING_REFERENCE_PRICE')
    sign=1 if p['direction']=='LONG' else -1 if p['direction']=='SHORT' else None
    result=dict(scope='POST_OBSERVATION_PRICE_PATH_NOT_EXECUTED_PNL',
        return_pct=(float(path[-1][4])/price-1)*100,
        max_up_pct=max(0,max(float(r[2])/price-1 for r in path)*100),
        max_down_pct=min(0,min(float(r[3])/price-1 for r in path)*100),
        first_level=None,first_level_minute=None,path_starts_at_next_full_minute=True)
    if sign is not None:
        result['directional_return_pct']=sign*result['return_pct']
        stop=p.get('stop');obstacle=p.get('obstacle')
        # Levels are an observation experiment, not presumed fills for WATCH or
        # REJECTED records. Same-minute ambiguity uses the pessimistic order.
        for i,r in enumerate(path,1):
            stop_hit=stop is not None and (float(r[3])<=stop if sign==1 else float(r[2])>=stop)
            target_hit=obstacle is not None and (float(r[2])>=obstacle if sign==1 else float(r[3])<=obstacle)
            if stop_hit or target_hit:
                result.update(first_level='INVALIDATION' if stop_hit else 'OBSTACLE',first_level_minute=i,
                              same_minute_ambiguous=bool(stop_hit and target_hit))
                break
    return result


def resolve(c,kdb,now_ms):
    rows=c.execute('''SELECT a.* FROM assessments a WHERE observed_ms<? AND
      (SELECT count(*) FROM outcomes o WHERE o.assessment_id=a.id AND o.state='RESOLVED')<3
      ORDER BY COALESCE((SELECT MAX(checked_ms) FROM outcomes WHERE assessment_id=a.id),0),a.id LIMIT 90''',
      (now_ms-16*60000,)).fetchall()
    for row in rows:
        p=json.loads(row['payload']);start=(row['observed_ms']//60000+1)*60000
        for horizon in (15,60,180):
            if now_ms<start+horizon*60000:continue
            old=c.execute('SELECT state,checked_ms FROM outcomes WHERE assessment_id=? AND horizon=?',(row['id'],horizon)).fetchone()
            if old and (old['state']=='RESOLVED' or now_ms-old['checked_ms']<3600000):continue
            try:
                bars=kdb.execute('''SELECT open_ms,open,high,low,close,volume,close_ms FROM futures_closed_klines
                  WHERE symbol=? AND interval='1m' AND open_ms>=? AND open_ms<? AND source IN (?,?,?) ORDER BY open_ms''',
                  (row['symbol'],start,start+horizon*60000,*sorted(VALID))).fetchall()
                outcome=path_outcome(p,bars,start,horizon);state='RESOLVED'
            except (ValueError,TypeError,KeyError) as exc:
                state='UNRESOLVED';outcome={'reason':str(exc)[:160]}
            c.execute('INSERT OR REPLACE INTO outcomes VALUES(?,?,?,?,?)',
                (row['id'],horizon,state,now_ms,json.dumps(outcome)))
    c.commit()


def advance(c,kdb,decisions,now_ms,publisher,token,chat):
    sent=0;changes=0
    for row in c.execute('SELECT * FROM episodes WHERE ended_ms IS NULL').fetchall():
        now_ms=max(now_ms,int(time.time()*1000))
        p=json.loads(row['payload']);price=quote(kdb,row['symbol'],now_ms)
        if price is None:continue
        latest=kdb.execute("SELECT close,close_ms FROM futures_closed_klines WHERE symbol=? AND interval='5m' ORDER BY open_ms DESC LIMIT 1",(row['symbol'],)).fetchone()
        if not latest:continue
        evaluation=dict(p,stage='WATCH') if row['stage']=='READY' and row['delivery_stage']!='READY' else p
        stage=live_stage(evaluation,price,float(latest[0]),int(latest[1]),now_ms)
        if p.get('strategy_id')!=STRATEGY_ID:stage='EXPIRED'
        decision=decisions.get(row['symbol'])
        if stage in ('WATCH','READY') and decision:
            if decision['direction'] not in (p['direction'],'NONE'):stage='EXPIRED'
            if stage=='READY' and row['stage']=='WATCH' and decision['stage']=='REJECTED':stage='WATCH'
            if decision['reason'] and (decision['reason'].startswith(('CHART_','DAILY_','VERIFIED_'))):
                # Missing chart data cannot be treated as a fresh confirmation.
                if stage=='READY' and row['stage']=='WATCH':stage='WATCH'
                if now_ms-int(latest[1])>600000:stage='EXPIRED'
        p.update(stage=stage,price=price,observed_ms=now_ms)
        if stage!=row['stage']:
            changes+=1
            if stage=='READY':p.update(ready_ms=now_ms,ready_price=price)
            c.execute('INSERT INTO transitions(episode_id,observed_ms,stage,price,payload) VALUES(?,?,?,?,?)',
                (row['id'],now_ms,stage,price,json.dumps(p)))
        c.execute('UPDATE episodes SET stage=?,payload=? WHERE id=?',(stage,json.dumps(p),row['id']))
        c.commit()
        visible_or_open=bool(row['delivery_stage']) or stage in ('WATCH','READY')
        if stage!=row['delivery_stage'] and token and chat and visible_or_open:
            # New READY alerts notify; completion edits the last delivered card.
            delivery_kind=stage if stage in ('WATCH','READY') else (row['delivery_stage'] or row['stage'])
            try:
                receipt=publisher(token,chat,row['symbol'],'CHART',message(p),row['direction'],
                    observed_at=datetime.fromtimestamp(now_ms/1000,timezone.utc).isoformat(),
                    episode_key=row['id']+':'+delivery_kind)
            except Exception as exc:
                print('CHART_FIRST_DELIVERY_DEFERRED',row['symbol'],type(exc).__name__,flush=True)
                continue
            if receipt['status'] in ('ACK','UNCHANGED'):
                c.execute('UPDATE episodes SET delivery_stage=? WHERE id=?',(stage,row['id']))
                sent+=int(receipt['status']=='ACK')
                if receipt['status']=='ACK' and stage in ('WATCH','READY'):
                    accepted_ms=int(time.time()*1000)
                    accepted_price=quote(kdb,row['symbol'],accepted_ms)
                    record(c,dict(p,observed_ms=accepted_ms,price=accepted_price,
                                  stage='DELIVERED_'+stage,reason='TELEGRAM_ACK',episode_id=row['id']))
        if stage not in ('WATCH','READY'):
            c.execute('UPDATE episodes SET ended_ms=? WHERE id=?',(now_ms,row['id']))
        c.commit()
    return sent,changes


def scan():
    from long_short_analyst import native_futures_websocket_universe,resolve_telegram_chat
    from long_short_native_derivatives import evidence
    from long_short_message_card import publish
    now_ms=int(time.time()*1000)
    universe=native_futures_websocket_universe(all_eligible=True,quiet=True)
    path=os.path.join(root(),'binance_futures_kline_shadow.db')
    c=connect();kdb=sqlite3.connect('file:'+os.path.abspath(path)+'?mode=ro',uri=True,timeout=5)
    decisions={};counts=Counter()
    try:
        for symbol,qv,_,_ in universe:
            stamp=int(time.time()*1000);price=quote(kdb,symbol,stamp)
            try:
                frames=candles(kdb,symbol)
                p=assess(symbol,frames,price,qv,stamp)
                if p['stage'] in ('WATCH','READY'):
                    # Cached validated fields only: no serial REST refresh can
                    # stall entry timing; the existing collector refreshes them.
                    try:
                        from long_short_chart_first import supporting_data
                        p['support']=supporting_data(evidence(symbol,refresh_stale=False))
                    except Exception as exc:p['support_error']=type(exc).__name__
            except (ValueError,TypeError,sqlite3.Error) as exc:
                p=dict(version=VERSION,config_hash=CONFIG_HASH,strategy_id=STRATEGY_ID,symbol=symbol,observed_ms=stamp,
                       price=price,direction='NONE',stage='REJECTED',reason=str(exc)[:120])
            record(c,p);decisions[symbol]=p;counts[p['reason']]+=1
        c.commit()
        token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
        if os.getenv('LS_CHART_FIRST_NOTIFY_V1','1')!='1':token=''
        chat=resolve_telegram_chat(token,os.getenv('TELEGRAM_CHAT_ID','').strip()) if token else None
        sent,changed=advance(c,kdb,decisions,int(time.time()*1000),publish,token,chat) if enabled() else (0,0)
        eligible=[p for p in decisions.values() if p['stage'] in ('WATCH','READY')]
        eligible.sort(key=lambda p:(p['stage']!='READY',abs(p['price']-p['trigger'])/p['atr5'],p['symbol']))
        created=0
        five_minute_start=int(time.time()*1000)//300000*300000
        already_created=c.execute('SELECT COUNT(*) FROM episodes WHERE created_ms>=?',(five_minute_start,)).fetchone()[0]
        for p in eligible if enabled() else []:
            now_ms=int(time.time()*1000)
            if created+already_created>=CONFIG['max_initial_notices_per_cycle']:break
            recent=c.execute('SELECT * FROM episodes WHERE symbol=? ORDER BY created_ms DESC LIMIT 1',(p['symbol'],)).fetchone()
            if recent and (recent['ended_ms'] is None or now_ms-recent['created_ms']<CONFIG['cooldown_ms']):continue
            # Revalidate price immediately, after any prior API delivery waits.
            fresh=quote(kdb,p['symbol'],now_ms)
            if fresh is None:continue
            probe=dict(p,created_ms=now_ms,stage='WATCH')
            current=live_stage(probe,fresh,p['context']['5m']['close'],p['context']['5m']['close_ms'],now_ms)
            if current not in ('WATCH','READY') or p['stage']=='READY' and current!='READY':continue
            p.update(price=fresh,stage=current,created_ms=now_ms,observed_ms=now_ms)
            eid=hashlib.sha256((p['setup_key']+':'+str(now_ms)).encode()).hexdigest()
            c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',
                (eid,p['symbol'],p['direction'],now_ms,p['stage'],None,None,json.dumps(p)))
            c.execute('INSERT INTO transitions(episode_id,observed_ms,stage,price,payload) VALUES(?,?,?,?,?)',
                (eid,now_ms,p['stage'],fresh,json.dumps(p)))
            c.commit();created+=1
        extra,_=advance(c,kdb,decisions,int(time.time()*1000),publish,token,chat) if enabled() else (0,0)
        resolve(c,kdb,int(time.time()*1000))
        print('CHART_FIRST_SUMMARY '+json.dumps(dict(version=VERSION,config_hash=CONFIG_HASH,strategy_id=STRATEGY_ID,
            checked=len(decisions),directions=dict(Counter(p['direction'] for p in decisions.values())),
            stages=dict(Counter(p['stage'] for p in decisions.values())),reasons=dict(counts),
            eligible=[dict(symbol=p['symbol'],side=p['direction'],stage=p['stage'],scenario=p.get('scenario'),
                daily=p['context']['1d']['direction'],four_hour=p['context']['4h']['direction'],hour=p['context']['1h']['direction']) for p in eligible],
            created=created,delivered=sent+extra,primary_enabled=enabled(),automatic_orders=False)),flush=True)
    finally:
        kdb.close();c.close()


def main():
    if not enabled() and os.getenv('LS_CHART_FIRST_PREVIEW','0')!='1':return
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:STOP.set())
    while not STOP.is_set():
        try:scan()
        except Exception as exc:print('CHART_FIRST_SCAN_ERROR',type(exc).__name__,str(exc)[:160],flush=True)
        STOP.wait(30)


if __name__=='__main__':main()
