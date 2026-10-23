"""Isolated two-direction research runtime, using existing market collectors.

No exchange account, balance, leverage or order API. The only outgoing request
owned here is an explicitly enabled Telegram send to the existing destination.
"""
import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import threading
import time
from pathlib import Path

from long_short_patterns import (VERSION, CONFIG, CONFIG_HASH, INTERVALS, HOUR, MINUTE,
                                 assess, executable, message, finite)
from long_short_pattern_outcomes import initial, advance, result
from long_short_major_move_store import root, db, SOURCES, quote
from long_short_swing_runtime import universe

PROFILE = 'PATTERN_STRUCTURE_V1'
DATABASE = 'pattern_structure_v1.db'
STOP = threading.Event()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def connect(path=None):
    path = Path(path) if path else root()/DATABASE
    path.parent.mkdir(parents=True, exist_ok=True)
    c = db(path)
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
      CREATE TABLE IF NOT EXISTS manifest(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS scans(symbol TEXT,interval TEXT,bar_ms INTEGER,observed_ms INTEGER,
        payload TEXT NOT NULL,PRIMARY KEY(symbol,interval,bar_ms));
      CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,symbol TEXT,observed_ms INTEGER,
        decision TEXT,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,symbol TEXT,direction TEXT,created_ms INTEGER,
        ack_ms INTEGER,entry_ms INTEGER,state TEXT,payload TEXT NOT NULL,progress TEXT,result TEXT);
      CREATE INDEX IF NOT EXISTS pattern_plan_state ON plans(state,created_ms);
      CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,created_ms INTEGER,ack_ms INTEGER,
        state TEXT,receipt INTEGER,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,created_ms INTEGER,state TEXT,text TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS health(bucket INTEGER PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS scan_failures(symbol TEXT,interval TEXT,bucket INTEGER,
        observed_ms INTEGER,reason TEXT,PRIMARY KEY(symbol,interval,bucket));
    ''')
    names = ('long_short_patterns.py', 'long_short_pattern_outcomes.py',
             'long_short_pattern_runtime.py', 'long_short_pattern_daemon.py')
    digest = hashlib.sha256(b''.join(Path(__file__).with_name(n).read_bytes() for n in names)).hexdigest()
    manifest = encoded(dict(version=VERSION, config=CONFIG, config_hash=CONFIG_HASH,
                            code_hash=digest, publication='TWO_WAY_RESEARCH_ONLY'))
    previous = c.execute('SELECT payload FROM manifest WHERE id=?', (VERSION,)).fetchone()
    if previous and previous[0] != manifest:
        raise ValueError('CODE_OR_CONFIG_CHANGED_WITHOUT_NEW_VERSION')
    c.execute('INSERT OR IGNORE INTO manifest VALUES(?,?)', (VERSION, manifest))
    c.commit()
    return c


def destination(env=None, directory=None):
    env = os.environ if env is None else env
    directory = root() if directory is None else Path(directory)
    token = env.get('TELEGRAM_BOT_TOKEN', '').strip()
    explicit = env.get('TELEGRAM_CHAT_ID', '').strip()
    expected = env.get('LS_TELEGRAM_EXPECTED_CHAT_ID', '').strip()
    cached = ''
    path = directory/'long_short_telegram_chat_cache.db'
    if path.exists():
        c = db(path, True)
        try:
            row = c.execute("SELECT value FROM runtime_settings WHERE key='telegram_chat_id'").fetchone()
            cached = str(row[0]).strip() if row else ''
        finally:
            c.close()
    chat = explicit or cached
    if not token or not re.fullmatch(r'-?[1-9][0-9]*', chat):
        raise ValueError('EXISTING_TELEGRAM_DESTINATION_MISSING')
    if (expected and chat != expected) or (explicit and cached and explicit != cached):
        raise ValueError('TELEGRAM_DESTINATION_CONFLICT')
    return token, chat


def recover(c):
    # No duplicate blind retry after a crash between network acceptance and ACK.
    c.execute("UPDATE outbox SET state='UNCERTAIN' WHERE state='PENDING'")
    for row in c.execute("SELECT id FROM plans WHERE state='PUBLISH_PENDING'").fetchall():
        receipts = c.execute("SELECT state,ack_ms FROM outbox WHERE id LIKE ?", ('%:PLAN:'+row[0],)).fetchall()
        if len(receipts) == 1 and receipts[0]['state'] == 'ACK' and receipts[0]['ack_ms']:
            ack = receipts[0]['ack_ms']
            c.execute("UPDATE plans SET state='WAIT_ENTRY',ack_ms=?,entry_ms=? WHERE id=?",
                      (ack, (ack//MINUTE+1)*MINUTE, row[0]))
        else:
            c.execute("UPDATE plans SET state='DELIVERY_UNCERTAIN' WHERE id=?", (row[0],))
    c.commit()


def send(c, key, text, token, chat, now_ms, post=None, clock=None):
    if len(text) > 4096 or not token or not re.fullmatch(r'-?[1-9][0-9]*', str(chat)):
        raise ValueError('INVALID_TELEGRAM_ENVELOPE')
    target = hashlib.sha256((token.split(':')[0]+':'+str(chat)).encode()).hexdigest()
    key = target+':'+key
    c.execute('BEGIN IMMEDIATE')
    old = c.execute('SELECT state,ack_ms,receipt FROM outbox WHERE id=?', (key,)).fetchone()
    if old:
        c.rollback()
        return dict(state=old[0], ack_ms=old[1], message_id=old[2])
    c.execute('INSERT INTO outbox VALUES(?,?,NULL,?,NULL,?)',
              (key, now_ms, 'PENDING', encoded(dict(text=text, destination_hash=target))))
    c.commit()
    if post is None:
        import requests
        post = requests.post
    state, ack, mid = 'UNCERTAIN', None, None
    try:
        response = post(f'https://api.telegram.org/bot{token}/sendMessage',
                        json=dict(chat_id=chat, text=text, disable_web_page_preview=True), timeout=10)
        body = response.json()
        receipt = body.get('result') or {}
        if body.get('ok') and str((receipt.get('chat') or {}).get('id')) == str(chat) and isinstance(receipt.get('message_id'), int):
            state, mid = 'ACK', receipt['message_id']
            ack = (clock or (lambda: int(time.time()*1000)))()
        elif body.get('ok') is False:
            state = 'REJECTED'
    except Exception:
        # HTTP exceptions can contain credentials in their URL: never log them.
        pass
    c.execute('UPDATE outbox SET state=?,ack_ms=?,receipt=? WHERE id=?', (state, ack, mid, key))
    c.commit()
    return dict(state=state, ack_ms=ack, message_id=mid)


def enqueue(c, key, text, now_ms):
    c.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?)', (key, now_ms, 'QUEUED', text))


def flush_events(c, token, chat, now_ms, sender=send):
    if not token or not chat:
        return
    for event in c.execute("SELECT * FROM events WHERE state='QUEUED' ORDER BY created_ms").fetchall():
        receipt = sender(c, event['id'], event['text'], token, chat, now_ms)
        c.execute('UPDATE events SET state=? WHERE id=?', (receipt['state'], event['id']))
        c.commit()


def read_bars(kdb, symbol, interval, now_ms, limit=220, start=None):
    query = '''SELECT open_ms,close_ms,open,high,low,close,quote_volume,
        taker_buy_quote,source,received_ms FROM futures_closed_klines
        WHERE symbol=? AND interval=? AND close_ms<? AND received_ms<=?'''
    args = [symbol, interval, now_ms, now_ms]
    if start is not None:
        query += ' AND open_ms>=?'; args.append(start)
    query += ' ORDER BY open_ms '+('ASC' if start is not None else 'DESC')+' LIMIT ?'
    args.append(limit)
    rows = kdb.execute(query, args).fetchall()
    if any(r['source'] not in SOURCES for r in rows):
        raise ValueError('UNVERIFIED_CANDLE_SOURCE')
    return [dict(r) for r in (rows if start is not None else reversed(rows))]


def demand(limit=90, full_chart=False):
    now = int(time.time()*1000)
    urgent = []
    path = root()/DATABASE
    if path.exists():
        c = db(path, True)
        try:
            urgent = [r[0] for r in c.execute("SELECT DISTINCT symbol FROM plans WHERE state IN ('WAIT_ENTRY','TRACKING','DATA_GAP','OBSERVING_CLOSED')")]
        finally:
            c.close()
    values = universe(now)
    symbols = [r[0] for r in values]
    offset = (now//HOUR*24) % max(1, len(symbols))
    return list(dict.fromkeys(urgent+['BTCUSDT']+symbols[offset:]+symbols[:offset]))[:limit], urgent[:limit]


def publish(c, kdb, p, now_ms, token, chat, sender=send):
    if p['version'] != VERSION or p['config_hash'] != CONFIG_HASH or p['stage'] != 'READY' or not p['research_only']:
        return 'POLICY_REJECTED'
    if now_ms > p['expires_ms']:
        return 'EXPIRED'
    if c.execute('SELECT 1 FROM plans WHERE id=?', (p['id'],)).fetchone():
        return 'EXISTING_EPISODE'
    active = c.execute("SELECT symbol FROM plans WHERE state IN ('WAIT_ENTRY','TRACKING','DATA_GAP','PUBLISH_PENDING','DELIVERY_UNCERTAIN')").fetchall()
    if p['symbol'] in {r[0] for r in active}:
        return 'SYMBOL_ALREADY_ACTIVE'
    if len(active) >= CONFIG['max_active_plans']:
        return 'ACTIVE_PLAN_LIMIT'
    count = c.execute("SELECT COUNT(*) FROM plans WHERE created_ms>=? AND state!='DELIVERY_REJECTED'", (now_ms-24*HOUR,)).fetchone()[0]
    if count >= CONFIG['max_notices_24h']:
        return 'DAILY_NOTICE_LIMIT'
    price = quote(kdb, p['symbol'], now_ms)
    if price is None:
        return 'LIVE_QUOTE_STALE'
    ok, _ = executable(p, price)
    if not ok:
        return 'LIVE_GEOMETRY_INVALID'
    from long_short_major_context import capture
    context = capture(kdb, p['symbol'], p['direction'], now_ms)
    plan = dict(p, publication_price=price, supporting_context=context)
    c.execute('INSERT INTO plans VALUES(?,?,?,?,NULL,NULL,?,?,NULL,NULL)',
              (p['id'], p['symbol'], p['direction'], now_ms, 'PUBLISH_PENDING', encoded(plan)))
    c.commit()
    receipt = sender(c, 'PLAN:'+p['id'], message(plan, price, context), token, chat, now_ms)
    ack = receipt.get('ack_ms')
    state = 'WAIT_ENTRY' if receipt['state'] == 'ACK' else 'DELIVERY_REJECTED' if receipt['state'] == 'REJECTED' else 'DELIVERY_UNCERTAIN'
    entry_ms = (ack//MINUTE+1)*MINUTE if ack else None
    c.execute('UPDATE plans SET state=?,ack_ms=?,entry_ms=? WHERE id=?', (state, ack, entry_ms, p['id']))
    c.commit()
    return state


def scan(c, kdb, now_ms, token=None, chat=None):
    values = universe(now_ms)
    ready, errors, scanned = [], {}, 0
    for symbol, volume, _, _ in values:
        for interval in ('15m', '1h'):
            expected = now_ms//INTERVALS[interval]*INTERVALS[interval]-1
            if c.execute('SELECT 1 FROM scans WHERE symbol=? AND interval=? AND bar_ms=?', (symbol, interval, expected)).fetchone():
                continue
            try:
                rows = read_bars(kdb, symbol, interval, now_ms)
                context = read_bars(kdb, symbol, '4h', now_ms)
                plans = assess(symbol, interval, rows, context, now_ms)
                c.execute('INSERT OR IGNORE INTO scans VALUES(?,?,?,?,?)',
                          (symbol, interval, expected, now_ms, encoded(dict(quote_volume_24h=volume, candidates=len(plans)))))
                scanned += 1
                for p in plans:
                    c.execute('INSERT OR IGNORE INTO candidates VALUES(?,?,?,?,?)',
                              (p['id'], symbol, now_ms, p['stage'], encoded(p)))
                    if p['stage'] == 'READY':
                        ready.append(p)
            except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                key = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                errors[key] = errors.get(key, 0)+1
                c.execute('INSERT OR REPLACE INTO scan_failures VALUES(?,?,?,?,?)',
                          (symbol, interval, now_ms//(15*MINUTE), now_ms, key))
    c.commit()
    # Include still-valid waiting candidates: a temporary quote gap is retryable,
    # but an uncertain network delivery is not.
    stored = c.execute("SELECT payload FROM candidates WHERE decision IN ('READY','LIVE_QUOTE_STALE','LIVE_GEOMETRY_INVALID') AND observed_ms>=?",
                       (now_ms-CONFIG['ready_minutes']*MINUTE,)).fetchall()
    ready = list({p['id']: p for p in ready+[json.loads(r[0]) for r in stored]}.values())
    ready.sort(key=lambda p: (-p['geometry']['rr_after_assumed_cost'], p['confirmation_ms'], p['id']))
    decisions = {}
    for p in ready:
        status = publish(c, kdb, p, int(time.time()*1000), token, chat) if token and chat else 'NOTIFICATIONS_DISABLED'
        decisions[status] = decisions.get(status, 0)+1
        c.execute('UPDATE candidates SET decision=? WHERE id=?', (status, p['id']))
        c.commit()
    coverage = dict(observed_ms=now_ms, universe=len(values), newly_scanned=scanned,
                    failed_symbol_intervals=errors, publication=decisions)
    c.execute('INSERT OR REPLACE INTO health VALUES(?,?)', (now_ms//(15*MINUTE), encoded(coverage)))
    c.commit()
    return coverage


def measure(c, kdb, now_ms, token=None, chat=None, sender=send):
    for row in c.execute("SELECT * FROM plans WHERE state IN ('WAIT_ENTRY','TRACKING','DATA_GAP','OBSERVING_CLOSED')").fetchall():
        p = json.loads(row['payload'])
        progress = json.loads(row['progress']) if row['progress'] else None
        start = progress['cursor_ms'] if progress else row['entry_ms']
        bars = read_bars(kdb, p['symbol'], '1m', now_ms, limit=2000, start=start)
        if not progress:
            if start > p['expires_ms']:
                c.execute("UPDATE plans SET state='ENTRY_CANCELLED' WHERE id=?", (p['id'],))
                enqueue(c, 'CANCEL:'+p['id'], f'⚪ {p["symbol"]} {p["direction"]} · DENEME GİRİŞİ İPTAL\nMesaj kabulünden sonra giriş süresi doldu. Plan: {p["id"][:10]}', now_ms)
                c.commit(); continue
            if bars and bars[0]['open_ms'] == start:
                try:
                    progress = initial(p, start, bars[0]['open'])
                except ValueError:
                    c.execute("UPDATE plans SET state='ENTRY_CANCELLED' WHERE id=?", (p['id'],))
                    enqueue(c, 'CANCEL:'+p['id'], f'⚪ {p["symbol"]} {p["direction"]} · DENEME GİRİŞİ İPTAL\nMesajdan sonraki referans fiyatta süre/giriş–SL–TP şartları korunmadı.\nPlan: {p["id"][:10]}', now_ms)
                    c.commit()
                    continue
            elif now_ms > start+24*HOUR:
                c.execute("UPDATE plans SET state='UNRESOLVED_ENTRY' WHERE id=?", (p['id'],)); c.commit(); continue
            else:
                continue
        had_exit = bool(progress['exit'])
        prior_gap = progress['gap_ms']
        progress = advance(p, progress, bars)
        if progress['cursor_ms']+2*MINUTE < now_ms and not bars:
            progress['gap_ms'] = progress['cursor_ms']
        outcome = result(p, progress)
        state = ('COMPLETE' if outcome['observation_complete'] else 'OBSERVING_CLOSED' if progress['exit']
                 else 'DATA_GAP' if progress['gap_ms'] is not None else 'TRACKING')
        if now_ms > progress['expires_ms']+24*HOUR and not outcome['observation_complete']:
            state = 'CLOSED_PATH_INCOMPLETE' if progress['exit'] else 'UNRESOLVED_PATH'
        c.execute('UPDATE plans SET state=?,progress=?,result=? WHERE id=?',
                  (state, encoded(progress), encoded(outcome), p['id']))
        if prior_gap is None and progress['gap_ms'] is not None and not progress['exit']:
            enqueue(c, 'GAP:'+p['id']+':'+str(progress['gap_ms']),
                    f'⚪ {p["symbol"]} {p["direction"]} · TAKİP VERİSİ EKSİK\n'
                    f'Dakika yolunda boşluk var; bu aralık için SL/TP sonucu bilinmiyor. Plan: {p["id"][:10]}', now_ms)
        if not had_exit and progress['exit']:
            ex = progress['exit']
            enqueue(c, 'EXIT:'+p['id'], f'📋 {p["symbol"]} {p["direction"]} · DENEME {ex["kind"]}\n'
                   f'Kağıt giriş: {progress["entry"]:.8g} · Çıkış: {ex["price"]:.8g}\n'
                   f'Komisyon/kayma varsayımı sonrası: %{outcome["fee_slippage_only_pct"]:+.2f}; funding bilinmiyor.\n'
                   f'Sonraki fiyat hareketleri bu işlem sonucuna eklenmez. Plan: {p["id"][:10]}', now_ms)
        c.commit()
    flush_events(c, token, chat, now_ms, sender)


def report(c):
    out = dict(version=VERSION, states=dict(c.execute('SELECT state,COUNT(*) FROM plans GROUP BY state')),
               directions={}, estimated_portfolio_performance=None)
    for direction in ('LONG', 'SHORT'):
        rows = c.execute('SELECT state,result FROM plans WHERE direction=?', (direction,)).fetchall()
        results = [json.loads(r['result']) for r in rows if r['result']]
        closed = [r for r in results if r['exit']]
        out['directions'][direction] = dict(notices=len(rows), closed=len(closed),
            positive_fee_only=sum(r['fee_slippage_only_pct'] > 0 for r in closed),
            missing_full_net=sum(r['full_net_pct'] is None for r in results),
            closed_fee_only_mean_pct=sum(r['fee_slippage_only_pct'] for r in closed)/len(closed) if closed else None,
            denominator_note='Closed plan description only; pending, cancelled and unknown counts are retained in states. No hit-rate claim.')
    return out


def health(directory):
    path = Path(directory)/DATABASE
    if not path.exists():
        return dict(profile=PROFILE, available=False)
    c = db(path, True)
    try:
        row = c.execute('SELECT payload FROM health ORDER BY bucket DESC LIMIT 1').fetchone()
        return dict(profile=PROFILE, available=True, latest_scan=json.loads(row[0]) if row else None,
                    **report(c))
    finally:
        c.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if os.getenv('LS_RUNTIME_PROFILE') != PROFILE:
        raise SystemExit('EXPLICIT_PATTERN_PROFILE_REQUIRED')
    c = connect()
    if args.report:
        print(encoded(report(c))); c.close(); return
    recover(c)
    token = chat = None
    if os.getenv('PATTERN_NOTIFICATIONS', '0') == '1':
        token, chat = destination()
        receipt = send(c, 'START:'+VERSION, '🧪 LONG / SHORT grafik motoru · DENEME\n'
             'Yeni sürüm başladı. Bu mesaj Telegram bağlantı testidir; işlem sinyali değildir.\n'
             '15dk ve 1s formasyonları, 4s bağlamı, Fibonacci, yapısal SL/TP hesaplanacak.\n'
             'Güncel veri ve giriş şartları doğrulanınca ayrı bildirim gelir. Günlük en fazla 10 plan.\n'
             'Başarı oranı henüz ölçülmedi; otomatik emir açılmaz.', token, chat, int(time.time()*1000))
        print('PATTERN_STARTUP_DELIVERY '+receipt['state'], flush=True)
        if receipt['state'] != 'ACK':
            raise RuntimeError('TELEGRAM_STARTUP_NOT_ACKNOWLEDGED')
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    while not STOP.is_set():
        now = int(time.time()*1000)
        kdb = None
        try:
            kdb = db(root()/'binance_futures_kline_shadow.db', True)
            measure(c, kdb, now, token, chat)
            coverage = scan(c, kdb, now, token, chat)
            print('PATTERN_HEALTH '+encoded(coverage), flush=True)
            if token and chat and coverage['failed_symbol_intervals']:
                send(c, 'PARTIAL:'+str(now//(6*HOUR)), '⚪ Grafik motoru · veri kapsamı\n'
                     f'Evren: {coverage["universe"]} coin. Bu taramada '
                     f'{sum(coverage["failed_symbol_intervals"].values())} coin/mum aralığı doğrulanamadı.\n'
                     'Eksik veya eski verili coinlerde plan üretilmiyor. Kapsam tam değil.', token, chat, now)
        except (sqlite3.Error, ValueError, KeyError, OSError) as exc:
            print('PATTERN_UNAVAILABLE '+type(exc).__name__, flush=True)
            if token and chat:
                send(c, 'UNAVAILABLE:'+str(now//(6*HOUR)),
                     '⚪ Grafik motoru: gerekli canlı veri doğrulanamıyor. Yeni plan gönderilmiyor; '
                     'bu süre başarılı tarama veya sıfır fırsat olarak sayılmaz.', token, chat, now)
        finally:
            if kdb is not None: kdb.close()
        if args.once: break
        STOP.wait(30)
    c.close()


if __name__ == '__main__':
    main()
