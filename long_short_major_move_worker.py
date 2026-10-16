"""One replacement signal worker with bounded measurement; never places orders."""
import argparse
import hashlib
import json
import os
import signal
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime,timezone

from long_short_major_move import CONFIG,CONFIG_HASH,VERSION,assess,rank,message,exit_message,tracking_message,strategy_id
from long_short_major_move_store import root,db,connect,universe,frames,quote,save_assessment,initial_progress,measure,health
from long_short_chart_flow import hourly_flow

STOP=threading.Event()


def eligible_to_publish(c,p,now_ms):
    prior=c.execute('SELECT created_ms FROM ideas WHERE symbol=? ORDER BY created_ms DESC LIMIT 1',(p['symbol'],)).fetchone()
    if prior and now_ms-prior[0]<CONFIG['cooldown_hours']*3600000:return 'COOLDOWN'
    n=c.execute("""SELECT COUNT(*) FROM ideas WHERE delivered_ms IS NOT NULL
        AND state IN ('TRACKING','DATA_PENDING') AND json_extract(progress,'$.plan_exit') IS NULL
        AND json_extract(payload,'$.expires_ms')>?""",(now_ms,)).fetchone()[0]
    return 'ACTIVE_LIMIT' if n>=CONFIG['max_active'] else None


def publish_candidate(c,kdb,p,sid,publisher,token,chat,now_ms):
    if not token or not chat:return 'TELEGRAM_NOT_CONFIGURED'
    reason=eligible_to_publish(c,p,now_ms)
    if reason:return reason
    fresh=quote(kdb,p['symbol'],now_ms)
    sign=1 if p['direction']=='LONG' else -1
    if fresh is None:return 'FRESH_PRICE_MISSING'
    if not 250<=now_ms-p['confirmation_ms']<=90000:return 'CONFIRMATION_EXPIRED'
    if sign*(fresh-p['trigger'])<0:return 'BREAK_NOT_HELD'
    if sign*(fresh-p['entry_limit'])>0:return 'LATE_CHASE'
    risk=sign*(fresh-p['stop'])/fresh*100
    if not 0<risk<=CONFIG['max_stop_pct']:return 'INVALID_CURRENT_RISK'
    if p['obstacle'] and sign*(p['obstacle']/fresh-1)*100<CONFIG['min_room_pct']:return 'ROOM_CHANGED'
    p=dict(p,price=fresh,observed_ms=now_ms,risk_pct=risk,strategy_id=sid,
           objective=fresh*(1+sign*CONFIG['min_room_pct']/100),expires_ms=now_ms+p['horizon_hours']*3600000)
    eid=hashlib.sha256(json.dumps([sid,p['symbol'],p['model'],p['direction'],p['confirmation_ms'],p['trigger']],sort_keys=True).encode()).hexdigest()
    c.execute('INSERT INTO ideas VALUES(?,?,?,?,?,?,?,?)',(eid,p['symbol'],now_ms,None,'PUBLISH_PENDING',json.dumps(p),'{}',now_ms));c.commit()
    try:
        receipt=publisher(token,chat,p['symbol'],'CHART',message(p),p['direction'],
                          observed_at=datetime.fromtimestamp(now_ms/1000,timezone.utc).isoformat(),episode_key=eid)
        if receipt.get('status') not in ('ACK','UNCHANGED'):raise ValueError('DELIVERY_NOT_ACKNOWLEDGED')
    except Exception as exc:
        c.execute("UPDATE ideas SET state='DELIVERY_UNCERTAIN',updated_ms=? WHERE id=?",(now_ms,eid));c.commit()
        print('MAJOR_MOVE_DELIVERY_ERROR '+json.dumps(dict(symbol=p['symbol'],error=type(exc).__name__)),flush=True)
        return 'DELIVERY_UNCERTAIN'
    accepted_ms=int(time.time()*1000);accepted_price=quote(kdb,p['symbol'],accepted_ms)
    p.update(notice_price=p['price'],observed_ms=accepted_ms,accepted_price=accepted_price)
    # Acknowledgement doesn't justify inventing a missing reference quote.
    if accepted_price is None:
        state='UNRESOLVED_ENTRY';progress={}
    else:
        p['price']=accepted_price
        p['notice_objective']=p['objective']
        p['objective']=accepted_price*(1+sign*CONFIG['min_room_pct']/100)
        p['gain_before_signal_pct']=(accepted_price/p['features']['leg_base']-1)*100
        state='TRACKING';progress=initial_progress(p)
    c.execute('UPDATE ideas SET delivered_ms=?,state=?,payload=?,progress=?,updated_ms=? WHERE id=?',
              (accepted_ms,state,json.dumps(p),json.dumps(progress),accepted_ms,eid));c.commit()
    print('MAJOR_MOVE_DELIVERED '+json.dumps(dict(idea_id=eid,state=state,signal=p)),flush=True)
    return 'DELIVERED'


def retire_old_episodes():
    path=root()/'chart_first_v1.db'
    if not path.exists():return
    c=db(path)
    try:
        # Retain every old assessment and outcome. Only close obsolete plans.
        c.execute("UPDATE episodes SET stage='EXPIRED',ended_ms=? WHERE ended_ms IS NULL",(int(time.time()*1000),))
        c.commit()
    finally:c.close()


def recover_interrupted(c):
    # Never replay a possibly accepted Telegram send. Without a contemporaneous
    # ACK quote it cannot become an evaluated trade, even if a card exists.
    c.execute("UPDATE ideas SET state='DELIVERY_UNCERTAIN' WHERE state='PUBLISH_PENDING'")
    c.commit()


