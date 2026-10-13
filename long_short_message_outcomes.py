"""Read-only delivered-message direction audit. No scoring, orders or network IO.

Measures gross price direction from the displayed reference, not executable PnL.
Missing receipts/references/candles remain explicit; edits are not new trades.
"""
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SOURCES = {'BINANCE_UM_FUTURES_WEBSOCKET', 'BINANCE_UM_FUTURES_ARCHIVE',
           'BINANCE_UM_FUTURES_REST'}
HORIZONS = (15, 30, 60, 180)


def read_rows(path, query, args=()):
    if not Path(path).is_file():
        return []
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=5) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(query, args)]


def decoded(raw):
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def positive(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n > 0 else None
    except (ValueError, TypeError):
        return None


def text_reference(text):
    # Never use the trigger/target or first early price as the current reference.
    m = re.search(r'(?:Şu an fiyat|Son ölçülen fiyat):\s*([0-9.,]+)', text or '')
    return positive(m.group(1).replace(',', '')) if m else None


def evaluate(message, candle_db, now):
    price = positive(message.get('price'))
    side = message.get('direction')
    sent = message['sent_epoch']
    results = {}
    for minutes in HORIZONS:
        end_ms = int((sent + minutes*60)*1000)
        if sent + minutes*60 > now:
            results[str(minutes)] = {'status': 'PENDING_HORIZON'}
            continue
        if price is None or side not in ('LONG', 'SHORT'):
            results[str(minutes)] = {'status': 'MISSING_REFERENCE'}
            continue
        # Most recent FULLY CLOSED 1m candle at/before the requested horizon.
        rows = read_rows(candle_db, '''SELECT open_ms,close_ms,high,low,close,source
            FROM futures_closed_klines WHERE symbol=? AND interval='1m'
            AND close_ms<=? ORDER BY open_ms DESC LIMIT 1''', (message['symbol'], end_ms))
        if not rows or not 0 <= end_ms-rows[0]['close_ms'] < 60000:
            results[str(minutes)] = {'status': 'MISSING_ENDPOINT'}
            continue
        r = rows[0]
        end_price = positive(r['close'])
        if r['source'] not in SOURCES or end_price is None:
            results[str(minutes)] = {'status': 'UNVERIFIED_ENDPOINT'}
            continue
        sign = 1 if side == 'LONG' else -1
        pct = sign * (end_price/price-1)*100
        results[str(minutes)] = {'status': 'RESOLVED', 'direction_pct': round(pct, 5),
            'direction': 'RIGHT' if pct > 0 else 'WRONG' if pct < 0 else 'FLAT',
            'end_price': end_price, 'endpoint_lag_seconds': round((end_ms-r['close_ms'])/1000, 3)}
    return results


def collect(root, since, now):
    messages, issues = [], []
    root = Path(root)
    for path in sorted(root.glob('long_short_simple_notify*.db')):
        try:
            rows = read_rows(path, 'SELECT * FROM sent_alerts WHERE sent_at_epoch>=? AND sent_at_epoch<=?', (since, now))
            for r in rows:
                p = decoded(r.get('payload_json'))
                messages.append({'id': path.name+':'+r['fingerprint'], 'symbol':r['symbol'],
                    'direction':r['direction'].removeprefix('RADAR_'), 'sent_epoch':r['sent_at_epoch'],
                    'price':positive(p.get('price')), 'kind':p.get('stage', 'WATCH_UNKNOWN'),
                    'reference':'displayed_queued_price', 'delivery':'ACK', 'method':'core_queue'})
        except sqlite3.Error as exc:
            issues.append({'file':path.name, 'error':str(exc)[:100]})
    # Card receipts retain ACKs even when the card has since changed. Historical
    # receipts did not preserve text: only reconstruct from an ACKed transition
    # with matching observation timestamp; never use the current card for the past.
    card = root/'long_short_message_cards.db'
    try:
        receipts = read_rows(card, "SELECT * FROM deliveries WHERE status='ACK'")
        for r in receipts:
            stamp = epoch(r['time_utc'])
            if not since <= stamp <= now:
                continue
            if any(m['symbol']==r['symbol'] and abs(m['sent_epoch']-stamp)<5
                   and m['method']=='core_queue' for m in messages):
                continue  # Same accepted core delivery, not another recommendation.
            detail = decoded(r.get('detail'))
            text = detail.get('source_text', detail.get('text', ''))
            if detail.get('accepted_at_utc'):
                stamp = epoch(detail['accepted_at_utc'])
            direction = detail.get('direction')
            price = text_reference(text)
            kind = detail.get('source', 'UNKNOWN_CARD')
            reference = 'immutable_delivered_text' if text else 'missing_historical_text'
            if not text:
                transitions = read_rows(root/'long_short_breakout_observer.db', '''
                    SELECT t.new_state,t.features_json,e.direction FROM transitions t
                    JOIN episodes e ON e.id=t.episode_id WHERE e.symbol=?
                    AND t.delivery_status='ACK' AND ABS(t.observed_ms-?)<2''',
                    (r['symbol'], round(stamp*1000)))
                if len(transitions)==1:
                    tr = transitions[0]
                    direction = tr['direction']
                    price = positive(decoded(tr['features_json']).get('price'))
                    kind = 'EARLY_'+tr['new_state']
                    reference = 'matching_acked_transition'
            elif detail.get('source') == 'EARLY':
                header = text.splitlines()[0]
                kind = ('EARLY_TERMINAL' if any(x in header for x in ('BOZULDU','KOVALAMA','UZAKLAŞTI','SÜRESİ'))
                        else 'EARLY_CONFIRMED' if 'KAPANIŞ' in header else 'EARLY_EARLY')
            messages.append({'id':card.name+':'+str(r['id']), 'symbol':r['symbol'],
                'direction':direction, 'price':price, 'sent_epoch':stamp, 'kind':kind,
                'reference':reference, 'delivery':'ACK', 'method':r['method']})
    except (sqlite3.Error, ValueError, TypeError) as exc:
        issues.append({'file':card.name, 'error':str(exc)[:100]})
    # Direct confirmations predate unified cards; exclude duplicates by symbol/time.
    for path in sorted(root.glob('long_short_live_pool*.db')):
        try:
            for r in read_rows(path, "SELECT * FROM events WHERE telegram_status='SENT'"):
                if not r.get('telegram_sent_time_utc'):
                    continue
                stamp = epoch(r['telegram_sent_time_utc'])
                if not since <= stamp <= now or any(m['symbol']==r['symbol'] and abs(m['sent_epoch']-stamp)<5 for m in messages):
                    continue
                messages.append({'id':path.name+':'+str(r['id']), 'symbol':r['symbol'],
                    'direction':r['direction'], 'price':positive(r['price']), 'sent_epoch':stamp,
                    'kind':r['stage_to'], 'reference':'sent_event_price', 'delivery':'SENT', 'method':'direct'})
        except (sqlite3.Error, ValueError, TypeError) as exc:
            issues.append({'file':path.name, 'error':str(exc)[:100]})
    return sorted(messages, key=lambda m:m['sent_epoch']), issues


def report(root, now=None, hours=24):
    now = time.time() if now is None else now
    messages, issues = collect(root, now-hours*3600, now)
    excluded = {'EARLY_BROKEN','EARLY_CHASE','EARLY_EXPIRED','EARLY_TERMINAL','INVALIDATED'}
    counts = {}
    for m in messages:
        m['sent_utc'] = datetime.fromtimestamp(m['sent_epoch'],timezone.utc).isoformat()
        m['included_as_direction_idea'] = m['kind'] not in excluded
        try:
            m['outcomes'] = evaluate(m, Path(root)/'binance_futures_kline_shadow.db', now)
        except sqlite3.Error:
            m['outcomes'] = {str(h):{'status':'CANDLE_DB_UNAVAILABLE'} for h in HORIZONS}
        if m['included_as_direction_idea']:
            for h, outcome in m['outcomes'].items():
                bucket = counts.setdefault(m['kind'], {}).setdefault(h, Counter())
                bucket[outcome.get('direction', outcome['status'])] += 1
    return {'version':'MESSAGE_DIRECTION_AUDIT_2026_10_14', 'hours':hours,
        'read_only':True, 'scope':'acknowledged retained messages and card updates; grouped by kind; gross direction, not trade profit',
        'endpoint_rule':'last closed Binance Futures minute at/before delivery plus horizon; lag under 60 seconds',
        'messages':messages, 'by_kind':counts, 'issues':issues}


def emit_report(root):
    result = report(root)
    print('MESSAGE_DIRECTION_SUMMARY '+json.dumps({k:v for k,v in result.items() if k!='messages'},ensure_ascii=False),flush=True)
    for m in result['messages']:
        print('MESSAGE_DIRECTION_RESULT '+json.dumps(m,ensure_ascii=False),flush=True)
    return result


if __name__ == '__main__':
    emit_report(os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state')
