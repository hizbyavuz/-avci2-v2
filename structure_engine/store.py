"""Independent append-only evidence, frozen snapshots, bounded notification policy."""
import gzip
import json
import os
from pathlib import Path
import resource
import shutil
import sqlite3
import time

from .config import CONFIG as C, CONFIG_HASH, HOUR, VERSION, identity


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def code_hash():
    import hashlib
    directory = Path(__file__).parent
    return hashlib.sha256(b''.join(p.name.encode()+p.read_bytes() for p in sorted(directory.glob('*.py')))).hexdigest()


def resource_check(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    used = sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())/1048576
    free = shutil.disk_usage(directory).free/1048576
    # Linux ru_maxrss is KiB. This is a conservative peak RSS guard, not billing.
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    reasons = []
    if used >= C['state_directory_limit_mb']: reasons.append('STATE_STORAGE_LIMIT')
    if free < C['disk_free_floor_mb']: reasons.append('DISK_FREE_FLOOR')
    if rss > C['memory_rss_limit_mb']: reasons.append('PROCESS_MEMORY_LIMIT')
    return dict(ok=not reasons, reasons=reasons, state_mb=used, disk_free_mb=free, peak_rss_mb=rss)


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA cache_size=-2048')
    c.executescript('''
      CREATE TABLE IF NOT EXISTS manifests(version TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY,asof_ms INTEGER,symbol TEXT,interval TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,idea_id TEXT,symbol TEXT,interval TEXT,
        stage TEXT,condition TEXT,asof_ms INTEGER,payload TEXT,snapshot TEXT);
      CREATE INDEX IF NOT EXISTS event_idea ON events(idea_id,asof_ms);
      CREATE TABLE IF NOT EXISTS transitions(id TEXT PRIMARY KEY,idea_id TEXT,asof_ms INTEGER,state TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY,event_id TEXT,asof_ms INTEGER,state TEXT,receipt TEXT);
      CREATE TABLE IF NOT EXISTS policies(event_id TEXT PRIMARY KEY,reason TEXT,asof_ms INTEGER);
      CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY,event_id TEXT,asof_ms INTEGER,payload TEXT);
      CREATE INDEX IF NOT EXISTS result_event ON results(event_id,asof_ms);
      CREATE INDEX IF NOT EXISTS notice_event ON notices(event_id);
      CREATE TABLE IF NOT EXISTS health(asof_ms INTEGER PRIMARY KEY,payload TEXT);
    ''')
    manifest = encoded(dict(version=VERSION, config=C, config_hash=CONFIG_HASH, code_hash=code_hash()))
    old = c.execute('SELECT payload FROM manifests WHERE version=?', (VERSION,)).fetchone()
    if old and old[0] != manifest:
        c.close()
        raise ValueError('CODE_OR_CONFIG_CHANGED_WITHOUT_NEW_VERSION')
    c.execute('INSERT OR IGNORE INTO manifests VALUES(?,?)', (VERSION, manifest))
    c.commit()
    return c


def save(c, assessment, frames, directory):
    a = assessment
    scan_id = identity(VERSION, a['symbol'], a['interval'], a.get('setup_close_ms', a['asof_ms']))
    summary = {k: v for k, v in a.items() if k not in ('decisions', 'levels', 'context')}
    summary['stages'] = [p['stage'] for p in a['decisions']]
    summary['warnings'] = [w for p in a['decisions'] for w in p['warnings']]
    summary['context_directions'] = {tf: x['direction'] for tf, x in a['context'].items()}
    c.execute('INSERT OR IGNORE INTO scans VALUES(?,?,?,?,?)',
              (scan_id, a['asof_ms'], a['symbol'], a['interval'], encoded(summary)))
    added = []
    for p in a['decisions']:
        # A WATCH can recur at every candle; preserve the first snapshot only.
        if c.execute('SELECT 1 FROM events WHERE id=?', (p['event_id'],)).fetchone():
            continue
        path = Path(directory)/'snapshots'/(p['event_id']+'.json.gz')
        path.parent.mkdir(parents=True, exist_ok=True)
        body = encoded(dict(assessment=a, frames=frames, plan=p)).encode()
        if not path.exists():
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(gzip.compress(body, mtime=0))
            os.replace(temporary, path)
        c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',
                  (p['event_id'], p['idea_id'], p['symbol'], p['interval'], p['stage'], p['condition'],
                   p['asof_ms'], encoded(p), str(path)))
        added.append(p)
    c.commit()
    return added