def notify_exits(c,publisher=None):
    token=os.getenv('TELEGRAM_BOT_TOKEN','');chat=os.getenv('TELEGRAM_CHAT_ID','')
    if not token or not chat:return
    if publisher is None:
        from long_short_message_card import publish
        publisher=publish
    rows=c.execute("""SELECT * FROM ideas WHERE
        (json_extract(progress,'$.plan_exit') IS NOT NULL AND json_extract(progress,'$.exit_notice') IS NULL)
        OR (json_extract(progress,'$.plan_exit') IS NULL AND json_extract(progress,'$.trail_armed')=1
            AND json_extract(progress,'$.active_stop')!=COALESCE(json_extract(progress,'$.notified_stop'),-1))
        ORDER BY created_ms DESC LIMIT 12""").fetchall()
    for r in rows:
        p=json.loads(r['payload']);g=json.loads(r['progress'])
        latest=c.execute('SELECT id FROM ideas WHERE symbol=? ORDER BY created_ms DESC LIMIT 1',(r['symbol'],)).fetchone()[0]
        if latest!=r['id']:
            g['exit_notice']='SUPERSEDED';g['notified_stop']=g['active_stop']
        else:
            try:
                result=publisher(token,chat,p['symbol'],'CHART',exit_message(p,g) if g['plan_exit'] else tracking_message(p,g),p['direction'],
                    observed_at=datetime.now(timezone.utc).isoformat(),episode_key=r['id'])
                if result.get('status') not in ('ACK','UNCHANGED'):continue
                if g['plan_exit']:g['exit_notice']='ACK'
                g['notified_stop']=g['active_stop']
            except Exception as exc:
                print('MAJOR_MOVE_UPDATE_ERROR '+json.dumps(dict(symbol=p['symbol'],error=type(exc).__name__)),flush=True)
                continue
        c.execute('UPDATE ideas SET progress=? WHERE id=?',(json.dumps(g),r['id']));c.commit()


def scan(c,kdb,sid,publisher=None):
    now_ms=int(time.time()*1000);items=universe(now_ms);btc=frames(kdb,'BTCUSDT')
    counts=Counter();candidates=[];bucket=now_ms//900000
    for symbol,qv,ticker_price,change in items:
        # Full eligible universe snapshot: later winners and failures survive.
        c.execute('INSERT OR IGNORE INTO market_snapshots VALUES(?,?,?)',(bucket,symbol,json.dumps(dict(observed_ms=now_ms,quote_volume=qv,price=ticker_price,change_24h_pct=change))))
        try:
            stamp=int(time.time()*1000)
            p=assess(symbol,frames(kdb,symbol),quote(kdb,symbol,stamp),qv,stamp,btc,
                     hourly_flow(kdb,symbol,stamp))
        except (ValueError,sqlite3.Error) as exc:
            p=dict(version=VERSION,symbol=symbol,observed_ms=now_ms,stage='REJECTED',reason=str(exc)[:80],direction='NONE')
        p['strategy_id']=sid;save_assessment(c,p,sid);counts[p['reason']]+=1
        if p['stage'] in ('WATCH','READY'):candidates.append(p)
    c.commit();ordered=rank(candidates);gates=Counter();sent=0
    if publisher is None:
        from long_short_message_card import publish
        publisher=publish
    token=os.getenv('TELEGRAM_BOT_TOKEN','');chat=os.getenv('TELEGRAM_CHAT_ID','')
    for p in ordered:
        if p['stage']!='READY' or sent>=CONFIG['max_new_per_scan']:continue
        result=publish_candidate(c,kdb,p,sid,publisher,token,chat,int(time.time()*1000))
        gates[result]+=1;sent+=result=='DELIVERED'
    print('MAJOR_MOVE_SCAN '+json.dumps(dict(version=VERSION,strategy_id=sid,checked=len(items),
        reasons=dict(counts),eligible=[dict(symbol=p['symbol'],direction=p['direction'],stage=p['stage'],model=p['model'],features=p['features'],risk_pct=p['risk_pct'],structural_room_pct=p['structural_room_pct']) for p in ordered[:12]],
        gates=dict(gates),delivered=sent)),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:STOP.set())
    sid=strategy_id();c=connect()
    c.execute('INSERT OR IGNORE INTO manifests VALUES(?,?)',(sid,json.dumps(dict(version=VERSION,config_hash=CONFIG_HASH,config=CONFIG,validation='UNPROVEN_RESEARCH_VERSION',automatic_orders=False))));c.commit()
    recover_interrupted(c);retire_old_episodes();c.close()
    print('MAJOR_MOVE_START '+json.dumps(dict(version=VERSION,strategy_id=sid,config_hash=CONFIG_HASH,automatic_orders=False,primary_horizon_hours=24)),flush=True)
    next_scan=0;next_health=0
    while not STOP.is_set():
        c=kdb=None
        try:
            c=connect();kdb=db(root()/'binance_futures_kline_shadow.db',True);now=int(time.time()*1000)
            measure(c,kdb,now,emit=lambda x:print(x,flush=True))
            notify_exits(c)
            if time.monotonic()>=next_scan:
                next_scan=time.monotonic()+CONFIG['scan_seconds']
                scan(c,kdb,sid)
            if time.monotonic()>=next_health:
                next_health=time.monotonic()+300
                print('MAJOR_MOVE_HEALTH '+json.dumps(health()),flush=True)
        except (sqlite3.Error,ValueError,OSError) as exc:
            print('MAJOR_MOVE_ERROR '+json.dumps(dict(error=type(exc).__name__,detail=str(exc)[:160])),flush=True)
            if args.once:raise
        finally:
            if c is not None:c.close()
            if kdb is not None:kdb.close()
        if args.once:return
        STOP.wait(10)


if __name__=='__main__':main()
