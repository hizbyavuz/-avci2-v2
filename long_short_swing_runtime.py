"""Isolated swing paper worker. Reuses collectors and the acknowledged outbox.

SWING_STRUCTURE_V1 selects this runtime; SWING_NOTIFICATIONS=1 explicitly
enables research notices. There is no exchange-order endpoint in this worker.
"""
import argparse
import hashlib
import json
import os
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from long_short_swing import CONFIG, CONFIG_HASH, VERSION, HOUR, MINUTE, assess, geometry, message, rank
from long_short_swing_outcomes import initial, advance, result
from long_short_major_move_store import db, root, SOURCES, quote, universe as old_universe
from long_short_signal_policy import publication_decision, status as publication_status

PROFILE='SWING_STRUCTURE_V1'
RUNTIME_VERSION='SWING_RUNTIME_V3_LONG_ONLY_2026_10_22'
UNIVERSE_VERSION='VERIFIED_COIN_USDT_PERP_V1_2026_10_22'
STOP=threading.Event()


def connect():
    root().mkdir(parents=True,exist_ok=True)
    c=db(root()/'swing_structure_v1.db');c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
        CREATE TABLE IF NOT EXISTS manifest(id TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS assessments(symbol TEXT,bar_ms INTEGER,observed_ms INTEGER,
          stage TEXT,payload TEXT,PRIMARY KEY(symbol,bar_ms));
        CREATE TABLE IF NOT EXISTS episodes(id TEXT PRIMARY KEY,symbol TEXT,direction TEXT,
          created_ms INTEGER,trigger REAL,reset_ms INTEGER,payload TEXT);
        CREATE INDEX IF NOT EXISTS swing_episode_symbol ON episodes(symbol,direction,created_ms);
        CREATE TABLE IF NOT EXISTS references_v1(id TEXT PRIMARY KEY,episode_id TEXT,symbol TEXT,
          kind TEXT,reference_ms INTEGER,state TEXT,payload TEXT,progress TEXT,result TEXT,updated_ms INTEGER);
        CREATE INDEX IF NOT EXISTS swing_reference_state ON references_v1(state,updated_ms);
        CREATE TABLE IF NOT EXISTS deliveries(episode_id TEXT PRIMARY KEY,created_ms INTEGER,
          ack_ms INTEGER,state TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS coverage(hour_ms INTEGER PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS context_snapshots(id TEXT PRIMARY KEY,phase TEXT,observed_ms INTEGER,
          symbol TEXT,observer_id TEXT,payload TEXT);
    ''')
    code_hash=hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in
        ('long_short_swing.py','long_short_swing_outcomes.py','long_short_swing_runtime.py',
         'long_short_signal_policy.py','long_short_short_retest.py'))).hexdigest()
    body=json.dumps(dict(version=VERSION,config=CONFIG,config_hash=CONFIG_HASH,code_hash=code_hash,
                         universe_version=UNIVERSE_VERSION,profile=PROFILE,runtime_version=RUNTIME_VERSION,
                         publication_policy=publication_status()),sort_keys=True)
    # Preserve old manifests and observations. Selection/outcomes are still V1;
    # publication policy and direction reports are a separately versioned runtime.
    manifest_id=VERSION+':'+RUNTIME_VERSION
    prior=c.execute('SELECT payload FROM manifest WHERE id=?',(manifest_id,)).fetchone()
    if prior and prior[0]!=body:
        raise ValueError('FROZEN_CODE_OR_CONFIG_CHANGED_WITHOUT_NEW_VERSION')
    c.execute('INSERT OR IGNORE INTO manifest VALUES(?,?)',(manifest_id,body));c.commit()
    return c


def universe(now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    c=db(root()/'native_derivatives_v2.db',True)
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='market_metadata'").fetchone():
            raise ValueError('ASSET_CLASS_METADATA_MISSING')
        row=c.execute('SELECT observed_ms,payload FROM market_metadata WHERE id=1').fetchone()
    finally:c.close()
    if not row or not 0<=now_ms-row[0]<=HOUR:raise ValueError('ASSET_CLASS_METADATA_STALE')
    metadata=json.loads(row[1])
    if metadata.get('source')!='BINANCE_UM_FUTURES_REST':raise ValueError('ASSET_CLASS_SOURCE_INVALID')
    coins={m['symbol'] for m in metadata.get('markets',[]) if m.get('underlyingType')=='COIN'}
    values=[r for r in old_universe(now_ms,min_quote_volume=CONFIG['min_quote_volume_24h']) if r[0] in coins]
    if not values:raise ValueError('CRYPTO_UNIVERSE_UNAVAILABLE')
    return values


def demand(limit=90,full_chart=False):
    now=int(time.time()*1000);urgent=[];path=root()/'swing_structure_v1.db'
    if path.exists():
        c=db(path,True)
        try:
            urgent=[r[0] for r in c.execute("SELECT DISTINCT symbol FROM references_v1 WHERE state IN ('WAIT_ENTRY','TRACKING','DATA_GAP')")]
        finally:c.close()
    values=universe(now);symbols=[r[0] for r in values]
    offset=(now//HOUR*24)%max(1,len(symbols))
    requested=list(dict.fromkeys(['BTCUSDT']+urgent+symbols[offset:]+symbols[:offset]))
    return requested[:limit],urgent[:limit]


def read_bars(kdb,symbol,interval='1h',limit=220,start=None,end=None):
    query='''SELECT open_ms,close_ms,open,high,low,close,quote_volume,taker_buy_quote,source
             FROM futures_closed_klines WHERE symbol=? AND interval=?'''
    args=[symbol,interval]
    if start is not None:query+=' AND open_ms>=?';args.append(start)
    if end is not None:query+=' AND close_ms<?';args.append(end)
    query+=' ORDER BY open_ms '+('ASC' if start is not None else 'DESC')+' LIMIT ?';args.append(limit)
    rows=kdb.execute(query,args).fetchall()
    if any(r['source'] not in SOURCES for r in rows):raise ValueError('UNVERIFIED_SOURCE')
    return [dict(r) for r in (rows if start is not None else reversed(rows))]


def admit_episode(c,p,rows):
    """Freeze each breakout level. Rising rolling highs cannot issue new ideas.

    A new episode requires six consecutive CLOSED hours back inside the old
    level, after the previous episode. Reset and new break can occur while the
    worker is down; the complete available six-hour sequence must prove it.
    """
    old=c.execute('SELECT * FROM episodes WHERE symbol=? AND direction=? AND reset_ms IS NULL ORDER BY created_ms DESC LIMIT 1',
                  (p['symbol'],p['direction'])).fetchone()
    if old:
        sign=1 if p['direction']=='LONG' else -1;inside=[];reset=None
        for b in rows:
            if b['close_ms'] <= old['created_ms']:continue
            if sign*(b['close']-old['trigger']) <= 0:
                if inside and b['open_ms'] != inside[-1]['open_ms']+HOUR:inside=[]
                inside.append(b)
                if len(inside)>=CONFIG['reset_inside_hours']:reset=b['close_ms'];break
            else:inside=[]
        if reset is None:return None
        c.execute('UPDATE episodes SET reset_ms=? WHERE id=?',(reset,old['id']))
    key=hashlib.sha256(json.dumps([VERSION,CONFIG_HASH,p['symbol'],p['direction'],p['confirmation_ms'],p['trigger']]).encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO episodes VALUES(?,?,?,?,?,?,?)',
              (key,p['symbol'],p['direction'],p['confirmation_ms'],p['trigger'],None,json.dumps(p)))
    return key


def reference(c,eid,p,kind,after_ms):
    key=eid+':'+kind
    entry_ms=(after_ms//MINUTE+1)*MINUTE
    c.execute('INSERT OR IGNORE INTO references_v1 VALUES(?,?,?,?,?,?,?,?,?,?)',
              (key,eid,p['symbol'],kind,entry_ms,'WAIT_ENTRY',json.dumps(p),'{}','{}',after_ms))


def publish(c,kdb,eid,p,now_ms,publisher=None):
    if os.getenv('SWING_NOTIFICATIONS','0')!='1':return 'PAPER_ONLY'
    policy=publication_decision(p)
    if not policy['research_publish_allowed']:return policy['reason']
    token=os.getenv('TELEGRAM_BOT_TOKEN','');chat=os.getenv('TELEGRAM_CHAT_ID','')
    if not token or not chat:return 'TELEGRAM_NOT_CONFIGURED'
    if c.execute('SELECT 1 FROM deliveries WHERE episode_id=?',(eid,)).fetchone():return 'ALREADY_ATTEMPTED'
    used=c.execute('SELECT COUNT(*) FROM deliveries WHERE created_ms>?',(now_ms-24*HOUR,)).fetchone()[0]
    if used>=CONFIG['max_notices_per_day']:return 'DAILY_NOTICE_BUDGET'
    fresh=quote(kdb,p['symbol'],now_ms)
    if fresh is None:return 'FRESH_PRICE_MISSING'
    if now_ms>=p['entry_valid_until_ms']:return 'EXPIRED'
    reason=geometry(p,fresh)
    if reason:return reason
    sign=1 if p['direction']=='LONG' else -1;risk=sign*(fresh-p['stop'])
    notice=dict(p,price=fresh,risk_pct=100*risk/fresh,
                known_room_r=sign*(p['obstacle']-fresh)/risk if p.get('obstacle') else None,
                move_before_signal_pct=100*sign*(fresh/p['origin_price']-1))
    c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(eid,now_ms,None,'PUBLISH_PENDING',json.dumps(notice)));c.commit()
    try:
        if publisher is None:
            from long_short_message_card import publish as publisher
        receipt=publisher(token,chat,p['symbol'],'CHART',message(notice),p['direction'],
                          observed_at=datetime.fromtimestamp(now_ms/1000,timezone.utc).isoformat(),episode_key=eid)
        if receipt.get('status') not in ('ACK','UNCHANGED'):raise ValueError('UNACKNOWLEDGED')
    except Exception as exc:
        c.execute("UPDATE deliveries SET state='DELIVERY_UNCERTAIN' WHERE episode_id=?",(eid,));c.commit()
        log('DELIVERY_UNCERTAIN',dict(symbol=p['symbol'],error=type(exc).__name__))
        return 'DELIVERY_UNCERTAIN'
    ack_ms=int(time.time()*1000)
    c.execute("UPDATE deliveries SET state='ACK',ack_ms=? WHERE episode_id=?",(ack_ms,eid))
    reference(c,eid,notice,'DELIVERED',ack_ms);c.commit()
    return 'ACK'


def measure(c,kdb,now_ms):
    rows=c.execute("SELECT * FROM references_v1 WHERE state IN ('WAIT_ENTRY','TRACKING','DATA_GAP') ORDER BY updated_ms LIMIT 24").fetchall()
    for r in rows:
        p=json.loads(r['payload']);g=json.loads(r['progress']);state=r['state'];out={}
        cursor=g.get('cursor_ms',r['reference_ms'])
        try:
            bars=read_bars(kdb,r['symbol'],'1m',limit=1440,start=cursor,end=now_ms-250)
            if not g:
                if not bars or bars[0]['open_ms']!=cursor:
                    state='WAIT_ENTRY' if now_ms<cursor+5*MINUTE else 'DATA_GAP'
                    out=dict(status=state,missing_ms=cursor,full_net_pct=None)
                else:
                    invalid=geometry(p,bars[0]['open'])
                    if invalid:
                        state='ENTRY_INVALID';out=dict(status=state,reason=invalid,full_net_pct=None)
                    else:g=initial(p,cursor,bars[0]['open'])
            if g:
                g=advance(p,g,bars)
                if not bars and cursor+MINUTE<now_ms-5*MINUTE:g['missing_ms']=cursor
                out=result(p,g)
                state='DONE' if out['status']=='COMPLETE' else 'DATA_GAP' if g['missing_ms'] else 'TRACKING'
            if state=='DATA_GAP' and now_ms>r['reference_ms']+(CONFIG['max_hold_hours']+24)*HOUR:
                state='DONE_DATA_GAP'
        except (ValueError,sqlite3.Error,KeyError,TypeError) as exc:
            state='DATA_GAP';out=dict(status=state,reason=str(exc)[:120],full_net_pct=None)
        c.execute('UPDATE references_v1 SET state=?,progress=?,result=?,updated_ms=? WHERE id=?',
                  (state,json.dumps(g),json.dumps(out),now_ms,r['id']))
    c.commit()


def notify_updates(c,publisher=None):
    """Edit the acknowledged episode card; edits may not trigger a Telegram push."""
    if os.getenv('SWING_NOTIFICATIONS','0')!='1':return
    token=os.getenv('TELEGRAM_BOT_TOKEN','');chat=os.getenv('TELEGRAM_CHAT_ID','')
    if not token or not chat:return
    rows=c.execute("SELECT * FROM references_v1 WHERE kind='DELIVERED' AND progress!='{}' ORDER BY reference_ms DESC").fetchall()
    for r in rows:
        g=json.loads(r['progress']);p=json.loads(r['payload'])
        # Recovered cards must obey the same reviewed-model policy as new
        # notices. A historical delivery is not permission to publish SHORT.
        if not publication_decision(p)['research_publish_allowed']:continue
        fingerprint=json.dumps([g['stop'],g['exit']],sort_keys=True)
        if g.get('notified_state')==fingerprint:continue
        if not g['exit'] and not g['trailing_armed']:continue
        latest=c.execute("SELECT episode_id FROM deliveries WHERE state='ACK' AND json_extract(payload,'$.symbol')=? ORDER BY ack_ms DESC LIMIT 1",(r['symbol'],)).fetchone()
        if not latest or latest[0]!=r['episode_id']:continue
        text=message(p)
        if g['exit']:
            cost_only=g['exit']['gross_pct']-CONFIG['round_trip_cost_pct']
            text+=f"\n\nKAĞIT PLAN KAPANDI · {g['exit']['kind']}\nÇıkış: {g['exit']['price']:.8g}\nÜcret/kayma varsayımı sonrası: %{cost_only:+.2f}\nFunding eksik; tüm maliyetler sonrası net sonuç bilinmiyor."
        else:text+=f"\n\nKAĞIT TAKİP GÜNCELLEMESİ\nYeni yapı stopu: {g['stop']:.8g}"
        try:
            if publisher is None:
                from long_short_message_card import publish as publisher
            receipt=publisher(token,chat,p['symbol'],'CHART',text,p['direction'],
                              observed_at=datetime.now(timezone.utc).isoformat(),episode_key=r['episode_id'])
            if receipt.get('status') not in ('ACK','UNCHANGED'):continue
            g['notified_state']=fingerprint
            c.execute('UPDATE references_v1 SET progress=? WHERE id=?',(json.dumps(g),r['id']));c.commit()
        except Exception as exc:log('UPDATE_UNAVAILABLE',dict(error=type(exc).__name__,symbol=r['symbol']))
        break


def report(c):
    """All records stay in denominators; conditional summaries name their subset."""
    answer=dict(version=VERSION,runtime_version=RUNTIME_VERSION,config_hash=CONFIG_HASH,universe_version=UNIVERSE_VERSION,
                publication_policy=publication_status(),
                deliveries=dict(c.execute('SELECT state,COUNT(*) FROM deliveries GROUP BY state')),
                cohorts={},notes=['Not independent trades; cluster by market day and symbol.',
                                  'Raw favorable movement is not plan profit.',
                                  'Funding missing: full net is unknown.'])
    for kind in ('RESEARCH','DELIVERED'):
        rows=c.execute('SELECT state,payload,progress,result FROM references_v1 WHERE kind=?',(kind,)).fetchall()
        group=dict(total=len(rows),states={},checkpoints={},closed_plan_fee_only_conditional_mean_pct=None)
        closed=[];full=[]
        for r in rows:
            group['states'][r['state']]=group['states'].get(r['state'],0)+1
            out=json.loads(r['result'])
            if out.get('fee_slippage_only_pct') is not None:closed.append(out['fee_slippage_only_pct'])
            if out.get('full_net_pct') is not None:full.append(out['full_net_pct'])
        group['closed_plan_count']=len(closed)
        group['closed_plan_fee_only_conditional_mean_pct']=sum(closed)/len(closed) if closed else None
        group['full_net_known_count']=len(full)
        for h in ('24','72','168'):
            points=[json.loads(r['progress']).get('checkpoints',{}).get(h) for r in rows]
            available=[x for x in points if x is not None]
            group['checkpoints'][h]=dict(available=len(available),unavailable=len(rows)-len(available),
                mark_to_horizon_fee_only_mean_pct=sum(x['fee_slippage_only_pct'] for x in available)/len(available) if available else None)
        answer['cohorts'][kind]=group
        # Aggregate LONG profit must never conceal a losing SHORT cohort.
        group['by_direction']={}
        for direction in ('LONG','SHORT','UNKNOWN'):
            side=[r for r in rows if (json.loads(r['payload']).get('direction')
                  if json.loads(r['payload']).get('direction') in ('LONG','SHORT') else 'UNKNOWN')==direction]
            outcomes=[json.loads(r['result']) for r in side]
            fees=[x['fee_slippage_only_pct'] for x in outcomes if x.get('fee_slippage_only_pct') is not None]
            nets=[x['full_net_pct'] for x in outcomes if x.get('full_net_pct') is not None]
            group['by_direction'][direction]=dict(total=len(side),closed_plan_count=len(fees),
                unknown_or_open_count=len(side)-len(fees),
                positive_closed_fee_only=sum(x>0 for x in fees),
                closed_plan_fee_only_conditional_mean_pct=sum(fees)/len(fees) if fees else None,
                full_net_known_count=len(nets),trading_validated=False,checkpoints={})
            for h in ('24','72','168'):
                points=[json.loads(r['progress']).get('checkpoints',{}).get(h) for r in side]
                available=[x for x in points if x is not None]
                group['by_direction'][direction]['checkpoints'][h]=dict(
                    available=len(available),unavailable=len(side)-len(available),
                    open_plans_included=sum(bool(x.get('open_plan')) for x in available),
                    mark_to_horizon_fee_only_mean_pct=sum(x['fee_slippage_only_pct'] for x in available)/len(available) if available else None)
    return answer


def scan(c,kdb,now_ms):
    values=universe(now_ms);btc=read_bars(kdb,'BTCUSDT',end=now_ms-250);ready=[];counts={}
    for symbol,*_ in values:
        try:
            latest=kdb.execute("SELECT MAX(close_ms) FROM futures_closed_klines WHERE symbol=? AND interval='1h' AND close_ms<?",
                               (symbol,now_ms-250)).fetchone()[0]
            mark=latest if latest is not None else now_ms//HOUR*HOUR-1
            old=c.execute('SELECT stage,payload FROM assessments WHERE symbol=? AND bar_ms=?',(symbol,mark)).fetchone()
            if old and old[0]=='READY':
                p=json.loads(old[1])
                episode=c.execute('SELECT id FROM episodes WHERE symbol=? AND created_ms=? AND direction=?',
                                  (symbol,p['confirmation_ms'],p['direction'])).fetchone()
                if episode and now_ms<p['entry_valid_until_ms']:ready.append((episode[0],p))
                counts['ALREADY_EVALUATED']=counts.get('ALREADY_EVALUATED',0)+1
                continue
            if old:
                previous=json.loads(old[1])
                retry_reasons={'HISTORY_MISSING_169H','HISTORY_MISSING','DATA_GAP','INVALID_BAR_TIME',
                               'INVALID_OHLC','VOLUME_OR_TAKER_MISSING','VOLUME_BASELINE_UNAVAILABLE'}
                if previous['reason'] not in retry_reasons:
                    counts[previous['reason']]=counts.get(previous['reason'],0)+1
                    continue
            rows=read_bars(kdb,symbol,end=now_ms-250)
            p=assess(symbol,rows,now_ms,btc)
            p['universe_version']=UNIVERSE_VERSION
            p['publication_policy']=publication_decision(p)
            c.execute('INSERT OR REPLACE INTO assessments VALUES(?,?,?,?,?)',
                      (symbol,mark,now_ms,p['stage'],json.dumps(p)))
            if p['stage']=='READY':
                eid=admit_episode(c,p,rows)
                if eid:
                    reference(c,eid,p,'RESEARCH',now_ms)
                    from long_short_major_context import capture,record
                    context=capture(kdb,symbol,p['direction'],now_ms)
                    record(c,eid,'SWING_CANDIDATE',context)
                    ready.append((eid,p))
                else:p=dict(p,reason='EPISODE_ALREADY_ACTIVE')
            counts[p['reason']]=counts.get(p['reason'],0)+1
        except (ValueError,sqlite3.Error,KeyError,TypeError) as exc:
            counts['DATA_UNAVAILABLE']=counts.get('DATA_UNAVAILABLE',0)+1
            log('SYMBOL_UNAVAILABLE',dict(symbol=symbol,error=str(exc)[:100]))
    c.commit()
    keyed={eid:p for eid,p in ready}
    for p in rank([dict(p,episode_id=eid) for eid,p in keyed.items()]):
        status=publish(c,kdb,p['episode_id'],p, int(time.time()*1000))
        counts['NOTICE_'+status]=counts.get('NOTICE_'+status,0)+1
    coverage=dict(profile=PROFILE,runtime_version=RUNTIME_VERSION,observed_ms=now_ms,universe=len(values),counts=counts,
                  publication_policy=publication_status(),
                  full_universe_coverage_claim=False,notifications=os.getenv('SWING_NOTIFICATIONS','0')=='1')
    c.execute('INSERT OR REPLACE INTO coverage VALUES(?,?)',(now_ms//HOUR*HOUR,json.dumps(coverage)));c.commit()
    return coverage


def health(state_root):
    path=state_root/'swing_structure_v1.db'
    if not path.exists():return dict(profile=PROFILE,runtime_version=RUNTIME_VERSION,
                                    publication_policy=publication_status(),available=False)
    c=db(path,True)
    try:
        latest=c.execute('SELECT payload FROM coverage ORDER BY hour_ms DESC LIMIT 1').fetchone()
        return dict(profile=PROFILE,runtime_version=RUNTIME_VERSION,publication_policy=publication_status(),
                    available=True,coverage=json.loads(latest[0]) if latest else None,
                    references=dict(c.execute('SELECT state,COUNT(*) FROM references_v1 GROUP BY state')),
                    deliveries=dict(c.execute('SELECT state,COUNT(*) FROM deliveries GROUP BY state')))
    finally:c.close()


def log(event,payload):
    print('SWING_'+event+' '+json.dumps(payload,sort_keys=True),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true')
    parser.add_argument('--report',action='store_true');args=parser.parse_args()
    for s in (signal.SIGTERM,signal.SIGINT):signal.signal(s,lambda *_:STOP.set())
    c=connect()
    if args.report:
        print(json.dumps(report(c),indent=2));c.close();return
    c.execute("UPDATE deliveries SET state='DELIVERY_UNCERTAIN' WHERE state='PUBLISH_PENDING'");c.commit()
    while not STOP.is_set():
        try:
            kdb=db(root()/'binance_futures_kline_shadow.db',True)
            try:
                now=int(time.time()*1000);coverage=scan(c,kdb,now);measure(c,kdb,now);notify_updates(c)
                log('HEALTH',coverage)
            finally:kdb.close()
        except (OSError,ValueError,sqlite3.Error) as exc:log('UNAVAILABLE',dict(error=str(exc)[:180]))
        if args.once:break
        STOP.wait(30)
    c.close()


if __name__=='__main__':main()
