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


def render(sections):
    # The original core message is preserved verbatim; it alone may claim a
    # trade signal. The early observer never appends invented SL/TP values.
    core = sections.get('CORE')
    early = sections.get('EARLY')
    parts = []
    opposite=bool(core and early and core['direction']!=early['direction'])
    if opposite:
        match=re.search(r'\|\s*([A-Z0-9]+USDT)\b',core['text'].splitlines()[0])
        symbol=match.group(1) if match else ''
        parts.append(f'⚪ YÖNLER FARKLI | {symbol}\nAna motor: {core["direction"]} | Erken gözlem: {early["direction"]}\nKarşı yöndeki gözlem yeni işlem teyidi değildir.')
    if core:
        parts.append(core['text'] + '\nAna motor mesaj zamanı: ' + core['observed_at'])
    if early:
        label = ('KARŞI YÖNDE KIRILIM GÖZLEMİ — GİRİŞ TEYİDİ DEĞİL' if opposite else
                 'KIRILIM TAKİBİ — GİRİŞ TEYİDİ DEĞİL' if core else '')
        parts.append((label + '\n' if label else '') + early['text'])
    text = '\n\n'.join(parts)
    if len(text) > 4096:
        # Keep the core decision intact, trim only the observational section.
        text = text[:4093] + '…'
    return text


def publish(token, chat, symbol, source, text, direction, *, observed_at=None, post=None):
    if source not in ('CORE','EARLY') or direction not in ('LONG','SHORT'):
        raise ValueError('invalid message card source/direction')
    if not token or not chat or not re.fullmatch(r'[A-Z0-9]+USDT', symbol):
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
            new_watch=('İÇİN İZLE' in new_header)
            age=now-datetime.fromisoformat(previous['observed_at'].replace('Z','+00:00')).timestamp()
            if old_trade and new_watch and age<600:
                c.rollback()
                return {'status':'UNCHANGED','message_id':row['message_id']}
        sections[source] = {'text':text,'direction':direction,'observed_at':stamp}
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
                      (symbol,method,'ACK',stamp,message_id,None))
            c.commit()
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
    match = re.search(r'\|\s*([A-Z0-9]+USDT)\b',header)
    if not match or not re.search(r'\b(LONG|SHORT)\b',header):
        return False  # startup/health/smoke messages keep their existing path
    direction = 'LONG' if re.search(r'\bLONG\b',header) else 'SHORT'
    result = publish(token,chat,match.group(1),'CORE',text,direction)
    if result['status'] not in ('ACK','UNCHANGED'):
        raise RuntimeError('MESSAGE_CARD_NOT_DELIVERED')
    return True
