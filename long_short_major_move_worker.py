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

from long_short_major_move import CONFIG,CONFIG_HASH,VERSION,CONTEXT_VERSIONS,assess,rank,message,exit_message,tracking_message,strategy_id,BASELINE_ID,baseline
from long_short_major_move_store import root,db,connect,universe,frames,quote,save_assessment,initial_progress,measure,health,EVALUATION_HOURS,OUTCOME_VERSION,record_research,measurement_contract
from long_short_major_context import capture as capture_context,record as record_context,compact as compact_context,observer_id,VERSION as CONTEXT_VERSION,RULES as CONTEXT_RULES
from long_short_major_delivery import eligible_to_publish,attach_first_eligibility
from long_short_early_observer import EarlyScanner,observer_id as early_observer_id,RULES as EARLY_RULES,VERSION as EARLY_VERSION

STOP=threading.Event()


def publish_candidate(c,kdb,p,sid,publisher,token,chat,now_ms):
    if not token or not chat:return 'TELEGRAM_NOT_CONFIGURED'
    reason=eligible_to_publish(c,p,now_ms)
    if reason:return reason
    context=capture_context(kdb,p['symbol'],p['direction'],now_ms)
    now_ms=int(time.time()*1000)
    fresh=quote(kdb,p['symbol'])
    now_ms=int(time.time()*1000)
    sign=1 if p['direction']=='LONG' else -1
    if fresh is None:return 'FRESH_PRICE_MISSING'
    if not 250<=now_ms-p['confirmation_ms'] or now_ms>=p.get('entry_valid_until_ms',p['confirmation_ms']+90001):return 'CONFIRMATION_EXPIRED'
    if sign*(fresh-p['trigger'])<0:return 'BREAK_NOT_HELD'
    if sign*(fresh-p['entry_limit'])>0:return 'LATE_CHASE'
    risk=sign*(fresh-p['stop'])/fresh*100
    if not 0<risk<=CONFIG['max_stop_pct']:return 'INVALID_CURRENT_RISK'
    if p.get('version') not in CONTEXT_VERSIONS and p['obstacle'] and sign*(p['obstacle']/fresh-1)*100<baseline.CONFIG['min_room_pct']:return 'ROOM_CHANGED'
    if p.get('version') in CONTEXT_VERSIONS and p['obstacle'] and sign*(fresh-p['obstacle'])>=0:return 'OBSTACLE_CROSSED_WAIT_CONFIRMATION'
    p=dict(p,price=fresh,observed_ms=now_ms,risk_pct=risk,strategy_id=sid,
           hourly_flow=context['flow'],
           structural_room_pct=sign*(p['obstacle']/fresh-1)*100 if p['obstacle'] else None,
           objective=fresh*(1+sign*CONFIG['min_room_pct']/100),expires_ms=now_ms+p['horizon_hours']*3600000)
    attach_first_eligibility(c,p)
    measurement_contract(p)
    eid=hashlib.sha256(json.dumps([sid,p['symbol'],p['model'],p['direction'],p['confirmation_ms'],p['trigger']],sort_keys=True).encode()).hexdigest()
    p['market_context']=compact_context(context);p['context_snapshot_id']=eid
    record_context(c,eid,'SIGNAL',context)
    c.execute('INSERT INTO ideas VALUES(?,?,?,?,?,?,?,?)',(eid,p['symbol'],now_ms,None,'PUBLISH_PENDING',json.dumps(p),'{}',now_ms));c.commit()
    try:
        receipt=publisher(token,chat,p['symbol'],'CHART',message(p),p['direction'],
                          observed_at=datetime.fromtimestamp(now_ms/1000,timezone.utc).isoformat(),episode_key=eid)
        if receipt.get('status') not in ('ACK','UNCHANGED'):raise ValueError('DELIVERY_NOT_ACKNOWLEDGED')
    except Exception as exc:
        c.execute("UPDATE ideas SET state='DELIVERY_UNCERTAIN',updated_ms=? WHERE id=?",(now_ms,eid));c.commit()
        print('MAJOR_MOVE_DELIVERY_ERROR '+json.dumps(dict(symbol=p['symbol'],error=type(exc).__name__)),flush=True)
        return 'DELIVERY_UNCERTAIN'
    accepted_price=quote(kdb,p['symbol']);accepted_ms=int(time.time()*1000)
    p.update(notice_price=p['price'],observed_ms=accepted_ms,accepted_price=accepted_price)
    # Acknowledgement doesn't justify inventing a missing reference quote.
    if accepted_price is None:
        state='UNRESOLVED_ENTRY';progress={}
    else:
        p['price']=accepted_price
        if p.get('first_eligibility'):
            p['first_eligibility_age_ms']=accepted_ms-p['first_eligibility']['observed_ms']
            p['move_since_first_eligibility_pct']=sign*(accepted_price/p['first_eligibility']['price']-1)*100
        p['notice_objective']=p['objective']
        p['objective']=accepted_price*(1+sign*CONFIG['min_room_pct']/100)
        p['gain_before_signal_pct']=(accepted_price/p['features']['leg_base']-1)*100
        state='TRACKING';progress=initial_progress(p)
    c.execute('UPDATE ideas SET delivered_ms=?,state=?,payload=?,progress=?,updated_ms=? WHERE id=?',
              (accepted_ms,state,json.dumps(p),json.dumps(progress),accepted_ms,eid));c.commit()
    print('MAJOR_MOVE_DELIVERED '+json.dumps(dict(idea_id=eid,state=state,signal=p)),flush=True)
    print('MAJOR_CONTEXT_SIGNAL '+json.dumps(dict(idea_id=eid,context=context)),flush=True)
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
        ORDER BY created_ms DESC LIMIT 1""").fetchall()
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


def attach_context(c,kdb,p,sid,bucket):
    if p['stage'] not in ('WATCH','READY'):return
    key=hashlib.sha256(json.dumps([sid,p['symbol'],p['direction'],bucket,observer_id()]).encode()).hexdigest()
    existing=c.execute('SELECT payload FROM context_snapshots WHERE id=?',(key,)).fetchone()
    if existing:
        context=json.loads(existing[0])
    else:
        context=capture_context(kdb,p['symbol'],p['direction'],int(time.time()*1000))
        record_context(c,key,'CANDIDATE',context)
    p['context_scan_id']=key;p['market_context']=compact_context(context)
    p['hourly_flow']=context['flow']


class CandleScanner:
    """Bounded state: one watermark, assessment and pending candidate per symbol.

    Poll indexed 15m watermarks every 2s. Full OHLC reads only on a new close
    or a 10s retry while a required feed is missing. Quotes retry every 5s.
    """
    def __init__(self):
        self.items=[];self.refresh_at=0;self.watermarks={};self.retry_at={}
        self.latest={};self.pending={};self.send_at=0;self.log_at=0

    def run(self,c,kdb,sid,publisher=None):
        started=time.monotonic();now_ms=int(time.time()*1000)
        if started>=self.refresh_at:
            self.items=universe(now_ms);self.refresh_at=started+CONFIG['scan_seconds']
            active={r[0] for r in self.items}
            for mapping in (self.watermarks,self.retry_at,self.latest,self.pending):
                for symbol in list(mapping):
                    if symbol not in active:del mapping[symbol]
        btc=None;checked=0;paired=Counter();gates=Counter();delays=[]
        for symbol,qv,ticker_price,change in self.items:
            latest=kdb.execute("SELECT close_ms FROM futures_closed_klines WHERE symbol=? AND interval='15m' AND close_ms<? ORDER BY open_ms DESC LIMIT 1",(symbol,now_ms-250)).fetchone()
            mark=latest[0] if latest else None
            if symbol in self.watermarks and self.watermarks[symbol]==mark and started<self.retry_at.get(symbol,float('inf')):
                continue
            self.watermarks[symbol]=mark;checked+=1
            stamp=int(time.time()*1000);bucket=stamp//900000
            c.execute('INSERT OR IGNORE INTO market_snapshots VALUES(?,?,?)',(bucket,symbol,json.dumps(dict(observed_ms=stamp,quote_volume=qv,price=ticker_price,change_24h_pct=change))))
            try:
                if btc is None:btc=frames(kdb,'BTCUSDT')
                chart=frames(kdb,symbol)
                price=quote(kdb,symbol);stamp=int(time.time()*1000)
                p=assess(symbol,chart,price,qv,stamp,btc)
                old=baseline.assess(symbol,chart,price,qv,stamp,btc)
                old['strategy_id']=BASELINE_ID
                attach_context(c,kdb,old,BASELINE_ID,bucket)
                save_assessment(c,old,BASELINE_ID)
                paired['baseline_'+old['stage']]+=1
                if record_research(c,old,BASELINE_ID):paired['baseline_new_reference']+=1
                if mark is not None:delays.append(stamp-mark)
            except (ValueError,sqlite3.Error) as exc:
                p=dict(version=VERSION,symbol=symbol,observed_ms=stamp,stage='REJECTED',reason=str(exc)[:80],direction='NONE')
            p['strategy_id']=sid
            self.latest[symbol]=p
            missing=any(x in p['reason'] for x in ('MISSING','STALE','INVALID','TIME_ALIGNED','UNVERIFIED','EMPTY','no such'))
            if missing:self.retry_at[symbol]=started+CONFIG['data_retry_seconds']
            else:self.retry_at.pop(symbol,None)
            attach_context(c,kdb,p,sid,bucket)
            attach_first_eligibility(c,p)
            if record_research(c,p,sid):paired['current_new_reference']+=1
            save_assessment(c,p,sid)
            if p['stage']=='READY':self.pending[symbol]=p
            else:self.pending.pop(symbol,None)
        c.commit();sent=0
        if started>=self.send_at:
            self.send_at=started+CONFIG['quote_retry_seconds']
            if publisher is None:
                from long_short_message_card import publish
                publisher=publish
            token=os.getenv('TELEGRAM_BOT_TOKEN','');chat=os.getenv('TELEGRAM_CHAT_ID','')
            attempts=0
            for p in rank(list(self.pending.values())):
                stamp=int(time.time()*1000)
                if stamp>=p['entry_valid_until_ms']:
                    self.pending.pop(p['symbol'],None);continue
                if attempts>=CONFIG['max_new_per_scan']:break
                result=publish_candidate(c,kdb,p,sid,publisher,token,chat,stamp)
                gates[result]+=1;sent+=result=='DELIVERED'
                attempts+=result in ('DELIVERED','DELIVERY_UNCERTAIN')
                # Rejected/cooling candidates must not starve the next ranked one.
                # A global budget block applies to every remaining candidate.
                if result=='NOTIFICATION_RATE_LIMIT':break
                if result not in ('FRESH_PRICE_MISSING','BREAK_NOT_HELD','LATE_CHASE','TELEGRAM_NOT_CONFIGURED'):
                    self.pending.pop(p['symbol'],None)
        if checked or gates or started>=self.log_at:
            self.log_at=started+60
            counts=Counter(p['reason'] for p in self.latest.values())
            print('MAJOR_MOVE_SCAN '+json.dumps(dict(version=VERSION,strategy_id=sid,universe=len(self.items),checked=checked,
                reasons=dict(counts),pending=len(self.pending),paired=dict(paired),gates=dict(gates),delivered=sent,
                elapsed_ms=round((time.monotonic()-started)*1000),
                max_evaluated_candle_age_ms=max(delays,default=None),
                eligible=[dict(symbol=p['symbol'],direction=p['direction'],stage=p['stage'],model=p['model'],reason=p['reason']) for p in rank([p for p in self.latest.values() if p['stage'] in ('WATCH','READY')])[:12]])),flush=True)


def scan(c,kdb,sid,publisher=None):
    return CandleScanner().run(c,kdb,sid,publisher)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:STOP.set())
    sid=strategy_id();c=connect()
    c.execute('INSERT OR IGNORE INTO manifests VALUES(?,?)',(sid,json.dumps(dict(version=VERSION,config_hash=CONFIG_HASH,config=CONFIG,validation='UNPROVEN_RESEARCH_VERSION',automatic_orders=False))));c.commit()
    c.execute('INSERT OR IGNORE INTO manifests VALUES(?,?)',('delivery:'+sid,json.dumps(dict(
        installed_ms=int(time.time()*1000),version=VERSION,source_strategy_id=sid,
        intervention='PAPER_CAP_REMOVED_NOTIFICATION_BUDGET_FIRST_ELIGIBILITY',
        selection_rules='UNCHANGED_FROM_CONTEXT_ENTRY_V2',active_plan_limit=False,
        notification_window_ms=CONFIG['notification_window_ms'],max_notifications=CONFIG['max_notifications_per_window'],
        origin_window_hours=CONFIG['cooldown_hours'],origin_effect='DESCRIPTIVE_NOT_A_NEW_VETO',
        outcome_version=OUTCOME_VERSION,evaluation_hours=EVALUATION_HOURS))));c.commit()
    c.execute('INSERT OR IGNORE INTO manifests VALUES(?,?)',('early:'+early_observer_id(),json.dumps(dict(
        version=EARLY_VERSION,observer_id=early_observer_id(),rules=EARLY_RULES,
        installed_ms=int(time.time()*1000),validation='UNPROVEN_OBSERVATION_ONLY',automatic_orders=False,
        telegram_enabled=False))));c.commit()
    c.execute('INSERT OR IGNORE INTO manifests VALUES(?,?)',('context:'+observer_id(),json.dumps(dict(
        version=CONTEXT_VERSION,rules=CONTEXT_RULES,installed_ms=int(time.time()*1000),
        outcome_version=OUTCOME_VERSION,evaluation_hours=EVALUATION_HOURS,
        selection_config_hash=CONFIG_HASH,intervention='OBSERVATION_ONLY_NO_SIGNAL_RULE_CHANGE'))));c.commit()
    c.execute('INSERT OR IGNORE INTO manifests VALUES(?,?)',('paired:'+sid,json.dumps(dict(
        baseline_id=BASELINE_ID,baseline_version=baseline.VERSION,v2_id=sid,installed_ms=int(time.time()*1000),
        reference_kind='FORWARD_PAPER_QUOTE_NOT_DELIVERED',same_data=True,per_strategy_symbol_cooldown_hours=24,
        portfolio_limits=False,evaluation_hours=EVALUATION_HOURS,primary_horizon_hours=24,
        frozen_baseline_source_sha256=hashlib.sha256(open(baseline.__file__,'rb').read()).hexdigest()))));c.commit()
    recover_interrupted(c);retire_old_episodes();c.close()
    print('MAJOR_MOVE_START '+json.dumps(dict(version=VERSION,strategy_id=sid,config_hash=CONFIG_HASH,automatic_orders=False,primary_horizon_hours=24)),flush=True)
    print('MAJOR_CONTEXT_START '+json.dumps(dict(version=CONTEXT_VERSION,observer_id=observer_id(),
        mode='OBSERVATION_ONLY',evaluation_hours=EVALUATION_HOURS,selection_config_hash=CONFIG_HASH)),flush=True)
    print('MAJOR_DELIVERY_START '+json.dumps(dict(version=VERSION,active_plan_limit=False,
        notification_window_ms=CONFIG['notification_window_ms'],max_notifications=CONFIG['max_notifications_per_window'],
        first_eligibility_persistent=True,outcome_version=OUTCOME_VERSION)),flush=True)
    print('EARLY_OBSERVER_START '+json.dumps(dict(version=EARLY_VERSION,observer_id=early_observer_id(),
        rules=EARLY_RULES,telegram_enabled=False)),flush=True)
    scanner=CandleScanner();early=EarlyScanner();next_maintenance=0;next_health=0;next_comparison=0
    while not STOP.is_set():
        c=kdb=None
        try:
            c=connect();kdb=db(root()/'binance_futures_kline_shadow.db',True);now=int(time.time()*1000)
            scanner.run(c,kdb,sid)
            early.run(c,kdb,scanner.items)
            if time.monotonic()>=next_maintenance:
                next_maintenance=time.monotonic()+CONFIG['maintenance_seconds']
                measure(c,kdb,now,emit=lambda x:print(x,flush=True))
                measure(c,kdb,now,emit=lambda x:print(x,flush=True),paper=True)
                notify_exits(c)
            if time.monotonic()>=next_health:
                next_health=time.monotonic()+300
                print('MAJOR_MOVE_HEALTH '+json.dumps(health()),flush=True)
            if time.monotonic()>=next_comparison:
                from long_short_major_context_report import emit_report
                emit_report(c,now)
                emit_report(c,now,paper=True)
                next_comparison=time.monotonic()+3600
        except (sqlite3.Error,ValueError,OSError) as exc:
            print('MAJOR_MOVE_ERROR '+json.dumps(dict(error=type(exc).__name__,detail=str(exc)[:160])),flush=True)
            if args.once:raise
        finally:
            if c is not None:c.close()
            if kdb is not None:kdb.close()
        if args.once:return
        STOP.wait(CONFIG['poll_seconds'])


if __name__=='__main__':main()