def lifecycle(c, symbol, interval, bars, now_ms):
    """First invalidation/expiry of a saved idea, checked from every intervening bar."""
    for row in c.execute('SELECT * FROM events WHERE symbol=? AND interval=? ORDER BY asof_ms', (symbol, interval)):
        p = json.loads(row['payload'])
        key = identity(p['idea_id'], 'TERMINAL')
        if c.execute('SELECT 1 FROM transitions WHERE id=?', (key,)).fetchone():
            continue
        after = [b for b in bars if b['close_ms'] >= p['asof_ms'] and b['close_ms'] < now_ms]
        hit = next((b for b in after if (b['low'] <= p['stop'] if p['direction'] == 'LONG'
                                       else b['high'] >= p['stop'])), None)
        state = 'INVALIDATED' if hit else 'EXPIRED' if now_ms >= p['asof_ms']+C['horizon_hours']*HOUR else None
        if state:
            c.execute('INSERT OR IGNORE INTO transitions VALUES(?,?,?,?,?)',
                      (key, p['idea_id'], now_ms, state, encoded(dict(event_id=p['event_id'], bar=hit))))
    c.commit()


def notification_reason(c, p, now_ms, cycle_sent=0):
    if p['stage'] == 'LATE': return 'LATE_RECORDED_ONLY'
    if c.execute('SELECT 1 FROM transitions WHERE idea_id=?', (p['idea_id'],)).fetchone():
        return 'IDEA_TERMINAL'
    if now_ms-p['asof_ms'] > 15*60_000: return 'EVENT_STALE'
    if c.execute('SELECT 1 FROM notices WHERE event_id=?', (p['event_id'],)).fetchone():
        return 'ALREADY_ATTEMPTED'
    if cycle_sent >= C['max_notifications_per_cycle']: return 'CYCLE_LIMIT'
    attempted = c.execute("SELECT COUNT(DISTINCT event_id) FROM notices WHERE asof_ms>=?", (now_ms-24*HOUR,)).fetchone()[0]
    if attempted >= C['max_notifications_24h']: return 'DAILY_LIMIT'
    # Promotion of the same idea may pass the symbol cooldown; WATCH never may.
    if p['stage'] == 'WATCH':
        recent = c.execute('''SELECT 1 FROM notices n JOIN events e ON e.id=n.event_id
          WHERE e.symbol=? AND e.interval=? AND n.asof_ms>=? LIMIT 1''',
          (p['symbol'], p['interval'], now_ms-C['notification_cooldown_minutes']*60_000)).fetchone()
        if recent: return 'SYMBOL_COOLDOWN'
    return 'ALLOW'


def publish(c, p, image_path, text, now_ms, token, chat, post=None):
    """Explicit caller opt-in. Separate receipts for PNG and text; no blind retry."""
    import re
    if not token or not re.fullmatch(r'-?[1-9][0-9]*', str(chat)):
        raise ValueError('EXPLICIT_TELEGRAM_DESTINATION_REQUIRED')
    if len(text) > 4096: raise ValueError('MESSAGE_TOO_LONG')
    if post is None:
        import requests
        post = requests.post
    states = []
    for part in (('chart', 'text') if image_path is not None else ('text',)):
        key = identity(p['event_id'], part)
        old = c.execute('SELECT state FROM notices WHERE id=?', (key,)).fetchone()
        if old:
            states.append(old[0])
            if old[0] != 'ACK': break
            continue
        c.execute('INSERT INTO notices VALUES(?,?,?,?,?)', (key, p['event_id'], now_ms, 'PENDING', '{}'))
        c.commit()
        state, receipt = 'UNCERTAIN', {}
        try:
            if part == 'chart':
                with open(image_path, 'rb') as f:
                    r = post(f'https://api.telegram.org/bot{token}/sendPhoto',
                             data={'chat_id': str(chat), 'caption': f"{p['symbol']} · {p['direction']} · {p['interval']} · {p['stage']}"},
                             files={'photo': f}, timeout=15)
            else:
                r = post(f'https://api.telegram.org/bot{token}/sendMessage',
                         json={'chat_id': str(chat), 'text': text}, timeout=15)
            body = r.json()
            result = body.get('result', {})
            if body.get('ok') and str(result.get('chat', {}).get('id')) == str(chat) and isinstance(result.get('message_id'), int):
                state, receipt = 'ACK', {'message_id': result['message_id'], 'ack_ms': int(time.time()*1000)}
            elif body.get('ok') is False:
                state = 'REJECTED'
        except Exception:
            pass  # Credential-bearing URLs and exception text must not be logged.
        c.execute('UPDATE notices SET state=?,receipt=? WHERE id=?', (state, encoded(receipt), key))
        c.commit()
        states.append(state)
        if state != 'ACK': break
    return states
