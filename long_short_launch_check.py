"""Read only the existing volume before a LONG launch. No network or workers.

Run once and exit. Never print tokens, chat identifiers, URLs or message text.
Success means this inspection ran, not that the signal service is operational.
"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path

VERSION = 'LONG_LAUNCH_CHECK_V1_2026_10_22'
HOUR = 3_600_000
INVENTORY = {
    'binance_futures_kline_shadow.db': ('futures_closed_klines',),
    'binance_futures_universe.db': ('futures_tickers',),
    'native_derivatives_v2.db': ('market_metadata', 'markets', 'snapshots'),
    'major_move_v1.db': ('research_ideas', 'assessments', 'market_snapshots', 'ideas'),
    'swing_structure_v1.db': ('manifest', 'assessments', 'episodes', 'references_v1', 'deliveries'),
    'long_short_message_cards.db': ('cards', 'deliveries'),
}
TIME_COLUMNS = ('open_ms', 'close_ms', 'observed_ms', 'received_ms',
                'created_ms', 'reference_ms', 'ack_ms', 'updated_ms')


def read_db(path):
    c = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
    c.execute('PRAGMA query_only=ON')
    deadline = time.monotonic()+5
    c.set_progress_handler(lambda: int(time.monotonic()>deadline), 10000)
    return c


def inventory(path, tables):
    if not path.is_file():
        return dict(status='MISSING')
    out = dict(status='PRESENT', bytes=path.stat().st_size, tables={})
    try:
        c = read_db(path)
        try:
            for table in tables:
                cols = {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}
                if not cols:
                    out['tables'][table] = dict(status='TABLE_MISSING')
                    continue
                stamps = [name for name in TIME_COLUMNS if name in cols]
                fields = ['COUNT(*)']+[f'MIN("{k}"),MAX("{k}")' for k in stamps]
                row = c.execute(f'SELECT {",".join(fields)} FROM "{table}"').fetchone()
                info = dict(count=row[0], time_ranges={})
                for i, key in enumerate(stamps):
                    info['time_ranges'][key] = [row[1+i*2], row[2+i*2]]
                out['tables'][table] = info
                if table == 'futures_closed_klines':
                    info['intervals'] = [dict(interval=r[0], rows=r[1], symbols=r[2],
                                              last_close_ms=r[3]) for r in c.execute(
                        'SELECT interval,COUNT(*),COUNT(DISTINCT symbol),MAX(close_ms) '
                        'FROM futures_closed_klines GROUP BY interval')]
        finally:
            c.close()
    except (sqlite3.Error, OSError) as exc:
        out.update(status='READ_INCOMPLETE', error_type=type(exc).__name__)
    return out


def telegram_status(root, env):
    out = dict(token_configured=bool(env.get('TELEGRAM_BOT_TOKEN', '').strip()),
               explicit_destination=bool(env.get('TELEGRAM_CHAT_ID', '').strip()),
               cached_destination=False, destination_match=None,
               recipient_verified=False, delivery_tested=False)
    path = root/'long_short_telegram_chat_cache.db'
    if not path.is_file():
        return out
    try:
        c = read_db(path)
        try:
            row = c.execute("SELECT value FROM runtime_settings WHERE key='telegram_chat_id'").fetchone()
            cached = str(row[0]).strip() if row else ''
            out['cached_destination'] = bool(re.fullmatch(r'-?[1-9][0-9]*', cached))
            configured = env.get('TELEGRAM_CHAT_ID', '').strip()
            if configured and out['cached_destination']:
                out['destination_match'] = configured == cached
        finally:
            c.close()
    except (sqlite3.Error, OSError) as exc:
        out['error_type'] = type(exc).__name__
    return out


def inspect(root, env, now_ms=None):
    now_ms = int(time.time()*1000) if now_ms is None else now_ms
    root = Path(root)
    data = {name: inventory(root/name, tables) for name, tables in INVENTORY.items()}
    intervals = data['binance_futures_kline_shadow.db'].get('tables', {}).get(
        'futures_closed_klines', {}).get('intervals', [])
    latest = {r['interval']: r['last_close_ms'] for r in intervals}
    freshness = {}
    for interval, allowed in [('1m', 5*60_000), ('1h', HOUR)]:
        stamp = latest.get(interval)
        age = now_ms-stamp if isinstance(stamp, (int, float)) else None
        freshness[interval] = dict(age_ms=age, recent=age is not None and 0 <= age <= allowed,
                                   note='Latest row only; full symbol coverage is not certified.')
    return dict(version=VERSION, checked_ms=now_ms,
                state_directory_exists=root.is_dir(), databases=data, latest_candle_freshness=freshness,
                telegram=telegram_status(root, env),
                profile=env.get('LS_RUNTIME_PROFILE') == 'SWING_STRUCTURE_V1',
                notifications_enabled=env.get('SWING_NOTIFICATIONS', '0') == '1',
                data_service_configured=bool(env.get('LS_FUTURES_HISTORY_RELAY_URL', '').strip()),
                runtime_network_requests=0, live_feed_verified=False, signal_engine_started=False,
                status='INSPECTION_ONLY_NOT_A_SIGNAL_LAUNCH')


def main():
    root = os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state'
    print('LONG_LAUNCH_CHECK '+json.dumps(inspect(root, os.environ), sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
