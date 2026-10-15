"""Explicit observation notices, separate from execution authorization.
One initial notification per coin episode; ends update that same card. Native
prices, known direction and verified 3/4 or 4/4 evidence are required. No orders.
"""
import hashlib
import json
import math
import os
import sqlite3
import time
from datetime import datetime,timezone

VERSION='OPPORTUNITY_NOTICES_V2_ROOM_AND_CHASE_2026_10_15'
FIELD_NAMES={'oi_change_1h':'açık pozisyon değişimi','funding_pct':'funding',
    'taker_ratio':'agresif alış/satış','long_short_ratio':'long/short hesap oranı'}

def assess(payload,price,observed_ms,now_ms):
    from long_short_data_opportunity_v2 import partial_fields
    p=payload;d=p.get('direction_engine') or {};e=p.get('evidence') or {};plan=p.get('setup_plan') or {}
    direction=d.get('direction');missing=partial_fields(e) if not e.get('ready') else []
    reasons=[]
    if not 0<=now_ms-observed_ms<=300000:reasons.append('STALE_ANALYSIS')
    if direction not in ('LONG','SHORT'):reasons.append('NO_DIRECTION')
    reasons.extend(x for x in d.get('hard_blockers',[]) if x!='derivatives_incomplete')
    if not e.get('market_verified') or not (e.get('ready') or missing and len(missing)==1):reasons.append('DATA_NOT_VERIFIED_3_OF_4')
    if e.get('source')!='BINANCE_UM_FUTURES_REST':reasons.append('UNVERIFIED_SOURCE')
    if not 0<=now_ms-int(e.get('observed_ms') or 0)<=180000:reasons.append('STALE_DERIVATIVES')
    if float(d.get('best_score') or 0)<42 or float(d.get('edge') or 0)<12:reasons.append('DIRECTION_THRESHOLD')
    gate=p.get('structure_gate') or {};geometry=gate.get('structural_plan_v3') or {}
    if not gate.get('qualified_precheck') or geometry.get('reason')!='OK':
        reasons.append('STRUCTURE_'+str(geometry.get('reason') or 'UNAVAILABLE'))
    context=p.get('opportunity_context') or {};chart=context.get('chart') or {};t15=context.get('t15') or {}
    extension='overextended_up' if direction=='LONG' else 'overextended_down'
    if extension not in chart or t15.get('rsi') is None:
        reasons.append('CHASE_CONTEXT_MISSING')
    else:
        try:
            rsi=float(t15['rsi'])
            if not math.isfinite(rsi):raise ValueError()
            if chart[extension] or (rsi>=76 if direction=='LONG' else rsi<=24):
                reasons.append('EXTENDED_MOVE_DO_NOT_CHASE')
        except (TypeError,ValueError):reasons.append('CHASE_CONTEXT_MISSING')
    local=plan.get('local_invalidation') or {}
    try:
        stop=float(local['level']);trigger=float(plan['trigger_level']);price=float(price)
        if not all(math.isfinite(x) and x>0 for x in (stop,trigger,price)):raise ValueError()
        if local.get('timeframe')!='5m' or local.get('confirmation_bars')!=2:raise ValueError()
        if not (stop<price if direction=='LONG' else price<stop):raise ValueError()
    except (KeyError,TypeError,ValueError):reasons.append('NO_VALID_LOCAL_INVALIDATION');stop=trigger=None
    # Check the remaining path at the fresh quote too: a valid scan-time plan
    # must not produce a new alert after price has run into its target.
    try:
        target=float(geometry['target']);floor=float(geometry['required_room_pct'])
        if not all(math.isfinite(x) and x>0 for x in (price,target,floor)):raise ValueError()
        room=(target-price)/price*100*(1 if direction=='LONG' else -1)
        if room<floor:reasons.append('LIVE_ROOM_TOO_SMALL')
    except (KeyError,TypeError,ValueError):reasons.append('NO_VERIFIED_TARGET_ROOM')
    return dict(allowed=not reasons,reasons=list(dict.fromkeys(reasons)),direction=direction,
        missing=missing or [],stop=stop,trigger=trigger,price=price)

