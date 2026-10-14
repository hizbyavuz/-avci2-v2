"""One persistent Telegram message per coin shared by early and core observers.

Only notification transport changes. The core's decisions remain its own.
An uncertain initial send is held for review rather than retried as a duplicate.
"""
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

import requests


def database_path():
    root = os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state'
    return os.getenv('LS_MESSAGE_CARD_DB') or os.path.join(root, 'long_short_message_cards.db')


def connect():
    path = database_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    c = sqlite3.connect(path, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('''CREATE TABLE IF NOT EXISTS cards(
        destination TEXT, symbol TEXT, message_id INTEGER, sections_json TEXT NOT NULL,
        delivered_text TEXT, status TEXT NOT NULL, retry_at REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL, PRIMARY KEY(destination,symbol))''')
    c.execute('''CREATE TABLE IF NOT EXISTS deliveries(
        id INTEGER PRIMARY KEY, symbol TEXT, method TEXT, status TEXT,
        time_utc TEXT, message_id INTEGER, detail TEXT)''')
    c.commit()
    return c


def simple_early(text):
    """Short display only; the stored source and trading decisions stay intact."""
    lines=text.splitlines()
    if not lines or ' | ' not in lines[0]:
        return text
    header=lines[0]
    direction='LONG' if 'LONG' in header else 'SHORT'
    level=re.search(r'İzlenen seviye: ([^\s|]+)',text)
    price=re.search(r'Şu an fiyat: ([^\s|]+)',text)
    if not level or not price:
        return text
    side='üstünde' if direction=='LONG' else 'altında'
    if 'FİKRİ BOZULDU' in header:
        header=header.replace('FİKRİ BOZULDU','TAKİBİ İPTAL')
        action=f'Bu fikirle yeni {direction} işlemi açma.'
        reason=f'Fiyat {level[1]} seviyesinin {side} kalamadı.'
        expected='Yeni bir fırsat oluşması bekleniyor.'
    elif 'GEÇ / KOVALAMA' in header:
        action=f'Yeni {direction} işlemi için bekle.'
        reason='Hareket uzadı veya giriş için yeterli alan kalmadı.'
        expected='Daha uygun bir giriş yeri bekleniyor.'
    elif 'KAPANIŞ TEYİDİ' in header:
        header=header.replace('KAPANIŞ TEYİDİ','İLK ŞART TAMAM')
        action='Henüz giriş onayı yok; bekle.'
        reason=f'5 dakikalık mum {level[1]} {side} kapandı.'
        expected=f'Fiyatın seviyeyi koruması ve ana analizin onayı.'
    elif 'İÇİN İZLE' in header:
        action='Henüz giriş onayı yok; bekle.'
        reason='Kısa süreli bir hareket başladı.'
        expected=f'5 dakikalık mumun {level[1]} {side} kapanması, ardından seviyeyi koruması ve ana analizin onayı.'
    else:
        action='Güncel giriş onayı yok.'
        reason='Bu takip sona erdi.'
        expected='Yeni bir fırsat oluşması bekleniyor.'
    result=[header,f'• Durum: {action}',f'• Şu an fiyat: {price[1]}',
            f'• Neden: {reason}',f'• Beklenen: {expected}']
    context=next((x for x in lines if x.startswith('Büyük resim:')),None)
    if context:
        context=context.split(' | ')[0].replace('üst zaman verisi eksik; yön teyit edilemiyor',
            '1 ve 4 saatlik veri eksik; genel yön bilinmiyor').replace('eğim yönü','yön').replace('eğime ters','yöne ters')
        result.append('• '+context)
    labels={'Kısa yön':'Son hareket','Hacim':'İşlem hacmi',
        'Alış payı':'Alımların payı','Yön 15 dk':'15 dakikalık yön',
        'Hareket gücü':'Hareket gücü (RSI)',
        'Açık işlemler 1 sa':'Açık pozisyon değişimi (son 1 saat)',
        'Taşıma oranı':'Pozisyon taşıma ücreti oranı',
        'Alış/satış':'Alış / satış işlem oranı',
        'Long/short hesap':'LONG / SHORT hesap oranı',
        'BTC':'Bitcoin yönü','Vadeli veri':'Vadeli veri kapsamı',
        'Ayrıntılı veri':'Ayrıntılı verinin durumu','Analiz':'Ayrıntılı analiz saati'}
    for label,display in labels.items():
        match=re.search(re.escape(label)+r': ([^|\n]+)',text)
        if match:
            value=match[1].strip()
            if label=='Hacim':value='normalin '+value.replace('×',' katı')
            result.append('• '+display+': '+value)
    first=next((x for x in lines if x.startswith('İlk gözlem:')),None)
    if first:result.append('• '+first.replace(' | ',' — saat '))
    # Preserve range position and volume context rather than discarding it.
    original_context=next((x for x in lines if x.startswith('Büyük resim:')),None)
    if original_context:
        result.extend('• '+field for field in original_context.split(' | ')[1:])
    update=next((x for x in lines if x.startswith('Güncelleme:')),None)
    if update:result.append('• '+update+' (Türkiye saati)')
    return '\n'.join(result)


def render(sections):
    """One header, one evidence line and one risk line; decisions stay explicit."""
    from long_short_display import local_time
    core,early=sections.get('CORE'),sections.get('EARLY')
    if (core and core['text'].startswith(('🟢 LONG SİNYALİ |', '🔴 SHORT SİNYALİ |'))
            and '📍 Giriş referansı:' in core['text']):
        # A complete trade plan is already ordered for the reader. Preserve
        # SL/TP labels and keep levels above evidence, in the same single card.
        text=core['text']+'\n🕒 Sinyal saati: '+local_time(core['observed_at'])+' (Türkiye)'
        return text if len(text)<=4096 else text[:4093]+'…'
    parts=[]; evidence={}; risks=[]
    def collect(text):
        body=[]
        for line in text.splitlines():
            line=line.strip()
            if not line:continue
            if line.startswith(('📊','•','Tip:','Veri:')):
                clean=line.removeprefix('📊').removeprefix('•').strip()
                if clean=='KISA VERİ ÖZETİ':continue
                clean=clean.replace('Veri:','Kaynak:',1)
                for field in clean.split(' | '):
                    key=field.split(':',1)[0].strip()
                    evidence[key]=field.strip()
            elif line.startswith(('🛡️','🎯')):
                risks.append(line.replace('SL:','Stop:').replace('TP1:','Hedef 1:').replace('TP2:','Hedef 2:'))
            else:
                body.append(line)
        return body
    core_body=collect(core['text']) if core else []
    early_body=collect(early['text']) if early else []
    opposite=bool(core and early and core['direction']!=early['direction'])
    if opposite:
        match=re.search(r'\|\s*([^\W_]+USDT)\b',core['text'].splitlines()[0])
        symbol=match.group(1) if match else ''
        parts.append(f'⚪ YÖNLER FARKLI | {symbol}\nAna motor: {core["direction"]} | Erken gözlem: {early["direction"]}\nKarşı yöndeki gözlem yeni işlem teyidi değildir.')
    if core:
        parts.extend(core_body)
        if early:
            if early_body:
                header=early_body[0]
                state=header.split('—',1)[-1].strip() if '—' in header else header
                parts.append(('Karşı yöndeki erken takip: ' if opposite else 'Erken takip: ')+state)
            # Early prices/references are distinct from the core's locked plan.
            parts.extend(line for line in early_body[1:]
                         if line.startswith(('İlk erken uyarı:','Son ölçülen fiyat:')))
    elif early:
        parts.extend(early_body)
    if early and not core:
        return simple_early(early['text'])
    for field in evidence.values():
        parts.append('• '+field)
    if risks:parts.extend(dict.fromkeys(risks))
    if core and 'Analiz' not in evidence:
        parts.append('Ana motor mesajı: '+local_time(core['observed_at']))
    # Keep every core instruction and risk level, each on its own bullet.
    text='\n'.join(line if i==0 or line.startswith('•') else '• '+line
                   for i,line in enumerate('\n'.join(parts).splitlines()) if line)
    return text if len(text)<=4096 else text[:4093]+'…'


def publish(token, chat, symbol, source, text, direction, *, observed_at=None, post=None, episode_key=None):
    from long_short_qualified_chain import enabled as qualified_chain_enabled
    if source == "EARLY" and qualified_chain_enabled():
        return {"status":"CANDIDATE_ONLY_V1","message_id":None}
    if source not in ('CORE','EARLY') or direction not in ('LONG','SHORT'):
        raise ValueError('invalid message card source/direction')
    if not token or not chat or not re.fullmatch(r'[^\W_]+USDT', symbol):
        raise ValueError('verified Telegram destination and symbol required')
    expected = os.getenv('LS_TELEGRAM_EXPECTED_CHAT_ID','').strip()
    if expected and str(chat) != expected:
        raise RuntimeError('TELEGRAM_CHAT_MISMATCH_PRE_SEND')
    destination = hashlib.sha256((token.split(':',1)[0] + ':' + str(chat)).encode()).hexdigest()
    now = time.time()
    stamp = observed_at or datetime.now(timezone.utc).isoformat()
    post = post or requests.post
    c = connect()
    try:
        # Serialize the two processes through ACK persistence: no second
        # sender can race an in-flight initial send. Bounded network timeout.
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM cards WHERE destination=? AND symbol=?',
                        (destination,symbol)).fetchone()
        sections = json.loads(row['sections_json']) if row else {}
        previous = sections.get(source)
        if previous and stamp < previous['observed_at']:
            c.rollback()
            return {'status':'STALE_SUPPRESSED','message_id':row['message_id']}
        if source == 'CORE' and previous and previous['direction'] == direction:
            old_header=previous['text'].splitlines()[0]
            new_header=text.splitlines()[0]
            old_trade=('SİNYALİ' in old_header or 'İŞLEM' in old_header)
            new_watch=('İÇİN İZLE' in new_header or 'RADAR' in new_header)
            age=now-datetime.fromisoformat(previous['observed_at'].replace('Z','+00:00')).timestamp()
            if old_trade and new_watch and age<600:
                c.rollback()
                return {'status':'UNCHANGED','message_id':row['message_id']}
        # A new episode needs a fresh Telegram notification; updates of that
        # episode still edit one card. Never reset an uncertain in-flight send.
        new_episode = bool(source == 'EARLY' and episode_key and previous
            and previous.get('episode_key') and previous['episode_key'] != episode_key
            and row and row['status'] == 'DELIVERED')
        if new_episode:
            c.execute('''UPDATE cards SET message_id=NULL, delivered_text=NULL,
                status='PENDING', retry_at=0 WHERE destination=? AND symbol=?''', (destination,symbol))
            row = dict(row)
            row.update(message_id=None, delivered_text=None, status='PENDING', retry_at=0)
            sections = {}  # an old core idea must not leak into a new episode
        sections[source] = {'text':text,'direction':direction,'observed_at':stamp,
                            'episode_key':episode_key}
        from long_short_qualified_chain import enabled as qualified_chain_enabled
        if source == "CORE" and qualified_chain_enabled():
            sections.pop("EARLY", None)
        if source == 'EARLY' and 'CORE' in sections:
            core_time = datetime.fromisoformat(sections['CORE']['observed_at'].replace('Z','+00:00'))
            if now-core_time.timestamp()>600:
                sections.pop('CORE')  # no stale entry idea on a fresh early update
        # When the core starts a new opposite-direction setup, an older early
        # idea cannot be mistaken for support for that new trade.
        if source == 'CORE' and 'EARLY' in sections and sections['EARLY']['direction'] != direction:
            sections.pop('EARLY')
        rendered = render(sections)
        encoded = json.dumps(sections,ensure_ascii=False)
        message_id = row['message_id'] if row else None
        state = row['status'] if row else 'PENDING'
        c.execute('''INSERT INTO cards VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(destination,symbol) DO UPDATE SET sections_json=excluded.sections_json,
            updated_at=excluded.updated_at''',
            (destination,symbol,message_id,encoded,None,state,0,now))
        if row and row['delivered_text'] == rendered:
            c.commit()
            return {'status':'UNCHANGED','message_id':message_id}
        if state == 'UNCERTAIN' and message_id is None:
            c.commit()
            raise RuntimeError('MESSAGE_CARD_INITIAL_SEND_UNCERTAIN_NO_DUPLICATE_RETRY')
        if row and now < row['retry_at']:
            c.commit()
            raise RuntimeError('MESSAGE_CARD_RATE_LIMIT_BACKOFF')
        method = 'editMessageText' if message_id else 'sendMessage'
        data = {'chat_id':chat,'text':rendered,'disable_web_page_preview':True}
        if message_id:
            data['message_id'] = message_id
        # Persist uncertainty BEFORE initial network delivery. A crash after
        # Telegram accepted a send cannot generate a second new message.
        if not message_id:
            c.execute("UPDATE cards SET status='UNCERTAIN' WHERE destination=? AND symbol=?",(destination,symbol))
            c.commit()
            c.execute('BEGIN IMMEDIATE')
            # A concurrent publisher will see UNCERTAIN and decline to send.
        try:
            response = post(f'https://api.telegram.org/bot{token}/{method}',json=data,timeout=10)
            body = response.json()
            if not body.get('ok'):
                description = str(body.get('description',''))
                if message_id and 'message is not modified' in description.lower():
                    receipt = {'message_id':message_id,'chat':{'id':chat}}
                else:
                    retry = int((body.get('parameters') or {}).get('retry_after') or 60)
                    c.execute('UPDATE cards SET status=?,retry_at=? WHERE destination=? AND symbol=?',
                              ('RETRY' if message_id or body.get('error_code') else 'UNCERTAIN',now+retry,destination,symbol))
                    c.commit()
                    raise RuntimeError('MESSAGE_CARD_API_REJECTED')
            else:
                receipt = body.get('result') or {}
            if (not isinstance(receipt,dict) or not receipt.get('message_id') or
                    str((receipt.get('chat') or {}).get('id')) != str(chat)):
                raise RuntimeError('MESSAGE_CARD_ACK_INVALID')
            message_id = int(receipt['message_id'])
            c.execute('''UPDATE cards SET message_id=?,delivered_text=?,status='DELIVERED',retry_at=0,
                         updated_at=? WHERE destination=? AND symbol=?''',
                         (message_id,rendered,now,destination,symbol))
            c.execute('INSERT INTO deliveries(symbol,method,status,time_utc,message_id,detail) VALUES(?,?,?,?,?,?)',
                      (symbol,method,'ACK',stamp,message_id,json.dumps({
                          'source':source,'direction':direction,'text':rendered,'episode_key':episode_key,
                          'source_text':text,
                          'accepted_at_utc':datetime.now(timezone.utc).isoformat()
                      },ensure_ascii=False)))
            c.commit()
            print('TELEGRAM_CARD_ACK '+json.dumps({'symbol':symbol,'source':source,'method':method,'new_episode':new_episode}),flush=True)
            return {'status':'ACK','message_id':message_id,'method':method}
        except Exception as exc:
            if c.in_transaction:
                c.execute('INSERT INTO deliveries(symbol,method,status,time_utc,message_id,detail) VALUES(?,?,?,?,?,?)',
                          (symbol,method,'FAILED',stamp,message_id,type(exc).__name__))
                c.commit()
            if isinstance(exc,requests.RequestException):
                # Exception text may contain the token-bearing HTTP URL.
                raise RuntimeError('MESSAGE_CARD_TRANSPORT_FAILURE') from None
            raise
    finally:
        c.close()


def route_core_message(token, chat, text):
    header = text.splitlines()[0] if text else ''
    match = re.search(r'\|\s*([^\W_]+USDT)\b',header)
    if not match or not re.search(r'\b(LONG|SHORT)\b',text):
        return False  # startup/health/smoke messages keep their existing path
    direction = 'LONG' if re.search(r'\bLONG\b',header or '') else 'SHORT'
    if not re.search(r'\b(LONG|SHORT)\b',header):
        side=re.search(r'Yön eğilimi:.*?\b(LONG|SHORT)\b',text)
        if not side:return False
        direction=side.group(1)
    result = publish(token,chat,match.group(1),'CORE',text,direction)
    if result['status'] not in ('ACK','UNCHANGED'):
        raise RuntimeError('MESSAGE_CARD_NOT_DELIVERED')
    return True

