"""Read the existing collector DB; no collectors, deployments or orders started.

python -m structure_engine.runtime --source-db PATH --state-dir PATH --once
Only --notify plus explicit env opt-in permits Telegram. Default is local only.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time

from .config import CONFIG as C, CONFIG_HASH, PERIODS, VERSION, identity
from .core import assess, message
from . import store
from .outcomes import measure

SOURCES = {'BINANCE_UM_FUTURES_WEBSOCKET', 'BINANCE_UM_FUTURES_ARCHIVE', 'BINANCE_UM_FUTURES_REST'}


def read_frames(c, symbol, interval, now_ms):
    frames = {}
    for tf in (interval, '1h', '4h'):
        found = c.execute('''SELECT open_ms,close_ms,open,high,low,close,quote_volume,
            taker_buy_quote,source,received_ms FROM futures_closed_klines
            WHERE symbol=? AND interval=? AND close_ms<? AND received_ms<=?
            ORDER BY open_ms DESC LIMIT ?''', (symbol, tf, now_ms, now_ms, C['maximum_bars'])).fetchall()
        if any(b['source'] not in SOURCES for b in found):
            raise ValueError('UNVERIFIED_SOURCE:'+tf)
        frames[tf] = [dict(b) for b in reversed(found)]
    return frames


def scan(c, source, symbols, now_ms, directory, interval='15m', notify=False):
    health = dict(version=VERSION, config_hash=CONFIG_HASH, observed_ms=now_ms,
                  interval=interval, requested=len(symbols), scanned=0, already_scanned=0,
                  stages=Counter(), issues=Counter(), policies=Counter(), notices=Counter())
    sent = 0
    for symbol in symbols:
        try:
            frames = read_frames(source, symbol, interval, now_ms)
            last = frames[interval][-1]['close_ms'] if frames[interval] else None
            key = identity(VERSION, symbol, interval, last)
            if last and c.execute('SELECT 1 FROM scans WHERE id=?', (key,)).fetchone():
                health['already_scanned'] += 1
                continue
            a = assess(symbol, frames, now_ms, interval)
            added = store.save(c, a, frames, directory)
            health['scanned'] += 1
            if a['status'] == 'DATA_INVALID':
                health['issues'].update(a['data_errors'])
                continue
            if a['status'] == 'ILLIQUID':
                health['issues']['ILLIQUID'] += 1
                continue
            store.lifecycle(c, symbol, interval, frames[interval], now_ms)
            for p in sorted(added, key=lambda p: (p['stage'] != 'CONDITION_MET', p['event_id'])):
                health['stages'][p['stage']] += 1
                policy = store.notification_reason(c, p, now_ms, sent) if notify else 'LOCAL_ONLY'
                health['policies'][policy] += 1
                c.execute('INSERT OR IGNORE INTO policies VALUES(?,?,?)', (p['event_id'], policy, now_ms))
                c.commit()
                if policy not in ('ALLOW', 'LOCAL_ONLY'):
                    continue
                from .chart import render
                image = Path(directory)/'charts'/(p['event_id']+'.png')
                render(a, frames, p, image)
                image.with_suffix('.txt').write_text(message(p), encoding='utf-8')
                if notify:
                    states = store.publish(c, p, image, message(p), now_ms,
                                           os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID'])
                    health['notices'].update(states)
                    sent += 1
        except (ValueError, KeyError, sqlite3.Error, OSError) as exc:
            # Exceptions here are local DB/data errors. Never include credentials.
            health['issues'][type(exc).__name__+':'+str(exc)[:120]] += 1
    return health


def measure_pending(c, source, now_ms):
    """Research starts after observation; delivered cohort after both receipts.

    All updates are append-only, including missing paths. Bound each minute read
    and each pass. Funding and actual fills are not inferred from candle data.
    """
    counts = Counter()
    pending = c.execute('''SELECT e.* FROM events e WHERE NOT EXISTS
        (SELECT 1 FROM results r WHERE r.event_id=e.id AND
         json_extract(r.payload,'$.cohort')='RESEARCH' AND
         (json_extract(r.payload,'$.observation_complete')=1 OR json_extract(r.payload,'$.final_unresolved')=1))
         OR ((SELECT COUNT(*) FROM notices n WHERE n.event_id=e.id AND n.state='ACK')=2 AND NOT EXISTS
         (SELECT 1 FROM results r WHERE r.event_id=e.id AND json_extract(r.payload,'$.cohort')='DELIVERED'
          AND (json_extract(r.payload,'$.observation_complete')=1 OR json_extract(r.payload,'$.final_unresolved')=1)))
         ORDER BY e.asof_ms LIMIT 100''').fetchall()
    for event in pending:
        original = json.loads(event['payload'])
        cohorts = [('RESEARCH', original)]
        receipts = [json.loads(r[0]) for r in c.execute("SELECT receipt FROM notices WHERE event_id=? AND state='ACK'", (event['id'],))]
        if len(receipts) == 2 and all(r.get('ack_ms') for r in receipts):
            delivered = dict(original, asof_ms=max(r['ack_ms'] for r in receipts))
            cohorts.append(('DELIVERED', delivered))
        for cohort, p in cohorts:
            if c.execute('''SELECT 1 FROM results WHERE event_id=? AND json_extract(payload,'$.cohort')=?
              AND (json_extract(payload,'$.observation_complete')=1 OR json_extract(payload,'$.final_unresolved')=1) LIMIT 1''',
              (event['id'], cohort)).fetchone():
                continue
            start = (p['asof_ms']//60_000+C['entry_delay_minutes'])*60_000
            end = start+C['horizon_hours']*3_600_000
            if now_ms < start+60_000: continue
            bars = source.execute('''SELECT open_ms,close_ms,open,high,low,close,source FROM futures_closed_klines
                WHERE symbol=? AND interval='1m' AND open_ms>=? AND open_ms<?
                AND close_ms<? AND received_ms<=? ORDER BY open_ms LIMIT 181''',
                (p['symbol'], start, end, now_ms, now_ms)).fetchall()
            if any(b['source'] not in SOURCES for b in bars):
                result = dict(status='UNVERIFIED_OUTCOME_SOURCE', observation_complete=False)
            else:
                result = measure(p, [dict(b) for b in bars])
            result.update(cohort=cohort, final_unresolved=now_ms>end+3_600_000 and not result.get('observation_complete'))
            key = identity(event['id'], cohort, now_ms)
            c.execute('INSERT OR IGNORE INTO results VALUES(?,?,?,?)',
                      (key, event['id'], now_ms, store.encoded(result)))
            counts[cohort+':'+result['status']] += 1
    c.commit()
    return counts


def notify_terminal(c, now_ms):
    counts = Counter()
    for transition in c.execute('SELECT * FROM transitions ORDER BY asof_ms').fetchall():
        row = c.execute('''SELECT e.payload FROM events e JOIN notices n ON n.event_id=e.id
          WHERE e.idea_id=? AND n.state='ACK' ORDER BY e.asof_ms DESC LIMIT 1''', (transition['idea_id'],)).fetchone()
        if not row: continue
        p = json.loads(row[0]); p['event_id'] = transition['id']
        p['stage'] = transition['state']
        if c.execute('SELECT 1 FROM notices WHERE event_id=?', (p['event_id'],)).fetchone(): continue
        label = 'FİKİR BOZULDU' if p['stage'] == 'INVALIDATED' else 'İZLEME SÜRESİ DOLDU'
        text = f"⚪ {p['symbol']} · {p['direction']} · {label}\nÖnceki {p['interval']} izleme fikri kapandı. Yeni giriş çağrısı değildir."
        states = store.publish(c, p, None, text, now_ms, os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID'])
        counts.update(states)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--interval', choices=['15m', '30m'], default='15m')
    parser.add_argument('--symbols', help='Comma-separated subset; default uses verified full universe')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--notify', action='store_true')
    args = parser.parse_args()
    if args.notify:
        if os.getenv('STRUCTURE_NOTIFICATIONS') != '1' or os.getenv('LS_RUNTIME_PROFILE') != 'STRUCTURE_CONTEXT_V1':
            raise SystemExit('EXPLICIT_STRUCTURE_NOTIFICATION_OPT_IN_REQUIRED')
        if not os.getenv('TELEGRAM_CHAT_ID') or not os.getenv('TELEGRAM_BOT_TOKEN'):
            raise SystemExit('EXPLICIT_EXISTING_TELEGRAM_DESTINATION_REQUIRED')
    if not args.source_db.is_file():
        raise SystemExit('SOURCE_DB_MISSING_NO_NEW_COLLECTOR_STARTED')
    c = store.connect(args.state_dir/'structure_context_v1.db')
    c.execute("UPDATE notices SET state='UNCERTAIN' WHERE state='PENDING'")
    c.commit()
    source = sqlite3.connect(args.source_db.resolve().as_uri()+'?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    source.execute('PRAGMA cache_size=-2048')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.is_set():
            limits = store.resource_check(args.state_dir.parent)
            if not limits['ok']:
                print('STRUCTURE_RESOURCE_HALT '+store.encoded(limits), flush=True)
                raise SystemExit(2)
            now = int(time.time()*1000)
            if args.symbols:
                symbols = list(dict.fromkeys(args.symbols.split(',')))
            else:
                from long_short_swing_runtime import universe
                symbols = [r[0] for r in universe(now)]
            health = scan(c, source, symbols, now, args.state_dir, args.interval, args.notify)
            health['outcomes'] = measure_pending(c, source, now)
            if args.notify:
                health['terminal_notices'] = notify_terminal(c, now)
            c.execute('INSERT OR IGNORE INTO health VALUES(?,?)', (now, store.encoded(health)))
            c.commit()
            print('STRUCTURE_HEALTH '+store.encoded(health), flush=True)
            if args.once:
                break
            # One read pass per closed setup candle, with a short retry for missing data.
            delay = 30 if health['issues'] else max(1, ((now//PERIODS[args.interval]+1)*PERIODS[args.interval]-now)/1000+2)
            stop.wait(delay)
    finally:
        source.close()
        c.close()


if __name__ == '__main__':
    main()