def message(symbol,p,decision,observed_ms):
    from long_short_display import local_time
    d=p['direction_engine'];e=p['evidence'];g=p.get('structure_gate') or {}
    side=decision['direction'];up=side=='LONG';price=decision['price']
    structure=(g.get('structural_plan_v3') or {}).get('reason')
    expected={
        'OK':'5 dk kapanış, seviyenin korunması ve son giriş kontrolleri.',
        'INSUFFICIENT_GEOMETRY':'Masraf sonrası yeterli hedef mesafesi olan bir giriş planı.',
        'COMMON_REGION_TOO_WIDE':'Uzak seviyeyi kovalamadan daha yakın bir giriş yapısı.',
        'ENTRY_INSIDE_OPPOSING_ZONE':'Öndeki destek/direnç bölgesinin aşılması ve yeni giriş planı.',
        'NO_OBSERVED_TARGET':'Ayrı ve gözlenmiş bir hedef seviyesi.',
        'NO_LOCAL_TRIGGER_ZONE':'Belirgin bir kısa vadeli giriş seviyesi.',
    }.get(structure,'Geçerli giriş planı ve 5 dk kapanış teyidi.')
    facts=[]
    if e.get('oi_fresh') and e.get('oi_change_1h') is not None:facts.append(f"• Açık pozisyon değişimi (1 sa): %{e['oi_change_1h']:+.2f}")
    if e.get('taker_fresh') and e.get('taker_ratio') is not None:facts.append(f"• Alış / satış oranı: {e['taker_ratio']:.2f}×")
    facts.append(f"• Yön puanı: {float(d['best_score']):.0f}/100")
    missing=', '.join(FIELD_NAMES[x] for x in decision['missing'])
    return '\n'.join([
        f'{"🟢" if up else "🔴"} {side} İÇİN İZLE | {symbol}',
        '',
        '⏳ Giriş bekleniyor — işlem sinyali değil.',
        '',
        f'• Gözlem fiyatı: {price:.10g}',
        f"• İzlenen seviye: {decision['trigger']:.10g}",
        '• Mum süresi: 5 dakika',
        f"• Fikir {decision['stop']:.10g} {('altında' if up else 'üstünde')} bozulur.",
        '',
        'NE BEKLENİYOR?',
        f"• 5 dk mum {decision['trigger']:.10g} {'üstünde' if up else 'altında'} kapanmalı.",
        '• '+expected,
        '• Giriş onaylanırsa ayrı bildirim gelecek.',
        '',
        'VERİ ÖZETİ',
        *facts,
        ('• Eksik veri: '+missing if missing else '• Veri durumu: 4/4 mevcut'),
        '',
        '🕒 Gözlem: '+local_time(datetime.fromtimestamp(observed_ms/1000,timezone.utc).isoformat())+' (Türkiye)',
    ])

def publish_recent(now_ms=None,publisher=None,token=None,chat=None):
    from long_short_chart_first import enabled as chart_first_enabled
    if chart_first_enabled():return
    from long_short_data_opportunity_v2 import connect,current_price
    from long_short_message_card import publish
    if os.getenv('LS_OPPORTUNITY_NOTICES_V1','1')=='0':return
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    token=token if token is not None else os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    if not token:return
    if chat is None:
        from long_short_analyst import resolve_telegram_chat
        chat=resolve_telegram_chat(token,os.getenv('TELEGRAM_CHAT_ID','').strip())
    publisher=publisher or publish
    c=connect();c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS opportunity_notices_v1(symbol TEXT PRIMARY KEY,
        direction TEXT,episode_key TEXT,started_ms INTEGER,last_eligible_ms INTEGER,
        stop REAL,body TEXT,active INTEGER,delivered_ms INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS opportunity_notice_decisions(
        observation_id INTEGER,version TEXT,checked_ms INTEGER,symbol TEXT,direction TEXT,
        allowed INTEGER,reasons TEXT,price REAL,PRIMARY KEY(observation_id,version))''')
    c.commit();sent=0;ended=0
    stamp=datetime.fromtimestamp(now_ms/1000,timezone.utc).isoformat()
    def deliver(symbol,direction,body,key):
        try:
            return publisher(token,chat,symbol,'OPPORTUNITY',body,direction,observed_at=stamp,episode_key=key)
        except Exception as exc:
            print('OPPORTUNITY_DELIVERY_DEFERRED '+json.dumps({'symbol':symbol,'error_type':type(exc).__name__}),flush=True)
            return {'status':'FAILED'}
    try:
        latest={r['symbol']:r for r in c.execute('''SELECT * FROM observations
            WHERE observed_ms BETWEEN ? AND ? AND cohort IN ('FULL_4_OF_4','PARTIAL_3_OF_4')
            ORDER BY observed_ms,id''',(now_ms-300000,now_ms))}
        candidates=[];decisions={};blocked_counts={}
        for symbol,row in latest.items():
            p=json.loads(row['payload']);price=current_price(symbol,now_ms)
            decision=assess(p,price,row['observed_ms'],now_ms)
            decisions[symbol]=decision
            c.execute('INSERT OR IGNORE INTO opportunity_notice_decisions VALUES(?,?,?,?,?,?,?,?)',
                (row['id'],VERSION,now_ms,symbol,decision['direction'],int(decision['allowed']),json.dumps(decision['reasons']),price))
            for reason in decision['reasons']:blocked_counts[reason]=blocked_counts.get(reason,0)+1
            if decision['allowed']:candidates.append((symbol,row,p,decision))
        c.commit()
        allowed={symbol:decision for symbol,_,_,decision in candidates}
        for old in c.execute('SELECT * FROM opportunity_notices_v1 WHERE active=1').fetchall():
            price=current_price(old['symbol'],now_ms)
            broken=price is not None and (price<=old['stop'] if old['direction']=='LONG' else price>=old['stop'])
            opposite=old['symbol'] in allowed and allowed[old['symbol']]['direction']!=old['direction']
            recent=latest.get(old['symbol'])
            lost_direction=bool(recent and recent['observed_ms']>old['last_eligible_ms'] and
                (json.loads(recent['payload']).get('direction_engine') or {}).get('direction')!=old['direction'])
            expired=now_ms-old['last_eligible_ms']>=45*60000 or now_ms-old['started_ms']>=180*60000
            unsuitable=any(r.startswith('STRUCTURE_') or r in ('LIVE_ROOM_TOO_SMALL','NO_VERIFIED_TARGET_ROOM','EXTENDED_MOVE_DO_NOT_CHASE')
                for r in decisions.get(old['symbol'],{}).get('reasons',[]))
            if not (broken or opposite or expired or lost_direction or unsuitable):continue
            why='Bozulma seviyesi aşıldı.' if broken else 'Yön değişti.' if opposite else 'Yön desteği zayıfladı.' if lost_direction else 'Giriş yapısı veya kalan mesafe uygun değil; yeni yapı bekleniyor.' if unsuitable else 'Güncel fırsat takibi sona erdi.'
            body=f"⚪ {old['direction']} TAKİBİ SONA ERDİ | {old['symbol']}\n{why}\nBu fırsat kaydı giriş onayı değildi."
            receipt=deliver(old['symbol'],old['direction'],body,old['episode_key'])
            if receipt['status'] in ('ACK','UNCHANGED','CORE_ACTIVE_SUPPRESSED'):
                c.execute('UPDATE opportunity_notices_v1 SET active=0 WHERE symbol=?',(old['symbol'],));c.commit();ended+=1
        candidates.sort(key=lambda x:(-float(x[2]['direction_engine']['best_score']),x[0]))
        for symbol,row,p,decision in candidates:
            old=c.execute('SELECT * FROM opportunity_notices_v1 WHERE symbol=?',(symbol,)).fetchone()
            if old and old['active']==-1:
                # Reuse the committed episode after a crash, including when
                # Telegram ACK persisted but this table was not updated yet.
                receipt=deliver(symbol,old['direction'],old['body'],old['episode_key'])
                if receipt['status'] in ('ACK','UNCHANGED'):
                    c.execute('UPDATE opportunity_notices_v1 SET active=1 WHERE symbol=?',(symbol,));c.commit()
                continue
            if old and old['active']==1 and old['direction']==decision['direction']:
                c.execute('UPDATE opportunity_notices_v1 SET last_eligible_ms=? WHERE symbol=?',(row['observed_ms'],symbol));c.commit();continue
            # No same-coin repeat burst after invalidation/restart. New opposite
            # direction is a real state change, still explicitly observation-only.
            if old and old['direction']==decision['direction'] and now_ms-old['delivered_ms']<3600000:continue
            if sent>=3:break
            key=hashlib.sha256(f"{VERSION}:{symbol}:{decision['direction']}:{row['id']}".encode()).hexdigest()
            body=message(symbol,p,decision,row['observed_ms'])
            c.execute('INSERT OR REPLACE INTO opportunity_notices_v1 VALUES(?,?,?,?,?,?,?,?,?)',
                (symbol,decision['direction'],key,now_ms,row['observed_ms'],decision['stop'],body,-1,now_ms));c.commit()
            receipt=deliver(symbol,decision['direction'],body,key)
            if receipt['status'] in ('ACK','UNCHANGED'):
                c.execute('UPDATE opportunity_notices_v1 SET active=1 WHERE symbol=?',(symbol,));c.commit();sent+=1
            elif receipt['status']=='CORE_ACTIVE_SUPPRESSED':
                c.execute('UPDATE opportunity_notices_v1 SET active=0 WHERE symbol=?',(symbol,));c.commit()
        print('OPPORTUNITY_NOTICE_SUMMARY '+json.dumps(dict(version=VERSION,eligible=len(candidates),sent=sent,ended=ended,blocked_reasons=blocked_counts,
            scope='OBSERVATION_NOT_ENTRY_PERMISSION')),flush=True)
    finally:c.close()
